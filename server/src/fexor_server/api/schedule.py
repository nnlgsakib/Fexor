"""Shifts, shift assignments, holidays and leave. Changes trigger a background recompute."""

import uuid
from datetime import date, time

import holidays as holidays_lib
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Holiday, Leave, Membership, Role, Shift, ShiftAssignment, Space
from ..security import Principal, audit, current, require_space
from ..services import queue_recompute, space_today

router = APIRouter(prefix="/api", tags=["schedule"])


# ---------- shifts ----------
class ShiftIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    start_time: time
    end_time: time
    weekdays: list[int] = [0, 1, 2, 3, 4]
    grace_minutes: int = Field(10, ge=0, le=240)
    early_window_minutes: int = Field(120, ge=0, le=720)
    late_window_minutes: int = Field(240, ge=0, le=720)
    half_day_minutes: int | None = Field(None, ge=1, le=1440)
    checkin_close_minutes: int | None = Field(None, ge=0, le=720)
    checkout_open_minutes: int | None = Field(None, ge=0, le=720)
    active: bool = True

    @model_validator(mode="after")
    def _check(self):
        if any(d < 0 or d > 6 for d in self.weekdays):
            raise ValueError("weekdays are 0 (Mon) to 6 (Sun)")
        if self.start_time == self.end_time:
            raise ValueError("start and end cannot be equal")
        self.weekdays = sorted(set(self.weekdays))
        return self


def shift_out(s: Shift) -> dict:
    return {"id": str(s.id), "name": s.name, "start_time": s.start_time.strftime("%H:%M"),
            "end_time": s.end_time.strftime("%H:%M"), "weekdays": s.weekdays,
            "grace_minutes": s.grace_minutes, "early_window_minutes": s.early_window_minutes,
            "late_window_minutes": s.late_window_minutes, "half_day_minutes": s.half_day_minutes,
            "checkin_close_minutes": s.checkin_close_minutes,
            "checkout_open_minutes": s.checkout_open_minutes,
            "active": s.active, "overnight": s.end_time <= s.start_time}


@router.get("/spaces/{space_id}/shifts")
async def list_shifts(space: Space = Depends(require_space(Role.VIEWER)),
                      db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Shift).where(Shift.space_id == space.id)
                             .order_by(Shift.start_time))).scalars()
    return [shift_out(s) for s in rows]


@router.post("/spaces/{space_id}/shifts", status_code=201)
async def create_shift(body: ShiftIn, request: Request,
                       space: Space = Depends(require_space(Role.MANAGER)),
                       p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    s = Shift(space_id=space.id, **body.model_dump())
    db.add(s)
    await db.flush()
    if space.default_shift_id is None:
        space.default_shift_id = s.id
    audit(db, p, "shift.created", space_id=space.id, target=s.id, request=request, name=s.name)
    queue_recompute(db, space)
    await db.commit()
    return shift_out(s)


async def _shift(db: AsyncSession, space: Space, shift_id: uuid.UUID) -> Shift:
    s = await db.get(Shift, shift_id)
    if not s or s.space_id != space.id:
        raise HTTPException(404, "shift not found")
    return s


@router.put("/spaces/{space_id}/shifts/{shift_id}")
async def update_shift(shift_id: uuid.UUID, body: ShiftIn, request: Request,
                       space: Space = Depends(require_space(Role.MANAGER)),
                       p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    s = await _shift(db, space, shift_id)
    for k, v in body.model_dump().items():
        setattr(s, k, v)
    audit(db, p, "shift.updated", space_id=space.id, target=s.id, request=request)
    queue_recompute(db, space)
    await db.commit()
    return shift_out(s)


@router.delete("/spaces/{space_id}/shifts/{shift_id}")
async def delete_shift(shift_id: uuid.UUID, request: Request,
                       space: Space = Depends(require_space(Role.MANAGER)),
                       p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    s = await _shift(db, space, shift_id)
    s.active = False
    if space.default_shift_id == s.id:
        space.default_shift_id = None
    audit(db, p, "shift.deactivated", space_id=space.id, target=s.id, request=request)
    # From today on only: past days keep the shift they were counted with.
    queue_recompute(db, space, start=space_today(space))
    await db.commit()
    return {"ok": True}


class AssignIn(BaseModel):
    member_ids: list[uuid.UUID]
    shift_id: uuid.UUID
    valid_from: date


@router.post("/spaces/{space_id}/shift-assignments")
async def assign_shift(body: AssignIn, request: Request,
                       space: Space = Depends(require_space(Role.MANAGER)),
                       p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    await _shift(db, space, body.shift_id)
    valid = set((await db.execute(select(Membership.id).where(
        Membership.space_id == space.id, Membership.id.in_(body.member_ids)))).scalars())
    for mid in valid:
        await db.execute(delete(ShiftAssignment).where(
            ShiftAssignment.membership_id == mid, ShiftAssignment.valid_from == body.valid_from))
        db.add(ShiftAssignment(membership_id=mid, shift_id=body.shift_id,
                               valid_from=body.valid_from))
    audit(db, p, "shift.assigned", space_id=space.id, target=body.shift_id, request=request,
          members=len(valid), valid_from=body.valid_from)
    queue_recompute(db, space, start=body.valid_from)
    await db.commit()
    return {"assigned": len(valid)}


@router.get("/spaces/{space_id}/members/{member_id}/shift-assignments")
async def member_assignments(member_id: uuid.UUID,
                             space: Space = Depends(require_space(Role.VIEWER)),
                             db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(ShiftAssignment, Shift.name).join(Shift, Shift.id == ShiftAssignment.shift_id)
        .join(Membership, Membership.id == ShiftAssignment.membership_id)
        .where(Membership.id == member_id, Membership.space_id == space.id)
        .order_by(ShiftAssignment.valid_from)
    )).all()
    return [{"id": str(a.id), "shift_id": str(a.shift_id), "shift": name,
             "valid_from": a.valid_from} for a, name in rows]


@router.delete("/spaces/{space_id}/shift-assignments/{assignment_id}")
async def remove_assignment(assignment_id: uuid.UUID, request: Request,
                            space: Space = Depends(require_space(Role.MANAGER)),
                            p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    a = await db.get(ShiftAssignment, assignment_id)
    m = await db.get(Membership, a.membership_id) if a else None
    if not a or not m or m.space_id != space.id:
        raise HTTPException(404, "assignment not found")
    await db.delete(a)
    audit(db, p, "shift.unassigned", space_id=space.id, target=m.id, request=request,
          valid_from=a.valid_from)
    queue_recompute(db, space, start=a.valid_from)
    await db.commit()
    return {"ok": True}


# ---------- holidays ----------
class HolidayIn(BaseModel):
    day: date
    name: str = Field(min_length=1, max_length=200)


class PresetIn(BaseModel):
    country: str = Field(min_length=2, max_length=3)
    year: int = Field(ge=2000, le=2100)
    subdiv: str | None = None


@router.get("/spaces/{space_id}/holidays")
async def list_holidays(year: int | None = Query(None, ge=2000, le=2100),
                        space: Space = Depends(require_space(Role.VIEWER)),
                        db: AsyncSession = Depends(get_db)):
    q = select(Holiday).where(Holiday.space_id == space.id)
    if year:
        q = q.where(Holiday.day.between(date(year, 1, 1), date(year, 12, 31)))
    return [{"id": str(h.id), "day": h.day, "name": h.name, "source": h.source}
            for h in (await db.execute(q.order_by(Holiday.day))).scalars()]


@router.post("/spaces/{space_id}/holidays", status_code=201)
async def add_holiday(body: HolidayIn, request: Request,
                      space: Space = Depends(require_space(Role.MANAGER)),
                      p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    stmt = insert(Holiday).values(space_id=space.id, day=body.day, name=body.name)
    await db.execute(stmt.on_conflict_do_update(index_elements=["space_id", "day"],
                                                set_={"name": body.name, "source": "custom"}))
    audit(db, p, "holiday.added", space_id=space.id, request=request, day=body.day)
    queue_recompute(db, space, body.day, body.day)
    await db.commit()
    return {"ok": True}


@router.post("/spaces/{space_id}/holidays/preset")
async def import_preset(body: PresetIn, request: Request,
                        space: Space = Depends(require_space(Role.MANAGER)),
                        p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    try:
        cal = holidays_lib.country_holidays(body.country.upper(), subdiv=body.subdiv,
                                            years=body.year)
    except NotImplementedError as e:
        raise HTTPException(422, "country or region not supported") from e
    for d, name in sorted(cal.items()):
        stmt = insert(Holiday).values(space_id=space.id, day=d, name=name, source="preset")
        await db.execute(stmt.on_conflict_do_nothing(index_elements=["space_id", "day"]))
    audit(db, p, "holiday.preset", space_id=space.id, request=request, country=body.country,
          year=body.year, count=len(cal))
    queue_recompute(db, space, date(body.year, 1, 1), date(body.year, 12, 31))
    await db.commit()
    return {"imported": len(cal)}


@router.delete("/spaces/{space_id}/holidays/{holiday_id}")
async def delete_holiday(holiday_id: uuid.UUID, request: Request,
                         space: Space = Depends(require_space(Role.MANAGER)),
                         p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    h = await db.get(Holiday, holiday_id)
    if not h or h.space_id != space.id:
        raise HTTPException(404, "holiday not found")
    await db.delete(h)
    audit(db, p, "holiday.deleted", space_id=space.id, request=request, day=h.day)
    queue_recompute(db, space, h.day, h.day)
    await db.commit()
    return {"ok": True}


# ---------- leave ----------
class LeaveIn(BaseModel):
    member_id: uuid.UUID
    start_date: date
    end_date: date
    kind: str = Field("leave", max_length=40)
    note: str = Field("", max_length=500)

    @model_validator(mode="after")
    def _order(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date before start_date")
        return self


@router.get("/spaces/{space_id}/leaves")
async def list_leaves(start: date | None = None, end: date | None = None,
                      member_id: uuid.UUID | None = None,
                      space: Space = Depends(require_space(Role.VIEWER)),
                      db: AsyncSession = Depends(get_db)):
    q = (select(Leave, Membership.external_id)
         .join(Membership, Membership.id == Leave.membership_id)
         .where(Membership.space_id == space.id))
    if member_id:
        q = q.where(Leave.membership_id == member_id)
    if start:
        q = q.where(Leave.end_date >= start)
    if end:
        q = q.where(Leave.start_date <= end)
    rows = (await db.execute(q.order_by(Leave.start_date.desc()).limit(1000))).all()
    return [{"id": str(lv.id), "member_id": str(lv.membership_id), "external_id": ext,
             "start_date": lv.start_date, "end_date": lv.end_date, "kind": lv.kind,
             "note": lv.note} for lv, ext in rows]


@router.post("/spaces/{space_id}/leaves", status_code=201)
async def add_leave(body: LeaveIn, request: Request,
                    space: Space = Depends(require_space(Role.MANAGER)),
                    p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    m = await db.get(Membership, body.member_id)
    if not m or m.space_id != space.id:
        raise HTTPException(404, "member not found")
    lv = Leave(membership_id=m.id, start_date=body.start_date, end_date=body.end_date,
               kind=body.kind, note=body.note)
    db.add(lv)
    audit(db, p, "leave.added", space_id=space.id, target=m.id, request=request,
          start=body.start_date, end=body.end_date)
    queue_recompute(db, space, body.start_date, body.end_date)
    await db.commit()
    return {"id": str(lv.id)}


@router.delete("/spaces/{space_id}/leaves/{leave_id}")
async def delete_leave(leave_id: uuid.UUID, request: Request,
                       space: Space = Depends(require_space(Role.MANAGER)),
                       p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    lv = await db.get(Leave, leave_id)
    m = await db.get(Membership, lv.membership_id) if lv else None
    if not lv or not m or m.space_id != space.id:
        raise HTTPException(404, "leave not found")
    await db.delete(lv)
    audit(db, p, "leave.deleted", space_id=space.id, target=m.id, request=request)
    queue_recompute(db, space, lv.start_date, lv.end_date)
    await db.commit()
    return {"ok": True}
