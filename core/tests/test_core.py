import os

import numpy as np
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fexor_core import crypto, qr
from fexor_core.challenge import Action, Challenge, Status, random_actions
from fexor_core.liveness import MeshMetrics


# ---------- QR ----------
def test_qr_roundtrip_and_tamper():
    key = Ed25519PrivateKey.generate()
    pubs = {"k1": key.public_key()}
    tok = qr.sign(key, "k1", "person-1", "cred-1")
    c = qr.verify(tok, pubs)
    assert (c.person_id, c.credential_id) == ("person-1", "cred-1")

    head, payload, sig = tok.split(".")
    forged = qr._b64e(b'{"k":"k1","p":"person-2","j":"cred-1"}')
    with pytest.raises(qr.InvalidToken, match="bad_signature"):
        qr.verify(f"{head}.{forged}.{sig}", pubs)
    with pytest.raises(qr.InvalidToken, match="unknown_key"):
        qr.verify(tok, {"other": key.public_key()})
    other = Ed25519PrivateKey.generate()
    with pytest.raises(qr.InvalidToken):
        qr.verify(qr.sign(other, "k1", "p", "j"), pubs)
    for junk in ["", "hello", "FX1.a.b", "FX1..", "X" * 2000]:
        with pytest.raises(qr.InvalidToken):
            qr.verify(junk, pubs)


def test_public_key_str_roundtrip():
    key = Ed25519PrivateKey.generate()
    s = qr.public_key_to_str(key.public_key())
    tok = qr.sign(key, "a", "p", "j")
    assert qr.verify(tok, {"a": qr.public_key_from_str(s)}).person_id == "p"


# ---------- crypto ----------
def test_seal_vector_roundtrip_and_aad():
    key = crypto.load_key(crypto.new_key_b64())
    v = np.random.default_rng(0).random(128).astype(np.float32)
    blob = crypto.seal_vector(key, v, b"person-1")
    assert np.allclose(crypto.unseal_vector(key, blob, b"person-1"), v)
    with pytest.raises(InvalidTag):
        crypto.unseal_vector(key, blob, b"person-2")  # blob moved to another person


# ---------- challenge ----------
NEUTRAL = MeshMetrics(ear=0.30, yaw=0.0, pitch=0.6, smile=0.55, presence=1.0)


def m(**kw):
    return MeshMetrics(**{**NEUTRAL.__dict__, **kw})


def run(ch, frames, t0=0.0, dt=0.1):
    t = t0
    for f in frames:
        ch.feed(f, t)
        t += dt
    return t


def test_random_actions_distinct():
    for _ in range(50):
        a = random_actions(3)
        assert len(set(a)) == 3


def test_challenge_passes_blink_then_turn_left():
    ch = Challenge([Action.BLINK, Action.TURN_LEFT])
    run(ch, [NEUTRAL] * 5)
    assert ch.status is Status.ACTIVE and ch.current is Action.BLINK
    run(ch, [m(ear=0.10), m(ear=0.10), m(ear=0.29)], t0=1)
    assert ch.current is Action.TURN_LEFT
    run(ch, [m(yaw=0.05), m(yaw=0.25)], t0=2)
    assert ch.status is Status.PASSED


def test_challenge_blink_needs_reopen():
    ch = Challenge([Action.BLINK])
    run(ch, [NEUTRAL] * 5 + [m(ear=0.1)] * 5)
    assert ch.status is Status.ACTIVE  # eyes closed (photo with closed eyes) is not a blink


def test_challenge_opposite_turn_fails():
    ch = Challenge([Action.TURN_RIGHT, Action.SMILE])
    run(ch, [NEUTRAL] * 5 + [m(yaw=0.3)])
    assert ch.status is Status.FAILED and "wrong_action" in ch.reason


def test_challenge_timeout():
    ch = Challenge([Action.SMILE], step_timeout=2)
    run(ch, [NEUTRAL] * 5 + [NEUTRAL] * 30)
    assert ch.status is Status.FAILED and ch.reason.startswith("timeout")


def test_challenge_baseline_requires_frontal():
    ch = Challenge([Action.SMILE])
    run(ch, [m(yaw=0.3)] * 10)
    assert ch.status is Status.BASELINE


def test_challenge_smile_and_look_up():
    ch = Challenge([Action.SMILE, Action.LOOK_UP])
    run(ch, [NEUTRAL] * 5 + [m(smile=0.66), m(pitch=0.4)])
    assert ch.status is Status.PASSED


# ---------- models (network on first run) ----------
@pytest.fixture(scope="module")
def engines():
    if os.environ.get("FEXOR_SKIP_MODELS"):
        pytest.skip("models disabled")
    import urllib.request

    import cv2
    from fexor_core.face import FaceEngine
    from fexor_core.liveness import AntiSpoof, FaceMesh
    from fexor_core.models import default_model_dir

    path = default_model_dir() / "test_lena.jpg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(  # noqa: S310
            "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/lena.jpg", path
        )
    return FaceEngine(), AntiSpoof(), FaceMesh(), cv2.imread(str(path))


def test_pipeline_on_sample_face(engines):
    import cv2
    from fexor_core.face import DEFAULT_MATCH_THRESHOLD, cosine, enroll_from_image

    eng, spoof, mesh, img = engines
    r = enroll_from_image(eng, img)
    assert r.error is None and r.embedding.shape == (128,)
    # Same face, rotated + scaled, still matches.
    rot = cv2.warpAffine(img, cv2.getRotationMatrix2D((256, 256), 15, 0.8), (512, 512))
    e2 = eng.embed(rot, eng.detect(rot)[0])
    assert cosine(r.embedding, e2) > DEFAULT_MATCH_THRESHOLD
    face = eng.detect(img)[0]
    assert 0.0 <= spoof.score(img, face) <= 1.0
    met = mesh.metrics(img, face)
    assert met.presence > 0.9 and 0.15 < met.ear < 0.6
    assert enroll_from_image(eng, np.zeros((300, 300, 3), np.uint8)).error == "no_face"


def test_settings_token_scoped_to_device_and_time():
    key = Ed25519PrivateKey.generate()
    pubs = {"k1": key.public_key()}
    tok = qr.sign_settings(key, "k1", "dev-1", 1_000)
    qr.verify_settings(tok, pubs, "dev-1", 999)
    with pytest.raises(qr.InvalidToken, match="other_device"):
        qr.verify_settings(tok, pubs, "dev-2", 999)
    with pytest.raises(qr.InvalidToken, match="expired"):
        qr.verify_settings(tok, pubs, "dev-1", 1_001)
    # A person QR is never accepted as a settings QR, and vice versa.
    person = qr.sign(key, "k1", "p", "j")
    with pytest.raises(qr.InvalidToken):
        qr.verify_settings(person, pubs, "dev-1", 0)
    with pytest.raises(qr.InvalidToken):
        qr.verify(tok, pubs)
