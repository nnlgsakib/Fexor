"""Query-cost regression tests. Each one failed before its fix.

They count the SQL a request sends (or the secrets it decrypts), so a change that quietly
brings back per-row queries or extra decryption on a hot path fails here.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import event, select, text

from .conftest import make_device, make_member, make_space, recent_monday, run_jobs

MON = recent_monday()


@contextmanager
def capture_sql():
    """Every SQL statement sent through the app's engine while the block runs."""
    from fexor_server.db import engine

    seen: list[str] = []

    def on_execute(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    target = engine().sync_engine
    event.listen(target, "before_cursor_execute", on_execute)
    try:
        yield seen
    finally:
        event.remove(target, "before_cursor_execute", on_execute)


def _ev(mid, at, status="accepted"):
    return {"id": str(uuid.uuid4()), "member_id": mid, "occurred_at": at, "status": status}


# ---------- recompute ----------
async def test_recompute_removes_stale_days_in_one_statement(admin):
    """Every day off of every member is 'stale'. One DELETE per person-day made the worker's
    10-minute close send thousands of statements for a big space."""
    from fexor_server.db import sessionmaker
    from fexor_server.models import AttendanceDay, Space
    from fexor_server.services import recompute

    s = await make_space(admin, template="office")  # Mon-Fri shift
    sid = uuid.UUID(s["id"])
    members = [await make_member(admin, s["id"], start_date=str(MON - timedelta(days=30)))
               for _ in range(3)]
    mids = [uuid.UUID(m["id"]) for m in members]
    saturday = MON - timedelta(days=2)
    start, end = MON - timedelta(days=7), MON + timedelta(days=4)  # two weekends
    async with sessionmaker()() as db:
        # A row left over from an earlier rule (e.g. the shift used to include Saturday).
        db.add(AttendanceDay(membership_id=mids[0], day=saturday, space_id=sid, status="present"))
        await db.commit()
        space = await db.get(Space, sid)
        at = datetime.combine(end + timedelta(days=1), datetime.min.time(), ZoneInfo("UTC"))
        with capture_sql() as sql:
            await recompute(db, space, mids, start, end, at=at)
        await db.commit()
        days = set((await db.execute(select(AttendanceDay.membership_id, AttendanceDay.day)
                                     .where(AttendanceDay.space_id == sid))).all())
    deletes = [q for q in sql if q.startswith("DELETE")]
    assert len(deletes) == 1, deletes
    assert (mids[0], saturday) not in days  # the stale row is gone
    assert {d for _, d in days} == {start + timedelta(days=i) for i in range(12)
                                     if (start + timedelta(days=i)).weekday() < 5}
    assert len(days) == 3 * 10  # 10 working days each, nothing on weekends


async def test_recompute_with_nothing_stale_sends_no_delete(admin):
    from fexor_server.db import sessionmaker
    from fexor_server.models import Space
    from fexor_server.services import recompute

    s = await make_space(admin, template="office")
    m = await make_member(admin, s["id"], start_date=str(MON - timedelta(days=30)))
    async with sessionmaker()() as db:
        space = await db.get(Space, uuid.UUID(s["id"]))
        with capture_sql() as sql:
            await recompute(db, space, [uuid.UUID(m["id"])], MON, MON)
        await db.commit()
    assert not [q for q in sql if q.startswith("DELETE")]


# ---------- kiosk roster sync ----------
async def test_unchanged_kiosk_sync_decrypts_nothing(app, admin, face_jpeg, monkeypatch):
    """Kiosks poll every 15 s. Answering 'not modified' must not unseal the QR signing key
    or any face template."""
    from fexor_core import crypto

    s = await make_space(admin)
    m = await make_member(admin, s["id"])
    await admin.post(f"/api/spaces/{s['id']}/members/{m['id']}/faces",
                     files=[("files", ("a.jpg", face_jpeg, "image/jpeg"))])
    await admin.post(f"/api/spaces/{s['id']}/members/{m['id']}/qr")
    dev = await make_device(app, admin, s["id"])
    first = await dev.get("/api/device/sync")
    assert first.status_code == 200 and len(first.json()["members"][0]["templates"]) == 1

    unsealed = []
    real = crypto.unseal
    monkeypatch.setattr(crypto, "unseal", lambda *a, **k: unsealed.append(a[2]) or real(*a, **k))
    with capture_sql() as sql:
        r = await dev.get("/api/device/sync", headers={"If-None-Match": first.headers["etag"]})
    assert r.status_code == 304
    assert unsealed == []
    # Face template blobs are only read when the roster is actually sent.
    assert not [q for q in sql if "face_template.embedding" in q], sql


async def test_sync_etag_follows_face_photos(app, admin, face_jpeg):
    s = await make_space(admin)
    m = await make_member(admin, s["id"])
    url = f"/api/spaces/{s['id']}/members/{m['id']}"
    dev = await make_device(app, admin, s["id"])
    e0 = (await dev.get("/api/device/sync")).headers["etag"]
    await admin.post(f"{url}/faces", files=[("files", ("a.jpg", face_jpeg, "image/jpeg"))])
    r = await dev.get("/api/device/sync", headers={"If-None-Match": e0})
    assert r.status_code == 200 and len(r.json()["members"][0]["templates"]) == 1
    e1 = r.headers["etag"]
    assert (await dev.get("/api/device/sync", headers={"If-None-Match": e1})).status_code == 304
    face_id = (await admin.get(f"{url}/faces")).json()[0]["id"]
    assert (await admin.delete(f"{url}/faces/{face_id}")).status_code == 200
    r = await dev.get("/api/device/sync", headers={"If-None-Match": e1})
    assert r.status_code == 200 and r.json()["members"][0]["templates"] == []


async def test_first_sync_creates_the_signing_key_once(app, admin):
    """The key must exist before the first roster goes out (settings QRs verify with it)."""
    from fexor_server.db import sessionmaker
    from fexor_server.models import SigningKey

    async with sessionmaker()() as db:
        await db.execute(text("DELETE FROM signing_key WHERE NOT EXISTS "
                              "(SELECT 1 FROM qr_credential c WHERE c.key_id = signing_key.id)"))
        before = (await db.execute(select(SigningKey.id).where(SigningKey.active))).scalars().all()
        await db.commit()
    s = await make_space(admin)
    dev = await make_device(app, admin, s["id"])
    keys = (await dev.get("/api/device/sync")).json()["keys"]
    assert keys
    async with sessionmaker()() as db:
        active = (await db.execute(select(SigningKey.id).where(SigningKey.active))).scalars().all()
    assert len(active) == max(1, len(before))
    assert set(active) <= set(keys)


# ---------- kiosk event upload ----------
async def test_event_upload_loads_the_schedule_once(app, admin):
    s = await make_space(admin, template="office")
    m = await make_member(admin, s["id"], start_date=str(MON - timedelta(days=30)))
    dev = await make_device(app, admin, s["id"])
    with capture_sql() as sql:
        r = await dev.post("/api/device/events", json={"events": [
            _ev(m["id"], f"{MON}T09:00:00Z"), _ev(m["id"], f"{MON}T18:00:00Z")]})
    assert r.status_code == 200
    assert len([q for q in sql if "FROM shift_assignment" in q]) == 1, sql
    days = (await admin.get(f"/api/spaces/{s['id']}/days?start={MON}&end={MON}")).json()
    assert days[0]["status"] == "present" and days[0]["worked_minutes"] == 540


async def test_a_schedule_that_does_not_cover_the_request_is_loaded_again(admin):
    from fexor_server.db import sessionmaker
    from fexor_server.models import Space
    from fexor_server.services import ScheduleContext, recompute

    s = await make_space(admin, template="office")
    a, b = [uuid.UUID((await make_member(admin, s["id"],
                                         start_date=str(MON - timedelta(days=30))))["id"])
            for _ in range(2)]
    async with sessionmaker()() as db:
        space = await db.get(Space, uuid.UUID(s["id"]))
        ctx = await ScheduleContext.load(db, space, [a], MON - timedelta(days=1),
                                         MON + timedelta(days=1))
        assert ctx.covers(space, [a], MON, MON)
        assert not ctx.covers(space, [a, b], MON, MON)  # other people
        assert not ctx.covers(space, [a], MON, MON + timedelta(days=2))  # other days
        with capture_sql() as reused:
            await recompute(db, space, [a], MON, MON, ctx=ctx)
        with capture_sql() as reloaded:
            await recompute(db, space, [a, b], MON, MON, ctx=ctx)
        await db.commit()
    assert not [q for q in reused if "FROM shift_assignment" in q]
    assert len([q for q in reloaded if "FROM shift_assignment" in q]) == 1
    days = (await admin.get(f"/api/spaces/{s['id']}/days?start={MON}&end={MON}")).json()
    assert sorted(d["status"] for d in days) == ["absent", "absent"]


# ---------- indexes ----------
async def test_review_queue_and_recent_activity_are_indexed():
    """The pending count / review queue and the dashboard's latest-received list ran over every
    event a space ever had."""
    from fexor_server.db import sessionmaker

    async with sessionmaker()() as db:
        idx = dict((await db.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'attendance_event'"
        ))).all())
    assert "ix_event_pending" in idx, idx
    assert "(space_id, occurred_at)" in idx["ix_event_pending"]
    assert "WHERE ((status)::text = 'pending'::text)" in idx["ix_event_pending"]
    assert "(space_id, received_at)" in idx["ix_event_space_received"]


async def test_planner_uses_the_new_indexes():
    from fexor_server.db import sessionmaker

    async with sessionmaker()() as db:
        await db.execute(text("SET LOCAL enable_seqscan = off"))
        sid = str(uuid.uuid4())
        pending = "\n".join((await db.execute(text(
            "EXPLAIN SELECT count(*) FROM attendance_event "
            f"WHERE space_id = '{sid}' AND status = 'pending'"))).scalars())
        recent = "\n".join((await db.execute(text(
            "EXPLAIN SELECT id FROM attendance_event "
            f"WHERE space_id = '{sid}' ORDER BY received_at DESC LIMIT 15"))).scalars())
        await db.rollback()
    assert "ix_event_pending" in pending, pending
    assert "ix_event_space_received" in recent, recent


async def test_models_match_the_migrations():
    """The ORM metadata and the migrated schema agree (no forgotten migration)."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from fexor_server.db import engine
    from fexor_server.models import Base

    def diff(conn):
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        return compare_metadata(ctx, Base.metadata)

    async with engine().connect() as conn:
        changes = await conn.run_sync(diff)
    assert changes == []


# ---------- webhooks ----------
async def test_webhook_deliveries_share_one_http_client(admin, monkeypatch):
    import httpx
    from fexor_server import worker
    from fexor_server.db import sessionmaker

    made, seen = [], []
    real = httpx.AsyncClient

    def receiver(req):
        seen.append(req)
        return httpx.Response(200, headers={"Set-Cookie": "track=1; Path=/"})

    def factory(**kw):
        made.append(kw)
        return real(transport=httpx.MockTransport(receiver), **kw)

    monkeypatch.setattr(worker.httpx, "AsyncClient", factory)
    monkeypatch.setattr(worker, "_http", None)
    hook = (await admin.post("/api/webhooks", json={"url": "https://hooks.example.com/y"})).json()
    try:
        async with sessionmaker()() as db:
            for i in range(3):
                await worker.handle_webhook(db, {"webhook_id": hook["id"], "type": "ping",
                                                 "data": {}, "event_id": f"e{i}"})
        assert len(made) == 1
        assert made[0]["follow_redirects"] is False and made[0]["timeout"] == 10
        # A shared client must not replay one delivery's cookies on the next.
        assert len(seen) == 3 and all("cookie" not in r.headers for r in seen)
        await worker.close_http()
        assert worker._http is None
    finally:
        await worker.close_http()
        await admin.delete(f"/api/webhooks/{hook['id']}")
        await run_jobs()

