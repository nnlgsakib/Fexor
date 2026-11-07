"""Local kiosk web server (127.0.0.1 only): kiosk UI, live preview + state over WebSocket."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from .camera import Camera, snapshot
from .store import Store
from .sync import Syncer, change_server, enroll, parse_pairing
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


class CameraIn(BaseModel):
    source: str


def _same_origin(origin: str | None, host: str | None) -> bool:
    # Only the kiosk page itself may drive the kiosk (blocks other sites in the browser).
    return origin is None or origin in (f"http://{host}", f"https://{host}")


def create_app(store: Store, camera: Camera, model_dir: Path | None = None,
               pipeline: Pipeline | None = None, on_quit=None, on_switch=None) -> FastAPI:
    verifier = Verifier(store, pipeline or RealPipeline(model_dir), camera)
    dev = dev_mode()
    syncer = Syncer(store, extra_info=lambda: {"camera_ok": camera.ok})
    pair: dict = {"status": "idle", "message": ""}
    last_failed: dict = {"text": "", "at": 0.0}
    loop_ref: dict = {}

    def unlocked() -> bool:
        """Local settings are open before pairing, or after scanning an admin settings QR."""
        return not store.has("token") or verifier.settings_open()

    def require_unlocked(request: Request) -> None:
        if not _same_origin(request.headers.get("origin"), request.headers.get("host")):
            raise HTTPException(403, "bad origin")
        if not unlocked():
            raise HTTPException(403, "settings are locked; scan a settings QR from the admin panel")

    async def pair_with(text: str) -> None:
        try:
            url, code = parse_pairing(text)
            pair.update(status="connecting", message=f"Connecting to {url}...")
            data = await enroll(store, url, code)
            pair.update(status="done", message=f"Connected to {data['space_name']}")
            syncer.poke()
        except Exception as e:  # noqa: BLE001 - shown to the person setting up
            last_failed.update(text=text, at=time.monotonic())
            msg = str(e) if isinstance(e, ValueError) else f"cannot reach the server ({type(e).__name__})"
            pair.update(status="error", message=msg)

    def on_pairing(text: str) -> None:  # called from the verifier thread
        if pair["status"] == "connecting" or "loop" not in loop_ref:
            return
        if text == last_failed["text"] and time.monotonic() - last_failed["at"] < 10:
            return  # don't hammer the server with the same bad code
        pair["status"] = "connecting"
        asyncio.run_coroutine_threadsafe(pair_with(text), loop_ref["loop"])

    verifier.on_pairing = on_pairing

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop_ref["loop"] = asyncio.get_running_loop()
        camera.start()
        worker = threading.Thread(target=verifier.run, daemon=True, name="verifier")
        worker.start()
        task = asyncio.create_task(syncer.run())
        yield
        task.cancel()
        verifier.stop()
        await asyncio.to_thread(worker.join, 5)  # finish the current frame before the store closes
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
                "server_url": store.get("server_url"),
                "sync": syncer.status, "outbox": store.outbox_size(), "camera_ok": camera.ok,
                "camera": str(camera.source), "dev": dev, "settings_open": unlocked(),
                "can_quit": on_quit is not None, "can_switch": on_switch is not None}

    @app.post("/api/settings/switch")
    async def switch_session(request: Request):
        """Desktop app only: back to the session list (settings must be unlocked)."""
        if on_switch is None:
            raise HTTPException(404, "not running as the desktop app")
        require_unlocked(request)
        on_switch()
        return {"ok": True}

    @app.post("/api/settings/quit")
    async def quit_app(request: Request):
        """Desktop app only: close the kiosk window (settings must be unlocked)."""
        if on_quit is None:
            raise HTTPException(404, "not running as the desktop app")
        require_unlocked(request)
        on_quit()
        return {"ok": True}

    @app.get("/api/cameras/{index}/snapshot")
    async def camera_snapshot(index: int, request: Request):
        require_unlocked(request)
        if not 0 <= index <= 9:
            raise HTTPException(404, "no such camera")
        data = None
        if camera.source == index:  # already open: reuse the live frame
            # seq > 0: wait for its first picture; right after start-up there may be none yet
            _, frame = await asyncio.to_thread(camera.latest, 0, 5.0)
            if frame is not None:
                ok, jpg = cv2.imencode(".jpg", cv2.resize(frame, (240, 180)))
                data = jpg.tobytes() if ok else None
        else:
            data = await asyncio.to_thread(snapshot, index)
        if not data:
            raise HTTPException(404, "no picture from this camera")
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.post("/api/camera")
    async def choose_camera(body: CameraIn, request: Request):
        require_unlocked(request)
        src = body.source.strip()
        if not src.isdigit():
            if not src.startswith(("http://", "https://", "rtsp://")):
                raise HTTPException(400, "enter a camera number or an address like "
                                         "http://192.168.1.5:4747/video")
            if not await asyncio.to_thread(snapshot, src):
                raise HTTPException(400, "no picture from that address. Check the phone app is "
                                         "open and on the same Wi-Fi.")
        store.set("camera", src)
        camera.set_source(src)
        return {"ok": True}

    @app.post("/api/settings/server")
    async def set_server(body: CameraIn, request: Request):
        """New address for the same server (body.source = the URL)."""
        require_unlocked(request)
        if not store.has("token"):
            raise HTTPException(409, "pair the kiosk first")
        try:
            url = await change_server(store, body.source)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        syncer.poke()
        return {"server_url": url}

    @app.post("/api/settings/repair")
    async def repair(request: Request):
        """Forget the server so the kiosk can be paired again (e.g. to another space)."""
        require_unlocked(request)
        try:
            await syncer.once()  # upload what we can first
        except Exception:  # noqa: BLE001, S110 - offline: remaining check-ins are dropped below
            pass
        lost = store.outbox_size()
        store.clear_outbox()
        store.reset()
        verifier.settings_until = 0.0
        pair.update(status="idle", message="")
        return {"ok": True, "dropped": lost}

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
            raise HTTPException(409, "already set up; open settings to pair it again")
        try:
            data = await enroll(store, body.server_url, body.code)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:  # noqa: BLE001 - network problems, shown to the person setting up
            raise HTTPException(400, f"cannot reach the server ({type(e).__name__})") from e
        pair.update(status="done", message=f"Connected to {data['space_name']}")
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
                if msg.get("cmd") in ("start", "cancel", "review", "dismiss", "close_settings"):
                    verifier.command(msg["cmd"])
                    if msg["cmd"] == "review":
                        syncer.poke()

        rtask = asyncio.create_task(reader())
        last_view, seq = None, -1
        try:
            while not rtask.done():
                view = {**verifier.view, "online": syncer.status["online"],
                        "outbox": store.outbox_size(), "camera_ok": camera.ok,
                        "space": store.get("space_name"), "pair": dict(pair),
                        "server": store.get("server_url"), "device": store.get("device_name"),
                        "camera": str(camera.source)}
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
