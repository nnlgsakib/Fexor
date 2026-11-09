"""Domain services shared by admin API, device API and worker."""

from __future__ import annotations

import secrets
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fexor_core import crypto, qr
from sqlalchemy import and_, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .models import (
    AttendanceDay,
    AttendanceEvent,
    EventStatus,
    Holiday,
    Job,
    Leave,
    Membership,
    QrCredential,
    Shift,
    ShiftAssignment,
    SigningKey,
    Space,
)
from .rules import ShiftSpec, compute_day, shift_day_for
from .security import now


# ---------- QR signing ----------
async def active_signing_key(db: AsyncSession) -> tuple[str, Ed25519PrivateKey]:
    key = (
        await db.execute(select(SigningKey).where(SigningKey.active).order_by(SigningKey.created_at.desc()))
    ).scalars().first()
    if key is None:
        priv = Ed25519PrivateKey.generate()
        raw = priv.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
        )
        key = SigningKey(
            id=secrets.token_hex(4),
            private_sealed=crypto.seal(get_settings().key, raw, b"qr-signing"),
            public_b64=qr.public_key_to_str(priv.public_key()),
        )
        db.add(key)
        await db.flush()
        return key.id, priv
    raw = crypto.unseal(get_settings().key, key.private_sealed, b"qr-signing")
    return key.id, Ed25519PrivateKey.from_private_bytes(raw)


async def public_keys(db: AsyncSession) -> dict[str, str]:
    rows = (await db.execute(select(SigningKey))).scalars()
    return {k.id: k.public_b64 for k in rows}


async def current_credential(db: AsyncSession, person_id: uuid.UUID) -> QrCredential | None:
    return (
        await db.execute(
            select(QrCredential)
            .where(QrCredential.person_id == person_id, QrCredential.revoked_at.is_(None))
            .order_by(QrCredential.issued_at.desc())
        )
    ).scalars().first()


async def issue_credential(db: AsyncSession, person_id: uuid.UUID) -> QrCredential:
    """New credential; all earlier ones for this person are revoked."""
    kid, _ = await active_signing_key(db)
    for old in (
        await db.execute(
            select(QrCredential).where(
                QrCredential.person_id == person_id, QrCredential.revoked_at.is_(None)
            )
        )
    ).scalars():
        old.revoked_at = now()
    cred = QrCredential(person_id=person_id, key_id=kid)
    db.add(cred)
    await db.flush()
    return cred


async def token_for(db: AsyncSession, cred: QrCredential) -> str:
    """Ed25519 signatures are deterministic, so the printed token can be re-derived."""
    key = await db.get(SigningKey, cred.key_id)
    assert key is not None
    raw = crypto.unseal(get_settings().key, key.private_sealed, b"qr-signing")
    priv = Ed25519PrivateKey.from_private_bytes(raw)
    return qr.sign(priv, key.id, str(cred.person_id), str(cred.id))


# ---------- schedule ----------
def to_spec(s: Shift) -> ShiftSpec:
    return ShiftSpec(
        id=s.id,
        start=s.start_time,
        end=s.end_time,
        weekdays=frozenset(s.weekdays or []),
        grace_minutes=s.grace_minutes,
        early_window_minutes=s.early_window_minutes,
        late_window_minutes=s.late_window_minutes,
        half_day_minutes=s.half_day_minutes,
    )


class ScheduleContext:
    """Everything needed to evaluate days for a set of memberships in one space."""

    def __init__(self, space: Space, shifts: dict, assignments: dict, holidays: set, leaves: dict):
        self.space = space
        self.tz = ZoneInfo(space.timezone)
        self.shifts: dict[uuid.UUID, ShiftSpec] = shifts
        self.assignments: dict[uuid.UUID, list[tuple[date, uuid.UUID]]] = assignments
        self.holidays: set[date] = holidays
        self.leaves: dict[uuid.UUID, list[tuple[date, date]]] = leaves

    def shift_for(self, membership_id: uuid.UUID, d: date) -> ShiftSpec | None:
        shift_id = self.space.default_shift_id
        for valid_from, sid in self.assignments.get(membership_id, []):  # sorted ascending
            if valid_from <= d:
                shift_id = sid
        return self.shifts.get(shift_id) if shift_id else None

    def on_leave(self, membership_id: uuid.UUID, d: date) -> bool:
        return any(a <= d <= b for a, b in self.leaves.get(membership_id, []))

    @classmethod
    async def load(cls, db: AsyncSession, space: Space, membership_ids: list[uuid.UUID],
                   start: date, end: date) -> ScheduleContext:
        shifts = {
            s.id: to_spec(s)
            for s in (await db.execute(
                select(Shift).where(Shift.space_id == space.id, Shift.active)
            )).scalars()
        }
        assignments: dict = defaultdict(list)
        for a in (await db.execute(
            select(ShiftAssignment)
            .where(ShiftAssignment.membership_id.in_(membership_ids))
            .order_by(ShiftAssignment.valid_from)
        )).scalars():
            assignments[a.membership_id].append((a.valid_from, a.shift_id))
        holidays = set((await db.execute(
            select(Holiday.day).where(
                Holiday.space_id == space.id, Holiday.day.between(start, end)
            )
        )).scalars())
        leaves: dict = defaultdict(list)
        for lv in (await db.execute(
            select(Leave).where(
                Leave.membership_id.in_(membership_ids),
                Leave.start_date <= end,
                Leave.end_date >= start,
            )
        )).scalars():
            leaves[lv.membership_id].append((lv.start_date, lv.end_date))
        return cls(space, shifts, assignments, holidays, leaves)


async def recompute(
    db: AsyncSession, space: Space, membership_ids: list[uuid.UUID], start: date, end: date,
    at: datetime | None = None,
) -> int:
    """Rebuild attendance_day rows for the given people and date range (inclusive)."""
    if not membership_ids or start > end:
        return 0
    at = at or now()
    ctx = await ScheduleContext.load(db, space, membership_ids, start - timedelta(days=1),
                                     end + timedelta(days=1))
    members = {
        m.id: m for m in (await db.execute(
            select(Membership).where(Membership.id.in_(membership_ids))
        )).scalars()
    }
    # Events from a day either side, since overnight shifts cross dates.
    lo = datetime.combine(start - timedelta(days=1), datetime.min.time(), ctx.tz)
    hi = datetime.combine(end + timedelta(days=2), datetime.min.time(), ctx.tz)
    by_day: dict[tuple[uuid.UUID, date], list[datetime]] = defaultdict(list)
    for mid, ts in (await db.execute(
        select(AttendanceEvent.membership_id, AttendanceEvent.occurred_at).where(
            AttendanceEvent.membership_id.in_(membership_ids),
            AttendanceEvent.status == EventStatus.ACCEPTED,
            AttendanceEvent.occurred_at.between(lo, hi),
        )
    )).all():
        d = shift_day_for(ts, ctx.tz, lambda x, mid=mid: ctx.shift_for(mid, x))
        by_day[(mid, d)].append(ts)

    rows = []
    stale: list[tuple[uuid.UUID, date]] = []
    d = start
    while d <= end:
        for mid, m in members.items():
            spec = ctx.shift_for(mid, d)
            events = by_day.get((mid, d), [])
            res = None
            if events or member_active_on(m, d, ctx.tz):
                res = compute_day(
                    day=d, tz=ctx.tz, mode=space.mode, shift=spec, events=events,
                    is_holiday=d in ctx.holidays, on_leave=ctx.on_leave(mid, d), now=at,
                    min_rescan_minutes=space.min_rescan_minutes,
                )
            if res is None:
                stale.append((mid, d))
                continue
            rows.append(dict(
                membership_id=mid, day=d, space_id=space.id, shift_id=spec.id if spec else None,
                status=res.status.value, first_in=res.first_in, last_out=res.last_out,
                worked_minutes=res.worked_minutes, late_minutes=res.late_minutes,
                early_leave_minutes=res.early_leave_minutes,
                overtime_minutes=res.overtime_minutes, flags=res.flags, computed_at=at,
            ))
        d += timedelta(days=1)

    for i in range(0, len(rows), 1000):
        chunk = rows[i:i + 1000]
        stmt = insert(AttendanceDay).values(chunk)
        await db.execute(stmt.on_conflict_do_update(
            index_elements=["membership_id", "day"],
            set_={c: stmt.excluded[c] for c in chunk[0] if c not in ("membership_id", "day")},
        ))
    for mid, dd in stale:
        await db.execute(delete(AttendanceDay).where(
            and_(AttendanceDay.membership_id == mid, AttendanceDay.day == dd)
        ))
    return len(rows)


def member_active_on(m: Membership, d: date, tz: ZoneInfo) -> bool:
    """Whether absences can be recorded for this person on day d.

    Judged by dates, never by the current active flag, so deactivating someone keeps their
    past days. Added without a start date: from the next day (being added mid-day is not an
    absence). Deactivated without an end date (older data): until the day of the change.
    """
    since = m.start_date or m.created_at.astimezone(tz).date() + timedelta(days=1)
    until = m.end_date or (None if m.active else m.updated_at.astimezone(tz).date())
    return since <= d and (until is None or d <= until)


async def recompute_space(db: AsyncSession, space: Space, start: date, end: date) -> int:
    ids = list((await db.execute(
        select(Membership.id).where(Membership.space_id == space.id)
    )).scalars())
    return await recompute(db, space, ids, start, end)


def event_dates(space: Space, times: list[datetime]) -> tuple[date, date]:
    """Range of shift days an event batch can affect."""
    tz = ZoneInfo(space.timezone)
    local = [t.astimezone(tz).date() for t in times]
    return min(local) - timedelta(days=1), max(local) + timedelta(days=1)


def enqueue(db: AsyncSession, kind: str, payload: dict, run_at: datetime | None = None) -> None:
    db.add(Job(kind=kind, payload=payload, run_at=run_at or now()))


RECOMPUTE_DAYS = 62  # how far back schedule edits are applied


def space_today(space: Space) -> date:
    return datetime.now(ZoneInfo(space.timezone)).date()


def queue_recompute(db: AsyncSession, space: Space, start: date | None = None,
                    end: date | None = None) -> None:
    """Recalculate days in the background (default: the last RECOMPUTE_DAYS days)."""
    today = space_today(space)
    end = min(end or today, today)
    start = max(start or end - timedelta(days=RECOMPUTE_DAYS), today - timedelta(days=366))
    if start <= end:
        enqueue(db, "recompute_space", {"space_id": str(space.id), "start": start.isoformat(),
                                         "end": end.isoformat()})
