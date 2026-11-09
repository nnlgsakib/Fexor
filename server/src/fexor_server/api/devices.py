"""Kiosk devices: admin management + the device-facing sync API."""

import base64
import hashlib
import secrets
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fexor_core import crypto
from fexor_core.face import MODEL_ID
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from ..config import get_settings
from ..db import get_db
from ..models import (
    AttendanceEvent,
    Device,
    EventStatus,
    FaceTemplate,
    Membership,
    Person,
    QrCredential,
    Role,
    Space,
    Webhook,
)
from ..security import (
    Principal,
    audit,
    current,
    current_device,
    new_enroll_code,
    normalize_code,
    now,
    require_space,
    sha256,
)
from ..services import active_signing_key, enqueue, event_dates, public_keys, recompute
from .auth import _rate_limit

router = APIRouter(prefix="/api", tags=["devices"])
ENROLL_TTL = timedelta(minutes=30)
ONLINE_WINDOW = timedelta(minutes=3)
# Kiosk events older than this mean a wrong clock (or a very long offline spell): an admin
# decides. It also bounds how far back one upload can make the server recalculate.
MAX_EVENT_AGE = timedelta(days=30)


# ---------- admin side ----------
class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


def device_out(d: Device) -> dict:
    return {
        "id": str(d.id), "name": d.name, "enrolled_at": d.enrolled_at,
        "last_seen_at": d.last_seen_at, "last_ip": d.last_ip, "info": d.info,
        "revoked_at": d.revoked_at, "created_at": d.created_at,
        "online": bool(d.last_seen_at and now() - d.last_seen_at < ONLINE_WINDOW
                       and not d.revoked_at),
        "enroll_pending": bool(d.enroll_code_hash and d.enroll_expires_at
                               and d.enroll_expires_at > now()),
    }


@router.get("/spaces/{space_id}/devices")
async def list_devices(space: Space = Depends(require_space(Role.VIEWER)),
                       db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Device).where(Device.space_id == space.id)
                             .order_by(Device.created_at))).scalars()
    return [device_out(d) for d in rows]


def _new_code(d: Device) -> str:
    code = new_enroll_code()
    d.enroll_code_hash = sha256(code)
    d.enroll_expires_at = now() + ENROLL_TTL
    return code


@router.post("/spaces/{space_id}/devices", status_code=201)
async def create_device(body: DeviceIn, request: Request,
                        space: Space = Depends(require_space(Role.MANAGER)),
                        p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    d = Device(space_id=space.id, name=body.name)
    code = _new_code(d)
    db.add(d)
    await db.flush()
    audit(db, p, "device.created", space_id=space.id, target=d.id, request=request, name=d.name)
    await db.commit()
    return {**device_out(d), "enroll_code": code, "enroll_expires_at": d.enroll_expires_at}


async def _device(db: AsyncSession, space: Space, device_id: uuid.UUID) -> Device:
    d = await db.get(Device, device_id)
    if not d or d.space_id != space.id:
        raise HTTPException(404, "device not found")
    return d


@router.post("/spaces/{space_id}/devices/{device_id}/enroll-code")
async def new_device_code(device_id: uuid.UUID, request: Request,
                          space: Space = Depends(require_space(Role.MANAGER)),
                          p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Re-enroll (e.g. replaced hardware). The old token stops working once the code is used."""
    d = await _device(db, space, device_id)
    d.revoked_at = None
    code = _new_code(d)
    audit(db, p, "device.enroll_code", space_id=space.id, target=d.id, request=request)
    await db.commit()
    return {"enroll_code": code, "enroll_expires_at": d.enroll_expires_at}


@router.patch("/spaces/{space_id}/devices/{device_id}")
async def rename_device(device_id: uuid.UUID, body: DeviceIn, request: Request,
                        space: Space = Depends(require_space(Role.MANAGER)),
                        p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    d = await _device(db, space, device_id)
    d.name = body.name.strip()
    audit(db, p, "device.renamed", space_id=space.id, target=d.id, request=request, name=d.name)
    await db.commit()
    return device_out(d)


@router.delete("/spaces/{space_id}/devices/{device_id}")
async def revoke_device(device_id: uuid.UUID, request: Request, remove: bool = False,
                        space: Space = Depends(require_space(Role.MANAGER)),
                        p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Disconnect a kiosk (it stops syncing at once). remove=true then deletes a disconnected
    kiosk from the list; its past check-ins stay."""
    d = await _device(db, space, device_id)
    if remove:
        if not d.revoked_at:
            raise HTTPException(409, "disconnect the kiosk first")
        await db.delete(d)
        audit(db, p, "device.removed", space_id=space.id, target=device_id, request=request)
        await db.commit()
        return {"ok": True}
    d.revoked_at, d.token_hash, d.enroll_code_hash = now(), None, None
    audit(db, p, "device.revoked", space_id=space.id, target=d.id, request=request)
    await db.commit()
    return {"ok": True}


SETTINGS_TTL = timedelta(minutes=10)


@router.post("/spaces/{space_id}/devices/{device_id}/settings-token")
async def settings_token(device_id: uuid.UUID, request: Request,
                         space: Space = Depends(require_space(Role.MANAGER)),
                         p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Short-lived QR that opens the settings screen on this one kiosk (checked offline)."""
    from fexor_core import qr

    d = await _device(db, space, device_id)
    if not d.enrolled_at or d.revoked_at:
        raise HTTPException(409, "kiosk is not set up")
    kid, key = await active_signing_key(db)
    expires = now() + SETTINGS_TTL
    token = qr.sign_settings(key, kid, str(d.id), int(expires.timestamp()))
    audit(db, p, "device.settings_token", space_id=space.id, target=d.id, request=request)
    await db.commit()
    return {"token": token, "expires_at": expires}


# ---------- device side ----------
class EnrollIn(BaseModel):
    code: str = Field(max_length=20)
    info: dict[str, Any] = {}


@router.post("/device/enroll")
async def device_enroll(body: EnrollIn, request: Request, db: AsyncSession = Depends(get_db)):
    _rate_limit(f"enroll:{request.client.host if request.client else '?'}", limit=10, window=300)
    d = (await db.execute(select(Device).where(
        Device.enroll_code_hash == sha256(normalize_code(body.code))
    ))).scalar_one_or_none()
    if not d or not d.enroll_expires_at or d.enroll_expires_at < now():
        raise HTTPException(401, "invalid or expired enrollment code")
    space = await db.get(Space, d.space_id)
    if not space or space.deleted_at:
        raise HTTPException(401, "space removed")
    token = "fxd_" + secrets.token_urlsafe(32)
    d.token_hash, d.enroll_code_hash, d.enroll_expires_at = sha256(token), None, None
    d.enrolled_at, d.revoked_at, d.info = now(), None, _clean_info(body.info)
    audit(db, None, "device.enrolled", space_id=space.id, target=d.id, request=request)
    await db.commit()
    return {"device_id": str(d.id), "token": token, "space_id": str(space.id),
            "space_name": space.name, "device_name": d.name}


def _clean_info(info: dict[str, Any]) -> dict[str, Any]:
    return {str(k)[:40]: (v if isinstance(v, int | float | bool) else str(v)[:200])
            for k, v in list(info.items())[:30]}


class HeartbeatIn(BaseModel):
    info: dict[str, Any] = {}


@router.post("/device/heartbeat")
async def heartbeat(body: HeartbeatIn, d: Device = Depends(current_device),
                    db: AsyncSession = Depends(get_db)):
    d.info = _clean_info(body.info)
    await db.commit()
    return {"server_time": now().isoformat()}


@router.get("/device/sync")
async def sync(response: Response, if_none_match: str | None = Header(None),
               d: Device = Depends(current_device), db: AsyncSession = Depends(get_db)):
    """Full roster of the device's space. ETag makes unchanged polls cheap."""
    space = await db.get(Space, d.space_id)
    assert space
    members = (await db.execute(
        select(Membership, Person).join(Person, Person.id == Membership.person_id)
        .where(Membership.space_id == space.id)
    )).all()
    person_ids = [p.id for _, p in members]
    templates: dict[uuid.UUID, list[FaceTemplate]] = {}
    for t in (await db.execute(select(FaceTemplate).options(defer(FaceTemplate.photo)).where(
            FaceTemplate.person_id.in_(person_ids), FaceTemplate.model_id == MODEL_ID))).scalars():
        templates.setdefault(t.person_id, []).append(t)
    creds = {c.person_id: c for c in (await db.execute(
        select(QrCredential).where(QrCredential.person_id.in_(person_ids),
                                   QrCredential.revoked_at.is_(None))
        .order_by(QrCredential.issued_at)
    )).scalars()}
    await active_signing_key(db)  # exists before first sync, so later settings QRs verify
    keys = await public_keys(db)

    today = now().astimezone(ZoneInfo(space.timezone)).date()
    active = {m.id: _member_active(m, today) for m, _ in members}
    h = hashlib.sha256()
    h.update(f"{space.updated_at}{sorted(keys.items())}".encode())
    for m, p in sorted(members, key=lambda r: r[0].id):
        c = creds.get(p.id)
        # active[] changes with the date alone (an end date passing), so it is hashed too.
        h.update(f"{m.id}{m.updated_at}{p.updated_at}{c.id if c else ''}{active[m.id]}".encode())
        h.update("".join(sorted(str(t.id) for t in templates.get(p.id, []))).encode())
    etag = f'"{h.hexdigest()}"'
    await db.commit()  # persist last_seen
    if if_none_match == etag:
        response.status_code = 304
        response.headers["ETag"] = etag
        return None

    key = get_settings().key
    response.headers["ETag"] = etag
    return {
        "space": {
            "id": str(space.id), "name": space.name, "mode": space.mode,
            "timezone": space.timezone, "member_label": space.member_label,
            "match_threshold": space.match_threshold,
            "liveness_threshold": space.liveness_threshold,
            "challenge_actions": space.challenge_actions,
            "min_rescan_minutes": space.min_rescan_minutes,
            "store_failure_snapshots": space.store_failure_snapshots,
        },
        "model_id": MODEL_ID,
        "keys": keys,
        "members": [
            {
                "id": str(m.id), "person_id": str(p.id), "name": p.full_name,
                "external_id": m.external_id, "title": m.title,
                "active": active[m.id],
                "credential_id": str(creds[p.id].id) if p.id in creds else None,
                "templates": [
                    base64.b64encode(crypto.unseal(key, t.embedding, str(p.id).encode())).decode()
                    for t in templates.get(p.id, [])
                ],
            }
            for m, p in members
        ],
        "server_time": now().isoformat(),
    }


def _member_active(m: Membership, today: date) -> bool:
    return m.active and (m.start_date is None or m.start_date <= today) \
        and (m.end_date is None or today <= m.end_date)


class EventIn(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    status: Literal["accepted", "rejected", "pending"]
    member_id: uuid.UUID | None = None
    reason: str = Field("", max_length=200)
    note: str = Field("", max_length=500)
    scores: dict[str, float | int | str | bool | None] = {}
    snapshot_jpeg_b64: str | None = Field(None, max_length=400_000)

    @field_validator("occurred_at")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        return v


class EventsIn(BaseModel):
    events: list[EventIn] = Field(max_length=500)


@router.post("/device/events")
async def push_events(body: EventsIn, d: Device = Depends(current_device),
                      db: AsyncSession = Depends(get_db)):
    """Idempotent: re-sending an event id is a no-op. Returns ids now stored server-side."""
    space = await db.get(Space, d.space_id)
    assert space
    ids = {e.member_id for e in body.events if e.member_id}
    valid = set((await db.execute(select(Membership.id).where(
        Membership.space_id == space.id, Membership.id.in_(ids)))).scalars()) if ids else set()
    key = get_settings().key
    rows = []
    for e in body.events:
        mid = e.member_id if e.member_id in valid else None
        status = e.status
        reason = e.reason
        if e.member_id and mid is None:
            status, reason = EventStatus.REJECTED, f"unknown_member;{reason}"
        if e.occurred_at > now() + timedelta(minutes=5):
            status, reason = EventStatus.PENDING, f"device_clock_ahead;{reason}"
        elif e.occurred_at < now() - MAX_EVENT_AGE and status == EventStatus.ACCEPTED:
            status, reason = EventStatus.PENDING, f"device_clock_behind;{reason}"
        snap = None
        if e.snapshot_jpeg_b64 and space.store_failure_snapshots:
            try:
                snap = crypto.seal(key, base64.b64decode(e.snapshot_jpeg_b64), b"snapshot")
            except ValueError:
                snap = None
        rows.append(dict(id=e.id, space_id=space.id, membership_id=mid, device_id=d.id,
                         occurred_at=e.occurred_at, status=status, source="kiosk",
                         reason=reason[:200], note=e.note,
                         scores={k: v for k, v in list(e.scores.items())[:20]}, snapshot=snap))
    inserted: list[uuid.UUID] = []
    if rows:
        res = await db.execute(insert(AttendanceEvent).values(rows)
                               .on_conflict_do_nothing(index_elements=["id"])
                               .returning(AttendanceEvent.id))
        inserted = list(res.scalars())
    new = [r for r in rows if r["id"] in set(inserted)]
    accepted = [r for r in new if r["status"] == EventStatus.ACCEPTED and r["membership_id"]]
    if accepted:
        start, end = event_dates(space, [r["occurred_at"] for r in accepted])
        await recompute(db, space, list({r["membership_id"] for r in accepted}), start, end)
    await notify_webhooks(db, space, new)
    await db.commit()
    return {"stored": [str(e.id) for e in body.events]}


async def notify_webhooks(db: AsyncSession, space: Space, events: list[dict]) -> None:
    if not events:
        return
    hooks = (await db.execute(select(Webhook.id).where(
        Webhook.active, (Webhook.space_id == space.id) | Webhook.space_id.is_(None),
        Webhook.events.contains(["attendance.event"]),
    ))).scalars().all()
    for hid in hooks:
        for e in events:
            enqueue(db, "webhook", {"webhook_id": str(hid), "type": "attendance.event",
                                    "event_id": str(uuid.uuid4()), "data": {
                "id": str(e["id"]), "space_id": str(space.id),
                "member_id": str(e["membership_id"]) if e["membership_id"] else None,
                "occurred_at": e["occurred_at"].isoformat(), "status": e["status"],
                "source": e["source"], "reason": e["reason"],
            }})
