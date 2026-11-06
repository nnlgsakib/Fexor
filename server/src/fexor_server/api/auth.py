import time
from collections import defaultdict, deque
from datetime import timedelta

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import AdminSession, AdminUser, Space, SpaceRole
from ..security import (
    COOKIE,
    Principal,
    _load_session,
    audit,
    create_session,
    current,
    hash_password,
    now,
    seal_secret,
    sha256,
    verify_password,
    verify_totp,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# ponytail: per-process sliding window; move to Postgres/Redis if running several replicas.
_attempts: dict[str, deque[float]] = defaultdict(deque)


def _rate_limit(key: str, limit: int = 10, window: float = 60.0) -> None:
    q, t = _attempts[key], time.monotonic()
    while q and t - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        raise HTTPException(429, "too many attempts, slow down")
    q.append(t)


class LoginIn(BaseModel):
    email: str
    password: str


class CodeIn(BaseModel):
    code: str


class PasswordIn(BaseModel):
    current_password: str
    new_password: str


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


@router.post("/login")
async def login(body: LoginIn, request: Request, response: Response,
                db: AsyncSession = Depends(get_db)):
    _rate_limit(f"ip:{_ip(request)}")
    s = get_settings()
    user = (await db.execute(
        select(AdminUser).where(func.lower(AdminUser.email) == body.email.lower())
    )).scalar_one_or_none()
    if user and user.locked_until and user.locked_until > now():
        raise HTTPException(423, "account temporarily locked, try later")
    ok = verify_password(user.password_hash if user else None, body.password)
    if not user or not ok or not user.active:
        if user:
            user.failed_logins += 1
            if user.failed_logins >= s.login_max_failures:
                user.locked_until = now() + timedelta(minutes=s.login_lock_minutes)
                user.failed_logins = 0
            audit(db, None, "login.failed", target=user.email, request=request)
            await db.commit()
        raise HTTPException(401, "invalid email or password")
    user.failed_logins = 0
    sess = await create_session(db, response, user, _ip(request), mfa_pending=user.totp_enabled)
    if not user.totp_enabled:
        user.last_login_at = now()
        audit(db, Principal(user, sess, {}), "login", request=request)
    await db.commit()
    return {"mfa_required": user.totp_enabled}


@router.post("/mfa")
async def mfa(body: CodeIn, request: Request, db: AsyncSession = Depends(get_db)):
    loaded = await _load_session(request, db)
    if not loaded or not loaded[0].mfa_pending:
        raise HTTPException(401, "no pending login")
    sess, user = loaded
    _rate_limit(f"mfa:{user.id}", limit=5)
    if not verify_totp(user, body.code):
        raise HTTPException(401, "invalid code")
    sess.mfa_pending = False
    user.last_login_at = now()
    audit(db, Principal(user, sess, {}), "login", request=request, mfa=True)
    await db.commit()
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get(COOKIE)
    if token:
        await db.execute(delete(AdminSession).where(AdminSession.token_hash == sha256(token)))
        await db.commit()
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def me(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    if not p.user:
        raise HTTPException(403, "admin session required")
    q = select(Space).where(Space.deleted_at.is_(None)).order_by(Space.name)
    if not p.is_superadmin:
        q = q.join(SpaceRole, SpaceRole.space_id == Space.id).where(SpaceRole.user_id == p.user.id)
    spaces = [
        {"id": str(s.id), "name": s.name, "mode": s.mode, "role": p.role_in(s.id)}
        for s in (await db.execute(q)).scalars()
    ]
    return {
        "id": str(p.user.id),
        "email": p.user.email,
        "name": p.user.name,
        "is_superadmin": p.user.is_superadmin,
        "totp_enabled": p.user.totp_enabled,
        "csrf": p.session.csrf if p.session else None,
        "spaces": spaces,
        "public_url": get_settings().public_url,
        "http_mode": not get_settings().secure_cookies,
    }


@router.post("/totp/setup")
async def totp_setup(p: Principal = Depends(current), db: AsyncSession = Depends(get_db)):
    assert p.user
    if p.user.totp_enabled:
        raise HTTPException(409, "2FA already enabled")
    secret = pyotp.random_base32()
    p.user.totp_secret = seal_secret(secret)
    await db.commit()
    uri = pyotp.TOTP(secret).provisioning_uri(name=p.user.email, issuer_name="Fexor")
    return {"secret": secret, "uri": uri}


@router.post("/totp/enable")
async def totp_enable(body: CodeIn, request: Request, p: Principal = Depends(current),
                      db: AsyncSession = Depends(get_db)):
    assert p.user
    if not p.user.totp_secret or not verify_totp(p.user, body.code):
        raise HTTPException(400, "invalid code")
    p.user.totp_enabled = True
    audit(db, p, "totp.enabled", request=request)
    await db.commit()
    return {"ok": True}


@router.post("/totp/disable")
async def totp_disable(body: CodeIn, request: Request, p: Principal = Depends(current),
                       db: AsyncSession = Depends(get_db)):
    assert p.user
    if not verify_totp(p.user, body.code):
        raise HTTPException(400, "invalid code")
    p.user.totp_enabled, p.user.totp_secret = False, None
    audit(db, p, "totp.disabled", request=request)
    await db.commit()
    return {"ok": True}


@router.post("/password")
async def change_password(body: PasswordIn, request: Request, p: Principal = Depends(current),
                          db: AsyncSession = Depends(get_db)):
    assert p.user and p.session
    if not verify_password(p.user.password_hash, body.current_password):
        raise HTTPException(400, "current password is wrong")
    p.user.password_hash = hash_password(body.new_password)
    # Sign out every other session.
    await db.execute(delete(AdminSession).where(
        AdminSession.user_id == p.user.id, AdminSession.token_hash != p.session.token_hash
    ))
    audit(db, p, "password.changed", request=request)
    await db.commit()
    return {"ok": True}

