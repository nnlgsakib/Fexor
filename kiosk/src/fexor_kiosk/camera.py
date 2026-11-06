"""Background camera reader: always holds the newest frame, reconnects on failure."""

from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np

log = logging.getLogger("fexor.camera")


class Camera:
    def __init__(self, source: str | int, width: int = 640, height: int = 480):
        self.source = int(source) if isinstance(source, str) and source.isdigit() else source
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
        self._stop.set()

    def _open(self) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(self.source)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def _run(self) -> None:
        cap = None
        while not self._stop.is_set():
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
