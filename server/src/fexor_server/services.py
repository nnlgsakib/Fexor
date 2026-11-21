"""Domain services shared by admin API, device API and worker."""

from __future__ import annotations

import base64
import binascii
import math
import secrets
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fexor_core import crypto, qr
from fexor_core.face import EMBEDDING_DIM, MODEL_ID
from fexor_core.schedule import shift_id_on, window_issue
from sqlalchemy import delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from .config import get_settings
from .models import (
    AttendanceDay,
    AttendanceEvent,
    EventStatus,
    FaceTemplate,
    Holiday,
    Job,
    Leave,
    Membership,
    Mode,
    QrCredential,
    Shift,
    ShiftAssignment,
    SigningKey,
    Space,
)
from .rules import ShiftSpec, compute_day, shift_day_for
from .security import audit, now


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


async def ensure_signing_key(db: AsyncSession) -> None:
    """An active key exists afterwards. Cheap when it already does: nothing is decrypted."""
    if (await db.execute(select(SigningKey.id).where(SigningKey.active).limit(1))).first() is None:
        await active_signing_key(db)


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
        checkin_close_minutes=s.checkin_close_minutes,
        checkout_open_minutes=s.checkout_open_minutes,
    )


class ScheduleContext:
    """Everything needed to evaluate days for a set of memberships in one space."""

    def __init__(self, space: Space, shifts: dict, assignments: dict, holidays: set, leaves: dict,
                 membership_ids: frozenset[uuid.UUID] = frozenset(),
                 span: tuple[date, date] | None = None):
        self.space = space
        self.membership_ids = membership_ids  # what was loaded: see covers()
        self.span = span
        self.tz = ZoneInfo(space.timezone)
        self.shifts: dict[uuid.UUID, ShiftSpec] = shifts
        self.assignments: dict[uuid.UUID, list[tuple[date, uuid.UUID]]] = assignments
        self.holidays: set[date] = holidays
        self.leaves: dict[uuid.UUID, list[tuple[date, date]]] = leaves

    def shift_for(self, membership_id: uuid.UUID, d: date) -> ShiftSpec | None:
        shift_id = shift_id_on(self.assignments.get(membership_id, []),
                               self.space.default_shift_id, d)
        return self.shifts.get(shift_id) if shift_id else None

    def on_leave(self, membership_id: uuid.UUID, d: date) -> bool:
        return any(a <= d <= b for a, b in self.leaves.get(membership_id, []))

    def covers(self, space: Space, membership_ids: list[uuid.UUID], start: date, end: date) -> bool:
        """Loaded for these people and days, so it can be reused instead of loading again."""
        return (self.space.id == space.id and self.span is not None
                and self.span[0] <= start and end <= self.span[1]
                and self.membership_ids.issuperset(membership_ids))

    @classmethod
    async def reuse_or_load(cls, db: AsyncSession, space: Space, membership_ids: list[uuid.UUID],
                            start: date, end: date,
                            reuse: ScheduleContext | None = None) -> ScheduleContext:
        if reuse is not None and reuse.covers(space, membership_ids, start, end):
            return reuse
        return await cls.load(db, space, membership_ids, start, end)

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
        return cls(space, shifts, assignments, holidays, leaves,
                   membership_ids=frozenset(membership_ids), span=(start, end))


async def recompute(
    db: AsyncSession, space: Space, membership_ids: list[uuid.UUID], start: date, end: date,
    at: datetime | None = None, ctx: ScheduleContext | None = None,
) -> int:
    """Rebuild attendance_day rows for the given people and date range (inclusive).
    ctx: a schedule already loaded in this request, used if it covers these people and days."""
    if not membership_ids or start > end:
        return 0
    at = at or now()
    ctx = await ScheduleContext.reuse_or_load(db, space, membership_ids,
                                              start - timedelta(days=1), end + timedelta(days=1),
                                              reuse=ctx)
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

    # Chunks keep each statement well under Postgres' 32767 bind-parameter limit.
    for i in range(0, len(rows), 1000):
        chunk = rows[i:i + 1000]
        stmt = insert(AttendanceDay).values(chunk)
        await db.execute(stmt.on_conflict_do_update(
            index_elements=["membership_id", "day"],
            set_={c: stmt.excluded[c] for c in chunk[0] if c not in ("membership_id", "day")},
        ))
    # Every day off of every member is stale: one statement per chunk, not one per person-day.
    for i in range(0, len(stale), 5000):
        await db.execute(delete(AttendanceDay).where(
            tuple_(AttendanceDay.membership_id, AttendanceDay.day).in_(stale[i:i + 5000])
        ))
    return len(rows)


# Verified scans outside a shift's windows. Too early to check in: refused (scan again
# later). Late check-in or early check-out: kept, and counted once an admin approves.
WINDOW_REASONS = {"checkin_not_open": EventStatus.REJECTED,
                  "checkin_closed": EventStatus.PENDING,
                  "checkout_not_open": EventStatus.PENDING}


async def window_issues(db: AsyncSession, space: Space, scans: list[tuple[uuid.UUID, datetime]],
                        ctx: ScheduleContext | None = None) -> list[str | None]:
    """For each (member, time) scan, in order: the WINDOW_REASONS key it breaks, or None.
    Scans that pass count for the ones after them (the check-in makes the next a check-out)."""
    if not scans:
        return []
    mids = list({m for m, _ in scans})
    start, end = event_dates(space, [t for _, t in scans])
    ctx = await ScheduleContext.reuse_or_load(db, space, mids, start - timedelta(days=1),
                                              end + timedelta(days=1), reuse=ctx)
    if all(s.checkin_close_minutes is None and s.checkout_open_minutes is None
           for s in ctx.shifts.values()):
        return [None] * len(scans)
    lo = datetime.combine(start - timedelta(days=1), datetime.min.time(), ctx.tz)
    hi = datetime.combine(end + timedelta(days=2), datetime.min.time(), ctx.tz)
    counted: dict[uuid.UUID, list[datetime]] = defaultdict(list)
    for mid, ts in (await db.execute(
        select(AttendanceEvent.membership_id, AttendanceEvent.occurred_at).where(
            AttendanceEvent.membership_id.in_(mids),
            AttendanceEvent.status == EventStatus.ACCEPTED,
            AttendanceEvent.occurred_at.between(lo, hi),
        )
    )).all():
        counted[mid].append(ts)
    out: list[str | None] = [None] * len(scans)
    for i in sorted(range(len(scans)), key=lambda i: scans[i][1]):
        mid, ts = scans[i]
        hit = window_issue(ts, ctx.tz, lambda d, mid=mid: ctx.shift_for(mid, d), counted[mid],
                           single=space.mode == Mode.SINGLE,
                           min_gap_minutes=space.min_rescan_minutes)
        if hit:
            out[i] = hit[0]
        else:
            counted[mid].append(ts)
    return out


async def recheck_waiting(db: AsyncSession, space: Space, membership_id: uuid.UUID,
                          around: datetime) -> list[AttendanceEvent]:
    """After an admin accepts a scan, this person's other scans that only waited because of
    a window (none reviewed by hand) may count now: a late check-out once the late
    check-in is accepted. Returns the events that now count."""
    start, end = event_dates(space, [around])
    tz = ZoneInfo(space.timezone)
    waiting = [e for e in (await db.execute(select(AttendanceEvent).where(
        AttendanceEvent.membership_id == membership_id,
        AttendanceEvent.status == EventStatus.PENDING,
        AttendanceEvent.reviewed_at.is_(None),
        AttendanceEvent.occurred_at.between(
            datetime.combine(start, datetime.min.time(), tz),
            datetime.combine(end + timedelta(days=1), datetime.min.time(), tz)),
    ))).scalars() if e.reason.split(";")[0] in WINDOW_REASONS]
    issues = await window_issues(db, space, [(membership_id, e.occurred_at) for e in waiting])
    now_counting = []
    for e, issue in zip(waiting, issues, strict=True):
        if issue is None:
            e.status = EventStatus.ACCEPTED
            now_counting.append(e)
    await db.flush()
    return now_counting


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


# ---------- learning faces at the kiosk ----------
# Like a phone adapting to a beard or new glasses: a kiosk sends the face of a verified check-in,
# and it becomes an extra template only when the check was clearly the right, live person.
# Each limit closes one way to abuse it.
AUTO_FACES_MAX = 3  # learned templates kept per person; the oldest is replaced
AUTO_FACE_EVERY = timedelta(days=7)  # at most one new one per person in this time
AUTO_MATCH_MARGIN = 0.05  # must match the admin's photos (never a learned one) by threshold + this
AUTO_OTHER_GAP = 0.10  # and look clearly more like them than like anyone else in the space
AUTO_LIVENESS_MARGIN = 0.15  # photo/screen check passed clearly (liveness threshold + this)
AUTO_SPOOF_LOW_FRAC = 0.05  # at most this share of frames looked like a photo
AUTO_NEW_BELOW = 0.80  # this close to a stored template already: it adds nothing


def _finite(v: object) -> float | None:
    return float(v) if isinstance(v, int | float) and math.isfinite(v) else None


def parse_embedding(b64: str | None) -> np.ndarray | None:
    """A face template sent by a kiosk, or None if it is not a sound unit vector."""
    if not b64:
        return None
    try:
        raw = base64.b64decode(b64, validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(raw) != EMBEDDING_DIM * 4:
        return None
    v = np.frombuffer(raw, "<f4")
    norm = float(np.linalg.norm(v)) if np.all(np.isfinite(v)) else 0.0
    if not 0.9 < norm < 1.1:  # kiosks send normalized embeddings
        return None
    return (v / norm).astype(np.float32)


async def learn_face(db: AsyncSession, space: Space, membership_id: uuid.UUID, probe: np.ndarray,
                     scores: dict, device_id: uuid.UUID) -> FaceTemplate | None:
    """Keep the face of a verified kiosk check-in as an extra template, if every limit above
    holds. Returns the new template, or None. The kiosk is recorded: disconnecting it removes
    what it taught (forget_faces_from)."""
    if not space.auto_update_faces:
        return None
    live, low = _finite(scores.get("liveness")), _finite(scores.get("spoof_low_frac"))
    other = _finite(scores.get("other_best"))
    if (live is None or live < min(0.95, space.liveness_threshold + AUTO_LIVENESS_MARGIN)
            or low is None or low > AUTO_SPOOF_LOW_FRAC
            or other is None  # the 1:N cross-check must have run
            or not scores.get("actions")):  # and the movement check
        return None
    # Locked: two uploads at once must not both add a template for the same person.
    m = await db.get(Membership, membership_id, with_for_update=True)
    if m is None:
        return None
    rows = (await db.execute(select(FaceTemplate).options(defer(FaceTemplate.photo)).where(
        FaceTemplate.person_id == m.person_id, FaceTemplate.model_id == MODEL_ID)
        .order_by(FaceTemplate.created_at))).scalars().all()
    learned = [t for t in rows if t.source == "auto"]
    if learned and learned[-1].created_at > now() - AUTO_FACE_EVERY:
        return None
    key, aad = get_settings().key, str(m.person_id).encode()
    vectors = {t.id: crypto.unseal_vector(key, t.embedding, aad) for t in rows}
    anchors = [vectors[t.id] for t in rows if t.source != "auto"]
    if not anchors:
        return None
    match = max(float(np.dot(probe, a)) for a in anchors)
    if (match < space.match_threshold + AUTO_MATCH_MARGIN or match - other < AUTO_OTHER_GAP
            or max(float(np.dot(probe, v)) for v in vectors.values()) >= AUTO_NEW_BELOW):
        return None
    while len(learned) >= AUTO_FACES_MAX:
        await db.delete(learned.pop(0))
    t = FaceTemplate(person_id=m.person_id, model_id=MODEL_ID, source="auto",
                     embedding=crypto.seal_vector(key, probe, aad), photo=None,
                     quality={"match": round(match, 3), "liveness": round(live, 3),
                              "device": str(device_id)})
    db.add(t)
    await db.flush()
    audit(db, None, "face.auto_added", space_id=space.id, target=t.id, member=membership_id,
          match=round(match, 3))
    return t


async def forget_faces_from(db: AsyncSession, device_id: uuid.UUID) -> int:
    """Remove the faces a kiosk taught. A stolen kiosk could send crafted face numbers; once it
    is disconnected, nothing it taught keeps working."""
    res = await db.execute(delete(FaceTemplate).where(
        FaceTemplate.source == "auto", FaceTemplate.quality["device"].astext == str(device_id)))
    return res.rowcount or 0
