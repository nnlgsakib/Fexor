"""Regression tests for bugs found in the pre-release audit. Each one failed before its fix."""

import asyncio
import io
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from openpyxl import load_workbook

from .conftest import (
    make_device,
    make_member,
    make_space,
    recent_monday,
    run_jobs,
    user_with_role,
)

MON = recent_monday()


def _ev(mid, at, status="accepted", **kw):
    return {"id": str(uuid.uuid4()), "member_id": mid, "occurred_at": at, "status": status, **kw}


async def _days(admin, sid, start, end, **params):
    q = "&".join(f"{k}={v}" for k, v in params.items())
    r = await admin.get(f"/api/spaces/{sid}/days?start={start}&end={end}&{q}")
    assert r.status_code == 200, r.text
    return r.json()


# ---------- reports ----------
async def test_report_neutralises_spreadsheet_formulas(app, admin):
    s = await make_space(admin)
    sid = s["id"]
    m = await make_member(admin, sid, "=1+1", name='=HYPERLINK("http://x","Click")',
                          title="@SUM(A1)", start_date=str(MON - timedelta(days=10)))
    await admin.post(f"/api/spaces/{sid}/events", json={
        "member_id": m["id"], "occurred_at": f"{MON}T09:00:00Z", "note": "badge reader down"})
    x = await admin.get(f"/api/spaces/{sid}/reports/attendance.xlsx?start={MON}&end={MON}")
    ws = load_workbook(io.BytesIO(x.content))["Daily"]
    row = [c.value for c in ws[2]]
    assert row[1] == "'=1+1" and row[2].startswith("'=HYPERLINK") and row[3] == "'@SUM(A1)"
    assert ws.cell(2, 3).data_type != "f"
    c = await admin.get(f"/api/spaces/{sid}/reports/attendance.csv?start={MON}&end={MON}")
    assert "'=HYPERLINK" in c.text and ",=1+1" not in c.text


# ---------- history must survive deactivation ----------
async def test_deactivating_a_member_keeps_past_absences(admin):
    s = await make_space(admin)
    sid = s["id"]
    m = await make_member(admin, sid, start_date=str(MON - timedelta(days=30)))
    await admin.post(f"/api/spaces/{sid}/recompute", json={"start": str(MON), "end": str(MON)})
    await run_jobs()
    assert [d["status"] for d in await _days(admin, sid, MON, MON)] == ["absent"]
    assert (await admin.delete(f"/api/spaces/{sid}/members/{m['id']}")).status_code == 200
    await admin.post(f"/api/spaces/{sid}/recompute", json={"start": str(MON), "end": str(MON)})
    await run_jobs()
    assert [d["status"] for d in await _days(admin, sid, MON, MON)] == ["absent"]
    # ...but no new absences after leaving.
    member = (await admin.get(f"/api/spaces/{sid}/members/{m['id']}")).json()
    assert member["active"] is False and member["end_date"] is not None


async def test_removing_a_shift_keeps_past_days(admin):
    s = await make_space(admin)
    sid = s["id"]
    m = await make_member(admin, sid, start_date=str(MON - timedelta(days=30)))
    await admin.post(f"/api/spaces/{sid}/events", json={
        "member_id": m["id"], "occurred_at": f"{MON}T09:45:00Z", "note": "late arrival"})
    assert (await _days(admin, sid, MON, MON))[0]["status"] == "late"
    r = await admin.delete(f"/api/spaces/{sid}/shifts/{s['default_shift_id']}")
    assert r.status_code == 200
    await run_jobs()
    assert (await _days(admin, sid, MON, MON))[0]["status"] == "late"


# ---------- worker ----------
async def test_one_failing_space_does_not_stop_the_worker(admin, monkeypatch):
    from fexor_server import worker

    s = await make_space(admin)
    calls = []
    real = worker.recompute_space

    async def flaky(db, space, start, end):
        calls.append(space.id)
        if str(space.id) == s["id"]:
            raise RuntimeError("boom")
        return await real(db, space, start, end)

    monkeypatch.setattr(worker, "recompute_space", flaky)
    await worker.close_days()  # must not raise
    assert len(calls) > 1  # other spaces still processed

    # The job queue still runs after a failing close.
    state = worker.WorkerState()
    other = await make_space(admin)
    await admin.post(f"/api/spaces/{other['id']}/holidays", json={"day": str(MON), "name": "x"})
    await worker.tick(state)
    from fexor_server.db import sessionmaker
    from fexor_server.models import Job
    from sqlalchemy import select

    async with sessionmaker()() as db:
        jobs = (await db.execute(select(Job).where(Job.kind == "recompute_space"))).scalars().all()
    assert all(j.done_at for j in jobs if j.payload["space_id"] == other["id"])
    async with sessionmaker()() as db:  # don't leave the deliberately failed job behind
        for j in (await db.execute(select(Job).where(Job.done_at.is_(None)))).scalars():
            j.done_at = j.run_at
        await db.commit()
    assert state.last_close > 0  # a failed close still counts as an attempt


# ---------- cross-space access ----------
async def test_manager_cannot_pull_a_person_from_another_space(app, admin):
    a, b = await make_space(admin), await make_space(admin)
    pb = await make_member(admin, b["id"])
    mgr = await user_with_role(app, admin, a["id"], "manager")
    r = await mgr.post(f"/api/spaces/{a['id']}/members",
                       json={"person_id": pb["person_id"], "external_id": "X1"})
    assert r.status_code == 404
    # A superadmin may link the same person into another space.
    r = await admin.post(f"/api/spaces/{a['id']}/members",
                         json={"person_id": pb["person_id"], "external_id": "X1"})
    assert r.status_code == 201


# ---------- kiosk data must not break admin pages ----------
async def test_dashboard_survives_bad_kiosk_info(app, admin):
    s = await make_space(admin)
    dev = await make_device(app, admin, s["id"])
    r = await dev.post("/api/device/heartbeat", json={"info": {"clock_offset_s": "n/a"}})
    assert r.status_code == 200
    assert (await admin.get(f"/api/spaces/{s['id']}/dashboard")).status_code == 200


async def test_very_old_kiosk_events_need_review(app, admin):
    s = await make_space(admin)
    m = await make_member(admin, s["id"])
    dev = await make_device(app, admin, s["id"])
    old = _ev(m["id"], "1970-01-02T09:00:00Z")
    assert (await dev.post("/api/device/events", json={"events": [old]})).status_code == 200
    review = (await admin.get(f"/api/spaces/{s['id']}/review")).json()
    assert [e["id"] for e in review] == [old["id"]]
    assert "device_clock_behind" in review[0]["reason"]


# ---------- validation: bad input is a 4xx, never a 500 ----------
async def test_bad_input_gets_clear_4xx(admin):
    s = await make_space(admin)
    sid = s["id"]
    other = await make_space(admin)
    cases = [
        ("patch", f"/api/spaces/{sid}", {"name": None}),
        ("patch", f"/api/spaces/{sid}", {"timezone": None}),
        ("patch", f"/api/spaces/{sid}", {"country": "X" * 9}),
        ("patch", f"/api/spaces/{sid}", {"member_label": "X" * 41}),
        ("post", "/api/spaces", {"name": "x" * 201}),
        ("post", "/api/spaces", {"name": "y", "default_shift_id": other["default_shift_id"]}),
        ("post", f"/api/spaces/{sid}/members", {"full_name": "A", "external_id": "P1",
                                                 "phone": "1" * 41}),
        ("post", f"/api/spaces/{sid}/members", {"full_name": "A", "external_id": "P2",
                                                 "email": "a" * 256}),
        ("put", f"/api/users/{uuid.uuid4()}/roles", [{"space_id": sid, "role": "viewer"}]),
        ("post", "/api/api-keys", {"name": "k", "space_id": str(uuid.uuid4())}),
        ("post", "/api/api-keys", {"name": "k" * 201}),
        ("post", "/api/webhooks", {"url": "https://example.com/h", "space_id": str(uuid.uuid4())}),
        ("post", "/api/users", {"email": "a@b.co", "name": "n" * 201,
                                "password": "long-enough-1"}),
        ("get", "/api/audit?limit=-5", None),
        ("get", f"/api/spaces/{sid}/holidays?year=0", None),
    ]
    me = (await admin.get("/api/auth/me")).json()["id"]
    cases.append(("put", f"/api/users/{me}/roles",
                  [{"space_id": str(uuid.uuid4()), "role": "viewer"}]))
    for method, url, body in cases:
        r = await getattr(admin, method)(url, **({"json": body} if body is not None else {}))
        assert 400 <= r.status_code < 500, (method, url, body, r.status_code, r.text)

    await admin.patch(f"/api/spaces/{sid}", json={"field_schema": [
        {"key": "age", "label": "Age", "type": "number"}]})
    r = await admin.post(f"/api/spaces/{sid}/members",
                         json={"full_name": "N", "external_id": "N1", "fields": {"age": "nan"}})
    assert r.status_code == 422


# ---------- QR card ----------
async def test_qr_png_with_non_latin_id(admin):
    s = await make_space(admin)
    m = await make_member(admin, s["id"], "छात्र1")
    r = await admin.get(f"/api/spaces/{s['id']}/members/{m['id']}/qr.png")
    assert r.status_code == 200 and "filename*=UTF-8''" in r.headers["content-disposition"]


async def test_parallel_qr_requests_issue_one_card(app, admin):
    s = await make_space(admin)
    m = await make_member(admin, s["id"])
    url = f"/api/spaces/{s['id']}/members/{m['id']}/qr.svg"
    a, b = await asyncio.gather(admin.get(url), admin.get(url))
    assert a.content == b.content  # same credential, so the printed card stays valid


# ---------- space settings ----------
async def test_space_changes_apply_from_today(admin):
    from fexor_server.db import sessionmaker
    from fexor_server.models import Job
    from sqlalchemy import select

    s = await make_space(admin, tz="Asia/Kolkata")
    await run_jobs()
    r = await admin.patch(f"/api/spaces/{s['id']}", json={"mode": "single"})
    assert r.status_code == 200
    async with sessionmaker()() as db:
        jobs = (await db.execute(select(Job).where(Job.kind == "recompute_space",
                                                   Job.done_at.is_(None)))).scalars().all()
    mine = [j for j in jobs if j.payload["space_id"] == s["id"]]
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    assert [j.payload["start"] for j in mine] == [today]
    await run_jobs()


# ---------- new people ----------
async def test_new_member_is_not_absent_on_the_day_they_are_added(admin):
    from fexor_server.db import sessionmaker
    from fexor_server.models import Space
    from fexor_server.services import recompute

    s = await make_space(admin)
    m = await make_member(admin, s["id"])
    today = date.today()
    async with sessionmaker()() as db:
        space = await db.get(Space, uuid.UUID(s["id"]))
        end_of_day = datetime.combine(today, datetime.max.time(), ZoneInfo("UTC"))
        await recompute(db, space, [uuid.UUID(m["id"])], today, today, at=end_of_day)
        await db.commit()
    assert await _days(admin, s["id"], today, today) == []


# ---------- kiosk sync ----------
async def test_sync_etag_changes_when_an_end_date_passes(app, admin, monkeypatch):
    from fexor_server.api import devices

    s = await make_space(admin)
    tomorrow = date.today() + timedelta(days=1)
    await make_member(admin, s["id"], end_date=str(tomorrow))
    dev = await make_device(app, admin, s["id"])
    first = await dev.get("/api/device/sync")
    assert first.json()["members"][0]["active"] is True
    later = devices.now() + timedelta(days=3)
    monkeypatch.setattr(devices, "now", lambda: later)
    r = await dev.get("/api/device/sync", headers={"If-None-Match": first.headers["etag"]})
    assert r.status_code == 200 and r.json()["members"][0]["active"] is False


# ---------- heavy endpoints ----------
async def test_recompute_endpoint_is_queued(admin):
    s = await make_space(admin)
    r = await admin.post(f"/api/spaces/{s['id']}/recompute",
                         json={"start": str(MON), "end": str(MON)})
    assert r.status_code == 202 and r.json() == {"queued": True}
    await run_jobs()


async def test_huge_images_are_refused_not_decoded():
    import cv2
    import numpy as np
    import pytest
    from fexor_core.face import decode_image

    ok, png = cv2.imencode(".png", np.zeros((8000, 8000), np.uint8))
    assert ok
    with pytest.raises(ValueError):
        decode_image(png.tobytes())


# ---------- consent ----------
async def test_face_photos_need_consent(admin, face_jpeg):
    s = await make_space(admin)
    sid = s["id"]
    m = await make_member(admin, sid, "C1", consent=False)
    url = f"/api/spaces/{sid}/members/{m['id']}/faces"
    r = await admin.post(url, files=[("files", ("a.jpg", face_jpeg, "image/jpeg"))])
    assert r.status_code == 409 and "consent" in r.json()["detail"].lower()

    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("C1.jpg", face_jpeg)
    r = await admin.post(f"/api/spaces/{sid}/faces/bulk",
                         files={"file": ("p.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200 and r.json()[0]["error"] == "no_consent"

    r = await admin.patch(f"/api/spaces/{sid}/members/{m['id']}", json={"consent": True})
    first = r.json()["consent_at"]
    assert first
    r = await admin.patch(f"/api/spaces/{sid}/members/{m['id']}",
                          json={"consent": True, "title": "x"})
    assert r.json()["consent_at"] == first  # re-saving doesn't move the consent date
    r = await admin.post(url, files=[("files", ("a.jpg", face_jpeg, "image/jpeg"))])
    assert r.status_code == 200 and r.json()[0]["ok"]


async def test_saving_a_person_after_the_field_list_changed(admin):
    s = await make_space(admin)
    sid = s["id"]
    await admin.patch(f"/api/spaces/{sid}", json={"field_schema": [
        {"key": "house", "label": "House", "type": "select", "options": ["Red", "Blue"]},
        {"key": "old", "label": "Old", "type": "text"}]})
    m = await make_member(admin, sid, fields={"house": "Red", "old": "x"})
    await admin.patch(f"/api/spaces/{sid}", json={"field_schema": [
        {"key": "house", "label": "House", "type": "select", "options": ["Green"]}]})
    r = await admin.patch(f"/api/spaces/{sid}/members/{m['id']}",
                          json={"full_name": "Renamed", "fields": m["fields"]})
    assert r.status_code == 200, r.text
    assert r.json()["fields"] == {"house": "Red"}  # unchanged value kept, removed field dropped
    r = await admin.patch(f"/api/spaces/{sid}/members/{m['id']}", json={"fields": {"nope": 1}})
    assert r.status_code == 422


# ---------- webhooks ----------
async def test_webhook_retries_keep_one_event_id_and_are_signed(admin, monkeypatch):
    import hashlib
    import hmac
    import json

    import httpx
    from fexor_server import worker

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500 if len(seen) == 1 else 200)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(worker.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    r = await admin.post("/api/webhooks", json={"url": "https://hooks.example.com/x"})
    hook = r.json()
    try:
        await admin.post(f"/api/webhooks/{hook['id']}/test")
        payload = {"webhook_id": hook["id"], "type": "ping", "data": {}, "event_id": "evt-1"}
        from fexor_server.db import sessionmaker

        async with sessionmaker()() as db:
            for _ in range(2):
                try:
                    await worker.handle_webhook(db, payload)
                except RuntimeError:
                    pass
        ids = [json.loads(q.content)["id"] for q in seen[-2:]]
        assert ids == ["evt-1", "evt-1"]
        t, v1 = (kv.split("=", 1)[1] for kv in seen[-1].headers["x-fexor-signature"].split(","))
        expect = hmac.new(hook["secret"].encode(), f"{t}.{seen[-1].content.decode()}".encode(),
                          hashlib.sha256).hexdigest()
        assert hmac.compare_digest(v1, expect)
    finally:
        await admin.delete(f"/api/webhooks/{hook['id']}")
        await run_jobs()


async def test_approving_a_review_notifies_webhooks(app, admin):
    from fexor_server.db import sessionmaker
    from fexor_server.models import Job
    from sqlalchemy import select

    s = await make_space(admin)
    m = await make_member(admin, s["id"])
    dev = await make_device(app, admin, s["id"])
    hook = (await admin.post("/api/webhooks", json={"url": "https://hooks.example.com/y",
                                                    "space_id": s["id"]})).json()
    pend = _ev(m["id"], f"{MON}T18:00:00Z", status="pending", reason="manual_review_requested")
    await dev.post("/api/device/events", json={"events": [pend]})
    await admin.post(f"/api/spaces/{s['id']}/events/{pend['id']}/review",
                     json={"decision": "approve"})
    async with sessionmaker()() as db:
        jobs = (await db.execute(select(Job).where(Job.kind == "webhook"))).scalars().all()
    statuses = [j.payload["data"]["status"] for j in jobs
                if j.payload["webhook_id"] == hook["id"]]
    assert statuses == ["pending", "accepted"]
    await admin.delete(f"/api/webhooks/{hook['id']}")
    from fexor_server.db import sessionmaker as sm

    async with sm()() as db:
        for j in (await db.execute(select(Job).where(Job.done_at.is_(None)))).scalars():
            j.done_at = j.run_at
        await db.commit()


# ---------- health ----------
async def test_health_checks_the_database(app, admin, monkeypatch):
    from .conftest import make_client

    c = make_client(app)
    assert (await c.get("/api/health")).json() == {"ok": True, "db": True}


async def test_worker_writes_a_heartbeat(tmp_path, monkeypatch):
    from fexor_server import worker

    monkeypatch.setattr(worker, "HEARTBEAT", tmp_path / "hb")
    await worker.tick(worker.WorkerState())
    assert (tmp_path / "hb").exists()


# ---------- endpoints the admin panel needs ----------
async def test_lookup_by_ids_and_per_person_lists(admin):
    s = await make_space(admin)
    sid = s["id"]
    a = await make_member(admin, sid, start_date=str(MON - timedelta(days=30)))
    b = await make_member(admin, sid, start_date=str(MON - timedelta(days=30)))
    c = await make_member(admin, sid, start_date=str(MON - timedelta(days=30)))
    r = await admin.get(f"/api/spaces/{sid}/members?ids={a['id']},{c['id']}")
    assert sorted(x["id"] for x in r.json()["items"]) == sorted([a["id"], c["id"]])

    await admin.post(f"/api/spaces/{sid}/recompute", json={"start": str(MON), "end": str(MON)})
    await run_jobs()
    rows = await _days(admin, sid, MON, MON, member_ids=f"{a['id']},{b['id']}")
    assert sorted(d["member_id"] for d in rows) == sorted([a["id"], b["id"]])

    await admin.post(f"/api/spaces/{sid}/leaves", json={
        "member_id": a["id"], "start_date": str(MON), "end_date": str(MON)})
    await admin.post(f"/api/spaces/{sid}/leaves", json={
        "member_id": b["id"], "start_date": str(MON), "end_date": str(MON)})
    lv = (await admin.get(f"/api/spaces/{sid}/leaves?member_id={a['id']}")).json()
    assert [x["member_id"] for x in lv] == [a["id"]]


async def test_undo_a_manual_check_in(admin):
    s = await make_space(admin)
    sid = s["id"]
    m = await make_member(admin, sid, start_date=str(MON - timedelta(days=30)))
    r = await admin.post(f"/api/spaces/{sid}/events", json={
        "member_id": m["id"], "occurred_at": f"{MON}T09:00:00Z", "note": "typo test"})
    eid = r.json()["id"]
    assert (await _days(admin, sid, MON, MON))[0]["status"] == "present"
    r = await admin.delete(f"/api/spaces/{sid}/events/{eid}")
    assert r.status_code == 200
    assert (await _days(admin, sid, MON, MON))[0]["status"] == "absent"
    audit = (await admin.get(f"/api/audit?space_id={sid}")).json()
    assert any(x["action"] == "event.deleted" for x in audit)


async def test_kiosk_events_cannot_be_deleted(app, admin):
    s = await make_space(admin)
    m = await make_member(admin, s["id"])
    dev = await make_device(app, admin, s["id"])
    e = _ev(m["id"], f"{MON}T09:00:00Z")
    await dev.post("/api/device/events", json={"events": [e]})
    r = await admin.delete(f"/api/spaces/{s['id']}/events/{e['id']}")
    assert r.status_code == 409


async def test_remove_a_shift_assignment(admin):
    s = await make_space(admin)
    sid = s["id"]
    m = await make_member(admin, sid)
    await admin.post(f"/api/spaces/{sid}/shift-assignments", json={
        "member_ids": [m["id"]], "shift_id": s["default_shift_id"], "valid_from": str(MON)})
    rows = (await admin.get(f"/api/spaces/{sid}/members/{m['id']}/shift-assignments")).json()
    r = await admin.delete(f"/api/spaces/{sid}/shift-assignments/{rows[0]['id']}")
    assert r.status_code == 200
    assert (await admin.get(f"/api/spaces/{sid}/members/{m['id']}/shift-assignments")).json() == []
    await run_jobs()


async def test_rename_and_remove_kiosks(app, admin):
    s = await make_space(admin)
    sid = s["id"]
    await make_device(app, admin, sid)
    did = (await admin.get(f"/api/spaces/{sid}/devices")).json()[0]["id"]
    r = await admin.patch(f"/api/spaces/{sid}/devices/{did}", json={"name": "Gate 2"})
    assert r.status_code == 200 and r.json()["name"] == "Gate 2"
    # Only a disconnected kiosk can be removed from the list.
    assert (await admin.delete(f"/api/spaces/{sid}/devices/{did}?remove=true")).status_code == 409
    await admin.delete(f"/api/spaces/{sid}/devices/{did}")
    assert (await admin.delete(f"/api/spaces/{sid}/devices/{did}?remove=true")).status_code == 200
    assert (await admin.get(f"/api/spaces/{sid}/devices")).json() == []
