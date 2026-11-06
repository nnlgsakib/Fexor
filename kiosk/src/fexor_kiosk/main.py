"""fexor-kiosk CLI: run, enroll, reset, bench."""

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path


def data_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("FEXOR_KIOSK_DATA") or Path.home() / ".fexor-kiosk")


def bench(camera: str, seconds: float) -> None:
    """Per-stage latency on this machine, using live camera frames."""
    import statistics

    from .camera import Camera
    from .verifier import RealPipeline

    pipe = RealPipeline()
    cam = Camera(camera).start()
    stages: dict[str, list[float]] = {k: [] for k in ("detect", "spoof", "mesh", "embed", "qr")}
    end, seq = time.time() + seconds, -1
    while time.time() < end:
        seq, frame = cam.latest(seq, 2.0)
        if frame is None:
            print("no camera frame")
            continue
        t = time.perf_counter()
        pipe.decode_qr(frame)
        stages["qr"].append(time.perf_counter() - t)
        t = time.perf_counter()
        faces = pipe.detect(frame)
        stages["detect"].append(time.perf_counter() - t)
        if not faces:
            continue
        for name, fn in (("spoof", pipe.spoof), ("mesh", pipe.mesh), ("embed", pipe.embed)):
            t = time.perf_counter()
            fn(frame, faces[0])
            stages[name].append(time.perf_counter() - t)
    cam.stop()
    for k, v in stages.items():
        if v:
            print(f"{k:7s} n={len(v):4d}  median {statistics.median(v) * 1000:6.1f} ms  "
                  f"p95 {sorted(v)[int(len(v) * 0.95) - 1] * 1000:6.1f} ms")
    if stages["mesh"]:
        per_frame = sum(statistics.median(stages[k]) for k in ("detect", "spoof", "mesh"))
        per_frame += statistics.median(stages["embed"]) / 3
        print(f"~{1 / per_frame:.1f} verification frames/s")


def main() -> None:
    p = argparse.ArgumentParser(prog="fexor-kiosk")
    p.add_argument("--data-dir")
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="start the kiosk")
    run.add_argument("--camera", default=os.environ.get("FEXOR_KIOSK_CAMERA"),
                     help="camera index or stream URL (default: the one chosen on the kiosk screen)")
    run.add_argument("--port", type=int, default=8300)
    run.add_argument("--width", type=int, default=640)
    run.add_argument("--height", type=int, default=480)
    en = sub.add_parser("enroll", help="enroll this device (headless)")
    en.add_argument("--server", required=True)
    en.add_argument("--code", required=True)
    en.add_argument("--allow-http", action="store_true",
                    help="allow a plain http:// server (local testing only)")
    sub.add_parser("reset", help="forget server enrollment and local roster")
    b = sub.add_parser("bench", help="measure model speed on this machine")
    b.add_argument("--camera", default="0")
    b.add_argument("--seconds", type=float, default=15)
    sub.add_parser("models", help="download and verify models")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    if a.cmd == "models":
        from fexor_core.models import ensure_all

        for n, path in ensure_all().items():
            print(f"{n}: {path}")
        return
    if a.cmd == "bench":
        bench(a.camera, a.seconds)
        return

    from .store import Store

    store = Store(data_dir(a.data_dir))
    if a.cmd == "reset":
        store.reset()
        print(f"reset done; {store.outbox_size()} unsent events kept in the outbox")
    elif a.cmd == "enroll":
        import httpx

        from .sync import enroll

        if a.allow_http:
            os.environ["FEXOR_KIOSK_ALLOW_HTTP"] = "1"
        try:
            data = asyncio.run(enroll(store, a.server, a.code))
        except ValueError as e:
            sys.exit(f"error: {e}")
        except httpx.HTTPError as e:
            sys.exit(f"error: cannot reach {a.server} ({type(e).__name__})")
        print(f"enrolled as '{data['device_name']}' in space '{data['space_name']}'")
    elif a.cmd == "run":
        import uvicorn
        from fexor_core.models import ensure_all

        from .app import create_app
        from .camera import Camera

        ensure_all()
        source = a.camera or store.get("camera") or "0"
        if a.camera:
            store.set("camera", a.camera)
        app = create_app(store, Camera(source, a.width, a.height))
        print(f"Kiosk UI: http://127.0.0.1:{a.port}/")
        uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
