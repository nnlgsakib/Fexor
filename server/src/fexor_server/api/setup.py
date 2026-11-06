"""First-run setup: create the first super admin from the browser instead of the CLI.

Open only while no admin exists. Requests from the server machine itself need nothing else;
anyone else must give the setup code (printed by the start script / `fexor-server setup-code`),
so a stranger on the network can't claim a fresh install first.
"""

import base64
import hashlib
import hmac

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_db
from ..models import AdminUser
from ..security import Principal, audit, create_session, hash_password, normalize_code
from .auth import _rate_limit

router = APIRouter(prefix="/api/setup", tags=["setup"])


def setup_code() -> str:
    """Derived from the data key: same in every worker and across restarts, no storage."""
    mac = hmac.new(get_settings().key, b"fexor-first-admin-setup", hashlib.sha256).digest()
    raw = base64.b32encode(mac).decode()[:10]
    return f"{raw[:5]}-{raw[5:]}"


def is_local(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1") and "x-forwarded-for" not in request.headers


async def needs_setup(db: AsyncSession) -> bool:
    return (await db.execute(select(func.count()).select_from(AdminUser))).scalar_one() == 0


class SetupIn(BaseModel):
    email: str
    name: str
    password: str
    code: str = ""


@router.get("/status")
async def status(request: Request, db: AsyncSession = Depends(get_db)):
    return {"needs_setup": await needs_setup(db), "code_required": not is_local(request)}


@router.post("")
async def create_first_admin(body: SetupIn, request: Request, response: Response,
                             db: AsyncSession = Depends(get_db)):
    _rate_limit(f"setup:{request.client.host if request.client else '?'}", limit=10, window=300)
    # Serialize concurrent attempts so only one first admin can ever be created.
    await db.execute(text("SELECT pg_advisory_xact_lock(724201)"))
    if not await needs_setup(db):
        raise HTTPException(409, "setup is already done; sign in instead")
    if not is_local(request) and not hmac.compare_digest(
            normalize_code(body.code), setup_code()):
        raise HTTPException(403, "wrong setup code")
    email = body.email.strip().lower()
    if "@" not in email or not body.name.strip():
        raise HTTPException(422, "enter your name and a valid email")
    user = AdminUser(email=email, name=body.name.strip(), password_hash=hash_password(body.password),
                     is_superadmin=True)
    db.add(user)
    await db.flush()
    sess = await create_session(db, response, user, request.client.host if request.client else None,
                                mfa_pending=False)
    audit(db, Principal(user, sess, {}), "setup.first_admin", request=request, email=email)
    await db.commit()
    return {"ok": True}
