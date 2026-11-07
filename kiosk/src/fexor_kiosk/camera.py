"""Background camera reader: always holds the newest frame, reconnects on failure."""

from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np

log = logging.getLogger("fexor.camera")


def parse_source(source: str | int) -> str | int:
    """Camera index ("0") or stream URL (e.g. DroidCam: http://192.168.1.5:4747/video)."""
    s = str(source).strip()
    return int(s) if s.isdigit() else s


def open_capture(source: str | int) -> cv2.VideoCapture:
    if isinstance(source, str):  # network stream: fail in seconds, not half a minute
        return cv2.VideoCapture(source, cv2.CAP_FFMPEG, [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
    return cv2.VideoCapture(source)


def snapshot(source: str | int, width: int = 240) -> bytes | None:
    """Open a camera briefly and return a small JPEG, or None if it gives no picture."""
    cap = open_capture(parse_source(source))
    try:
        if not cap.isOpened():
            return None
        frame = None
        for _ in range(8):  # many cameras return dark frames while warming up
            ok, f = cap.read()
            if ok:
                frame = f
        if frame is None:
            return None
        frame = cv2.resize(frame, (width, int(width * frame.shape[0] / frame.shape[1])))
        ok, jpg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        return jpg.tobytes() if ok else None
    finally:
        cap.release()


class Camera:
    def __init__(self, source: str | int, width: int = 640, height: int = 480):
        self.source = parse_source(source)
        self._reopen = threading.Event()
        self.width, self.height = width, height
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self.ok = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="camera")

    def start(self) -> Camera:
        self._thread.start()
        return self

    def stop(self) -> None:
        # Wait for the reader to release the device: exiting while it is inside a read can hang
        # some Windows camera drivers.
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def set_source(self, source: str | int) -> None:
        """Switch camera without restarting the kiosk."""
        self.source = parse_source(source)
        self.ok = False
        self._reopen.set()

    def _open(self) -> cv2.VideoCapture:
        cap = open_capture(self.source)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self) -> None:
        cap = None
        while not self._stop.is_set():
            if self._reopen.is_set() and cap is not None:
                cap.release()
                cap = None
            self._reopen.clear()
            if cap is None or not cap.isOpened():
                cap = self._open()
                if not cap.isOpened():
                    self.ok = False
                    log.warning("camera %s not available, retrying", self.source)
                    time.sleep(2)
                    continue
            ok, frame = cap.read()
            if not ok:
                self.ok = False
                cap.release()
                cap = None
                time.sleep(0.5)
                continue
            if frame.shape[1] > self.width:  # some cameras ignore the size hint
                scale = self.width / frame.shape[1]
                frame = cv2.resize(frame, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            self.ok = True
            with self._cond:
                self._frame, self._seq = frame, self._seq + 1
                self._cond.notify_all()
        if cap is not None:
            cap.release()

    def latest(self, after_seq: int = -1, timeout: float = 1.0) -> tuple[int, np.ndarray | None]:
        """Newest frame with seq > after_seq (waits up to timeout)."""
        with self._cond:
            self._cond.wait_for(lambda: self._seq > after_seq, timeout=timeout)
            return self._seq, self._frame
