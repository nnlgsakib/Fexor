"""Background worker: job queue (Postgres SKIP LOCKED) + periodic day closing.

Run one or more: `fexor-server worker`. Jobs survive restarts; failures retry with backoff.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
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
# Touched after every loop; the container health check reads its age.
HEARTBEAT = Path(os.environ.get("FEXOR_WORKER_HEARTBEAT",
                                Path(tempfile.gettempdir(), "fexor-worker-heartbeat")))
# After a restart, fill in absences for days the worker may have missed while it was down.
CATCH_UP_DAYS = 31


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
    # The id is fixed when the job is queued, so a receiver can drop retried deliveries.
    body = json.dumps({"id": payload.get("event_id") or str(uuid.uuid4()), "type": payload["type"],
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


async def close_days(lookback_days: int = 2) -> None:
    """Mark absences once shift windows close; also refreshes today's partial rows.
    One space failing (bad data) must not stop the others."""
    async with sessionmaker()() as db:
        spaces = (await db.execute(select(Space).where(Space.deleted_at.is_(None)))).scalars().all()
        for s in spaces:
            try:
                today = datetime.now(ZoneInfo(s.timezone)).date()
                await recompute_space(db, s, today - timedelta(days=lookback_days), today)
                await db.commit()
            except Exception:
                log.exception("closing days failed for space %s", s.id)
                await db.rollback()
        await db.execute(delete(AdminSession).where(AdminSession.expires_at < now()))
        await db.execute(delete(Job).where(Job.done_at < now() - timedelta(days=30),
                                           Job.last_error.is_(None)))
        await db.commit()


@dataclass
class WorkerState:
    last_close: float = 0.0
    caught_up: bool = False


async def tick(state: WorkerState) -> None:
    """One loop: close days when due, then drain the job queue. Each part fails alone."""
    if not state.last_close or time.monotonic() - state.last_close > CLOSE_DAYS_EVERY:
        # Counted before running: a failing close waits for the next round, not 2 seconds.
        state.last_close = time.monotonic()
        try:
            await close_days(2 if state.caught_up else CATCH_UP_DAYS)
            state.caught_up = True
        except Exception:
            log.exception("closing days failed")
    try:
        while await run_one():
            pass
    except Exception:
        log.exception("job queue error")
    try:
        HEARTBEAT.touch()  # noqa: ASYNC240 - one tiny local write per loop
    except OSError:
        log.warning("cannot write heartbeat file %s", HEARTBEAT)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log.info("worker started")
    state = WorkerState()
    while True:
        await tick(state)
        await asyncio.sleep(2)
