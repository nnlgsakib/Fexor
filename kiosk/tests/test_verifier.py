import base64

import numpy as np
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fexor_core import qr
from fexor_core.challenge import Action
from fexor_core.face import Face, Quality
from fexor_core.liveness import MeshMetrics
from fexor_kiosk.store import Store
from fexor_kiosk.verifier import LOCKOUT_FAILURES, Verifier

KEY = Ed25519PrivateKey.generate()
FRAME = np.zeros((480, 640, 3), np.uint8)
NEUTRAL = MeshMetrics(ear=0.3, yaw=0.0, pitch=0.6, smile=0.55, presence=1.0)


def unit(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(128).astype(np.float32)
    return v / np.linalg.norm(v)


ALICE, BOB = unit(1), unit(2)


def b64(v: np.ndarray) -> str:
    return base64.b64encode(v.astype("<f4").tobytes()).decode()


def roster(**space):
    return {
        "space": {"id": "s1", "name": "Office", "mode": "in_out", "timezone": "UTC",
                  "match_threshold": 0.42, "liveness_threshold": 0.6, "challenge_actions": 2,
                  "min_rescan_minutes": 2, "store_failure_snapshots": False, **space},
        "keys": {"k1": qr.public_key_to_str(KEY.public_key())},
        "model_id": "sface-2021dec",
        "members": [
            {"id": "m-alice", "person_id": "p-alice", "name": "Alice", "external_id": "A1",
             "title": "", "active": True, "credential_id": "c-alice",
             "templates": [b64(ALICE)]},
            {"id": "m-bob", "person_id": "p-bob", "name": "Bob", "external_id": "B1",
             "title": "", "active": True, "credential_id": "c-bob-new",
             "templates": [b64(BOB)]},
        ],
    }


def face(x=200.0):
    raw = np.zeros(15, np.float32)
    return Face(raw=raw, box=(x, 120.0, 200.0, 240.0), landmarks=np.zeros((5, 2)), score=0.9)


class FakePipe:
    """A cooperative live user unless told otherwise."""

    def __init__(self):
        self.qr_text = None
        self.faces = [face()]
        self.spoof_score = 0.95
        self.embedding = ALICE
        self.verifier = None
        self.scripted_mesh = None

    def decode_qr(self, frame):
        return self.qr_text

    def detect(self, frame):
        return self.faces

    def spoof(self, frame, f):
        return self.spoof_score

    def mesh(self, frame, f):
        if self.scripted_mesh is not None:
            return next(self.scripted_mesh)
        ch = self.verifier.session.challenge
        if ch.baseline is None:
            return NEUTRAL
        want = ch.current
        if want is Action.BLINK:
            self._blink = not getattr(self, "_blink", False)
            return MeshMetrics(**{**NEUTRAL.__dict__, "ear": 0.1 if self._blink else 0.3})
        delta = {Action.TURN_LEFT: {"yaw": 0.3}, Action.TURN_RIGHT: {"yaw": -0.3},
                 Action.SMILE: {"smile": 0.7}, Action.LOOK_UP: {"pitch": 0.4}}.get(want, {})
        return MeshMetrics(**{**NEUTRAL.__dict__, **delta})

    def embed(self, frame, f):
        q = Quality(True, 200, 100, 120, 0.05)
        return q, self.embedding


@pytest.fixture
def kiosk(tmp_path):
    store = Store(tmp_path)
    store.save_roster(roster(), '"e1"')
    pipe = FakePipe()
    v = Verifier(store, pipe)
    pipe.verifier = v
    return v, pipe, store


def run(v, seconds=10.0, t0=1_000_000.0, fps=10):
    t = t0
    while t < t0 + seconds:
        v.step(FRAME, t)
        if v.state == "result":
            return t
        t += 1 / fps
    return t


def token(person="p-alice", cred="c-alice", key=KEY, kid="k1"):
    return qr.sign(key, kid, person, cred)


def start(v, pipe, tok, t0=1_000_000.0):
    v.command("start")
    pipe.qr_text = tok
    v.step(FRAME, t0)


def test_success_check_in_then_out(kiosk):
    v, pipe, store = kiosk
    start(v, pipe, token())
    run(v)
    assert v.view["ok"] and v.view["message"] == "Checked in" and v.view["name"] == "Alice"
    ev = store.pending()
    assert len(ev) == 1 and ev[0]["status"] == "accepted" and ev[0]["member_id"] == "m-alice"
    assert ev[0]["scores"]["match"] > 0.9 and ev[0]["scores"]["liveness"] > 0.9

    later = 1_000_000.0 + 3600
    v.step(FRAME, later)  # result expires -> idle
    start(v, pipe, token(), later)
    run(v, t0=later)
    assert v.view["ok"] and v.view["message"] == "Checked out"


def test_rescan_within_gap_is_already_recorded(kiosk):
    v, pipe, store = kiosk
    start(v, pipe, token())
    t = run(v)
    v.step(FRAME, t + 10)
    start(v, pipe, token(), t + 10)
    run(v, t0=t + 10)
    assert v.view["ok"] and v.view["message"] == "Already recorded"


def test_wrong_face_rejected(kiosk):
    v, pipe, store = kiosk
    pipe.embedding = unit(99)  # stranger holding Alice's QR
    start(v, pipe, token())
    run(v)
    assert not v.view["ok"] and v.view["can_review"]
    assert store.pending()[-1]["reason"] == "face_mismatch"


def test_photo_spoof_rejected(kiosk):
    v, pipe, store = kiosk
    pipe.spoof_score = 0.1
    start(v, pipe, token())
    run(v)
    assert not v.view["ok"]
    assert store.pending()[-1]["reason"] == "liveness_failed"


def test_static_video_fails_challenge(kiosk):
    v, pipe, store = kiosk
    pipe.scripted_mesh = iter([NEUTRAL] * 10_000)  # a replay that ignores the prompts
    start(v, pipe, token())
    run(v, seconds=40)
    assert not v.view["ok"]
    assert "timeout" in store.pending()[-1]["reason"]


def test_forged_and_foreign_qr(kiosk):
    v, pipe, store = kiosk
    start(v, pipe, token(key=Ed25519PrivateKey.generate()))
    assert v.state == "result" and not v.view["ok"] and not v.view["can_review"]
    assert store.pending()[-1]["reason"].startswith("qr_invalid")
    assert store.pending()[-1]["member_id"] is None

    v.step(FRAME, 1_000_100.0)  # back to idle
    n = len(store.pending())
    start(v, pipe, "https://example.com/menu", 1_000_100.0)
    assert v.state == "scan_qr" and len(store.pending()) == n  # not ours: no event


def test_revoked_qr(kiosk):
    v, pipe, store = kiosk
    start(v, pipe, token("p-bob", "c-bob-old"))
    assert not v.view["ok"] and store.pending()[-1]["reason"] == "qr_revoked"


def test_face_matches_someone_else_better(kiosk):
    v, pipe, store = kiosk
    mixed = ALICE * 0.45 + BOB * 0.9
    pipe.embedding = mixed / np.linalg.norm(mixed)  # passes Alice threshold, but is Bob
    start(v, pipe, token())
    run(v)
    assert not v.view["ok"]
    assert store.pending()[-1]["reason"] == "face_matches_other"


def test_face_swap_mid_session(kiosk):
    v, pipe, store = kiosk
    start(v, pipe, token())
    for i in range(5):
        v.step(FRAME, 1_000_000.1 + i * 0.1)
    pipe.faces = [face(x=430.0)]  # a different face pops in elsewhere
    run(v, t0=1_000_001.0)
    assert store.pending()[-1]["reason"] == "face_changed"


def test_lockout_after_repeated_failures(kiosk):
    v, pipe, store = kiosk
    pipe.spoof_score = 0.1
    t = 1_000_000.0
    for _ in range(LOCKOUT_FAILURES):
        start(v, pipe, token(), t)
        t = run(v, t0=t) + 20
        v.step(FRAME, t)
    pipe.spoof_score = 0.95
    start(v, pipe, token(), t)
    assert v.state == "result" and "Too many failed attempts" in v.view["message"]
    # Others are not affected.
    v.step(FRAME, t + 20)
    pipe.embedding = BOB
    start(v, pipe, token("p-bob", "c-bob-new"), t + 20)
    run(v, t0=t + 20)
    assert v.view["ok"] and v.view["name"] == "Bob"


def test_review_request_creates_pending_event(kiosk):
    v, pipe, store = kiosk
    pipe.embedding = unit(99)
    start(v, pipe, token())
    t = run(v)
    v.command("review")
    v.step(FRAME, t + 1)
    v.command("review")  # double tap is ignored
    v.step(FRAME, t + 1.5)
    pend = [e for e in store.pending() if e["status"] == "pending"]
    assert len(pend) == 1 and pend[0]["reason"] == "review_requested:face_mismatch"
    assert pend[0]["member_id"] == "m-alice"


def test_single_mode_and_no_challenge(tmp_path):
    store = Store(tmp_path)
    store.save_roster(roster(mode="single", challenge_actions=0), None)
    pipe = FakePipe()
    v = Verifier(store, pipe)
    pipe.verifier = v
    start(v, pipe, token())
    run(v)
    assert v.view["ok"] and v.view["message"] == "Marked present"


def test_store_survives_restart_and_seals_roster(tmp_path):
    store = Store(tmp_path)
    store.save_roster(roster(), '"e1"')
    store.set("token", "fxd_secret", sealed=True)
    store.enqueue({"id": "e1", "x": 1})
    raw = (tmp_path / "kiosk.db").read_bytes()
    assert b"fxd_secret" not in raw and b"Alice" not in raw
    again = Store(tmp_path)
    assert again.has("token") and again.get("token", sealed=True) == "fxd_secret"
    assert again.roster and again.roster.by_person["p-alice"].name == "Alice"
    assert [e["id"] for e in again.pending()] == ["e1"]


@pytest.fixture(scope="module")
def real_pipe():
    import os

    if os.environ.get("FEXOR_SKIP_MODELS"):
        pytest.skip("models disabled")
    from fexor_kiosk.verifier import RealPipeline

    return RealPipeline()


def test_real_models_qr_then_printed_photo_is_rejected(tmp_path, real_pipe):
    """Real QR decode + real face models: a static photo must never pass."""
    import io

    import cv2
    import segno
    from fexor_core.face import FaceEngine, enroll_from_image
    from fexor_core.models import default_model_dir

    photo = cv2.imread(str(default_model_dir() / "test_lena.jpg"))
    if photo is None:
        pytest.skip("sample face image not downloaded (run core tests first)")
    emb = enroll_from_image(FaceEngine(), photo).embedding
    data = roster()
    data["members"][0]["templates"] = [b64(emb)]
    store = Store(tmp_path)
    store.save_roster(data, None)
    v = Verifier(store, real_pipe)

    buf = io.BytesIO()
    segno.make(token(), error="m").save(buf, kind="png", scale=8, border=4)
    qr_img = cv2.imdecode(np.frombuffer(buf.getvalue(), np.uint8), cv2.IMREAD_COLOR)
    frame = np.full((480, 640, 3), 255, np.uint8)
    h, w = qr_img.shape[:2]
    frame[40:40 + h, 120:120 + w] = qr_img[: 480 - 40, : 640 - 120]
    v.command("start")
    v.step(frame, 0.0)
    v.step(frame, 0.1)
    assert v.state == "face" and v.view["name"] == "Alice"

    still = cv2.resize(photo, (480, 480))
    frame = np.zeros((480, 640, 3), np.uint8)
    frame[:, 80:560] = still
    t = 0.2
    while v.state == "face" and t < 40:
        v.step(frame, t)
        t += 0.1
    assert v.state == "result" and not v.view["ok"]
    reason = store.pending()[-1]["reason"]
    assert reason.startswith(("challenge", "timeout", "liveness_failed")), reason


def _qr_png(text: str) -> bytes:
    import io

    import segno

    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="png", scale=8, border=4)
    return buf.getvalue()


class _NoCamera:
    ok = False

    def start(self):
        return self

    def stop(self):
        pass

    def latest(self, after_seq=-1, timeout=1.0):
        return after_seq, None


def _dev_client(tmp_path, monkeypatch, env: str):
    from fastapi.testclient import TestClient
    from fexor_kiosk.app import create_app

    monkeypatch.setenv("FEXOR_ENV", env)
    store = Store(tmp_path)
    store.save_roster(roster(), None)
    pipe = FakePipe()
    app = create_app(store, _NoCamera(), pipeline=pipe)
    pipe.verifier = app.state.verifier
    return TestClient(app), app.state.verifier, store  # no `with`: background threads stay off


def test_dev_qr_upload_feeds_the_normal_check(tmp_path, monkeypatch):
    client, v, store = _dev_client(tmp_path, monkeypatch, "dev")
    assert client.get("/api/status").json()["dev"] is True
    r = client.post("/api/dev/qr", content=_qr_png(token()), headers={"content-type": "image/png"})
    assert r.status_code == 200
    v.step(None, 1_000_000.0)
    assert v.state == "face" and v.view["name"] == "Alice"

    # Ignored while a face check is running, so it can't cut into someone's check.
    client.post("/api/dev/qr", content=_qr_png(token("p-bob", "c-bob-new")), headers={"content-type": "image/png"})
    v.step(None, 1_000_000.5)
    assert v.state == "face" and v.view["name"] == "Alice"
    v.command("cancel")
    v.step(None, 1_000_000.6)

    # Upload is only a different input: forged tokens are still rejected.
    forged = token(key=Ed25519PrivateKey.generate())
    client.post("/api/dev/qr", content=_qr_png(forged), headers={"content-type": "image/png"})
    v.step(None, 1_000_001.0)
    assert v.state == "result" and not v.view["ok"]
    assert store.pending()[-1]["reason"].startswith("qr_invalid")

    assert client.post("/api/dev/qr", content=b"not an image").status_code == 400
    assert client.post("/api/dev/qr", content=b"").status_code == 413
    import cv2

    ok, white = cv2.imencode(".png", np.full((200, 200, 3), 255, np.uint8))
    r = client.post("/api/dev/qr", content=white.tobytes())
    assert r.status_code == 400 and "no QR" in r.json()["detail"]
    r = client.post("/api/dev/qr", content=_qr_png(token()), headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_dev_qr_upload_absent_outside_dev(tmp_path, monkeypatch):
    client, _, _ = _dev_client(tmp_path, monkeypatch, "production")
    assert client.get("/api/status").json()["dev"] is False
    assert client.post("/api/dev/qr", content=_qr_png(token())).status_code in (404, 405)
