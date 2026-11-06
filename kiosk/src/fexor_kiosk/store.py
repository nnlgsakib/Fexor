"""Local kiosk storage (SQLite, WAL). Secrets and biometrics are sealed with a device key.

ponytail: the device key sits next to the database (0600). Use full-disk encryption
(LUKS / BitLocker) on kiosks in untrusted places; a TPM-backed key is the upgrade path.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from fexor_core import crypto


@dataclass
class Member:
    id: str
    person_id: str
    name: str
    external_id: str
    title: str
    active: bool
    credential_id: str | None
    templates: list[np.ndarray] = field(default_factory=list)


@dataclass
class Roster:
    space: dict[str, Any]
    keys: dict[str, str]
    model_id: str
    by_person: dict[str, Member]


def _load_key(path: Path) -> bytes:
    if path.exists():
        return path.read_bytes()
    key = secrets.token_bytes(32)
    path.write_bytes(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows: rely on the user profile ACLs
    return key


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.key = _load_key(data_dir / "device.key")
        self._lock = threading.Lock()
        self._db = sqlite3.connect(data_dir / "kiosk.db", check_same_thread=False,
                                   isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v BLOB);
            CREATE TABLE IF NOT EXISTS outbox (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS recent (member_id TEXT, ts REAL);
            CREATE INDEX IF NOT EXISTS recent_m ON recent(member_id, ts);
        """)
        self.roster: Roster | None = self._load_roster()

    # ---------- kv ----------
    def has(self, k: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM kv WHERE k=?", (k,)).fetchone() is not None

    def get(self, k: str, sealed: bool = False) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        if row is None:
            return None
        v = row[0]
        if sealed:
            return crypto.unseal(self.key, v, k.encode()).decode()
        return v if isinstance(v, str) else v.decode()

    def set(self, k: str, v: str | None, sealed: bool = False) -> None:
        with self._lock:
            if v is None:
                self._db.execute("DELETE FROM kv WHERE k=?", (k,))
                return
            blob: Any = crypto.seal(self.key, v.encode(), k.encode()) if sealed else v
            self._db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (k, blob))

    # ---------- roster ----------
    def save_roster(self, data: dict[str, Any], etag: str | None) -> None:
        raw = json.dumps(data, separators=(",", ":"))
        self.set("roster", base64.b64encode(
            crypto.seal(self.key, raw.encode(), b"roster")).decode())
        self.set("etag", etag)
        self.roster = parse_roster(data)

    def _load_roster(self) -> Roster | None:
        b = self.get("roster")
        if not b:
            return None
        try:
            raw = crypto.unseal(self.key, base64.b64decode(b), b"roster")
        except Exception:  # noqa: BLE001 - key changed / corrupt: force a full re-sync
            self.set("etag", None)
            return None
        return parse_roster(json.loads(raw))

    # ---------- outbox ----------
    def enqueue(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO outbox (id, payload, created) VALUES (?,?,?)",
                             (event["id"], json.dumps(event), time.time()))

    def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT payload FROM outbox ORDER BY created LIMIT ?", (limit,)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def ack(self, ids: list[str]) -> None:
        with self._lock:
            self._db.executemany("DELETE FROM outbox WHERE id=?", [(i,) for i in ids])

    def clear_outbox(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM outbox")

    def outbox_size(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    # ---------- recent accepted scans (for in/out labels) ----------
    def add_recent(self, member_id: str, ts: float) -> None:
        with self._lock:
            self._db.execute("INSERT INTO recent VALUES (?, ?)", (member_id, ts))
            self._db.execute("DELETE FROM recent WHERE ts < ?", (ts - 3 * 86400,))

    def recent_since(self, member_id: str, since: float) -> list[float]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts FROM recent WHERE member_id=? AND ts>=? ORDER BY ts",
                (member_id, since)).fetchall()
        return [r[0] for r in rows]

    def reset(self) -> None:
        with self._lock:
            # Keep local device settings (camera choice) across re-pairing.
            self._db.executescript("DELETE FROM kv WHERE k NOT IN ('camera'); DELETE FROM recent;")
        self.roster = None


def parse_roster(data: dict[str, Any]) -> Roster:
    members = {}
    for m in data.get("members", []):
        members[m["person_id"]] = Member(
            id=m["id"], person_id=m["person_id"], name=m["name"], external_id=m["external_id"],
            title=m.get("title", ""), active=bool(m.get("active")),
            credential_id=m.get("credential_id"),
            templates=[np.frombuffer(base64.b64decode(t), "<f4").copy()
                       for t in m.get("templates", [])],
        )
    return Roster(space=data["space"], keys=data.get("keys", {}),
                  model_id=data.get("model_id", ""), by_person=members)
