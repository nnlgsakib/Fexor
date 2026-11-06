"""Org-level administration: admin users + roles, API keys, webhooks, audit log."""

import secrets
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fexor_core import crypto
from pydantic import BaseModel, HttpUrl
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import AdminSession, AdminUser, ApiKey, AuditLog, Role, SpaceRole, Webhook
from ..security import (
    Principal,
    audit,
    check_space,
    current,
    hash_password,
    now,
    require_superadmin,
    sha256,
)
from ..services import enqueue

router = APIRouter(prefix="/api", tags=["admin"])


# ---------- users ----------
class UserIn(BaseModel):
    email: str
    name: str
    password: str
    is_superadmin: bool = False


class UserPatch(BaseModel):
    name: str | None = None
    active: bool | None = None
    is_superadmin: bool | None = None
    password: str | None = None
    reset_2fa: bool = False


class RoleIn(BaseModel):
    space_id: uuid.UUID
    role: Role


def _user_out(u: AdminUser, roles: list[SpaceRole]) -> dict:
    return {
        "id": str(u.id), "email": u.email, "name": u.name, "active": u.active,
        "is_superadmin": u.is_superadmin, "totp_enabled": u.totp_enabled,
        "last_login_at": u.last_login_at, "created_at": u.created_at,
        "roles": [{"space_id": str(r.space_id), "role": r.role} for r in roles],
    }


@router.get("/users")
async def list_users(_: Principal = Depends(require_superadmin), db: AsyncSession = Depends(get_db)):
    users = (await db.execute(select(AdminUser).order_by(AdminUser.email))).scalars().all()
    roles = (await db.execute(select(SpaceRole))).scalars().all()
    return [_user_out(u, [r for r in roles if r.user_id == u.id]) for u in users]


@router.post("/users", status_code=201)
async def create_user(body: UserIn, request: Request, p: Principal = Depends(require_superadmin),
                      db: AsyncSession = Depends(get_db)):
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, "invalid email")
    if (await db.execute(select(AdminUser).where(func.lower(AdminUser.email) == email))).first():
        raise HTTPException(409, "email already exists")
    u = AdminUser(email=email, name=body.name.strip(), password_hash=hash_password(body.password),
                  is_superadmin=body.is_superadmin)
    db.add(u)
    await db.flush()
    audit(db, p, "user.created", target=u.id, request=request, email=email)
    await db.commit()
    return _user_out(u, [])


@router.patch("/users/{user_id}")
async def patch_user(user_id: uuid.UUID, body: UserPatch, request: Request,
                     p: Principal = Depends(require_superadmin), db: AsyncSession = Depends(get_db)):
    u = await db.get(AdminUser, user_id)
    if not u:
        raise HTTPException(404, "user not found")
    if u.id == p.user.id and (body.active is False or body.is_superadmin is False):
        raise HTTPException(400, "you cannot deactivate or demote yourself")
    if body.name is not None:
        u.name = body.name
    if body.active is not None:
        u.active = body.active
    if body.is_superadmin is not None:
        u.is_superadmin = body.is_superadmin
    if body.password:
        u.password_hash = hash_password(body.password)
    if body.reset_2fa:
        u.totp_enabled, u.totp_secret = False, None
    if body.password or body.active is False or body.reset_2fa:
        await db.execute(delete(AdminSession).where(AdminSession.user_id == u.id))
    audit(db, p, "user.updated", target=u.id, request=request,
          **body.model_dump(exclude={"password"}, exclude_none=True), password_reset=bool(body.password))
    await db.commit()
    roles = (await db.execute(select(SpaceRole).where(SpaceRole.user_id == u.id))).scalars().all()
    return _user_out(u, list(roles))


@router.put("/users/{user_id}/roles")
async def set_roles(user_id: uuid.UUID, body: list[RoleIn], request: Request,
                    p: Principal = Depends(require_superadmin), db: AsyncSession = Depends(get_db)):
    if not await db.get(AdminUser, user_id):
        raise HTTPException(404, "user not found")
    await db.execute(delete(SpaceRole).where(SpaceRole.user_id == user_id))
    for r in {r.space_id: r for r in body}.values():
        db.add(SpaceRole(user_id=user_id, space_id=r.space_id, role=r.role))
    audit(db, p, "user.roles", target=user_id, request=request,
          roles=[f"{r.space_id}:{r.role}" for r in body])
    await db.commit()
    return {"ok": True}


# ---------- API keys ----------
class ApiKeyIn(BaseModel):
    name: str
    space_id: uuid.UUID | None = None


@router.get("/api-keys")
async def list_keys(_: Principal = Depends(require_superadmin), db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))).scalars()
    return [{"id": str(k.id), "name": k.name, "prefix": k.prefix,
             "space_id": str(k.space_id) if k.space_id else None, "created_at": k.created_at,
             "last_used_at": k.last_used_at, "revoked_at": k.revoked_at} for k in rows]


@router.post("/api-keys", status_code=201)
async def create_key(body: ApiKeyIn, request: Request, p: Principal = Depends(require_superadmin),
                     db: AsyncSession = Depends(get_db)):
    token = "fxk_" + secrets.token_urlsafe(32)
    k = ApiKey(name=body.name, prefix=token[:10], key_hash=sha256(token), space_id=body.space_id,
               created_by=p.user.id)
    db.add(k)
    await db.flush()
    audit(db, p, "api_key.created", target=k.id, request=request, name=body.name)
    await db.commit()
    return {"id": str(k.id), "token": token}  # shown once


@router.delete("/api-keys/{key_id}")
async def revoke_key(key_id: uuid.UUID, request: Request, p: Principal = Depends(require_superadmin),
                     db: AsyncSession = Depends(get_db)):
    k = await db.get(ApiKey, key_id)
    if not k:
        raise HTTPException(404, "not found")
    k.revoked_at = now()
    audit(db, p, "api_key.revoked", target=k.id, request=request)
    await db.commit()
    return {"ok": True}


# ---------- webhooks ----------
class WebhookIn(BaseModel):
    url: HttpUrl
    space_id: uuid.UUID | None = None
    events: list[Literal["attendance.event", "attendance.day"]] = ["attendance.event"]
    active: bool = True


def _hook_out(h: Webhook) -> dict:
    return {"id": str(h.id), "url": h.url, "space_id": str(h.space_id) if h.space_id else None,
            "events": h.events, "active": h.active, "created_at": h.created_at}


@router.get("/webhooks")
async def list_hooks(_: Principal = Depends(require_superadmin), db: AsyncSession = Depends(get_db)):
    return [_hook_out(h) for h in (await db.execute(select(Webhook))).scalars()]


@router.post("/webhooks", status_code=201)
async def create_hook(body: WebhookIn, request: Request, p: Principal = Depends(require_superadmin),
                      db: AsyncSession = Depends(get_db)):
    if body.url.scheme != "https" and body.url.host not in ("localhost", "127.0.0.1"):
        raise HTTPException(422, "webhook URL must use https")
    secret = "whsec_" + secrets.token_urlsafe(24)
    h = Webhook(url=str(body.url), space_id=body.space_id, events=list(body.events),
                active=body.active,
                secret=crypto.seal(get_settings().key, secret.encode(), b"webhook"))
    db.add(h)
    await db.flush()
    audit(db, p, "webhook.created", target=h.id, request=request, url=str(body.url))
    await db.commit()
    return {**_hook_out(h), "secret": secret}  # shown once


@router.delete("/webhooks/{hook_id}")
async def delete_hook(hook_id: uuid.UUID, request: Request, p: Principal = Depends(require_superadmin),
                      db: AsyncSession = Depends(get_db)):
    await db.execute(delete(Webhook).where(Webhook.id == hook_id))
    audit(db, p, "webhook.deleted", target=hook_id, request=request)
    await db.commit()
    return {"ok": True}


@router.post("/webhooks/{hook_id}/test")
async def test_hook(hook_id: uuid.UUID, _: Principal = Depends(require_superadmin),
                    db: AsyncSession = Depends(get_db)):
    if not await db.get(Webhook, hook_id):
        raise HTTPException(404, "not found")
    enqueue(db, "webhook", {"webhook_id": str(hook_id), "type": "ping", "data": {"at": str(now())}})
    await db.commit()
    return {"queued": True}


# ---------- audit ----------
@router.get("/audit")
async def audit_log(space_id: uuid.UUID | None = None, before: int | None = None, limit: int = 100,
                    p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if space_id:
        check_space(p, space_id, Role.OWNER)
    elif not p.is_superadmin:
        raise HTTPException(403, "superadmin only")
    q = select(AuditLog, AdminUser.email).outerjoin(AdminUser, AdminUser.id == AuditLog.user_id)
    if space_id:
        q = q.where(AuditLog.space_id == space_id)
    if before:
        q = q.where(AuditLog.id < before)
    rows = (await db.execute(q.order_by(AuditLog.id.desc()).limit(min(limit, 500)))).all()
    return [{"id": a.id, "at": a.at, "user": email, "action": a.action, "target": a.target,
             "space_id": str(a.space_id) if a.space_id else None, "detail": a.detail, "ip": a.ip}
            for a, email in rows]
