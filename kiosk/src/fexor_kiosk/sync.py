"""Background sync with the central server: heartbeat + clock offset, push outbox, pull roster."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import platform
import time
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from urllib.parse import urlparse

import httpx

from .store import Store

log = logging.getLogger("fexor.sync")
try:
    VERSION = version("fexor-kiosk")  # shown on the admin's Kiosks page, to spot old kiosks
except PackageNotFoundError:
    VERSION = "unknown"


def _verify() -> str | bool:
    # Custom CA bundle for on-prem servers with a private certificate.
    return os.environ.get("FEXOR_KIOSK_CA") or True


PAIR_PREFIX = "FXP1:"


def http_allowed(url: str) -> bool:
    """Plain http only for servers on the local network (office mode) or when forced."""
    if os.environ.get("FEXOR_KIOSK_ALLOW_HTTP"):
        return True
    host = (urlparse(url).hostname or "").lower()
    if host == "localhost" or "." not in host or host.endswith((".local", ".lan", ".home.arpa")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def normalize_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = ("http://" if http_allowed("http://" + url) else "https://") + url
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("enter the server address, e.g. https://attendance.example.com")
    return url


def parse_pairing(text: str) -> tuple[str, str]:
    """Pairing QR from the admin panel: FXP1:{"u": server url, "c": setup code}."""
    if not text.startswith(PAIR_PREFIX) or len(text) > 400:
        raise ValueError("not a pairing code")
    try:
        data = json.loads(text[len(PAIR_PREFIX):])
        return normalize_url(str(data["u"])), str(data["c"])
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError("this pairing QR is damaged; show a new one") from e


async def enroll(store: Store, server_url: str, code: str) -> dict[str, Any]:
    server_url = normalize_url(server_url)
    if server_url.startswith("http://") and not http_allowed(server_url):
        raise ValueError("this server is on the internet but not using https://, which is not "
                         "safe. Ask your admin to turn on HTTPS.")
    try:
        async with httpx.AsyncClient(base_url=server_url, timeout=20, verify=_verify()) as c:
            r = await c.post("/api/device/enroll", json={"code": code, "info": device_info(store)})
    except httpx.HTTPError as e:
        raise ValueError(_unreachable(server_url, e)) from e
    if not r.headers.get("content-type", "").startswith("application/json"):
        raise ValueError(f"{server_url} answered, but it is not a Fexor server (or a sign-in page "
                         "of the network is in the way). Check the address.")
    if r.status_code == 401:
        raise ValueError("This pairing code is wrong or has expired. Show a new one from the admin "
                         "panel (Kiosks, Set up again).")
    if r.status_code == 429:
        raise ValueError("Too many tries. Wait five minutes, then try again.")
    if r.status_code != 200:
        raise ValueError(f"Setup failed: {r.json().get('detail', r.status_code)}")
    data = r.json()
    store.reset()
    store.set("server_url", server_url)
    store.set("token", data["token"], sealed=True)
    store.set("device_id", data["device_id"])
    store.set("device_name", data["device_name"])
    store.set("space_name", data["space_name"])
    return data


async def change_server(store: Store, server_url: str) -> str:
    """Point a paired kiosk at a new address of the SAME server (e.g. its IP changed).

    Saved only if that address accepts this kiosk's token, so a typo or a different server
    can never take over the kiosk.
    """
    url = normalize_url(server_url)
    if url.startswith("http://") and not http_allowed(url):
        raise ValueError("this address is on the internet but not using https://, which is not safe")
    token = store.get("token", sealed=True)
    try:
        async with httpx.AsyncClient(base_url=url, timeout=10, verify=_verify()) as c:
            r = await c.post("/api/device/heartbeat", json={"info": device_info(store)},
                             headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        raise ValueError(_unreachable(url, e)) from e
    if r.status_code == 401:
        raise ValueError("that server does not know this kiosk. To use a different server, "
                         "disconnect the kiosk and pair it again.")
    if r.status_code != 200:
        raise ValueError(f"{url} answered with an error ({r.status_code}); is it the Fexor server?")
    store.set("server_url", url)
    return url


def _unreachable(url: str, e: Exception) -> str:
    if isinstance(e, httpx.TimeoutException):
        why = "it did not answer in time"
    elif "certificate" in str(e).lower() or "ssl" in type(e).__name__.lower():
        why = "its security certificate is not trusted"
    else:
        why = "nothing answered at that address"
    return (f"Can't reach {url}: {why}. Check the address, and that this computer is on the "
            "same network as the server.")


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
        # The kiosk clock is trusted once a heartbeat measured it against the server's, since
        # this start (a Raspberry Pi has no clock battery: after a power cut its time is wrong).
        self.clock_verified = False
        self._wake = asyncio.Event()

    def poke(self) -> None:
        """Sync soon (e.g. after an event was recorded)."""
        self._wake.set()

    async def sync_now(self) -> float:
        """One sync with status bookkeeping. Returns how long to wait before the next one."""
        try:
            await self.once()
            self.status = {"online": True, "last_ok": time.time(), "error": None}
            return self.interval
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            self.status = {**self.status, "online": False,
                           "error": "revoked" if code == 401 else f"http {code}"}
            return 300.0 if code == 401 else 60.0
        except Exception as e:  # noqa: BLE001 - offline is normal, keep retrying
            self.status = {**self.status, "online": False, "error": type(e).__name__}
            return 30.0

    async def run(self) -> None:
        backoff = self.interval
        while True:
            if self.store.has("token"):
                wait = await self.sync_now()
                backoff = self.interval if wait == self.interval else min(max(backoff * 2, wait), 300)
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
            self.clock_verified = True

            # Roster first: revoked cards and new people must arrive even if uploads have trouble.
            etag = self.store.get("etag") if self.store.roster else None
            r = await c.get("/api/device/sync", headers={"If-None-Match": etag} if etag else {})
            if r.status_code != 304:
                r.raise_for_status()
                # Decrypting and parsing a big roster takes seconds on a Pi: not on the event loop.
                await asyncio.to_thread(self.store.save_roster, r.json(), r.headers.get("etag"))
                log.info("roster updated: %d members", len(self.store.roster.by_person))

            await self._upload(c)
            await asyncio.to_thread(self.store.drop_old_snapshots)

    async def _upload(self, c: httpx.AsyncClient) -> None:
        while batch := self.store.pending(100):
            r = await c.post("/api/device/events", json={"events": batch})
            if 400 <= r.status_code < 500 and r.status_code not in (401, 429):
                # Something in this batch is refused (e.g. after a version change). Send one by
                # one so only the bad event is held back, never the whole queue.
                for e in batch:
                    one = await c.post("/api/device/events", json={"events": [e]})
                    if one.status_code < 400:
                        self.store.ack(one.json()["stored"])
                    elif 400 <= one.status_code < 500 and one.status_code not in (401, 429):
                        log.warning("server refused event %s: %s", e["id"], one.text[:200])
                        self.store.refused([e["id"]])
                    else:
                        one.raise_for_status()
                continue
            r.raise_for_status()
            stored = r.json().get("stored") or []
            self.store.ack(stored)
            if not stored:
                return  # nothing taken: stop instead of resending the same batch forever
