"""Background worker: job queue (Postgres SKIP LOCKED) + periodic day closing.

Run one or more: `fexor-server worker`. Jobs survive restarts; failures retry with backoff.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from fexor_core import crypto
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import sessionmaker
from .models import AdminSession, Job, Space, Webhook
from .security import now
from .services import recompute_space

log = logging.getLogger("fexor.worker")
CLOSE_DAYS_EVERY = 600  # seconds


async def handle_recompute(db: AsyncSession, payload: dict) -> None:
    space = await db.get(Space, uuid.UUID(payload["space_id"]))
    if space and not space.deleted_at:
        await recompute_space(db, space, date.fromisoformat(payload["start"]),
                              date.fromisoformat(payload["end"]))


async def handle_webhook(db: AsyncSession, payload: dict) -> None:
    hook = await db.get(Webhook, uuid.UUID(payload["webhook_id"]))
    if not hook or not hook.active:
        return
    secret = crypto.unseal(get_settings().key, hook.secret, b"webhook")
    body = json.dumps({"id": str(uuid.uuid4()), "type": payload["type"],
                       "created_at": now().isoformat(), "data": payload["data"]},
                      separators=(",", ":"))
    ts = str(int(time.time()))
    sig = hmac.new(secret, f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        r = await client.post(hook.url, content=body, headers={
            "Content-Type": "application/json", "User-Agent": "Fexor-Webhook/1",
            "X-Fexor-Signature": f"t={ts},v1={sig}"})
    if r.status_code >= 300:
        raise RuntimeError(f"webhook responded {r.status_code}")


HANDLERS = {"recompute_space": handle_recompute, "webhook": handle_webhook}


async def run_one() -> bool:
    """Claim and run a single due job. Returns False when the queue is empty."""
    async with sessionmaker()() as db:
        job = (await db.execute(
            select(Job).where(Job.done_at.is_(None), Job.run_at <= now())
            .order_by(Job.run_at).limit(1).with_for_update(skip_locked=True)
        )).scalar_one_or_none()
        if job is None:
            return False
        job.attempts += 1
        try:
            async with db.begin_nested():
                await HANDLERS[job.kind](db, job.payload)
            job.done_at = now()
            job.last_error = None
        except Exception as e:  # noqa: BLE001 - every failure is retried / recorded
            log.warning("job %s (%s) failed: %s", job.id, job.kind, e)
            job.last_error = str(e)[:2000]
            if job.attempts >= job.max_attempts:
                job.done_at = now()  # dead letter: kept for inspection
            else:
                job.run_at = now() + timedelta(seconds=30 * 2 ** (job.attempts - 1))
        await db.commit()
        return True


async def close_days() -> None:
    """Mark absences once shift windows close; also refreshes today's partial rows."""
    async with sessionmaker()() as db:
        spaces = (await db.execute(select(Space).where(Space.deleted_at.is_(None)))).scalars().all()
        for s in spaces:
            today = datetime.now(ZoneInfo(s.timezone)).date()
            await recompute_space(db, s, today - timedelta(days=2), today)
            await db.commit()
        await db.execute(delete(AdminSession).where(AdminSession.expires_at < now()))
        await db.execute(delete(Job).where(Job.done_at < now() - timedelta(days=30),
                                           Job.last_error.is_(None)))
        await db.commit()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("worker started")
    last_close = 0.0
    while True:
        try:
            if time.monotonic() - last_close > CLOSE_DAYS_EVERY:
                await close_days()
                last_close = time.monotonic()
            while await run_one():
                pass
        except Exception:
            log.exception("worker loop error")
        await asyncio.sleep(2)
