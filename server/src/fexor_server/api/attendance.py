"""Attendance events, review queue, daily records, dashboard and reports."""

import csv
import io
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from fexor_core import crypto
from openpyxl import Workbook
from openpyxl.styles import Font
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import (
    AdminUser,
    AttendanceDay,
    AttendanceEvent,
    Device,
    EventStatus,
    FaceTemplate,
    Membership,
    Person,
    Role,
    Shift,
    Space,
)
from ..security import Principal, audit, current, now, require_space
from ..services import event_dates, recompute, recompute_space
from .devices import ONLINE_WINDOW, notify_webhooks

router = APIRouter(prefix="/api", tags=["attendance"])
MAX_RANGE_DAYS = 400


def today(space: Space) -> date:
    return datetime.now(ZoneInfo(space.timezone)).date()


def _range(space: Space, start: date | None, end: date | None) -> tuple[date, date]:
    end = end or today(space)
    start = start or end
    if start > end:
        raise HTTPException(422, "start after end")
    if (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(422, f"range too long (max {MAX_RANGE_DAYS} days)")
    return start, end


# ---------- events ----------
def event_out(e: AttendanceEvent, name: str | None, ext: str | None, device: str | None,
              reviewer: str | None = None) -> dict:
    return {"id": str(e.id), "member_id": str(e.membership_id) if e.membership_id else None,
            "member_name": name, "external_id": ext, "device": device,
            "occurred_at": e.occurred_at, "received_at": e.received_at, "status": e.status,
            "source": e.source, "reason": e.reason, "scores": e.scores, "note": e.note,
            "has_snapshot": e.snapshot is not None, "reviewed_by": reviewer,
            "reviewed_at": e.reviewed_at}


@router.get("/spaces/{space_id}/events")
async def list_events(start: date | None = None, end: date | None = None,
                      status: Literal["accepted", "rejected", "pending"] | None = None,
                      member_id: uuid.UUID | None = None, limit: int = 200,
                      space: Space = Depends(require_space(Role.VIEWER)),
                      db: AsyncSession = Depends(get_db)):
    start, end = _range(space, start, end)
    tz = ZoneInfo(space.timezone)
    q = (select(AttendanceEvent, Person.full_name, Membership.external_id, Device.name,
                AdminUser.email)
         .outerjoin(Membership, Membership.id == AttendanceEvent.membership_id)
         .outerjoin(Person, Person.id == Membership.person_id)
         .outerjoin(Device, Device.id == AttendanceEvent.device_id)
         .outerjoin(AdminUser, AdminUser.id == AttendanceEvent.reviewed_by)
         .where(AttendanceEvent.space_id == space.id,
                AttendanceEvent.occurred_at >= datetime.combine(start, datetime.min.time(), tz),
                AttendanceEvent.occurred_at < datetime.combine(end + timedelta(days=1),
                                                               datetime.min.time(), tz)))
    if status:
        q = q.where(AttendanceEvent.status == status)
    if member_id:
        q = q.where(AttendanceEvent.membership_id == member_id)
    rows = (await db.execute(q.order_by(AttendanceEvent.occurred_at.desc())
                             .limit(max(1, min(limit, 2000))))).all()
    return [event_out(*r) for r in rows]


@router.get("/spaces/{space_id}/review")
async def review_queue(space: Space = Depends(require_space(Role.MANAGER)),
                       db: AsyncSession = Depends(get_db)):
    q = (select(AttendanceEvent, Person.full_name, Membership.external_id, Device.name)
         .outerjoin(Membership, Membership.id == AttendanceEvent.membership_id)
         .outerjoin(Person, Person.id == Membership.person_id)
         .outerjoin(Device, Device.id == AttendanceEvent.device_id)
         .where(AttendanceEvent.space_id == space.id,
                AttendanceEvent.status == EventStatus.PENDING)
         .order_by(AttendanceEvent.occurred_at).limit(500))
    return [event_out(*r) for r in (await db.execute(q)).all()]


class ManualIn(BaseModel):
    member_id: uuid.UUID
    occurred_at: datetime
    note: str = Field(min_length=3, max_length=500)  # reason is mandatory for audit


@router.post("/spaces/{space_id}/events", status_code=201)
async def manual_event(body: ManualIn, request: Request,
                       space: Space = Depends(require_space(Role.MANAGER)),
                       p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    m = await db.get(Membership, body.member_id)
    if not m or m.space_id != space.id:
        raise HTTPException(404, "member not found")
    occurred = body.occurred_at
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=ZoneInfo(space.timezone))
    if occurred > now() + timedelta(minutes=5):
        raise HTTPException(422, "cannot record attendance in the future")
    e = AttendanceEvent(id=uuid.uuid4(), space_id=space.id, membership_id=m.id,
                        occurred_at=occurred, status=EventStatus.ACCEPTED, source="admin",
                        reason="manual_entry", note=body.note,
                        reviewed_by=p.user.id if p.user else None, reviewed_at=now())
    db.add(e)
    await db.flush()
    start, end = event_dates(space, [occurred])
    await recompute(db, space, [m.id], start, end)
    await notify_webhooks(db, space, [dict(id=e.id, membership_id=m.id, occurred_at=occurred,
                                           status=e.status, source=e.source, reason=e.reason)])
    audit(db, p, "event.manual", space_id=space.id, target=m.id, request=request,
          at=occurred, note=body.note)
    await db.commit()
    return {"id": str(e.id)}


class ReviewIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = Field("", max_length=500)


@router.post("/spaces/{space_id}/events/{event_id}/review")
async def review_event(event_id: uuid.UUID, body: ReviewIn, request: Request,
                       space: Space = Depends(require_space(Role.MANAGER)),
                       p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    e = await db.get(AttendanceEvent, event_id)
    if not e or e.space_id != space.id:
        raise HTTPException(404, "event not found")
    if body.decision == "approve" and not e.membership_id:
        raise HTTPException(422, "cannot approve an event without a known member")
    e.status = EventStatus.ACCEPTED if body.decision == "approve" else EventStatus.REJECTED
    e.reviewed_by, e.reviewed_at = (p.user.id if p.user else None), now()
    if body.note:
        e.note = f"{e.note}\n[review] {body.note}".strip()[:500]
    if e.membership_id:
        start, end = event_dates(space, [e.occurred_at])
        await recompute(db, space, [e.membership_id], start, end)
    audit(db, p, f"event.{body.decision}", space_id=space.id, target=e.id, request=request)
    await db.commit()
    return {"status": e.status}


@router.get("/spaces/{space_id}/events/{event_id}/snapshot")
async def event_snapshot(event_id: uuid.UUID, request: Request,
                         space: Space = Depends(require_space(Role.MANAGER)),
                         p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    e = await db.get(AttendanceEvent, event_id)
    if not e or e.space_id != space.id or not e.snapshot:
        raise HTTPException(404, "no snapshot")
    audit(db, p, "event.snapshot_viewed", space_id=space.id, target=e.id, request=request)
    await db.commit()
    return Response(crypto.unseal(get_settings().key, e.snapshot, b"snapshot"),
                    media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# ---------- days ----------
async def day_rows(db: AsyncSession, space: Space, start: date, end: date,
                   member_id: uuid.UUID | None = None, status: str | None = None,
                   limit: int | None = None):
    q = (select(AttendanceDay, Person.full_name, Membership.external_id, Membership.title)
         .join(Membership, Membership.id == AttendanceDay.membership_id)
         .join(Person, Person.id == Membership.person_id)
         .where(AttendanceDay.space_id == space.id, AttendanceDay.day.between(start, end)))
    if member_id:
        q = q.where(AttendanceDay.membership_id == member_id)
    if status:
        q = q.where(AttendanceDay.status == status)
    q = q.order_by(AttendanceDay.day.desc(), Person.full_name)
    if limit:
        q = q.limit(limit)
    return (await db.execute(q)).all()


def day_out(d: AttendanceDay, name: str, ext: str, title: str) -> dict:
    return {"member_id": str(d.membership_id), "member_name": name, "external_id": ext,
            "title": title, "day": d.day, "status": d.status, "first_in": d.first_in,
            "last_out": d.last_out, "worked_minutes": d.worked_minutes,
            "late_minutes": d.late_minutes, "early_leave_minutes": d.early_leave_minutes,
            "overtime_minutes": d.overtime_minutes, "flags": d.flags}


@router.get("/spaces/{space_id}/days")
async def list_days(start: date | None = None, end: date | None = None,
                    member_id: uuid.UUID | None = None, status: str | None = None,
                    limit: int = 1000, space: Space = Depends(require_space(Role.VIEWER)),
                    db: AsyncSession = Depends(get_db)):
    start, end = _range(space, start, end)
    rows = await day_rows(db, space, start, end, member_id, status, max(1, min(limit, 10000)))
    return [day_out(*r) for r in rows]


class RangeIn(BaseModel):
    start: date
    end: date


@router.post("/spaces/{space_id}/recompute")
async def recompute_range(body: RangeIn, request: Request,
                          space: Space = Depends(require_space(Role.OWNER)),
                          p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    start, end = _range(space, body.start, min(body.end, today(space)))
    n = await recompute_space(db, space, start, end)
    audit(db, p, "days.recomputed", space_id=space.id, request=request, start=start, end=end)
    await db.commit()
    return {"rows": n}


# ---------- dashboard ----------
@router.get("/spaces/{space_id}/dashboard")
async def dashboard(day: date | None = None, space: Space = Depends(require_space(Role.VIEWER)),
                    db: AsyncSession = Depends(get_db)):
    day = day or today(space)
    tz = ZoneInfo(space.timezone)
    total = (await db.execute(select(func.count()).select_from(Membership).where(
        Membership.space_id == space.id, Membership.active))).scalar_one()

    counts = dict((await db.execute(
        select(AttendanceDay.status, func.count()).where(
            AttendanceDay.space_id == space.id, AttendanceDay.day == day)
        .group_by(AttendanceDay.status))).all())

    trend_start = day - timedelta(days=13)
    trend: dict[date, Counter] = defaultdict(Counter)
    for d, st, n in (await db.execute(
        select(AttendanceDay.day, AttendanceDay.status, func.count()).where(
            AttendanceDay.space_id == space.id, AttendanceDay.day.between(trend_start, day))
        .group_by(AttendanceDay.day, AttendanceDay.status))).all():
        trend[d][st] = n

    late_top = (await db.execute(
        select(Person.full_name, Membership.external_id, func.count(),
               func.sum(AttendanceDay.late_minutes))
        .join(Membership, Membership.id == AttendanceDay.membership_id)
        .join(Person, Person.id == Membership.person_id)
        .where(AttendanceDay.space_id == space.id,
               AttendanceDay.day.between(day - timedelta(days=29), day),
               AttendanceDay.late_minutes > 0)
        .group_by(Person.full_name, Membership.external_id)
        .order_by(func.count().desc()).limit(10))).all()

    day_lo = datetime.combine(day, datetime.min.time(), tz)
    week_lo = day_lo - timedelta(days=6)
    day_hi = day_lo + timedelta(days=1)
    failures = Counter()
    for reason, n in (await db.execute(
        select(AttendanceEvent.reason, func.count()).where(
            AttendanceEvent.space_id == space.id,
            AttendanceEvent.status == EventStatus.REJECTED,
            AttendanceEvent.occurred_at.between(week_lo, day_hi))
        .group_by(AttendanceEvent.reason))).all():
        failures[(reason or "unknown").split(";")[0].split(":")[0]] += n
    failed_today = (await db.execute(select(func.count()).select_from(AttendanceEvent).where(
        AttendanceEvent.space_id == space.id, AttendanceEvent.status == EventStatus.REJECTED,
        AttendanceEvent.occurred_at.between(day_lo, day_hi)))).scalar_one()
    pending = (await db.execute(select(func.count()).select_from(AttendanceEvent).where(
        AttendanceEvent.space_id == space.id,
        AttendanceEvent.status == EventStatus.PENDING))).scalar_one()
    recent = (await db.execute(
        select(AttendanceEvent, Person.full_name, Membership.external_id, Device.name)
        .outerjoin(Membership, Membership.id == AttendanceEvent.membership_id)
        .outerjoin(Person, Person.id == Membership.person_id)
        .outerjoin(Device, Device.id == AttendanceEvent.device_id)
        .where(AttendanceEvent.space_id == space.id)
        .order_by(AttendanceEvent.received_at.desc()).limit(15))).all()
    devices = (await db.execute(select(Device).where(
        Device.space_id == space.id, Device.revoked_at.is_(None)))).scalars().all()

    no_face = (await db.execute(
        select(func.count()).select_from(Membership).where(
            Membership.space_id == space.id, Membership.active,
            ~select(FaceTemplate.id).where(FaceTemplate.person_id == Membership.person_id).exists(),
        ))).scalar_one()
    shifts = (await db.execute(select(func.count()).select_from(Shift).where(
        Shift.space_id == space.id, Shift.active))).scalar_one()

    present = sum(counts.get(s, 0) for s in ("present", "late", "half_day"))
    return {
        "day": day,
        "members": total,
        "counts": counts,
        "present": present,
        "not_checked_in": max(0, total - sum(counts.values())),
        "failed_today": failed_today,
        "pending_reviews": pending,
        "trend": [{"day": trend_start + timedelta(days=i),
                   **trend.get(trend_start + timedelta(days=i), {})} for i in range(14)],
        "late_top": [{"name": n, "external_id": e, "days": c, "minutes": int(m or 0)}
                     for n, e, c, m in late_top],
        "failure_reasons": dict(failures.most_common()),
        "recent": [event_out(*r) for r in recent],
        "setup": {
            "members": total, "members_without_face": no_face, "shifts": shifts,
            "kiosks_ready": sum(1 for d in devices if d.enrolled_at),
            "has_checkins": bool(trend and any(trend.values())) or bool(recent),
        },
        "devices": {
            "total": len(devices),
            "online": sum(1 for d in devices if d.last_seen_at
                          and now() - d.last_seen_at < ONLINE_WINDOW),
            "clock_drift": [d.name for d in devices
                            if abs(float((d.info or {}).get("clock_offset_s", 0) or 0)) > 120],
        },
    }


# ---------- reports ----------
def _fmt(dt: datetime | None, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M") if dt else ""


@router.get("/spaces/{space_id}/reports/attendance.{fmt}")
async def report(fmt: Literal["xlsx", "csv"], start: date | None = None, end: date | None = None,
                 space: Space = Depends(require_space(Role.VIEWER)),
                 db: AsyncSession = Depends(get_db)):
    start, end = _range(space, start or today(space).replace(day=1), end)
    tz = ZoneInfo(space.timezone)
    rows = list(reversed(await day_rows(db, space, start, end)))
    in_out = space.mode == "in_out"
    head = ["Date", "ID", "Name", "Title", "Status", "In"] + (
        ["Out", "Worked (h)", "Late (min)", "Early leave (min)", "Overtime (min)"] if in_out
        else ["Late (min)"]) + ["Flags"]

    def line(d: AttendanceDay, name: str, ext: str, title: str) -> list:
        base = [d.day.isoformat(), ext, name, title, d.status, _fmt(d.first_in, tz)]
        extra = ([_fmt(d.last_out, tz), round(d.worked_minutes / 60, 2), d.late_minutes,
                  d.early_leave_minutes, d.overtime_minutes] if in_out else [d.late_minutes])
        return base + extra + [", ".join(d.flags or [])]

    summary: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for d, name, ext, _title in rows:
        s = summary[(ext, name)]
        s[d.status] += 1
        s["worked"] += d.worked_minutes
        s["late_min"] += d.late_minutes
    sum_head = ["ID", "Name", "Present", "Late", "Half day", "Absent", "Leave", "Holiday",
                "Late (min)"] + (["Worked (h)"] if in_out else [])

    def sum_line(ext: str, name: str, s: Counter) -> list:
        return [ext, name, s["present"], s["late"], s["half_day"], s["absent"], s["leave"],
                s["holiday"], s["late_min"]] + ([round(s["worked"] / 60, 2)] if in_out else [])

    fname = f"attendance-{start}-{end}"
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(head)
        for r in rows:
            w.writerow(line(*r))
        return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{fname}.csv"'})
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append([f"{space.name}: {start} to {end}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(sum_head)
    for c in ws[2]:
        c.font = Font(bold=True)
    for (ext, name), s in sorted(summary.items(), key=lambda kv: kv[0][1]):
        ws.append(sum_line(ext, name, s))
    ws2 = wb.create_sheet("Daily")
    ws2.append(head)
    for c in ws2[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws2.append(line(*r))
    for sheet in (ws, ws2):
        sheet.freeze_panes = "A3" if sheet is ws else "A2"
        for col in sheet.columns:
            width = max(len(str(c.value or "")) for c in col[1:] or col)
            sheet.column_dimensions[col[0].column_letter].width = min(max(10, width + 2), 40)
    out = io.BytesIO()
    wb.save(out)
    return Response(out.getvalue(), media_type=(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={"Content-Disposition": f'attachment; filename="{fname}.xlsx"'})
