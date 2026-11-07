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


def paired_store(path, data=None, etag=None):
    store = Store(path)
    store.set("token", "fxd_test", sealed=True)
    store.set("device_id", "dev-1")
    store.save_roster(data or roster(), etag)
    return store


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
    store = paired_store(tmp_path, etag='"e1"')
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
    store = paired_store(tmp_path, roster(mode="single", challenge_actions=0))
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
    store = paired_store(tmp_path, data)
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
    source = 0

    def set_source(self, src):
        self.source = src

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
    store = paired_store(tmp_path)
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


def test_http_only_for_local_network_servers():
    from fexor_kiosk.sync import http_allowed, normalize_url, parse_pairing

    for ok in ["http://192.168.1.20", "http://10.0.0.5:8000", "http://localhost:8000",
               "http://attendance-pc", "http://office.local", "http://127.0.0.1"]:
        assert http_allowed(ok), ok
    for bad in ["http://attendance.example.com", "http://8.8.8.8"]:
        assert not http_allowed(bad), bad
    assert normalize_url("192.168.1.20/") == "http://192.168.1.20"
    assert normalize_url("attendance.example.com") == "https://attendance.example.com"
    assert parse_pairing('FXP1:{"u":"http://192.168.1.20","c":"ABCDE-FGHJK"}') == (
        "http://192.168.1.20", "ABCDE-FGHJK")
    with pytest.raises(ValueError):
        parse_pairing("FXP1:{broken")


async def test_enroll_refuses_plain_http_to_internet_host(tmp_path):
    from fexor_kiosk.sync import enroll

    with pytest.raises(ValueError, match="not using https"):
        await enroll(Store(tmp_path), "http://attendance.example.com", "ABCDE-FGHJK")


def test_unpaired_kiosk_looks_for_pairing_qr(tmp_path):
    pipe = FakePipe()
    v = Verifier(Store(tmp_path), pipe)
    seen = []
    v.on_pairing = seen.append
    pipe.qr_text = token()  # an attendance QR is ignored while unpaired
    for i in range(6):
        v.step(FRAME, float(i))
    assert v.view["state"] == "setup" and seen == []
    pipe.qr_text = 'FXP1:{"u":"http://192.168.1.20","c":"X"}'
    for i in range(6):
        v.step(FRAME, float(i))
    assert seen and seen[0].startswith("FXP1:")


def test_settings_qr_unlocks_only_this_kiosk(kiosk):
    v, pipe, store = kiosk
    t = 1_000_000.0
    pipe.qr_text = qr.sign_settings(KEY, "k1", "other-device", int(t) + 600)
    v.command("start")
    v.step(FRAME, t)
    assert not v.settings_open(t) and "different kiosk" in v.view["message"]
    v.step(FRAME, t + 10)  # result expires
    pipe.qr_text = qr.sign_settings(KEY, "k1", "dev-1", int(t) - 1)
    v.command("start")
    v.step(FRAME, t + 10)
    assert not v.settings_open(t + 10) and "expired" in v.view["message"]
    v.step(FRAME, t + 20)
    pipe.qr_text = qr.sign_settings(KEY, "k1", "dev-1", int(t) + 600)
    v.command("start")
    v.step(FRAME, t + 20)
    assert v.settings_open(t + 21) and v.view["state"] == "settings"
    assert not store.pending()  # opening settings is not an attendance event
    v.command("close_settings")
    v.step(FRAME, t + 30)
    assert not v.settings_open(t + 30) and v.view["state"] == "idle"


def test_kiosk_settings_api_locked_until_settings_qr(tmp_path, monkeypatch):
    client, v, store = _dev_client(tmp_path, monkeypatch, "production")
    assert client.post("/api/camera", json={"source": "1"}).status_code == 403
    assert client.post("/api/settings/repair").status_code == 403
    v.settings_until = v.clock() + 600
    assert client.post("/api/camera", json={"source": "1"}).status_code == 200
    assert store.get("camera") == "1"
    assert client.post("/api/camera", json={"source": "ftp://x"}).status_code == 400
    store.enqueue({"id": "e1"})
    r = client.post("/api/settings/repair")
    assert r.status_code == 200 and r.json()["dropped"] == 1
    assert not store.has("token") and store.get("camera") == "1"  # camera choice survives
    # Unpaired kiosks are open for setup.
    assert client.get("/api/status").json()["settings_open"] is True


def test_exit_app_only_in_desktop_and_when_unlocked(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from fexor_kiosk.app import create_app

    monkeypatch.setenv("FEXOR_ENV", "")
    store = paired_store(tmp_path)
    plain = TestClient(create_app(store, _NoCamera(), pipeline=FakePipe()))
    assert plain.post("/api/settings/quit").status_code == 404  # not the desktop app

    quits = []
    app = create_app(store, _NoCamera(), pipeline=FakePipe(), on_quit=lambda: quits.append(1))
    client = TestClient(app)
    assert client.get("/api/status").json()["can_quit"] is True
    assert client.post("/api/settings/quit").status_code == 403  # paired kiosk, settings locked
    app.state.verifier.settings_until = app.state.verifier.clock() + 600
    assert client.post("/api/settings/quit", headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/settings/quit").status_code == 200 and quits == [1]


def test_desktop_data_dir_per_platform(monkeypatch, tmp_path):
    from fexor_kiosk import desktop

    monkeypatch.setenv("FEXOR_KIOSK_DATA", str(tmp_path / "x"))
    assert desktop.app_data_dir() == tmp_path / "x"
    monkeypatch.delenv("FEXOR_KIOSK_DATA")
    assert desktop.app_data_dir().name == "Fexor Kiosk"
    assert desktop.bundled_models() is None  # not packaged


def test_dismiss_closes_result_at_once(kiosk):
    v, pipe, store = kiosk
    start(v, pipe, token())
    t = run(v)
    assert v.state == "result"
    pipe.qr_text = None
    v.command("dismiss")
    v.step(FRAME, t + 0.1)
    assert v.state == "idle"


async def test_change_server_requires_same_server(tmp_path, monkeypatch):
    import httpx
    from fexor_kiosk import sync

    store = paired_store(tmp_path)
    store.set("server_url", "http://192.168.1.101")
    answers = {"http://192.168.1.103": 200, "http://192.168.1.104": 401}

    class FakeClient:
        def __init__(self, base_url, **kw):
            self.base = base_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, path, **kw):
            if self.base not in answers:
                raise httpx.ConnectError("refused")
            return httpx.Response(answers[self.base])

    monkeypatch.setattr(sync.httpx, "AsyncClient", FakeClient)
    assert await sync.change_server(store, "192.168.1.103") == "http://192.168.1.103"
    assert store.get("server_url") == "http://192.168.1.103"
    for bad, why in [("192.168.1.104", "does not know this kiosk"), ("192.168.1.105", "cannot reach"),
                     ("http://attendance.example.com", "not using https")]:
        with pytest.raises(ValueError, match=why):
            await sync.change_server(store, bad)
    assert store.get("server_url") == "http://192.168.1.103"  # unchanged by failures


def test_startup_question_continue_auto_and_new_connection(kiosk):
    from fexor_kiosk.verifier import RESUME_SECONDS

    v, pipe, store = kiosk
    t = 1_000_000.0
    v.resume_until = t + RESUME_SECONDS
    pipe.qr_text = token()
    v.step(FRAME, t)
    assert v.view["state"] == "resume" and v.state == "idle"  # no check-ins while asking
    v.command("resume")
    v.step(FRAME, t + 1)
    assert v.view["state"] != "resume"

    v.resume_until = t + 100 + RESUME_SECONDS  # nobody answers: carries on by itself
    v.step(None, t + 100)
    assert v.view["state"] == "resume"
    v.step(None, t + 100 + RESUME_SECONDS + 1)
    assert v.view["state"] == "idle" and not v.resume_until

    v.command("new_connection")
    v.step(None, t + 200)
    assert v.view == {"state": "setup", "repairing": True}
    assert store.has("token")  # old pairing kept until a new one works
    v.command("cancel_new")
    v.step(None, t + 201)
    assert v.view["state"] == "idle" and not v.repairing


def test_app_startup_question_rules(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from fexor_kiosk.app import create_app

    monkeypatch.setenv("FEXOR_ENV", "")
    store = paired_store(tmp_path)
    quits = []
    app = create_app(store, _NoCamera(), pipeline=FakePipe(), on_quit=lambda: quits.append(1),
                     ask_on_start=True)
    v, client = app.state.verifier, TestClient(app)
    assert v.resume_until > v.clock()
    assert client.post("/api/settings/repair").status_code == 403  # asking doesn't unlock settings
    assert client.post("/api/settings/quit").status_code == 200 and quits == [1]  # but may exit

    v.resume_until, v.repairing = 0.0, True
    r = client.post("/api/setup", json={"server_url": "http://127.0.0.1:9", "code": "X"})
    assert r.status_code == 400  # allowed to try (fails: nothing listens there), not "already set up"
    assert store.has("token")  # failed attempt keeps the old pairing


def test_no_question_for_unpaired_or_cli_kiosk(tmp_path):
    from fexor_kiosk.app import create_app

    unpaired = create_app(Store(tmp_path / "a"), _NoCamera(), pipeline=FakePipe(), ask_on_start=True)
    assert unpaired.state.verifier.resume_until == 0.0
    cli = create_app(paired_store(tmp_path / "b"), _NoCamera(), pipeline=FakePipe())
    assert cli.state.verifier.resume_until == 0.0
