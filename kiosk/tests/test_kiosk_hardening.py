"""Regression tests for kiosk bugs found in the pre-release audit. Each failed before its fix."""

import json
import sqlite3
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from fexor_kiosk import sync
from fexor_kiosk.app import create_app
from fexor_kiosk.store import Store
from fexor_kiosk.verifier import LOCKOUT_FAILURES, SETTINGS_SECONDS, Verifier
from test_verifier import FRAME, FakePipe, _NoCamera, paired_store, roster, run, start, token

T0 = 1_000_000.0


def _kiosk(tmp_path, **kw):
    store = paired_store(tmp_path, data=roster(**kw.pop("space", {})))
    pipe = FakePipe()
    v = Verifier(store, pipe, **kw)
    pipe.verifier = v
    return v, pipe, store


# ---------- a paired kiosk that never synced must not be stuck ----------
def test_settings_open_while_waiting_for_first_sync(tmp_path):
    store = Store(tmp_path)
    store.set("token", "fxd_test", sealed=True)  # paired, but the first sync never happened
    c = TestClient(create_app(store, _NoCamera(), pipeline=FakePipe()))
    assert c.get("/api/status").json()["settings_open"] is True
    assert c.post("/api/camera", json={"source": "0"}).status_code == 200


# ---------- sync ----------
def _mock(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(sync.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def _event(n):
    return {"id": f"00000000-0000-0000-0000-00000000000{n}", "occurred_at": "2026-11-02T09:00:00+00:00",
            "status": "accepted", "member_id": "m-alice", "reason": "verified", "scores": {}}


def _server(calls, bad_ids=(), stored_override=None, heartbeat=200):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if req.url.path == "/api/device/heartbeat":
            if heartbeat != 200:
                return httpx.Response(heartbeat, json={"detail": "device revoked or unknown"})
            return httpx.Response(200, json={"server_time": datetime.now(UTC).isoformat()})
        if req.url.path == "/api/device/sync":
            return httpx.Response(200, json=roster(name="Renamed"), headers={"etag": '"e2"'})
        ids = [e["id"] for e in json.loads(req.content)["events"]]
        if any(i in bad_ids for i in ids):
            return httpx.Response(422, json={"detail": "bad event"})
        return httpx.Response(200, json={"stored": ids if stored_override is None else stored_override})
    return handler


async def test_sync_gets_the_roster_first_and_sets_aside_a_rejected_event(tmp_path, monkeypatch):
    store = paired_store(tmp_path, etag='"e1"')
    store.set("server_url", "http://srv")
    good, bad = _event(1), _event(2)
    store.enqueue(good)
    store.enqueue(bad)
    calls = []
    _mock(monkeypatch, _server(calls, bad_ids={bad["id"]}))
    s = sync.Syncer(store)
    for _ in range(store.MAX_ATTEMPTS):
        await s.once()  # a rejected event must not make the whole sync fail
    assert calls.index("/api/device/sync") < calls.index("/api/device/events")
    assert store.roster.space["name"] == "Renamed"
    assert store.pending() == [] and store.stuck_count() == 1  # kept, but no longer resent
    assert store.outbox_size() == 1


async def test_sync_stops_when_the_server_acknowledges_nothing(tmp_path, monkeypatch):
    store = paired_store(tmp_path)
    store.set("server_url", "http://srv")
    store.enqueue(_event(1))
    _mock(monkeypatch, _server([], stored_override=[]))
    import asyncio

    await asyncio.wait_for(sync.Syncer(store).once(), 5)


async def test_removed_kiosk_says_so_and_stops_check_ins(tmp_path, monkeypatch):
    store = paired_store(tmp_path)
    store.set("server_url", "http://srv")
    _mock(monkeypatch, _server([], heartbeat=401))
    s = sync.Syncer(store)
    await s.sync_now()
    assert s.status["error"] == "revoked" and s.status["online"] is False
    v, pipe, _ = _kiosk(tmp_path / "v", revoked=lambda: True)
    v.step(FRAME, T0)
    assert v.view["state"] == "not_ready" and "removed" in v.view["message"]


def test_kiosk_version_comes_from_the_package():
    from importlib.metadata import version

    assert sync.VERSION == version("fexor-kiosk")


# ---------- camera ----------
def test_a_frozen_camera_gives_no_frame():
    from fexor_kiosk.camera import Camera

    cam = Camera(0)  # reader thread not started
    with cam._cond:
        cam._frame, cam._seq = FRAME, 5
    cam._ok, cam._last_frame_at = True, time.monotonic()
    assert cam.latest(4, timeout=0.01)[1] is FRAME
    assert cam.latest(5, timeout=0.01) == (5, None)  # nothing new: no stale frame
    assert cam.ok
    cam._last_frame_at -= 10
    assert not cam.ok  # a driver stuck inside read() counts as not working


# ---------- errors and timeouts must not lock people out ----------
def test_errors_go_back_to_idle_without_events(tmp_path):
    v, pipe, store = _kiosk(tmp_path)

    def boom(frame):
        raise RuntimeError("camera returned an odd frame")

    pipe.decode_qr = boom
    for i in range(6):
        v.tick(FRAME, T0 + i)
    assert v.state == "idle" and store.outbox_size() == 0


def test_errors_and_timeouts_do_not_count_toward_lockout(tmp_path):
    v, pipe, store = _kiosk(tmp_path)
    for i in range(LOCKOUT_FAILURES + 1):
        t = T0 + i * 100
        start(v, pipe, token(), t)
        pipe.faces = []  # nobody in front of the camera: times out
        run(v, 40, t0=t + 0.1)
        assert v.state == "result" and not v.view["ok"]
        v.command("dismiss")
        v.step(FRAME, t + 50)
    assert v.locked_until == {}

    def boom(frame):
        raise RuntimeError("driver hiccup")

    for i in range(LOCKOUT_FAILURES + 1):
        t = T0 + 1000 + i * 100
        pipe.faces = [__import__("test_verifier").face()]
        start(v, pipe, token(), t)
        real, pipe.detect = pipe.detect, boom
        v.tick(FRAME, t + 0.1)
        pipe.detect = real
        v.command("dismiss")
        v.step(FRAME, t + 50)
    assert v.locked_until == {}
    assert any(e["reason"] == "error" for e in store.pending())


def test_nan_liveness_counts_as_a_failure(tmp_path):
    v, pipe, _ = _kiosk(tmp_path)
    pipe.spoof_score = float("nan")
    start(v, pipe, token(), T0)
    run(v, t0=T0 + 0.1)
    assert v.state == "result" and not v.view["ok"]
    assert v.store.pending()[-1]["reason"] == "liveness_failed"


# ---------- clock ----------
def test_result_time_uses_server_clock_and_space_time_zone(tmp_path):
    v, pipe, store = _kiosk(tmp_path, space={"timezone": "Asia/Kolkata"})
    store.set("clock_offset", "3600")
    start(v, pipe, token(), T0)
    t = run(v, t0=T0 + 0.1)
    assert v.view["ok"]
    assert v.view["time"] == datetime.fromtimestamp(t + 3600, ZoneInfo("Asia/Kolkata")).strftime("%H:%M")


def test_check_ins_before_the_first_sync_are_flagged(tmp_path):
    v, pipe, store = _kiosk(tmp_path, clock_verified=lambda: False)
    start(v, pipe, token(), T0)
    run(v, t0=T0 + 0.1)
    assert store.pending()[-1]["scores"]["clock_unverified"] is True


def test_clock_jump_closes_settings_and_results(tmp_path):
    v, pipe, _ = _kiosk(tmp_path)
    v.step(FRAME, T0)
    v.settings_until = T0 + SETTINGS_SECONDS
    v.step(FRAME, T0 + 1)
    assert v.view["state"] == "settings"
    v.step(FRAME, T0 - 7200)  # NTP pulled the clock back two hours
    assert v.view["state"] != "settings" and v.state == "idle"


# ---------- QR handling ----------
def test_unrelated_qr_codes_in_view_are_ignored_while_idle(tmp_path):
    v, pipe, _ = _kiosk(tmp_path)
    pipe.qr_text = "https://example.com/menu"
    for i in range(6):
        v.step(FRAME, T0 + i)
    assert v.state == "idle" and v.view["message"] == "Show your QR code or tap Start"


def test_pairing_qr_in_settings_changes_the_server_address(tmp_path):
    seen = []
    v, pipe, _ = _kiosk(tmp_path)
    v.on_server_qr = seen.append
    v.settings_until = T0 + 100
    pipe.qr_text = 'FXP1:{"u":"http://192.168.1.50","c":"ABCDE-FGHJK"}'
    for i in range(6):
        v.step(FRAME, T0 + i)
    assert seen and seen[0].startswith("FXP1:")


def test_settings_stay_open_while_used(tmp_path):
    assert SETTINGS_SECONDS == 120
    store = paired_store(tmp_path)
    app = create_app(store, _NoCamera(), pipeline=FakePipe())
    v = app.state.verifier
    v.settings_until = v.clock() + 5
    c = TestClient(app)
    assert c.post("/api/camera", json={"source": "0"}).status_code == 200
    assert v.settings_until >= v.clock() + SETTINGS_SECONDS - 2


# ---------- storage ----------
def test_offline_queue_is_encrypted_and_old_snapshots_are_dropped(tmp_path):
    store = Store(tmp_path)
    evt = {**_event(1), "snapshot_jpeg_b64": "AAAA", "note": "Jane Doe"}
    store.enqueue(evt)
    raw = sqlite3.connect(tmp_path / "kiosk.db").execute("SELECT payload FROM outbox").fetchone()[0]
    assert b"m-alice" not in bytes(raw if isinstance(raw, bytes) else raw.encode())
    assert store.pending() == [evt]
    store._db.execute("UPDATE outbox SET created = created - 31 * 86400")
    store.drop_old_snapshots()
    assert "snapshot_jpeg_b64" not in store.pending()[0]


def test_queue_written_by_an_older_version_still_uploads(tmp_path):
    store = Store(tmp_path)
    store._db.execute("INSERT INTO outbox (id, payload, created) VALUES (?,?,?)",
                      ("old", json.dumps(_event(3)), time.time()))
    assert store.pending() == [_event(3)]


# ---------- local API safety ----------
def test_other_hostnames_are_refused(tmp_path):
    app = create_app(paired_store(tmp_path), _NoCamera(), pipeline=FakePipe())
    evil = TestClient(app, base_url="http://attacker.example:8300")
    assert evil.get("/api/status").status_code == 400  # DNS rebinding: wrong Host header
    ok = TestClient(app, base_url="http://127.0.0.1:8300")
    assert ok.get("/api/status").status_code == 200


# ---------- desktop app ----------
def test_session_list_offers_the_last_used_session(tmp_path):
    from fexor_kiosk import sessions

    root = tmp_path
    a = sessions.new_session(root)
    paired_store(a).close()
    time.sleep(0.05)
    sessions.new_session(root)  # newer, but never paired
    c = TestClient(sessions.create_sessions_app(root, on_open=lambda p: None, on_quit=lambda: None))
    body = c.get("/api/sessions").json()
    assert body["auto_open"] == a.name and body["auto_open_seconds"] == 15
    empty = TestClient(sessions.create_sessions_app(tmp_path / "none", lambda p: None, lambda: None))
    assert empty.get("/api/sessions").json()["auto_open"] is None


def test_chromium_runs_without_keyring_prompts_or_gestures(tmp_path):
    from fexor_kiosk.desktop import chromium_args

    args = chromium_args("/usr/bin/chromium", tmp_path, windowed=False)
    for flag in ("--kiosk", "--password-store=basic", "--disable-pinch",
                 "--overscroll-history-navigation=0", "--hide-crash-restore-bubble"):
        assert flag in args


def test_gallery_cache_follows_roster_object(tmp_path):
    v, pipe, store = _kiosk(tmp_path)
    m1, _ = v._gallery()
    store.save_roster(roster(), None)
    m2, _ = v._gallery()
    assert m1 is not m2 and np.array_equal(m1, m2)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Nothing here may reach a real server."""
    yield


def test_kiosk_screens_load_their_bundled_fonts(tmp_path):
    """Kiosks are often offline: the fonts ship with the app, and both screens must serve them."""
    import re

    from fexor_kiosk import sessions

    apps = [create_app(paired_store(tmp_path / "a"), _NoCamera(), pipeline=FakePipe()),
            sessions.create_sessions_app(tmp_path / "s", on_open=lambda p: None, on_quit=lambda: None)]
    for app in apps:
        c = TestClient(app)
        page = c.get("/").text
        fonts = set(re.findall(r'url\("(/static/fonts/[^"]+)"\)', page))
        assert fonts
        for f in fonts:
            r = c.get(f)
            assert r.status_code == 200 and r.content[:4] == b"wOF2", f


# ---------- kiosk PIN ----------
def _pinned_client(tmp_path, pin="482913"):
    from fexor_core.pin import hash_pin

    data = roster()
    data["device"] = {"id": "dev-1", "name": "Gate", "unlock_pin": hash_pin(pin) if pin else None}
    store = paired_store(tmp_path, data=data)
    app = create_app(store, _NoCamera(), pipeline=FakePipe(), on_quit=lambda: None)
    return TestClient(app), app.state.verifier


def test_kiosk_pin_unlocks_settings_offline(tmp_path):
    c, v = _pinned_client(tmp_path)
    assert c.get("/api/status").json()["has_pin"] is True
    assert c.post("/api/settings/quit").status_code == 403  # locked
    r = c.post("/api/unlock", json={"pin": "000000"})
    assert r.status_code == 403 and "4 tries left" in r.json()["detail"]
    assert c.post("/api/unlock", json={"pin": "482913"}).status_code == 200
    assert v.settings_open() and c.post("/api/settings/quit").status_code == 200


def test_wrong_pins_lock_the_pad_for_a_while(tmp_path):
    c, v = _pinned_client(tmp_path)
    codes = [c.post("/api/unlock", json={"pin": "111111"}).status_code for _ in range(5)]
    assert codes == [403, 403, 403, 403, 429]
    r = c.post("/api/unlock", json={"pin": "482913"})  # even the right PIN waits now
    assert r.status_code == 429 and "minute" in r.json()["detail"] and not v.settings_open()
    assert c.post("/api/unlock", json={"pin": "12"}).status_code == 422
    assert c.post("/api/unlock", json={"pin": "482913"}, headers={"origin": "https://evil.example"}).status_code == 403


def test_without_a_pin_only_the_settings_qr_works(tmp_path):
    c, _ = _pinned_client(tmp_path, pin=None)
    assert c.get("/api/status").json()["has_pin"] is False
    r = c.post("/api/unlock", json={"pin": "482913"})
    assert r.status_code == 404 and "Settings QR" in r.json()["detail"]
