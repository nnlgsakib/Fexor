"""Saved kiosk sessions for the desktop app.

Each session is one pairing with one server/space, kept in its own folder (sessions/<id>/) with
its own encrypted store, camera choice and offline queue. The app starts on a list of them.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).parent / "static"
# After a reboot or power cut nobody is there to tap: the list opens the last session itself.
AUTO_OPEN_SECONDS = 15
_LEGACY_FILES = ("kiosk.db", "kiosk.db-wal", "kiosk.db-shm", "device.key")


def sessions_dir(root: Path) -> Path:
    d = root / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def migrate_legacy(root: Path) -> None:
    """Older versions kept one connection directly in the app folder: make it a session."""
    if not (root / "kiosk.db").exists():
        return
    target = sessions_dir(root) / uuid.uuid4().hex[:12]
    target.mkdir()
    for name in _LEGACY_FILES:
        if (root / name).exists():
            (root / name).replace(target / name)


def _read(folder: Path) -> dict:
    keys = ("space_name", "device_name", "server_url", "token")
    info = {k: None for k in keys}
    db = folder / "kiosk.db"
    if db.exists():
        try:
            with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as c:
                for k, v in c.execute("SELECT k, v FROM kv WHERE k IN (?,?,?,?)", keys):
                    info[k] = v
        except sqlite3.Error:
            pass
    last = max((p.stat().st_mtime for p in folder.iterdir()), default=folder.stat().st_mtime)
    return {
        "id": folder.name,
        "space": info["space_name"],
        "kiosk": info["device_name"],
        "server": info["server_url"],
        "paired": info["token"] is not None,
        "last_used": last,
    }


def list_sessions(root: Path) -> list[dict]:
    items = [_read(p) for p in sessions_dir(root).iterdir() if p.is_dir()]
    return sorted(items, key=lambda s: s["last_used"], reverse=True)


def session_path(root: Path, session_id: str) -> Path:
    path = sessions_dir(root) / session_id
    if not session_id.isalnum() or not path.is_dir():
        raise HTTPException(404, "session not found")
    return path


def new_session(root: Path) -> Path:
    path = sessions_dir(root) / uuid.uuid4().hex[:12]
    path.mkdir()
    return path


def create_sessions_app(root: Path, on_open, on_quit) -> FastAPI:
    """Start screen: list saved sessions, open one, start a new one, delete, exit."""
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    from .app import allowed_hosts

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def check_origin(request: Request) -> None:
        origin, host = request.headers.get("origin"), request.headers.get("host")
        if origin is not None and origin not in (f"http://{host}", f"https://{host}"):
            raise HTTPException(403, "bad origin")

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "sessions.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/status")
    async def status():
        return {"mode": "sessions"}

    @app.get("/api/sessions")
    async def sessions():
        items = list_sessions(root)
        last = next((s["id"] for s in items if s["paired"]), None)  # newest paired session
        return {"sessions": items, "now": time.time(), "auto_open": last,
                "auto_open_seconds": AUTO_OPEN_SECONDS}

    @app.post("/api/sessions/{session_id}/open")
    async def open_session(session_id: str, request: Request):
        check_origin(request)
        on_open(session_path(root, session_id))
        return {"ok": True}

    @app.post("/api/sessions")
    async def create(request: Request):
        check_origin(request)
        on_open(new_session(root))
        return {"ok": True}

    @app.delete("/api/sessions/{session_id}")
    async def delete(session_id: str, request: Request):
        check_origin(request)
        shutil.rmtree(session_path(root, session_id), ignore_errors=True)
        return {"ok": True}

    @app.post("/api/quit")
    async def quit_app(request: Request):
        check_origin(request)
        on_quit()
        return {"ok": True}

    return app
