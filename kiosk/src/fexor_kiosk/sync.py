"""Background sync with the central server: heartbeat + clock offset, push outbox, pull roster."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import time
from datetime import datetime
from typing import Any

import httpx

from .store import Store

log = logging.getLogger("fexor.sync")
VERSION = "0.1.0"


def _verify() -> str | bool:
    # Custom CA bundle for on-prem servers with a private certificate.
    return os.environ.get("FEXOR_KIOSK_CA") or True


async def enroll(store: Store, server_url: str, code: str) -> dict[str, Any]:
    server_url = server_url.rstrip("/")
    if not server_url.startswith("https://") and not os.environ.get("FEXOR_KIOSK_ALLOW_HTTP"):
        raise ValueError("server URL must start with https:// (set FEXOR_KIOSK_ALLOW_HTTP=1 "
                         "for local testing only)")
    async with httpx.AsyncClient(base_url=server_url, timeout=20, verify=_verify()) as c:
        r = await c.post("/api/device/enroll", json={"code": code, "info": device_info(store)})
    if r.status_code != 200:
        detail = r.json().get("detail") if r.headers.get("content-type", "").startswith(
            "application/json") else r.text
        raise ValueError(f"enrollment failed: {detail}")
    data = r.json()
    store.reset()
    store.set("server_url", server_url)
    store.set("token", data["token"], sealed=True)
    store.set("device_id", data["device_id"])
    store.set("device_name", data["device_name"])
    store.set("space_name", data["space_name"])
    return data


def device_info(store: Store, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    r = store.roster
    return {
        "version": VERSION, "platform": platform.platform(terse=True),
        "machine": platform.machine(), "python": platform.python_version(),
        "outbox": store.outbox_size(), "members": len(r.by_person) if r else 0,
        "clock_offset_s": round(float(store.get("clock_offset") or 0), 2),
        **(extra or {}),
    }


class Syncer:
    def __init__(self, store: Store, interval: float = 15.0, extra_info=None):
        self.store = store
        self.interval = interval
        self.extra_info = extra_info or (lambda: {})
        self.status: dict[str, Any] = {"online": False, "last_ok": None, "error": None}
        self._wake = asyncio.Event()

    def poke(self) -> None:
        """Sync soon (e.g. after an event was recorded)."""
        self._wake.set()

    async def run(self) -> None:
        backoff = self.interval
        while True:
            if self.store.has("token"):
                try:
                    await self.once()
                    self.status = {"online": True, "last_ok": time.time(), "error": None}
                    backoff = self.interval
                except httpx.HTTPStatusError as e:
                    code = e.response.status_code
                    self.status = {**self.status, "online": False,
                                   "error": "revoked" if code == 401 else f"http {code}"}
                    backoff = min(backoff * 2, 300)
                except Exception as e:  # noqa: BLE001 - offline is normal, keep retrying
                    self.status = {**self.status, "online": False, "error": type(e).__name__}
                    backoff = min(backoff * 2, 120)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=backoff)
            except TimeoutError:
                pass
            self._wake.clear()

    async def once(self) -> None:
        token = self.store.get("token", sealed=True)
        url = self.store.get("server_url")
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(base_url=url, timeout=20, headers=headers,
                                     verify=_verify()) as c:
            t0 = time.time()
            r = await c.post("/api/device/heartbeat",
                             json={"info": device_info(self.store, self.extra_info())})
            t1 = time.time()
            r.raise_for_status()
            server = datetime.fromisoformat(r.json()["server_time"]).timestamp()
            self.store.set("clock_offset", f"{server - (t0 + t1) / 2:.3f}")

            while batch := self.store.pending(100):
                r = await c.post("/api/device/events", json={"events": batch})
                r.raise_for_status()
                self.store.ack(r.json()["stored"])

            etag = self.store.get("etag") if self.store.roster else None
            r = await c.get("/api/device/sync", headers={"If-None-Match": etag} if etag else {})
            if r.status_code == 304:
                return
            r.raise_for_status()
            self.store.save_roster(r.json(), r.headers.get("etag"))
            log.info("roster updated: %d members", len(self.store.roster.by_person))
