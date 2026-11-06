import base64
import io
import uuid

import numpy as np
from fexor_core import qr
from openpyxl import load_workbook

from .conftest import make_client

MON = "2026-11-02"


async def _space(admin, name, template="office", tz="UTC"):
    r = await admin.post("/api/spaces", json={"name": name, "template": template, "timezone": tz})
    assert r.status_code == 201, r.text
    return r.json()


async def _member(admin, sid, ext, name="Jane Doe"):
    r = await admin.post(f"/api/spaces/{sid}/members",
                         json={"full_name": name, "external_id": ext, "title": "Engineer",
                               "fields": {"department": "R&D"}})
    assert r.status_code == 201, r.text
    return r.json()


async def _device(admin, app, sid):
    r = await admin.post(f"/api/spaces/{sid}/devices", json={"name": "Lobby kiosk"})
    assert r.status_code == 201, r.text
    code = r.json()["enroll_code"]
    dev = make_client(app)
    r = await dev.post("/api/device/enroll", json={"code": code.lower().replace("-", " ")})
    assert r.status_code == 200, r.text
    dev.headers["Authorization"] = f"Bearer {r.json()['token']}"
    # Code is single-use.
    r2 = await make_client(app).post("/api/device/enroll", json={"code": code})
    assert r2.status_code == 401
    return dev


def _ev(mid, at, status="accepted", **kw):
    return {"id": str(uuid.uuid4()), "member_id": mid, "occurred_at": at, "status": status, **kw}


async def test_auth_basics(app, admin):
    anon = make_client(app)
    assert (await anon.get("/api/spaces")).status_code == 401
    r = await anon.post("/api/auth/login", json={"email": "root@example.com", "password": "nope"})
    assert r.status_code == 401
    # Missing CSRF header on a write is refused even with a valid session.
    c = make_client(app)
    await c.post("/api/auth/login", json={"email": "root@example.com",
                                         "password": "correct-horse-battery"})
    r = await c.post("/api/spaces", json={"name": "x"})
    assert r.status_code == 403 and "csrf" in r.text


async def test_space_template_creates_shift(admin):
    s = await _space(admin, "School morning", "school", "Asia/Kolkata")
    assert s["mode"] == "single" and s["member_label"] == "Student"
    shifts = (await admin.get(f"/api/spaces/{s['id']}/shifts")).json()
    assert len(shifts) == 1 and shifts[0]["id"] == s["default_shift_id"]


async def test_rbac_viewer_and_outsider(app, admin):
    s1 = await _space(admin, "Office A")
    s2 = await _space(admin, "Office B")
    r = await admin.post("/api/users", json={"email": "viewer@example.com", "name": "V",
                                             "password": "viewer-password-1"})
    uid = r.json()["id"]
    await admin.put(f"/api/users/{uid}/roles", json=[{"space_id": s1["id"], "role": "viewer"}])
    v = make_client(app)
    await v.post("/api/auth/login", json={"email": "viewer@example.com",
                                         "password": "viewer-password-1"})
    v.headers["X-CSRF-Token"] = (await v.get("/api/auth/me")).json()["csrf"]
    assert (await v.get(f"/api/spaces/{s1['id']}/members")).status_code == 200
    r = await v.post(f"/api/spaces/{s1['id']}/members", json={"full_name": "a", "external_id": "1"})
    assert r.status_code == 403
    assert (await v.get(f"/api/spaces/{s2['id']}/members")).status_code == 404
    assert [s["id"] for s in (await v.get("/api/spaces")).json()] == [s1["id"]]
    assert (await v.get("/api/users")).status_code == 403


async def test_member_validation_and_import(admin):
    s = await _space(admin, "Import office")
    sid = s["id"]
    await _member(admin, sid, "E1")
    r = await admin.post(f"/api/spaces/{sid}/members",
                         json={"full_name": "Dup", "external_id": "E1"})
    assert r.status_code == 409
    r = await admin.post(f"/api/spaces/{sid}/members",
                         json={"full_name": "X", "external_id": "E9", "fields": {"nope": 1}})
    assert r.status_code == 422

    csv_data = (
        "Name,Employee ID,Designation,Department\n"
        "Amy,E2,Dev,Ops\nBob,E3,QA,Ops\n,E4,,\nAmy2,E2,,\nJane Updated,E1,Lead,R&D\n"
    )
    files = {"file": ("people.csv", csv_data.encode(), "text/csv")}
    r = await admin.post(f"/api/spaces/{sid}/import?dry_run=true", files=files)
    rep = r.json()
    assert (rep["created"], rep["updated"], len(rep["errors"])) == (2, 1, 2), rep
    assert (await admin.get(f"/api/spaces/{sid}/members")).json()["total"] == 1  # dry run
    r = await admin.post(f"/api/spaces/{sid}/import?dry_run=false", files=files)
    assert r.json()["created"] == 2
    members = (await admin.get(f"/api/spaces/{sid}/members?q=E")).json()
    assert members["total"] == 3
    jane = next(m for m in members["items"] if m["external_id"] == "E1")
    assert jane["full_name"] == "Jane Updated" and jane["title"] == "Lead"
    r = await admin.get(f"/api/spaces/{sid}/import/template.xlsx")
    assert r.status_code == 200 and load_workbook(io.BytesIO(r.content)).active["A1"].value


async def test_face_enrollment_and_qr(admin, face_jpeg):
    s = await _space(admin, "Faces office")
    sid = s["id"]
    m = await _member(admin, sid, "F1")
    url = f"/api/spaces/{sid}/members/{m['id']}"
    r = await admin.post(f"{url}/faces", files=[("files", ("lena.jpg", face_jpeg, "image/jpeg")),
                                               ("files", ("bad.jpg", b"notimage", "image/jpeg"))])
    res = r.json()
    assert res[0]["ok"] and not res[1]["ok"], res
    faces = (await admin.get(f"{url}/faces")).json()
    assert len(faces) == 1 and faces[0]["has_photo"]
    photo = await admin.get(f"{url}/faces/{faces[0]['id']}/photo")
    assert photo.headers["content-type"] == "image/jpeg"
    svg = await admin.get(f"{url}/qr.svg")
    assert svg.status_code == 200 and b"xmlns=\"http://www.w3.org/2000/svg\"" in svg.content
    png = await admin.get(f"{url}/qr.png")
    assert png.content[1:4] == b"PNG" and "attachment" in png.headers["content-disposition"]
    old = (await admin.get(f"{url}")).json()
    assert old["has_qr"] and old["face_count"] == 1


async def test_device_sync_events_days_and_reports(app, admin, face_jpeg):
    s = await _space(admin, "Kiosk office")
    sid = s["id"]
    m = await _member(admin, sid, "K1", "Kiran")
    m2 = await _member(admin, sid, "K2", "Lee")
    await admin.post(f"/api/spaces/{sid}/members/{m['id']}/faces",
                     files=[("files", ("a.jpg", face_jpeg, "image/jpeg"))])
    await admin.post(f"/api/spaces/{sid}/members/{m['id']}/qr")
    dev = await _device(admin, app, sid)

    r = await dev.get("/api/device/sync")
    assert r.status_code == 200
    roster = r.json()
    etag = r.headers["etag"]
    k1 = next(x for x in roster["members"] if x["external_id"] == "K1")
    assert len(k1["templates"]) == 1 and k1["credential_id"]
    emb = np.frombuffer(base64.b64decode(k1["templates"][0]), "<f4")
    assert emb.shape == (128,) and abs(np.linalg.norm(emb) - 1) < 1e-3
    # Kiosk can verify printed QR offline with synced public keys.
    svg_token_cred = k1["credential_id"]
    keys = {k: qr.public_key_from_str(v) for k, v in roster["keys"].items()}
    assert keys and svg_token_cred
    r = await dev.get("/api/device/sync", headers={"If-None-Match": etag})
    assert r.status_code == 304

    evs = [_ev(m["id"], f"{MON}T09:05:00Z"), _ev(m["id"], f"{MON}T18:10:00Z"),
           _ev(m2["id"], f"{MON}T09:40:00Z"),
           _ev(m2["id"], f"{MON}T09:41:00Z", status="rejected", reason="face_mismatch",
               scores={"match": 0.2}),
           _ev(str(uuid.uuid4()), f"{MON}T10:00:00Z")]  # member from nowhere
    r = await dev.post("/api/device/events", json={"events": evs})
    assert r.status_code == 200 and len(r.json()["stored"]) == 5
    r = await dev.post("/api/device/events", json={"events": evs})  # idempotent replay
    assert r.status_code == 200
    all_ev = (await admin.get(f"/api/spaces/{sid}/events?start={MON}&end={MON}")).json()
    assert len(all_ev) == 5
    assert sum(e["status"] == "rejected" for e in all_ev) == 2  # incl. unknown member

    days = {d["external_id"]: d for d in
            (await admin.get(f"/api/spaces/{sid}/days?start={MON}&end={MON}")).json()}
    assert days["K1"]["status"] == "present" and days["K1"]["worked_minutes"] == 545
    assert days["K2"]["status"] == "late" and days["K2"]["late_minutes"] == 40
    assert "missing_out" in days["K2"]["flags"]

    # Pending event -> review -> counted.
    pend = _ev(m2["id"], f"{MON}T18:00:00Z", status="pending", reason="manual_review_requested")
    await dev.post("/api/device/events", json={"events": [pend]})
    q = (await admin.get(f"/api/spaces/{sid}/review")).json()
    assert [e["id"] for e in q] == [pend["id"]]
    r = await admin.post(f"/api/spaces/{sid}/events/{pend['id']}/review",
                         json={"decision": "approve", "note": "seen on CCTV"})
    assert r.json()["status"] == "accepted"
    d2 = (await admin.get(f"/api/spaces/{sid}/days?start={MON}&end={MON}&member_id={m2['id']}")
          ).json()[0]
    assert d2["last_out"] is not None and "missing_out" not in d2["flags"]

    dash = (await admin.get(f"/api/spaces/{sid}/dashboard?day={MON}")).json()
    assert dash["present"] == 2 and dash["failure_reasons"]["face_mismatch"] == 1
    assert dash["devices"]["total"] == 1

    x = await admin.get(f"/api/spaces/{sid}/reports/attendance.xlsx?start={MON}&end={MON}")
    wb = load_workbook(io.BytesIO(x.content))
    assert wb["Summary"].max_row == 4 and wb["Daily"].max_row == 3
    c = await admin.get(f"/api/spaces/{sid}/reports/attendance.csv?start={MON}&end={MON}")
    assert c.text.count("\n") == 3

    # Revoked device is locked out.
    dev_id = (await admin.get(f"/api/spaces/{sid}/devices")).json()[0]["id"]
    await admin.delete(f"/api/spaces/{sid}/devices/{dev_id}")
    assert (await dev.get("/api/device/sync")).status_code == 401


async def test_holiday_leave_and_manual_entry(admin):
    s = await _space(admin, "Holiday office")
    sid = s["id"]
    m = await _member(admin, sid, "H1")
    await admin.patch(f"/api/spaces/{sid}/members/{m['id']}", json={"start_date": "2026-10-01"})
    tue, wed = "2026-11-03", "2026-11-04"
    await admin.post(f"/api/spaces/{sid}/holidays", json={"day": tue, "name": "Founders day"})
    await admin.post(f"/api/spaces/{sid}/leaves", json={"member_id": m["id"], "start_date": wed,
                                                         "end_date": wed})
    r = await admin.post(f"/api/spaces/{sid}/events", json={
        "member_id": m["id"], "occurred_at": f"{MON}T09:00:00Z", "note": "badge reader down"})
    assert r.status_code == 201
    r = await admin.post(f"/api/spaces/{sid}/recompute", json={"start": MON, "end": "2026-11-05"})
    assert r.status_code == 200
    days = {d["day"]: d["status"] for d in
            (await admin.get(f"/api/spaces/{sid}/days?start={MON}&end=2026-11-05")).json()}
    assert days == {MON: "present", tue: "holiday", wed: "leave", "2026-11-05": "absent"}
    r = await admin.post(f"/api/spaces/{sid}/holidays/preset", json={"country": "IN",
                                                                      "year": 2026})
    assert r.json()["imported"] > 0
    audit = (await admin.get(f"/api/audit?space_id={sid}")).json()
    assert any(a["action"] == "event.manual" for a in audit)


async def test_delete_space_requires_confirmation(admin):
    s = await _space(admin, "Temp space")
    assert (await admin.delete(f"/api/spaces/{s['id']}?confirm=wrong")).status_code == 400
    assert (await admin.delete(f"/api/spaces/{s['id']}?confirm=Temp space")).status_code == 200
    assert (await admin.get(f"/api/spaces/{s['id']}")).status_code == 404


async def test_worker_runs_recompute_and_retries_webhooks(admin):
    from fexor_server.db import sessionmaker
    from fexor_server.models import Job
    from fexor_server.worker import run_one
    from sqlalchemy import select

    s = await _space(admin, "Worker office")
    await admin.post(f"/api/spaces/{s['id']}/holidays", json={"day": MON, "name": "x"})
    r = await admin.post("/api/webhooks", json={"url": "http://localhost:9/hook"})
    hook = r.json()
    assert hook["secret"].startswith("whsec_")
    await admin.post(f"/api/webhooks/{hook['id']}/test")
    while await run_one():
        pass
    async with sessionmaker()() as db:
        jobs = (await db.execute(select(Job))).scalars().all()
    rec = [j for j in jobs if j.kind == "recompute_space"]
    assert rec and all(j.done_at for j in rec)
    wh = [j for j in jobs if j.kind == "webhook"][-1]
    assert wh.done_at is None and wh.attempts == 1 and wh.last_error  # scheduled for retry
    await admin.delete(f"/api/webhooks/{hook['id']}")
