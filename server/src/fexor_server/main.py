from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import admin, attendance, auth, devices, faces, schedule, setup, spaces
from .config import get_settings

CSP = (
    "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
    "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
    "form-action 'self'"
)


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Fexor", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json",
                  redoc_url=None)
    for r in (setup, auth, admin, spaces, faces, schedule, devices, attendance):
        app.include_router(r.router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not request.url.path.startswith("/api/docs"):
            resp.headers.setdefault("Content-Security-Policy", CSP)
        if s.secure_cookies:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if request.url.path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    @app.get("/api/health")
    async def health():
        """Also checks the database, so the container shows unhealthy when Postgres is down."""
        from sqlalchemy import text

        from .db import sessionmaker

        try:
            async with sessionmaker()() as db:
                await db.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001 - any failure means "not healthy"
            return JSONResponse({"ok": False, "db": False}, status_code=503)
        return {"ok": True, "db": True}

    web = s.web_dir
    if web and Path(web, "index.html").exists():
        app.mount("/assets", StaticFiles(directory=Path(web, "assets")), name="assets")
        index = Path(web, "index.html")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):  # sync: runs in a thread, file checks may block
            if path.startswith("api/"):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            f = Path(web, path)
            if path and f.is_file() and f.resolve().is_relative_to(Path(web).resolve()):
                return FileResponse(f)
            return FileResponse(index)

    return app


app = create_app()
