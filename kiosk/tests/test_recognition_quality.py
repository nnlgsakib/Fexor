"""What the kiosk records and says so faces that change keep working: scores on every result
(for setting the threshold), the face sent for learning, and a hint for sunglasses."""

import base64

import numpy as np
from fexor_core.challenge import Action, Challenge
from fexor_kiosk.verifier import Verifier
from test_verifier import (
    ALICE,
    FRAME,
    NEUTRAL,
    FakePipe,
    paired_store,
    roster,
    run,
    start,
    token,
    unit,
)

T0 = 1_000_000.0


def _kiosk(tmp_path, **space):
    store = paired_store(tmp_path, data=roster(**space))
    pipe = FakePipe()
    v = Verifier(store, pipe)
    pipe.verifier = v
    return v, pipe, store


# ---------- scores on every result ----------
def test_a_quick_face_mismatch_still_records_its_scores(tmp_path):
    """The early 'face did not match' left no scores, so the failures an admin most needs to
    look at could not be measured."""
    v, pipe, store = _kiosk(tmp_path)
    pipe.embedding = unit(99)
    start(v, pipe, token())
    run(v)
    ev = store.pending()[-1]
    assert ev["reason"] == "face_mismatch"
    s = ev["scores"]
    assert s["match"] < 0.3 and s["frames"] > 0 and 0 < s["liveness"] <= 1
    assert "other_best" not in s  # the 1:N check never ran


def test_a_review_request_carries_the_scores(tmp_path):
    v, pipe, store = _kiosk(tmp_path)
    pipe.embedding = unit(99)
    start(v, pipe, token())
    t = run(v)
    v.command("review")
    v.step(FRAME, t + 1)
    pending = [e for e in store.pending() if e["status"] == "pending"][0]
    assert pending["scores"]["match"] < 0.3


def test_a_failed_movement_check_records_the_scores(tmp_path):
    v, pipe, store = _kiosk(tmp_path)
    pipe.scripted_mesh = iter([NEUTRAL] * 10_000)
    start(v, pipe, token())
    run(v, seconds=40)
    ev = store.pending()[-1]
    assert ev["reason"].startswith("challenge:timeout")
    assert ev["scores"]["match"] > 0.9 and ev["scores"]["frames"] > 0


# ---------- learning ----------
def _probe(ev):
    return np.frombuffer(base64.b64decode(ev["embedding_b64"]), "<f4")


def test_a_verified_check_in_sends_the_face_for_learning(tmp_path):
    v, pipe, store = _kiosk(tmp_path, auto_update_faces=True)
    start(v, pipe, token())
    run(v)
    ev = store.pending()[-1]
    assert ev["status"] == "accepted"
    assert np.allclose(_probe(ev), ALICE)


def test_without_learning_no_face_leaves_the_kiosk(tmp_path):
    for space in ({"auto_update_faces": False}, {}):  # off, or a server that predates it
        v, pipe, store = _kiosk(tmp_path / str(len(space)), **space)
        start(v, pipe, token())
        run(v)
        assert "embedding_b64" not in store.pending()[-1]


def test_failed_checks_never_send_a_face(tmp_path):
    v, pipe, store = _kiosk(tmp_path, auto_update_faces=True)
    pipe.embedding = unit(99)
    start(v, pipe, token())
    t = run(v)
    v.command("review")
    v.step(FRAME, t + 1)
    assert all("embedding_b64" not in e for e in store.pending())


# ---------- sunglasses ----------
def _blink_only(v, pipe):
    start(v, pipe, token())
    v.session.challenge = Challenge([Action.BLINK])
    pipe.scripted_mesh = iter([NEUTRAL] * 10_000)  # eyes never seen closing (dark glasses)


def test_a_blink_that_is_not_seen_asks_about_sunglasses(tmp_path):
    v, pipe, store = _kiosk(tmp_path)
    _blink_only(v, pipe)
    t = T0 + 0.1
    while v.session.challenge.current is not Action.BLINK:  # baseline frames
        v.step(FRAME, t)
        t += 0.1
    v.step(FRAME, t + 0.5)
    assert v.view["cue"] == "blink" and "sunglasses" not in v.view["message"]
    v.step(FRAME, t + 3.5)
    assert v.view["cue"] == "remove_sunglasses" and "sunglasses" in v.view["message"]


def test_a_blink_timeout_says_to_take_off_sunglasses(tmp_path):
    v, pipe, store = _kiosk(tmp_path)
    _blink_only(v, pipe)
    run(v, t0=T0 + 0.1, seconds=20)
    assert store.pending()[-1]["reason"] == "challenge:timeout:blink"
    assert not v.view["ok"] and "sunglasses" in v.view["message"]


def test_other_movement_failures_keep_their_message(tmp_path):
    v, pipe, store = _kiosk(tmp_path)
    start(v, pipe, token())
    v.session.challenge = Challenge([Action.SMILE])
    pipe.scripted_mesh = iter([NEUTRAL] * 10_000)
    run(v, t0=T0 + 0.1, seconds=20)
    assert store.pending()[-1]["reason"] == "challenge:timeout:smile"
    assert "sunglasses" not in v.view["message"]


def test_the_sunglasses_hint_has_bangla_voice_and_text(tmp_path):
    from pathlib import Path

    import fexor_kiosk

    static = Path(fexor_kiosk.__file__).parent / "static"
    assert (static / "audio" / "remove_sunglasses.mp3").stat().st_size > 1000
    assert "remove_sunglasses:" in (static / "index.html").read_text(encoding="utf-8")


# ---------- upload safety ----------
async def test_a_nan_score_does_not_block_the_upload_queue(tmp_path, monkeypatch):
    """httpx refuses NaN in JSON: one such event (a model glitch) stopped every later upload."""
    import json

    import httpx
    from fexor_kiosk import sync
    from test_kiosk_hardening import _event

    store = paired_store(tmp_path)
    store.set("server_url", "http://srv")
    store.enqueue({**_event(1), "scores": {"liveness": float("nan"), "match": float("inf"),
                                           "frames": 3}})
    store.enqueue(_event(2))
    got = []

    def handler(req):
        if req.url.path == "/api/device/heartbeat":
            return httpx.Response(200, json={"server_time": "2026-11-02T09:00:00+00:00"})
        if req.url.path == "/api/device/sync":
            return httpx.Response(304)
        events = json.loads(req.content)["events"]
        got.extend(events)
        return httpx.Response(200, json={"stored": [e["id"] for e in events]})

    real = httpx.AsyncClient
    monkeypatch.setattr(sync.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    s = sync.Syncer(store)
    await s.once()
    await s.close()
    assert store.pending() == []
    assert got[0]["scores"] == {"liveness": None, "match": None, "frames": 3}
