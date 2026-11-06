"""Local kiosk web server (127.0.0.1 only): kiosk UI, live preview + state over WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .camera import Camera
from .store import Store
from .sync import Syncer, enroll
from .verifier import Pipeline, RealPipeline, Verifier

STATIC = Path(__file__).parent / "static"
log = logging.getLogger("fexor.kiosk")


def dev_mode() -> bool:
    return os.environ.get("FEXOR_ENV", "").strip().lower() == "dev"


def decode_qr_image(data: bytes) -> str | None:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("not an image; use PNG or JPG")
    scale = 1280 / max(img.shape[:2])
    if scale < 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    text, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
    return text or None


class SetupIn(BaseModel):
    server_url: str
    code: str


def _same_origin(origin: str | None, host: str | None) -> bool:
    # Only the kiosk page itself may drive the kiosk (blocks other sites in the browser).
    return origin is None or origin in (f"http://{host}", f"https://{host}")


def create_app(store: Store, camera: Camera, model_dir: Path | None = None,
               pipeline: Pipeline | None = None) -> FastAPI:
    verifier = Verifier(store, pipeline or RealPipeline(model_dir), camera)
    dev = dev_mode()
    syncer = Syncer(store, extra_info=lambda: {"camera_ok": camera.ok})

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        camera.start()
        threading.Thread(target=verifier.run, daemon=True, name="verifier").start()
        task = asyncio.create_task(syncer.run())
        yield
        task.cancel()
        verifier.stop()
        camera.stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.verifier, app.state.syncer = verifier, syncer

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/status")
    async def status():
        return {"enrolled": store.has("token"),
                "space_name": store.get("space_name"), "device_name": store.get("device_name"),
                "sync": syncer.status, "outbox": store.outbox_size(), "camera_ok": camera.ok,
                "dev": dev}

    if dev:
        log.warning("FEXOR_ENV=dev: QR image upload is enabled. Never use this on a real kiosk.")

        @app.post("/api/dev/qr")
        async def dev_qr(request: Request):
            """Test helper: decode a QR from an uploaded image instead of the camera.
            The token goes through exactly the same signature/revocation checks."""
            if not _same_origin(request.headers.get("origin"), request.headers.get("host")):
                raise HTTPException(403, "bad origin")
            data = await request.body()
            if not data or len(data) > 10 * 1024 * 1024:
                raise HTTPException(413, "send an image up to 10 MB")
            try:
                text = await asyncio.to_thread(decode_qr_image, data)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
            if not text:
                raise HTTPException(400, "no QR code found in this image")
            verifier.submit_qr(text)
            return {"ok": True}

    @app.post("/api/setup")
    async def setup(body: SetupIn, request: Request):
        if not _same_origin(request.headers.get("origin"), request.headers.get("host")):
            raise HTTPException(403, "bad origin")
        if store.has("token"):
            raise HTTPException(409, "already enrolled; run `fexor-kiosk reset` to re-enroll")
        try:
            data = await enroll(store, body.server_url, body.code)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        syncer.poke()
        return {"space_name": data["space_name"]}

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        if not _same_origin(sock.headers.get("origin"), sock.headers.get("host")):
            await sock.close(code=1008)
            return
        await sock.accept()

        async def reader():
            while True:
                msg = json.loads(await sock.receive_text())
                if msg.get("cmd") in ("start", "cancel", "review"):
                    verifier.command(msg["cmd"])
                    if msg["cmd"] == "review":
                        syncer.poke()

        rtask = asyncio.create_task(reader())
        last_view, seq = None, -1
        try:
            while not rtask.done():
                view = {**verifier.view, "online": syncer.status["online"],
                        "outbox": store.outbox_size(), "camera_ok": camera.ok,
                        "space": store.get("space_name")}
                if view != last_view:
                    await sock.send_text(json.dumps(view))
                    if view.get("state") == "result" and (last_view or {}).get("state") != "result":
                        syncer.poke()  # upload the new event promptly
                    last_view = view
                seq, frame = await asyncio.to_thread(camera.latest, seq, 0.5)
                if frame is not None:
                    small = cv2.resize(frame, (480, int(480 * frame.shape[0] / frame.shape[1])))
                    ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    if ok:
                        await sock.send_bytes(jpg.tobytes())
                await asyncio.sleep(0.08)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            rtask.cancel()

    return app
