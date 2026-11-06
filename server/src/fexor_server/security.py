"""Auth: Argon2id passwords, server-side sessions + CSRF, TOTP, RBAC, device and API-key tokens."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response
from fexor_core import crypto
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_db
from .models import (
    ROLE_RANK,
    AdminSession,
    AdminUser,
    ApiKey,
    AuditLog,
    Device,
    Role,
    Space,
    SpaceRole,
)

COOKIE = "fexor_session"
_ph = PasswordHasher()  # argon2id, library defaults (RFC 9106 low-memory profile)
_DUMMY_HASH = _ph.hash("timing-equalizer")


def now() -> datetime:
    return datetime.now(UTC)


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def hash_password(pw: str) -> str:
    if len(pw) < 10:
        raise HTTPException(422, "password must be at least 10 characters")
    return _ph.hash(pw)


def verify_password(hash_: str | None, pw: str) -> bool:
    try:
        return _ph.verify(hash_ or _DUMMY_HASH, pw) and hash_ is not None
    except (VerifyMismatchError, InvalidHashError):
        return False


# ---------- TOTP ----------
def seal_secret(s: str) -> bytes:
    return crypto.seal(get_settings().key, s.encode(), b"totp")


def unseal_secret(b: bytes) -> str:
    return crypto.unseal(get_settings().key, b, b"totp").decode()


def verify_totp(user: AdminUser, code: str) -> bool:
    if not user.totp_secret:
        return False
    return pyotp.TOTP(unseal_secret(user.totp_secret)).verify(code.strip(), valid_window=1)


# ---------- sessions ----------
async def create_session(
    db: AsyncSession, response: Response, user: AdminUser, ip: str | None, mfa_pending: bool
) -> AdminSession:
    token = secrets.token_urlsafe(32)
    sess = AdminSession(
        token_hash=sha256(token),
        user_id=user.id,
        csrf=secrets.token_urlsafe(24),
        mfa_pending=mfa_pending,
        expires_at=now() + timedelta(hours=get_settings().session_hours),
        ip=ip,
    )
    db.add(sess)
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=get_settings().secure_cookies,
        samesite="strict",
        max_age=get_settings().session_hours * 3600,
        path="/",
    )
    return sess


@dataclass
class Principal:
    user: AdminUser | None
    session: AdminSession | None
    roles: dict[uuid.UUID, Role]
    api_key: ApiKey | None = None

    @property
    def is_superadmin(self) -> bool:
        return bool(self.user and self.user.is_superadmin)

    def role_in(self, space_id: uuid.UUID) -> Role | None:
        if self.is_superadmin:
            return Role.OWNER
        if self.api_key:
            if self.api_key.space_id in (None, space_id):
                return Role.VIEWER
            return None
        return self.roles.get(space_id)


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


async def _load_session(request: Request, db: AsyncSession) -> tuple[AdminSession, AdminUser] | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    sess = await db.get(AdminSession, sha256(token))
    if not sess or sess.expires_at < now():
        return None
    user = await db.get(AdminUser, sess.user_id)
    if not user or not user.active:
        return None
    return sess, user


async def _api_key_principal(request: Request, db: AsyncSession) -> Principal | None:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer fxk_"):
        return None
    key = (
        await db.execute(select(ApiKey).where(ApiKey.key_hash == sha256(auth[7:].strip())))
    ).scalar_one_or_none()
    if not key or key.revoked_at:
        raise HTTPException(401, "invalid api key")
    if request.method not in SAFE_METHODS:
        raise HTTPException(403, "api keys are read-only")
    await db.execute(update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now()))
    await db.commit()
    return Principal(None, None, {}, api_key=key)


async def current(request: Request, db: AsyncSession = Depends(get_db)) -> Principal:
    """Authenticated admin (MFA complete) or API key. Enforces CSRF on unsafe methods."""
    p = await _api_key_principal(request, db)
    if p:
        return p
    loaded = await _load_session(request, db)
    if not loaded:
        raise HTTPException(401, "not authenticated")
    sess, user = loaded
    if sess.mfa_pending:
        raise HTTPException(401, "mfa required")
    if request.method not in SAFE_METHODS:
        sent = request.headers.get("x-csrf-token", "")
        if not hmac.compare_digest(sent, sess.csrf):
            raise HTTPException(403, "csrf token missing or invalid")
    rows = (await db.execute(select(SpaceRole).where(SpaceRole.user_id == user.id))).scalars()
    return Principal(user, sess, {r.space_id: Role(r.role) for r in rows})


def require_superadmin(p: Principal = Depends(current)) -> Principal:
    if not p.is_superadmin:
        raise HTTPException(403, "superadmin only")
    return p


def require_space(min_role: Role):
    """Dependency: caller has at least min_role in the path's space_id; returns the space."""

    async def dep(
        space_id: uuid.UUID, p: Principal = Depends(current), db: AsyncSession = Depends(get_db)
    ) -> Space:
        role = p.role_in(space_id)
        if role is None or ROLE_RANK[role] < ROLE_RANK[min_role]:
            # 404 for no access at all, so space ids don't leak
            raise HTTPException(404 if role is None else 403, "space not found or forbidden")
        space = await db.get(Space, space_id)
        if not space or space.deleted_at:
            raise HTTPException(404, "space not found")
        return space

    return dep


def check_space(p: Principal, space_id: uuid.UUID, min_role: Role) -> None:
    role = p.role_in(space_id)
    if role is None or ROLE_RANK[role] < ROLE_RANK[min_role]:
        raise HTTPException(404 if role is None else 403, "not found or forbidden")


# ---------- devices ----------
ENROLL_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_enroll_code() -> str:
    raw = "".join(secrets.choice(ENROLL_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def normalize_code(code: str) -> str:
    c = code.strip().upper().replace("-", "").replace(" ", "")
    return f"{c[:5]}-{c[5:]}"


async def current_device(request: Request, db: AsyncSession = Depends(get_db)) -> Device:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer fxd_"):
        raise HTTPException(401, "device token required")
    dev = (
        await db.execute(select(Device).where(Device.token_hash == sha256(auth[7:].strip())))
    ).scalar_one_or_none()
    if not dev or dev.revoked_at:
        raise HTTPException(401, "device revoked or unknown")
    space = await db.get(Space, dev.space_id)
    if not space or space.deleted_at:
        raise HTTPException(401, "space removed")
    dev.last_seen_at = now()
    dev.last_ip = request.client.host if request.client else None
    return dev


# ---------- audit ----------
def audit(
    db: AsyncSession,
    p: Principal | None,
    action: str,
    *,
    space_id: uuid.UUID | None = None,
    target: object = "",
    request: Request | None = None,
    **detail: object,
) -> None:
    db.add(
        AuditLog(
            user_id=p.user.id if p and p.user else None,
            space_id=space_id,
            action=action,
            target=str(target),
            detail={k: (str(v) if not isinstance(v, int | float | bool | None) else v)
                    for k, v in detail.items()},
            ip=request.client.host if request and request.client else None,
        )
    )
