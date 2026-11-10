"""Spaces, members (people in a space) and Excel/CSV import."""

import asyncio
import csv
import io
import math
import uuid
from datetime import date, time
from typing import Any, Literal
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from openpyxl import Workbook, load_workbook
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import (
    ROLE_RANK,
    FaceTemplate,
    Membership,
    Mode,
    Person,
    QrCredential,
    Role,
    Shift,
    Space,
)
from ..security import Principal, audit, current, now, require_space, require_superadmin
from ..services import queue_recompute, space_today

router = APIRouter(prefix="/api", tags=["spaces"])


# ---------- spaces ----------
class FieldDef(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=100)
    type: Literal["text", "number", "date", "select"] = "text"
    required: bool = False
    options: list[str] = Field([], max_length=200)


class SpaceBase(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    timezone: str | None = None
    mode: Mode | None = None
    country: str | None = Field(None, max_length=8)
    member_label: str | None = Field(None, min_length=1, max_length=40)
    field_schema: list[FieldDef] | None = Field(None, max_length=50)
    default_shift_id: uuid.UUID | None = None
    min_rescan_minutes: int | None = Field(None, ge=0, le=240)
    match_threshold: float | None = Field(None, ge=0.3, le=0.9)
    liveness_threshold: float | None = Field(None, ge=0.1, le=0.99)
    challenge_actions: int | None = Field(None, ge=0, le=4)
    store_failure_snapshots: bool | None = None
    keep_enrollment_photos: bool | None = None
    time_format: Literal["24h", "12h"] | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is not None and v not in available_timezones():
            raise ValueError("unknown timezone")
        return v


# Columns that can't be empty: a PATCH may change them but not clear them.
REQUIRED_SPACE_FIELDS = {"name", "timezone", "mode", "member_label", "field_schema",
                         "min_rescan_minutes", "match_threshold", "liveness_threshold",
                         "challenge_actions", "store_failure_snapshots", "keep_enrollment_photos",
                         "time_format"}
# Changing these changes how days are counted; applied from today (past days are kept).
RULE_FIELDS = {"timezone", "mode", "default_shift_id", "min_rescan_minutes"}


class SpaceCreate(SpaceBase):
    name: str = Field(min_length=1, max_length=200)
    timezone: str = "UTC"
    template: Literal["school", "office", "custom"] = "custom"


TEMPLATES: dict[str, dict[str, Any]] = {
    "school": dict(
        mode=Mode.SINGLE, member_label="Student",
        field_schema=[
            {"key": "class", "label": "Class", "type": "text", "required": False, "options": []},
            {"key": "section", "label": "Section", "type": "text", "required": False, "options": []},
            {"key": "guardian_phone", "label": "Guardian phone", "type": "text",
             "required": False, "options": []},
        ],
        shift=dict(name="School day", start_time=time(8), end_time=time(14), grace_minutes=10,
                   weekdays=[0, 1, 2, 3, 4]),
    ),
    "office": dict(
        mode=Mode.IN_OUT, member_label="Employee",
        field_schema=[
            {"key": "department", "label": "Department", "type": "text", "required": False,
             "options": []},
        ],
        shift=dict(name="General", start_time=time(9), end_time=time(18), grace_minutes=10,
                   half_day_minutes=240, weekdays=[0, 1, 2, 3, 4]),
    ),
    "custom": dict(mode=Mode.IN_OUT, member_label="Member", field_schema=[], shift=None),
}


def space_out(s: Space, role: str | None = None) -> dict:
    return {
        "id": str(s.id), "name": s.name, "description": s.description, "timezone": s.timezone,
        "mode": s.mode, "country": s.country, "member_label": s.member_label,
        "field_schema": s.field_schema,
        "default_shift_id": str(s.default_shift_id) if s.default_shift_id else None,
        "min_rescan_minutes": s.min_rescan_minutes, "match_threshold": s.match_threshold,
        "liveness_threshold": s.liveness_threshold, "challenge_actions": s.challenge_actions,
        "store_failure_snapshots": s.store_failure_snapshots,
        "keep_enrollment_photos": s.keep_enrollment_photos, "time_format": s.time_format,
        "created_at": s.created_at,
        "role": role,
    }


@router.post("/spaces", status_code=201)
async def create_space(body: SpaceCreate, request: Request, p: Principal = Depends(require_superadmin),
                       db: AsyncSession = Depends(get_db)):
    tpl = TEMPLATES[body.template]
    if body.default_shift_id:
        raise HTTPException(422, "a new space has no shifts yet; set the default shift later")
    data = body.model_dump(exclude={"template"}, exclude_none=True)
    data.setdefault("mode", tpl["mode"])
    data.setdefault("member_label", tpl["member_label"])
    data["field_schema"] = [f if isinstance(f, dict) else f.model_dump()
                            for f in data.get("field_schema", tpl["field_schema"])]
    s = Space(**data)
    db.add(s)
    await db.flush()
    if tpl["shift"]:
        sh = Shift(space_id=s.id, **tpl["shift"])
        db.add(sh)
        await db.flush()
        s.default_shift_id = sh.id
    audit(db, p, "space.created", space_id=s.id, target=s.id, request=request, name=s.name)
    await db.commit()
    return space_out(s, Role.OWNER)


@router.get("/spaces")
async def list_spaces(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(Space).where(Space.deleted_at.is_(None)).order_by(Space.name)
    )).scalars()
    out = []
    for s in rows:
        role = p.role_in(s.id)
        if role:
            out.append(space_out(s, role))
    return out


@router.get("/spaces/{space_id}")
async def get_space(space: Space = Depends(require_space(Role.VIEWER)),
                    p: Principal = Depends(current)):
    return space_out(space, p.role_in(space.id))


@router.patch("/spaces/{space_id}")
async def update_space(body: SpaceBase, request: Request,
                       space: Space = Depends(require_space(Role.OWNER)),
                       p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    data = body.model_dump(exclude_unset=True)
    empty = sorted(k for k in REQUIRED_SPACE_FIELDS if k in data and data[k] is None)
    if empty:
        raise HTTPException(422, f"{', '.join(empty)} cannot be empty")
    if "description" in data and data["description"] is None:
        data["description"] = ""
    if "default_shift_id" in data and data["default_shift_id"]:
        sh = await db.get(Shift, data["default_shift_id"])
        if not sh or sh.space_id != space.id or not sh.active:
            raise HTTPException(422, "shift belongs to another space")
    changed = {k for k, v in data.items() if getattr(space, k) != v}
    for k, v in data.items():
        setattr(space, k, v)
    if changed & RULE_FIELDS:
        queue_recompute(db, space, start=space_today(space))
    audit(db, p, "space.updated", space_id=space.id, target=space.id, request=request,
          fields=sorted(data))
    await db.commit()
    return space_out(space, p.role_in(space.id))


@router.delete("/spaces/{space_id}")
async def delete_space(space_id: uuid.UUID, request: Request, confirm: str = "",
                       p: Principal = Depends(require_superadmin), db: AsyncSession = Depends(get_db)):
    space = await db.get(Space, space_id)
    if not space or space.deleted_at:
        raise HTTPException(404, "space not found")
    if confirm != space.name:
        raise HTTPException(400, "type the space name to confirm")
    space.deleted_at = now()  # soft delete; devices stop syncing immediately
    audit(db, p, "space.deleted", space_id=space.id, target=space.id, request=request)
    await db.commit()
    return {"ok": True}


# ---------- members ----------
class MemberIn(BaseModel):
    full_name: str | None = Field(None, min_length=1, max_length=200)
    external_id: str | None = Field(None, min_length=1, max_length=100)
    title: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=40)
    fields: dict[str, Any] | None = None
    active: bool | None = None
    start_date: date | None = None
    end_date: date | None = None
    consent: bool | None = None  # biometric consent captured
    person_id: uuid.UUID | None = None  # add an existing person from another space


def validate_fields(schema: list[dict], values: dict[str, Any], partial: bool = False) -> dict:
    out: dict[str, Any] = {}
    defs = {f["key"]: f for f in schema}
    for key, v in values.items():
        d = defs.get(key)
        if d is None:
            raise ValueError(f"unknown field '{key}'")
        if v in (None, ""):
            continue
        t = d.get("type", "text")
        if t == "number":
            v = float(v)
            if not math.isfinite(v):
                raise ValueError(f"{d['label']} must be a number")
            if not math.isfinite(v):
                raise ValueError(f"{d['label']} must be a number")
        elif t == "date":
            v = date.fromisoformat(str(v)[:10]).isoformat()
        elif t == "select" and str(v) not in d.get("options", []):
            raise ValueError(f"'{v}' is not an option for {d['label']}")
        else:
            v = str(v)[:500]
        out[key] = v
    if not partial:
        for d in schema:
            if d.get("required") and d["key"] not in out:
                raise ValueError(f"{d['label']} is required")
    return out


async def member_rows(db: AsyncSession, space_id: uuid.UUID, ids: list[uuid.UUID] | None = None,
                      q: str | None = None, active: bool | None = None, offset: int = 0,
                      limit: int = 50) -> tuple[list[dict], int]:
    faces = (select(FaceTemplate.person_id, func.count().label("n"))
             .group_by(FaceTemplate.person_id).subquery())
    creds = (select(QrCredential.person_id, func.count().label("n"))
             .where(QrCredential.revoked_at.is_(None)).group_by(QrCredential.person_id).subquery())
    base = (select(Membership, Person, func.coalesce(faces.c.n, 0), func.coalesce(creds.c.n, 0))
            .join(Person, Person.id == Membership.person_id)
            .outerjoin(faces, faces.c.person_id == Person.id)
            .outerjoin(creds, creds.c.person_id == Person.id)
            .where(Membership.space_id == space_id))
    if ids is not None:
        base = base.where(Membership.id.in_(ids))
    if q:
        like = f"%{q.strip()}%"
        base = base.where(or_(Person.full_name.ilike(like), Membership.external_id.ilike(like),
                              Membership.title.ilike(like)))
    if active is not None:
        base = base.where(Membership.active == active)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.order_by(Person.full_name).offset(offset).limit(limit))).all()
    return [member_out(m, p, nf, nq) for m, p, nf, nq in rows], total


def member_out(m: Membership, p: Person, faces: int = 0, creds: int = 0) -> dict:
    return {
        "id": str(m.id), "person_id": str(p.id), "full_name": p.full_name, "email": p.email,
        "phone": p.phone, "external_id": m.external_id, "title": m.title, "fields": m.fields,
        "active": m.active, "start_date": m.start_date, "end_date": m.end_date,
        "consent_at": p.consent_at, "face_count": faces, "has_qr": creds > 0,
        "created_at": m.created_at,
    }


def parse_ids(raw: str | None, limit: int = 500) -> list[uuid.UUID] | None:
    """Comma-separated ids from a query string (?ids=a,b,c)."""
    if raw is None:
        return None
    try:
        ids = [uuid.UUID(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError as e:
        raise HTTPException(422, "ids must be a comma-separated list of ids") from e
    if len(ids) > limit:
        raise HTTPException(422, f"at most {limit} ids")
    return ids


@router.get("/spaces/{space_id}/members")
async def list_members(q: str | None = None, active: bool | None = None, page: int = 1,
                       size: int = 50, ids: str | None = None,
                       space: Space = Depends(require_space(Role.VIEWER)),
                       db: AsyncSession = Depends(get_db)):
    size = max(1, min(size, 500))
    items, total = await member_rows(db, space.id, ids=parse_ids(ids), q=q, active=active,
                                     offset=(max(page, 1) - 1) * size, limit=size)
    return {"items": items, "total": total}


async def _get_member(db: AsyncSession, space: Space, member_id: uuid.UUID) -> Membership:
    m = await db.get(Membership, member_id)
    if not m or m.space_id != space.id:
        raise HTTPException(404, "member not found")
    return m


@router.get("/spaces/{space_id}/members/{member_id}")
async def get_member(member_id: uuid.UUID, space: Space = Depends(require_space(Role.VIEWER)),
                     db: AsyncSession = Depends(get_db)):
    await _get_member(db, space, member_id)
    items, _ = await member_rows(db, space.id, ids=[member_id])
    return items[0]


def _changed_fields(space: Space, m: Membership | None, values: dict[str, Any]) -> dict:
    """On an edit, skip what the person already has: fields since removed from the space and
    values that no longer pass newer rules (an option renamed) must not block other changes."""
    if m is None:
        return values
    keys = {f["key"] for f in space.field_schema}
    old = m.fields or {}
    return {k: v for k, v in values.items()
            if not (k in old and (k not in keys or old[k] == v))}


async def _check_person_access(db: AsyncSession, p: Principal, person_id: uuid.UUID) -> Person:
    """Adding an existing person links their identity, faces and QR card into this space.
    Only allowed for someone who can already manage that person somewhere else."""
    person = await db.get(Person, person_id)
    spaces = (await db.execute(select(Membership.space_id).where(
        Membership.person_id == person_id))).scalars().all()
    if person and (p.is_superadmin or any(
            (r := p.role_in(s)) and ROLE_RANK[r] >= ROLE_RANK[Role.MANAGER] for s in spaces)):
        return person
    raise HTTPException(404, "person not found")


async def _upsert_member(db: AsyncSession, space: Space, body: MemberIn,
                         m: Membership | None, p: Principal | None = None) -> Membership:
    try:
        # A new person is always checked, so required fields can't be skipped by leaving
        # "fields" out of the request.
        fields = (validate_fields(space.field_schema, _changed_fields(space, m, body.fields or {}),
                                  partial=m is not None)
                  if body.fields is not None or m is None else None)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    ext = body.external_id.strip() if body.external_id else None
    if ext:
        clash = (await db.execute(select(Membership.id).where(
            Membership.space_id == space.id, Membership.external_id == ext,
        ))).scalar_one_or_none()
        if clash and (m is None or clash != m.id):
            raise HTTPException(409, f"ID '{ext}' is already used in this space")
    if m is None:
        if body.person_id:
            if p is None:
                raise HTTPException(422, "person_id is not allowed here")
            person = await _check_person_access(db, p, body.person_id)
            if (await db.execute(select(Membership.id).where(
                    Membership.space_id == space.id, Membership.person_id == person.id))).first():
                raise HTTPException(409, "person is already in this space")
        else:
            if not body.full_name or not ext:
                raise HTTPException(422, "full_name and external_id are required")
            person = Person(full_name=body.full_name.strip())
            db.add(person)
            await db.flush()
        m = Membership(space_id=space.id, person_id=person.id, external_id=ext or "",
                       fields=fields or {})
        db.add(m)
    person = await db.get(Person, m.person_id)
    assert person
    if body.full_name:
        person.full_name = body.full_name.strip()
    for attr in ("email", "phone"):
        v = getattr(body, attr)
        if v is not None:
            setattr(person, attr, v.strip() or None)
    if body.consent is not None and bool(person.consent_at) != body.consent:
        # Only a real change moves the date: it records when consent was given.
        person.consent_at = now() if body.consent else None
    if ext:
        m.external_id = ext
    if body.title is not None:
        m.title = body.title.strip()
    if fields is not None:
        keys = {f["key"] for f in space.field_schema}
        m.fields = {k: v for k, v in {**(m.fields or {}), **fields}.items() if k in keys}
    for attr in ("start_date", "end_date"):
        if attr in body.model_fields_set:
            setattr(m, attr, getattr(body, attr))
    if body.active is not None and body.active != m.active:
        set_active(m, body.active, space)
    await db.flush()
    return m


def set_active(m: Membership, active: bool, space: Space) -> None:
    """Deactivating records the last day (so past days stay and no new absences start);
    reactivating removes it."""
    m.active = active
    today = space_today(space)
    if not active and (m.end_date is None or m.end_date > today):
        m.end_date = today
    elif active and m.end_date is not None and m.end_date <= today:
        m.end_date = None


@router.post("/spaces/{space_id}/members", status_code=201)
async def create_member(body: MemberIn, request: Request,
                        space: Space = Depends(require_space(Role.MANAGER)),
                        p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    m = await _upsert_member(db, space, body, None, p)
    audit(db, p, "member.created", space_id=space.id, target=m.id, request=request,
          external_id=m.external_id)
    await db.commit()
    items, _ = await member_rows(db, space.id, ids=[m.id])
    return items[0]


@router.patch("/spaces/{space_id}/members/{member_id}")
async def update_member(member_id: uuid.UUID, body: MemberIn, request: Request,
                        space: Space = Depends(require_space(Role.MANAGER)),
                        p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    m = await _upsert_member(db, space, body, await _get_member(db, space, member_id))
    audit(db, p, "member.updated", space_id=space.id, target=m.id, request=request,
          fields=sorted(body.model_fields_set))
    await db.commit()
    items, _ = await member_rows(db, space.id, ids=[m.id])
    return items[0]


@router.delete("/spaces/{space_id}/members/{member_id}")
async def delete_member(member_id: uuid.UUID, request: Request, erase: bool = False,
                        space: Space = Depends(require_space(Role.MANAGER)),
                        p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    """Deactivate by default. erase=true (owner) permanently removes the person's data from
    this space; if they belong to no other space their identity, faces and QR are erased too."""
    m = await _get_member(db, space, member_id)
    if not erase:
        set_active(m, False, space)
        audit(db, p, "member.deactivated", space_id=space.id, target=m.id, request=request)
        await db.commit()
        return {"ok": True}
    if p.role_in(space.id) != Role.OWNER:
        raise HTTPException(403, "only space owners can erase")
    person_id = m.person_id
    await db.delete(m)
    await db.flush()
    others = (await db.execute(
        select(func.count()).select_from(Membership).where(Membership.person_id == person_id)
    )).scalar_one()
    if not others:
        person = await db.get(Person, person_id)
        if person:
            await db.delete(person)  # cascades faces + credentials
    audit(db, p, "member.erased", space_id=space.id, target=member_id, request=request,
          person_erased=not others)
    await db.commit()
    return {"ok": True}


# ---------- import ----------
CORE_COLUMNS = {
    "full_name": ("full_name", "name", "full name", "student name", "employee name"),
    "external_id": ("external_id", "id", "employee_id", "employee id", "roll_no", "roll no",
                    "student id", "emp id", "code"),
    "title": ("title", "designation", "position", "role", "grade"),
    "email": ("email", "e-mail", "mail"),
    "phone": ("phone", "mobile", "phone number", "contact"),
    "start_date": ("start_date", "start date", "joining date", "joined"),
    "end_date": ("end_date", "end date", "leaving date"),
}


MAX_IMPORT_ROWS = 20000


class TooManyRows(Exception):
    pass


def _read_table(filename: str, data: bytes) -> list[dict[str, Any]]:
    """Rows as dicts. Stops reading past MAX_IMPORT_ROWS (a small xlsx can hold millions)."""
    out: list[dict[str, Any]] = []

    def add(row: dict[str, Any]) -> None:
        if len(out) >= MAX_IMPORT_ROWS:
            raise TooManyRows
        out.append(row)

    if filename.lower().endswith(".csv"):
        for row in csv.DictReader(io.StringIO(data.decode("utf-8-sig"))):
            add(row)
        return out
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    rows = wb.active.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows, [])]
    for r in rows:
        if r is None or all(v in (None, "") for v in r):
            continue
        add({header[i]: v for i, v in enumerate(r) if i < len(header) and header[i]})
    return out


def _map_row(raw: dict[str, Any], schema: list[dict]) -> MemberIn:
    norm = {str(k).strip().lower(): v for k, v in raw.items()}
    data: dict[str, Any] = {}
    for key, aliases in CORE_COLUMNS.items():
        for a in aliases:
            if a in norm and norm[a] not in (None, ""):
                v = norm[a]
                if key in ("start_date", "end_date"):
                    v = v.date() if hasattr(v, "date") else date.fromisoformat(str(v)[:10])
                else:
                    v = str(v).strip()
                    if key == "external_id" and v.endswith(".0"):
                        v = v[:-2]  # Excel turns 1001 into 1001.0
                data[key] = v
                break
    fields = {}
    for f in schema:
        for a in (f["key"], f["label"].strip().lower()):
            if a in norm and norm[a] not in (None, ""):
                v = norm[a]
                fields[f["key"]] = v.date().isoformat() if hasattr(v, "date") else v
                break
    data["fields"] = fields
    return MemberIn(**data)


@router.post("/spaces/{space_id}/import")
async def import_members(request: Request, file: UploadFile = File(...), dry_run: bool = True,
                         space: Space = Depends(require_space(Role.MANAGER)),
                         p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "file too large (max 10 MB)")
    try:
        rows = await asyncio.to_thread(_read_table, file.filename or "", data)
    except TooManyRows as e:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"too many rows (max {MAX_IMPORT_ROWS})") from e
    except Exception as e:
        raise HTTPException(422, "could not read this file; save it as .xlsx or .csv") from e
    existing = {
        m.external_id: m for m in (await db.execute(
            select(Membership).where(Membership.space_id == space.id)
        )).scalars()
    }
    report: dict[str, Any] = {"total": len(rows), "created": 0, "updated": 0, "errors": [],
                              "dry_run": dry_run}
    seen: set[str] = set()
    for i, raw in enumerate(rows, start=2):  # row 1 is the header
        try:
            body = _map_row(raw, space.field_schema)
            if not body.external_id or not body.full_name:
                raise ValueError("name and ID columns are required")
            if body.external_id in seen:
                raise ValueError(f"duplicate ID '{body.external_id}' in file")
            seen.add(body.external_id)
            validate_fields(space.field_schema, body.fields or {},
                            partial=body.external_id in existing)
            target = existing.get(body.external_id)
            if not dry_run:
                async with db.begin_nested():
                    await _upsert_member(db, space, body, target)
            report["updated" if target else "created"] += 1
        except HTTPException as e:
            report["errors"].append({"row": i, "error": e.detail})
        except Exception as e:  # noqa: BLE001 - surface per-row problems to the user
            report["errors"].append({"row": i, "error": str(e)})
    if not dry_run:
        audit(db, p, "member.imported", space_id=space.id, request=request,
              created=report["created"], updated=report["updated"],
              errors=len(report["errors"]))
        await db.commit()
    return report


@router.get("/spaces/{space_id}/import/template.xlsx")
async def import_template(space: Space = Depends(require_space(Role.VIEWER))):
    wb = Workbook()
    ws = wb.active
    ws.title = "Members"
    ws.append(["full_name", "external_id", "title", "email", "phone", "start_date"]
              + [f["key"] for f in space.field_schema])
    ws.append(["Jane Doe", "1001", "Engineer" if space.mode == Mode.IN_OUT else "Grade 5",
               "jane@example.com", "", date.today().isoformat()]
              + ["" for _ in space.field_schema])
    buf = io.BytesIO()
    wb.save(buf)
    return Response(buf.getvalue(), media_type=(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={"Content-Disposition": 'attachment; filename="members-template.xlsx"'})

