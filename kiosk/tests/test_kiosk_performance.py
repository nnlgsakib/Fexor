"""Kiosk cost regressions (a Raspberry Pi has little CPU to spare). Each failed before its fix."""

import asyncio
import json
import sqlite3
import time

import httpx
from fastapi.testclient import TestClient
from fexor_kiosk import app as kiosk_app
from fexor_kiosk import sync
from fexor_kiosk.app import create_app
from fexor_kiosk.store import Store
from test_kiosk_hardening import _event, _server
from test_verifier import FRAME, FakePipe, _NoCamera, paired_store


class _LiveCamera(_NoCamera):
    """A new frame on every call, like a camera running at full speed."""

    ok = True

    def latest(self, after_seq=-1, timeout=1.0):
        return after_seq + 1, FRAME


def _frames(ws, n):
    """Read until n video frames arrived; returns the JSON views seen on the way."""
    views, frames = [], 0
    while frames < n:
        msg = ws.receive()
        if msg.get("bytes") is not None:
            frames += 1
        elif msg.get("text") is not None:
            views.append(json.loads(msg["text"]))
    return views


# ---------- live view ----------
def test_live_view_reads_the_database_once_per_status_interval(tmp_path, monkeypatch):
    """The screen loop runs ~12 times a second; it used to run COUNT(*) queries on the offline
    queue every time, competing with the verifier for the store lock."""
    store = paired_store(tmp_path)
    reads = []
    for name in ("outbox_size", "stuck_count", "get"):
        real = getattr(store, name)
        monkeypatch.setattr(store, name, lambda *a, _r=real, _n=name, **k: reads.append(_n) or _r(*a, **k))
    monkeypatch.setattr(kiosk_app, "STATUS_SECONDS", 3600)
    c = TestClient(create_app(store, _LiveCamera(), pipeline=FakePipe()))
    reads.clear()
    with c.websocket_connect("/ws") as ws:
        _frames(ws, 10)
    assert reads.count("outbox_size") == 1 and reads.count("stuck_count") == 1
    assert len(reads) <= 6  # one status read: queue sizes and a few settings


def test_live_view_shows_a_new_queued_event_after_the_interval(tmp_path, monkeypatch):
    store = paired_store(tmp_path)
    monkeypatch.setattr(kiosk_app, "STATUS_SECONDS", 0)
    c = TestClient(create_app(store, _LiveCamera(), pipeline=FakePipe()))
    with c.websocket_connect("/ws") as ws:
        assert _frames(ws, 1)[0]["outbox"] == 0
        store.enqueue(_event(1))
        views = _frames(ws, 5)
    assert views and views[-1]["outbox"] == 1


def test_video_frames_are_encoded_off_the_event_loop(tmp_path, monkeypatch):
    """JPEG encoding on the event loop stalls every other request of the kiosk app."""
    store = paired_store(tmp_path)
    on_loop = []
    real = kiosk_app.cv2.imencode

    def imencode(*a, **k):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(*a, **k)

    monkeypatch.setattr(kiosk_app.cv2, "imencode", imencode)
    c = TestClient(create_app(store, _LiveCamera(), pipeline=FakePipe()))
    with c.websocket_connect("/ws") as ws:
        _frames(ws, 3)
    assert on_loop and not any(on_loop)


# ---------- sync connection ----------
def _counting_client(monkeypatch, handler):
    made = []
    real = httpx.AsyncClient

    def factory(**kw):
        client = real(transport=httpx.MockTransport(handler), **kw)
        made.append((kw["base_url"], client))
        return client

    monkeypatch.setattr(sync.httpx, "AsyncClient", factory)
    return made


async def test_sync_keeps_one_connection_until_the_server_changes(tmp_path, monkeypatch):
    """A new client per sync meant a new TLS handshake every 15 s."""
    store = paired_store(tmp_path)
    store.set("server_url", "http://srv")
    made = _counting_client(monkeypatch, _server([]))
    s = sync.Syncer(store)
    for _ in range(3):
        await s.once()
    assert [u for u, _ in made] == ["http://srv"]
    store.set("server_url", "http://srv2")  # address changed in the settings
    await s.once()
    assert [u for u, _ in made] == ["http://srv", "http://srv2"]
    assert made[0][1].is_closed
    store.set("token", "fxd_other", sealed=True)  # paired again
    await s.once()
    assert len(made) == 3 and made[2][1].headers["authorization"] == "Bearer fxd_other"
    await s.close()
    assert made[2][1].is_closed


async def test_sync_reconnects_after_a_network_error(tmp_path, monkeypatch):
    store = paired_store(tmp_path)
    store.set("server_url", "http://srv")
    ok = _server([])
    fail = {"n": 1}

    def flaky(req):
        if fail["n"]:
            fail["n"] -= 1
            raise httpx.ConnectError("network is unreachable")
        return ok(req)

    made = _counting_client(monkeypatch, flaky)
    s = sync.Syncer(store)
    assert await s.sync_now() == 30.0 and s.status["online"] is False
    assert made[0][1].is_closed  # a broken connection pool is not reused
    assert await s.sync_now() == s.interval and s.status["online"] is True
    assert len(made) == 2
    await s.close()


async def test_sync_loop_closes_its_connection_when_stopped(tmp_path, monkeypatch):
    store = paired_store(tmp_path)
    store.set("server_url", "http://srv")
    made = _counting_client(monkeypatch, _server([]))
    s = sync.Syncer(store)
    task = asyncio.create_task(s.run())
    for _ in range(100):
        if s.status["online"]:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert made and made[0][1].is_closed


# ---------- offline queue ----------
def test_old_snapshots_are_stripped_once_not_on_every_sync(tmp_path, monkeypatch):
    """Events the server keeps refusing stay queued; each sync decrypted all of them again."""
    store = Store(tmp_path)
    store.enqueue({**_event(1), "snapshot_jpeg_b64": "AAAA"})
    store.enqueue(_event(2))
    store._db.execute("UPDATE outbox SET created = created - 31 * 86400")
    opened = []
    real = store._open_event
    monkeypatch.setattr(store, "_open_event", lambda p: opened.append(1) or real(p))
    store.drop_old_snapshots()
    assert len(opened) == 1  # the event without a snapshot is not even opened
    store.drop_old_snapshots()
    assert len(opened) == 1
    assert [e.get("snapshot_jpeg_b64") for e in store.pending()] == [None, None]


def test_queue_from_before_the_snapshot_flag_is_upgraded(tmp_path):
    db = sqlite3.connect(tmp_path / "kiosk.db")
    db.execute("CREATE TABLE outbox (id TEXT PRIMARY KEY, payload TEXT NOT NULL, created REAL NOT NULL,"
               " attempts INTEGER NOT NULL DEFAULT 0)")
    old = {**_event(3), "snapshot_jpeg_b64": "AAAA"}
    db.execute("INSERT INTO outbox VALUES (?, ?, ?, 0)", (old["id"], json.dumps(old),
                                                          time.time() - 31 * 86400))
    db.commit()
    db.close()
    store = Store(tmp_path)
    store.drop_old_snapshots()
    assert store.pending() == [_event(3)]
    store.close()


# ---------- face check ----------
def test_spoof_and_mesh_models_run_at_the_same_time(tmp_path):
    """They are independent models on the same frame; one after the other doubled their cost
    on every frame of a check."""
    import threading

    from fexor_kiosk.verifier import Verifier
    from test_verifier import run, start, token

    store = paired_store(tmp_path)
    pipe = FakePipe()
    v = Verifier(store, pipe)
    pipe.verifier = v
    mesh_started, overlapped = threading.Event(), []
    real_mesh = pipe.mesh

    def spoof(frame, f):
        overlapped.append(mesh_started.wait(2))
        return 0.95

    def mesh(frame, f):
        mesh_started.set()
        return real_mesh(frame, f)

    pipe.spoof, pipe.mesh = spoof, mesh
    start(v, pipe, token())
    v.step(FRAME, 1_000_000.1)
    assert overlapped == [True]
    run(v, t0=1_000_000.2)  # the check still completes normally
    assert v.view["ok"] is True and v.session.scores["liveness"] == 0.95


def test_a_failing_model_still_ends_the_check_cleanly(tmp_path):
    from fexor_kiosk.verifier import Verifier
    from test_verifier import start, token

    store = paired_store(tmp_path)
    pipe = FakePipe()
    v = Verifier(store, pipe)
    pipe.verifier = v

    def broken(frame, f):
        raise RuntimeError("model crashed")

    pipe.spoof = broken
    start(v, pipe, token())
    v.tick(FRAME, 1_000_000.1)
    assert v.state == "result" and v.view["ok"] is False
    assert store.pending()[-1]["reason"] == "error"
