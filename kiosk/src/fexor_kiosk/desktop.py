"""Desktop app: runs the kiosk in the background and shows it full screen in its own window.

It opens on a list of saved sessions (one per pairing with a server and space): open one or
start a new one. "Session list" in the kiosk's settings returns to it.

Windows and macOS use a native window (pywebview). Linux and Raspberry Pi use Chromium in
kiosk mode, which Raspberry Pi OS ships with. Everything else (pairing, camera, settings)
happens on the kiosk screen itself.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

APP_NAME = "Fexor Kiosk"
PORT = int(os.environ.get("FEXOR_KIOSK_PORT", "8300"))  # change only to run a second copy
URL = f"http://127.0.0.1:{PORT}/"
log = logging.getLogger("fexor.desktop")


def app_data_dir() -> Path:
    if override := os.environ.get("FEXOR_KIOSK_DATA"):
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_NAME


def bundled_models() -> Path | None:
    """Models shipped inside the packaged app (PyInstaller puts data files in _MEIPASS)."""
    base = getattr(sys, "_MEIPASS", None)
    path = Path(base) / "models" if base else None
    return path if path and path.is_dir() else None


def _port_open() -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", PORT)) == 0


def _setup_logging(data: Path) -> None:
    import faulthandler

    # A windowed build has no console: give the standard streams somewhere to go.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")  # noqa: SIM115
    # Native crashes (camera drivers, GPU, webview) leave a stack trace here.
    faulthandler.enable(open(data / "crash.log", "a"))  # noqa: SIM115
    handler = logging.handlers.RotatingFileHandler(data / "kiosk.log", maxBytes=2_000_000,
                                                   backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()],
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _serve(app):
    """Run a web app on the kiosk port in a background thread; wait until it answers."""
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning",
                                           log_config=None))
    thread = threading.Thread(target=server.run, daemon=True, name="kiosk-server")
    thread.start()
    for _ in range(300):  # model loading takes a few seconds on small machines
        if _port_open():
            return server, thread
        if not thread.is_alive():
            break
        time.sleep(0.1)
    raise RuntimeError("kiosk server failed to start; see kiosk.log")


def _stop(server, thread) -> None:
    server.should_exit = True
    thread.join(timeout=15)


def _wait(*events: threading.Event) -> None:
    while not any(e.is_set() for e in events):
        time.sleep(0.1)


def _controller(root: Path, quit_event: threading.Event) -> None:
    """Session list -> chosen session's kiosk -> back to the list, until the app is closed."""
    from .app import create_app
    from .camera import Camera
    from .sessions import create_sessions_app, migrate_legacy
    from .store import Store

    migrate_legacy(root)
    models = Path(os.environ["FEXOR_MODEL_DIR"])
    try:
        while not quit_event.is_set():
            chosen: dict = {}
            picked = threading.Event()

            def open_session(path: Path, chosen=chosen, picked=picked) -> None:
                chosen["path"] = path
                picked.set()

            srv = _serve(create_sessions_app(root, on_open=open_session, on_quit=quit_event.set))
            _wait(picked, quit_event)
            _stop(*srv)
            if quit_event.is_set():
                break

            log.info("opening session %s", chosen["path"].name)
            store = Store(chosen["path"])
            camera = Camera(os.environ.get("FEXOR_KIOSK_CAMERA") or store.get("camera") or "0")
            switch = threading.Event()
            srv = _serve(create_app(store, camera, models, on_quit=quit_event.set,
                                    on_switch=switch.set))
            _wait(switch, quit_event)
            _stop(*srv)
            store.close()
    except Exception:
        log.exception("kiosk app failed")
    finally:
        quit_event.set()


def _find_chromium() -> str | None:
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                 "microsoft-edge"):
        if path := shutil.which(name):
            return path
    return None


def _show_window(data: Path, quit_event: threading.Event) -> None:
    windowed = bool(os.environ.get("FEXOR_KIOSK_WINDOWED"))
    try:
        import webview
    except ImportError:
        webview = None

    if webview is not None:
        window = webview.create_window(APP_NAME, URL, fullscreen=not windowed,
                                       width=1280, height=800, background_color="#0d1117")

        def close_on_quit():
            quit_event.wait()
            window.destroy()

        threading.Thread(target=close_on_quit, daemon=True).start()
        webview.start(private_mode=False, storage_path=str(data / "webview"))
        return

    browser = _find_chromium()
    if browser is None:
        import webbrowser

        log.warning("no Chromium found; opening the default browser")
        webbrowser.open(URL)
        quit_event.wait()
        return
    args = [browser, f"--app={URL}", "--noerrdialogs", "--disable-infobars", "--no-first-run",
            "--check-for-update-interval=31536000", f"--user-data-dir={data / 'browser'}"]
    if not windowed:
        args.insert(1, "--kiosk")
    proc = subprocess.Popen(args)  # noqa: S603 - fixed browser path and our own local URL
    threading.Thread(target=lambda: (quit_event.wait(), proc.terminate()), daemon=True).start()
    proc.wait()


def run() -> None:
    data = app_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    _setup_logging(data)
    models = bundled_models() or data / "models"
    os.environ.setdefault("FEXOR_MODEL_DIR", str(models))
    quit_event = threading.Event()

    if _port_open():
        log.info("kiosk already running; opening its window")
    else:
        from fexor_core.models import ensure_all

        ensure_all(Path(os.environ["FEXOR_MODEL_DIR"]))  # no-op when bundled
        threading.Thread(target=_controller, args=(data, quit_event), daemon=True,
                         name="controller").start()
        for _ in range(300):  # wait for the session list before showing the window
            if _port_open() or quit_event.is_set():
                break
            time.sleep(0.1)
    _show_window(data, quit_event)
    quit_event.set()
    time.sleep(1)  # let the current screen's server stop cleanly
    os._exit(0)  # camera drivers can keep threads alive; the window is closed, so leave


if __name__ == "__main__":
    run()
