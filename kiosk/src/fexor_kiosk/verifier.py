"""Kiosk verification state machine: QR -> one tracked face -> liveness + challenge + 1:1 match.

All decisions are local. `step()` is pure with respect to time and frames, so the whole
flow is unit-tested with a fake pipeline (see tests/test_verifier.py).
"""

from __future__ import annotations

import base64
import logging
import statistics
import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import cv2
import numpy as np
from fexor_core import qr
from fexor_core.challenge import Action, Challenge, Status, random_actions
from fexor_core.face import Face, Quality
from fexor_core.liveness import MeshMetrics

from .store import Member, Roster, Store

log = logging.getLogger("fexor.verifier")

SESSION_TIMEOUT = 25.0
QR_TIMEOUT = 20.0
RESULT_OK_SECONDS = 3.5
RESULT_FAIL_SECONDS = 8.0
MIN_SPOOF_FRAMES = 8
MIN_MATCH_FRAMES = 2
EMBED_EVERY = 3
LOCKOUT_FAILURES = 3
LOCKOUT_WINDOW = 600.0
LOCKOUT_SECONDS = 300.0
MIN_FACE_PX = 80

PROMPTS = {
    Action.BLINK: "Blink your eyes",
    Action.TURN_LEFT: "Slowly turn your head to your left",
    Action.TURN_RIGHT: "Slowly turn your head to your right",
    Action.SMILE: "Smile",
    Action.LOOK_UP: "Look up",
}

FAIL_MESSAGES = {
    "qr_invalid": "This QR code is not valid.",
    "qr_unknown_person": "This QR code is not registered in this space.",
    "qr_revoked": "This QR code was replaced. Please ask the admin for your new code.",
    "member_inactive": "Your registration is not active. Please contact the admin.",
    "no_face_enrolled": "No face photo is registered for you yet. Please contact the admin.",
    "face_mismatch": "Face did not match this QR code.",
    "face_matches_other": "Face did not match this QR code.",
    "liveness_failed": "Live face check failed. Make sure your face is well lit.",
    "face_changed": "A different face appeared. Please try again alone.",
    "challenge": "Movement check failed. Please follow the instructions.",
    "timeout": "Verification timed out. Please try again.",
}


class Pipeline(Protocol):
    def decode_qr(self, frame: np.ndarray) -> str | None: ...
    def detect(self, frame: np.ndarray) -> list[Face]: ...
    def spoof(self, frame: np.ndarray, face: Face) -> float: ...
    def mesh(self, frame: np.ndarray, face: Face) -> MeshMetrics: ...
    def embed(self, frame: np.ndarray, face: Face) -> tuple[Quality, np.ndarray | None]: ...


class RealPipeline:
    def __init__(self, model_dir=None):
        from fexor_core.face import FaceEngine, assess
        from fexor_core.liveness import AntiSpoof, FaceMesh

        self._engine = FaceEngine(model_dir)
        self._spoof = AntiSpoof(model_dir)
        self._mesh = FaceMesh(model_dir)
        self._assess = assess
        self._qr = cv2.QRCodeDetector()

    def decode_qr(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        text, _, _ = self._qr.detectAndDecode(gray)
        return text or None

    def detect(self, frame):
        return self._engine.detect(frame)

    def spoof(self, frame, face):
        return self._spoof.score(frame, face)

    def mesh(self, frame, face):
        return self._mesh.metrics(frame, face)

    def embed(self, frame, face):
        aligned = self._engine.align(frame, face)
        q = self._assess(aligned, face)
        return q, (self._engine.embed_aligned(aligned) if q.ok else None)


def iou(a: tuple, b: tuple) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Session:
    started: float
    member: Member | None = None
    credential_id: str | None = None
    challenge: Challenge | None = None
    spoof: list[float] = field(default_factory=list)
    matches: list[float] = field(default_factory=list)
    embeddings: list[np.ndarray] = field(default_factory=list)
    last_box: tuple | None = None
    last_frame: np.ndarray | None = None
    last_face: Face | None = None
    frames: int = 0
    scores: dict[str, Any] = field(default_factory=dict)


class Verifier:
    def __init__(self, store: Store, pipeline: Pipeline, camera=None, clock=time.time):
        self.store = store
        self.pipe = pipeline
        self.camera = camera
        self.clock = clock
        self._lock = threading.Lock()
        self._cmd: str | None = None
        self._qr_text: str | None = None
        self.state = "idle"
        self.session: Session | None = None
        self.result: dict[str, Any] | None = None
        self.result_until = 0.0
        self.failures: dict[str, deque[float]] = defaultdict(deque)
        self.locked_until: dict[str, float] = {}
        self.view: dict[str, Any] = {"state": "idle"}
        self._idle_tick = 0
        self._gallery_cache: tuple[int, np.ndarray, list[str]] | None = None
        self._stop = threading.Event()

    # ---------- commands from the UI (thread-safe) ----------
    def command(self, cmd: str) -> None:
        with self._lock:
            self._cmd = cmd

    def _take_cmd(self) -> str | None:
        with self._lock:
            c, self._cmd = self._cmd, None
            return c

    def submit_qr(self, text: str) -> None:
        """QR text from somewhere other than the camera (dev-mode upload). Same checks apply."""
        with self._lock:
            self._qr_text = text

    def _take_qr(self) -> str | None:
        with self._lock:
            t, self._qr_text = self._qr_text, None
            return t

    @property
    def roster(self) -> Roster | None:
        return self.store.roster

    def _space(self, key: str, default: Any) -> Any:
        r = self.roster
        return r.space.get(key, default) if r else default

    # ---------- main loop ----------
    def run(self) -> None:
        seq = -1
        while not self._stop.is_set():
            if self.camera is None:
                time.sleep(0.1)
                continue
            seq, frame = self.camera.latest(seq, timeout=1.0)
            try:
                self.step(frame, self.clock())
            except Exception:
                log.exception("verifier step failed")
                self._finish(False, "error", self.clock())

    def stop(self) -> None:
        self._stop.set()

    def step(self, frame: np.ndarray | None, now: float) -> None:
        cmd = self._take_cmd()
        if cmd == "cancel" and self.state in ("scan_qr", "face"):
            self._to_idle()
        if self.roster is None:
            self.view = {"state": "not_ready", "message": "Waiting for first sync with server"}
            return
        injected = self._take_qr()
        if injected and self.state in ("idle", "scan_qr", "result"):
            self._to_idle()
            self.session = Session(started=now)
            self._on_qr(injected, now)
            return
        if self.state == "result":
            if cmd == "review":
                self._request_review(now)
            if cmd == "start" or now >= self.result_until:
                self._to_idle()
                if cmd != "start":
                    return
            else:
                return
        if self.state == "idle":
            if cmd == "start":
                self.state = "scan_qr"
                self.session = Session(started=now)
            else:
                self._idle_tick += 1
                if frame is not None and self._idle_tick % 3 == 0:
                    text = self.pipe.decode_qr(frame)
                    if text:
                        self.session = Session(started=now)
                        self._on_qr(text, now)
                        return
                self.view = {"state": "idle", "message": "Show your QR code or tap Start"}
                return
        if frame is None:
            return
        if self.state == "scan_qr":
            assert self.session
            if now - self.session.started > QR_TIMEOUT:
                self._to_idle()
                return
            text = self.pipe.decode_qr(frame)
            if text:
                self._on_qr(text, now)
            else:
                self.view = {"state": "scan_qr", "message": "Hold your QR code up to the camera",
                             "remaining": round(QR_TIMEOUT - (now - self.session.started))}
            return
        if self.state == "face":
            self._face_step(frame, now)

    def _to_idle(self) -> None:
        self.state, self.session, self.result = "idle", None, None
        self.view = {"state": "idle", "message": "Show your QR code or tap Start"}

    # ---------- QR ----------
    def _on_qr(self, text: str, now: float) -> None:
        s = self.session
        assert s is not None and self.roster is not None
        if not text.startswith(qr.PREFIX + "."):
            self.state = "scan_qr"
            self.view = {"state": "scan_qr", "message": "That is not an attendance QR code"}
            return
        keys = {k: qr.public_key_from_str(v) for k, v in self.roster.keys.items()}
        try:
            claims = qr.verify(text, keys)
        except qr.InvalidToken as e:
            self._finish(False, f"qr_invalid:{e}", now)
            return
        member = self.roster.by_person.get(claims.person_id)
        s.member, s.credential_id = member, claims.credential_id
        if member is None:
            self._finish(False, "qr_unknown_person", now)
            return
        locked = self.locked_until.get(claims.credential_id, 0)
        if locked > now:
            mins = int((locked - now) // 60) + 1
            self._show_result(False, member, f"Too many failed attempts. Try again in {mins} min.",
                              now, can_review=False)
            return
        if member.credential_id != claims.credential_id:
            self._finish(False, "qr_revoked", now)
            return
        if not member.active:
            self._finish(False, "member_inactive", now)
            return
        if not member.templates:
            self._finish(False, "no_face_enrolled", now)
            return
        n = int(self._space("challenge_actions", 2))
        s.challenge = Challenge(random_actions(n))
        s.started = now  # face step gets its own full time budget, however long the QR took
        self.state = "face"
        self.view = {"state": "face", "name": member.name, "message": "Look straight at the camera"}

    # ---------- face ----------
    def _face_step(self, frame: np.ndarray, now: float) -> None:
        s = self.session
        assert s and s.member and s.challenge
        if now - s.started > SESSION_TIMEOUT:
            stage = s.challenge.current or "match"
            self._finish(False, f"timeout:{stage}", now)
            return
        faces = self.pipe.detect(frame)
        h, w = frame.shape[:2]
        base = {"state": "face", "name": s.member.name,
                "remaining": round(SESSION_TIMEOUT - (now - s.started))}
        if not faces:
            self.view = {**base, "message": "Look straight at the camera", "box": None}
            return
        face = faces[0]
        box_n = [face.box[0] / w, face.box[1] / h, face.box[2] / w, face.box[3] / h]
        if len(faces) > 1 and faces[1].box[2] * faces[1].box[3] > 0.25 * face.box[2] * face.box[3]:
            self.view = {**base, "message": "Only one person in front of the camera please",
                         "box": box_n}
            return
        if min(face.box[2], face.box[3]) < MIN_FACE_PX * (w / 640):
            self.view = {**base, "message": "Come a little closer", "box": box_n}
            return
        if s.last_box is not None and iou(s.last_box, face.box) < 0.15:
            self._finish(False, "face_changed", now)
            return
        s.last_box, s.last_frame, s.last_face = face.box, frame, face
        s.frames += 1

        s.spoof.append(self.pipe.spoof(frame, face))
        status = s.challenge.feed(self.pipe.mesh(frame, face), now)
        if s.frames % EMBED_EVERY == 1:
            q, emb = self.pipe.embed(frame, face)
            if emb is not None:
                score = max(float(np.dot(emb, t)) for t in s.member.templates)
                s.matches.append(score)
                s.embeddings.append(emb)
                th = float(self._space("match_threshold", 0.42))
                if sum(m < th - 0.12 for m in s.matches) >= 3:
                    self._finish(False, "face_mismatch", now)
                    return

        if status is Status.FAILED:
            self._finish(False, f"challenge:{s.challenge.reason}", now)
            return
        ch = s.challenge
        challenge_ok = status is Status.PASSED or (not ch.actions and status is Status.ACTIVE)
        if challenge_ok and len(s.spoof) >= MIN_SPOOF_FRAMES and len(s.matches) >= MIN_MATCH_FRAMES:
            self._decide(now)
            return

        if status is Status.BASELINE:
            msg = "Look straight at the camera and hold still"
        elif ch.current:
            msg = PROMPTS[ch.current]
        else:
            msg = "Hold still..."
        self.view = {**base, "message": msg, "box": box_n,
                     "action": ch.current.value if ch.current else None,
                     "step": min(ch.step + 1, len(ch.actions)), "steps": len(ch.actions)}

    def _gallery(self) -> tuple[np.ndarray, list[str]]:
        """All templates of the roster as one matrix, for the 1:N cross-check."""
        r = self.roster
        assert r
        if self._gallery_cache is None or self._gallery_cache[0] != id(r):
            mats, owners = [], []
            for pid, m in r.by_person.items():
                for t in m.templates:
                    mats.append(t)
                    owners.append(pid)
            mat = np.vstack(mats) if mats else np.zeros((0, 128), np.float32)
            self._gallery_cache = (id(r), mat, owners)
        return self._gallery_cache[1], self._gallery_cache[2]

    def _decide(self, now: float) -> None:
        s = self.session
        assert s and s.member
        th_match = float(self._space("match_threshold", 0.42))
        th_live = float(self._space("liveness_threshold", 0.6))
        liveness = statistics.fmean(s.spoof)
        low_frac = sum(x < 0.3 for x in s.spoof) / len(s.spoof)
        best = max(s.matches)
        s.scores = {"match": round(best, 4), "liveness": round(liveness, 4),
                    "spoof_low_frac": round(low_frac, 3), "frames": s.frames,
                    "actions": ",".join(a.value for a in s.challenge.actions)}
        if liveness < th_live or low_frac > 0.2:
            self._finish(False, "liveness_failed", now)
            return
        if sum(m >= th_match for m in s.matches) < MIN_MATCH_FRAMES:
            self._finish(False, "face_mismatch", now)
            return
        # 1:N sanity: the face must not look more like someone else in this space.
        mat, owners = self._gallery()
        if len(owners):
            probe = s.embeddings[int(np.argmax(s.matches))]
            sims = mat @ probe
            other = max((float(v) for v, o in zip(sims, owners, strict=True)
                         if o != s.member.person_id), default=-1.0)
            s.scores["other_best"] = round(other, 4)
            if other >= th_match and other > best:
                self._finish(False, "face_matches_other", now)
                return
        self._finish(True, "verified", now)

    # ---------- results & events ----------
    def _event_time(self, now: float) -> datetime:
        offset = float(self.store.get("clock_offset") or 0)
        return datetime.fromtimestamp(now + offset, UTC)

    def _label(self, member: Member, now: float) -> tuple[str, bool]:
        tz = ZoneInfo(self._space("timezone", "UTC"))
        local_now = datetime.fromtimestamp(now, tz)
        midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        recent = self.store.recent_since(member.id, midnight)
        gap = float(self._space("min_rescan_minutes", 2)) * 60
        if recent and now - recent[-1] < gap:
            return "Already recorded", False
        if self._space("mode", "in_out") == "single":
            return ("Already marked present" if recent else "Marked present"), True
        return ("Checked out" if recent else "Checked in"), True

    def _record(self, status: str, reason: str, now: float, scores: dict | None = None,
                note: str = "", snapshot: str | None = None) -> None:
        s = self.session
        member = s.member if s else None
        evt = {"id": str(uuid.uuid4()), "occurred_at": self._event_time(now).isoformat(),
               "status": status, "member_id": member.id if member else None,
               "reason": reason[:200], "note": note,
               "scores": {**(scores or {}), "device_time": datetime.fromtimestamp(now, UTC).isoformat()}}
        if snapshot:
            evt["snapshot_jpeg_b64"] = snapshot
        self.store.enqueue(evt)

    def _snapshot(self) -> str | None:
        s = self.session
        if not (s and s.last_frame is not None and s.last_face is not None
                and self._space("store_failure_snapshots", False)):
            return None
        x, y, w, h = (int(v) for v in s.last_face.box)
        pad = int(0.3 * max(w, h))
        crop = s.last_frame[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad]
        if crop.size == 0:
            return None
        crop = cv2.resize(crop, (160, int(160 * crop.shape[0] / crop.shape[1])))
        ok, jpg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return base64.b64encode(jpg.tobytes()).decode() if ok else None

    def _finish(self, ok: bool, reason: str, now: float) -> None:
        s = self.session
        member = s.member if s else None
        scores = s.scores if s else {}
        if ok and member:
            label, counts = self._label(member, now)
            self._record("accepted", reason, now, scores)
            if counts:
                self.store.add_recent(member.id, now)
            tz = ZoneInfo(self._space("timezone", "UTC"))
            self._show_result(True, member, label, now,
                              time_str=datetime.fromtimestamp(now, tz).strftime("%H:%M"))
            return
        key = reason.split(":")[0]
        self._record("rejected", reason, now, scores, snapshot=self._snapshot())
        if s and s.credential_id and key not in ("qr_invalid", "qr_unknown_person"):
            q = self.failures[s.credential_id]
            q.append(now)
            while q and now - q[0] > LOCKOUT_WINDOW:
                q.popleft()
            if len(q) >= LOCKOUT_FAILURES:
                self.locked_until[s.credential_id] = now + LOCKOUT_SECONDS
                q.clear()
        can_review = bool(member) and key not in ("qr_invalid", "qr_unknown_person",
                                                  "qr_revoked", "member_inactive")
        self._show_result(False, member, FAIL_MESSAGES.get(key, "Verification failed."), now,
                          can_review=can_review, reason=reason)

    def _show_result(self, ok: bool, member: Member | None, message: str, now: float,
                     can_review: bool = False, time_str: str = "", reason: str = "") -> None:
        self.state = "result"
        self.result_until = now + (RESULT_OK_SECONDS if ok else RESULT_FAIL_SECONDS)
        self.result = {"ok": ok, "reason": reason, "review_sent": False,
                       "can_review": can_review}
        self.view = {"state": "result", "ok": ok, "message": message,
                     "name": member.name if member else None,
                     "external_id": member.external_id if member else None,
                     "time": time_str, "can_review": can_review}

    def _request_review(self, now: float) -> None:
        if not self.result or not self.result["can_review"] or self.result["review_sent"]:
            return
        self._record("pending", f"review_requested:{self.result['reason']}", now,
                     self.session.scores if self.session else {}, note="Requested at kiosk",
                     snapshot=self._snapshot())
        self.result["review_sent"] = True
        self.result_until = now + RESULT_OK_SECONDS
        self.view = {**self.view, "can_review": False,
                     "message": "Sent to the admin for review. You may leave."}
