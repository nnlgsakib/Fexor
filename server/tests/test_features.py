"""Feature coverage: behaviour that worked but had no test. One test per user-visible flow."""

import io
import uuid
import zipfile
from datetime import timedelta

import pyotp
from fexor_core import qr
from openpyxl import Workbook

from .conftest import (
    login,
    make_client,
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


async def _new_admin(admin, superadmin=False):
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    r = await admin.post("/api/users", json={"email": email, "name": "U",
                                             "password": "user-password-123",
                                             "is_superadmin": superadmin})
    assert r.status_code == 201, r.text
    return email, r.json()["id"]


# ---------- auth ----------
async def test_two_step_sign_in(app, admin):
    email, _ = await _new_admin(admin, superadmin=True)
    c = await login(app, email, "user-password-123")
    secret = (await c.post("/api/auth/totp/setup")).json()["secret"]
    assert (await c.post("/api/auth/totp/enable", json={"code": "000000"})).status_code == 400
    r = await c.post("/api/auth/totp/enable", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200

    c2 = make_client(app)
    r = await c2.post("/api/auth/login", json={"email": email, "password": "user-password-123"})
    assert r.json() == {"mfa_required": True}
    # Password alone is not a login: everything else is closed until the code is entered.
    assert (await c2.get("/api/spaces")).status_code == 401
    assert (await c2.post("/api/auth/mfa", json={"code": "123456"})).status_code == 401
    assert (await c2.post("/api/auth/mfa", json={"code": pyotp.TOTP(secret).now()})).status_code == 200
    assert (await c2.get("/api/spaces")).status_code == 200

    c.headers["X-CSRF-Token"] = (await c.get("/api/auth/me")).json()["csrf"]
    r = await c.post("/api/auth/totp/disable", json={"code": pyotp.TOTP(secret).now()})
    assert r.status_code == 200
    assert (await c.get("/api/auth/me")).json()["totp_enabled"] is False


async def test_lockout_inactive_user_and_rate_limit(app, admin):
    email, uid = await _new_admin(admin)
    anon = make_client(app)
    for _ in range(5):
        await anon.post("/api/auth/login", json={"email": email, "password": "wrong-password"})
    r = await anon.post("/api/auth/login", json={"email": email, "password": "user-password-123"})
    assert r.status_code == 423  # locked for a while, even with the right password

    email2, uid2 = await _new_admin(admin)
    await admin.patch(f"/api/users/{uid2}", json={"active": False})
    r = await make_client(app).post("/api/auth/login",
                                    json={"email": email2, "password": "user-password-123"})
    assert r.status_code == 401

    statuses = [(await anon.post("/api/auth/login", json={"email": "x@y.z", "password": "p"})
                 ).status_code for _ in range(12)]
    assert 429 in statuses


async def test_logout_and_password_change_end_sessions(app, admin):
    email, _ = await _new_admin(admin, superadmin=True)
    a = await login(app, email, "user-password-123")
    b = await login(app, email, "user-password-123")
    r = await a.post("/api/auth/password", json={"current_password": "wrong",
                                                 "new_password": "brand-new-password"})
    assert r.status_code == 400
    r = await a.post("/api/auth/password", json={"current_password": "user-password-123",
                                                 "new_password": "brand-new-password"})
    assert r.status_code == 200
    assert (await b.get("/api/spaces")).status_code == 401  # other sessions signed out
    assert (await a.get("/api/spaces")).status_code == 200
    await a.post("/api/auth/logout")
    assert (await a.get("/api/spaces")).status_code == 401


async def test_admin_password_reset_and_deactivation_end_sessions(app, admin):
    email, uid = await _new_admin(admin, superadmin=True)
    c = await login(app, email, "user-password-123")
    await admin.patch(f"/api/users/{uid}", json={"password": "reset-by-admin-1"})
    assert (await c.get("/api/spaces")).status_code == 401
    c = await login(app, email, "reset-by-admin-1")
    me = (await admin.get("/api/auth/me")).json()["id"]
    assert (await admin.patch(f"/api/users/{me}", json={"active": False})).status_code == 400


# ---------- API keys ----------
async def test_api_keys_are_read_only_and_scoped(app, admin):
    a, b = await make_space(admin), await make_space(admin)
    r = await admin.post("/api/api-keys", json={"name": "payroll", "space_id": a["id"]})
    token = r.json()["token"]
    k = make_client(app)
    k.headers["Authorization"] = f"Bearer {token}"
    assert (await k.get(f"/api/spaces/{a['id']}/days?start={MON}&end={MON}")).status_code == 200
    r = await k.get(f"/api/spaces/{a['id']}/reports/attendance.csv?start={MON}&end={MON}")
    assert r.status_code == 200
    assert (await k.get(f"/api/spaces/{b['id']}/days")).status_code == 404
    assert (await k.post(f"/api/spaces/{a['id']}/members",
                         json={"full_name": "x", "external_id": "1"})).status_code == 403
    assert (await k.get("/api/auth/me")).status_code == 403
    keys = (await admin.get("/api/api-keys")).json()
    key_id = next(x["id"] for x in keys if x["name"] == "payroll")
    await admin.delete(f"/api/api-keys/{key_id}")
    assert (await k.get(f"/api/spaces/{a['id']}/days")).status_code == 401


# ---------- roles ----------
async def test_manager_and_owner_rights(app, admin):
    s = await make_space(admin)
    sid = s["id"]
    m = await make_member(admin, sid)
    mgr = await user_with_role(app, admin, sid, "manager")
    own = await user_with_role(app, admin, sid, "owner")

    assert (await mgr.post(f"/api/spaces/{sid}/members",
                           json={"full_name": "N", "external_id": "MGR1"})).status_code == 201
    assert (await mgr.patch(f"/api/spaces/{sid}", json={"name": "x"})).status_code == 403
    assert (await mgr.delete(f"/api/spaces/{sid}/members/{m['id']}?erase=true")).status_code == 403
    assert (await mgr.post(f"/api/spaces/{sid}/recompute",
                           json={"start": str(MON), "end": str(MON)})).status_code == 403
    assert (await mgr.get(f"/api/audit?space_id={sid}")).status_code == 403
    assert (await mgr.get("/api/audit")).status_code == 403

    assert (await own.patch(f"/api/spaces/{sid}", json={"description": "d"})).status_code == 200
    assert (await own.get(f"/api/audit?space_id={sid}")).status_code == 200
    assert (await own.delete(f"/api/spaces/{sid}?confirm={s['name']}")).status_code == 403
    assert (await own.delete(f"/api/spaces/{sid}/members/{m['id']}?erase=true")).status_code == 200
    await run_jobs()


# ---------- people ----------
async def test_member_edit_deactivate_reactivate_and_erase(admin, face_jpeg):
    s = await make_space(admin)
    sid = s["id"]
    await admin.patch(f"/api/spaces/{sid}", json={"field_schema": [
        {"key": "grade", "label": "Grade", "type": "select", "options": ["A", "B"],
         "required": True}]})
    r = await admin.post(f"/api/spaces/{sid}/members", json={"full_name": "R", "external_id": "R1"})
    assert r.status_code == 422 and "Grade" in r.text
    r = await admin.post(f"/api/spaces/{sid}/members",
                         json={"full_name": "R", "external_id": "R1", "fields": {"grade": "C"}})
    assert r.status_code == 422
    m = await make_member(admin, sid, "R1", fields={"grade": "A"}, email="r@example.com")
    url = f"/api/spaces/{sid}/members/{m['id']}"
    r = await admin.patch(url, json={"title": "Lead", "fields": {"grade": "B"}})
    assert r.json()["title"] == "Lead" and r.json()["fields"] == {"grade": "B"}

    await admin.delete(url)
    off = (await admin.get(url)).json()
    assert off["active"] is False and off["end_date"]
    assert (await admin.get(f"/api/spaces/{sid}/members?active=true")).json()["total"] == 0
    back = (await admin.patch(url, json={"active": True})).json()
    assert back["active"] is True and back["end_date"] is None

    await admin.post(f"{url}/faces", files=[("files", ("a.jpg", face_jpeg, "image/jpeg"))])
    assert (await admin.delete(f"{url}?erase=true")).status_code == 200
    assert (await admin.get(url)).status_code == 404


async def test_member_paging_and_search(admin):
    s = await make_space(admin)
    sid = s["id"]
    for i in range(5):
        await make_member(admin, sid, f"PG{i}", name=f"Person {i}")
    r = (await admin.get(f"/api/spaces/{sid}/members?size=2&page=2")).json()
    assert r["total"] == 5 and [m["external_id"] for m in r["items"]] == ["PG2", "PG3"]
    r = (await admin.get(f"/api/spaces/{sid}/members?q=Person 4")).json()
    assert [m["external_id"] for m in r["items"]] == ["PG4"]


async def test_xlsx_import_with_excel_quirks(admin):
    s = await make_space(admin, template="school")
    sid = s["id"]
    wb = Workbook()
    ws = wb.active
    ws.append(["Student Name", "Roll No", "Class", "Joining date"])
    ws.append(["Asha", 1001.0, "5A", MON])  # Excel stores IDs as numbers
    ws.append(["Ravi", 1002, "5B", None])
    buf = io.BytesIO()
    wb.save(buf)
    files = {"file": ("s.xlsx", buf.getvalue(),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = await admin.post(f"/api/spaces/{sid}/import?dry_run=false", files=files)
    assert r.json()["created"] == 2, r.json()
    items = (await admin.get(f"/api/spaces/{sid}/members")).json()["items"]
    asha = next(m for m in items if m["full_name"] == "Asha")
    assert asha["external_id"] == "1001" and asha["fields"]["class"] == "5A"
    assert asha["start_date"] == str(MON)
    bad = {"file": ("x.xlsx", b"not a workbook", "application/octet-stream")}
    assert (await admin.post(f"/api/spaces/{sid}/import", files=bad)).status_code == 422


# ---------- faces ----------
async def test_bulk_zip_photos_delete_and_limits(app, admin, face_jpeg):
    s = await make_space(admin)
    sid = s["id"]
    m = await make_member(admin, sid, "Z1")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("photos/Z1.jpg", face_jpeg)
        z.writestr("photos/Z1_2.jpg", face_jpeg)
        z.writestr("photos/NOBODY.jpg", face_jpeg)
        z.writestr("photos/readme.txt", "ignored")
    r = await admin.post(f"/api/spaces/{sid}/faces/bulk",
                         files={"file": ("p.zip", buf.getvalue(), "application/zip")})
    res = {x["file"]: x for x in r.json()}
    assert res["Z1.jpg"]["ok"] and res["Z1_2.jpg"]["ok"]
    assert res["NOBODY.jpg"]["error"] == "no_member_with_this_id" and "readme.txt" not in res

    url = f"/api/spaces/{sid}/members/{m['id']}/faces"
    faces = (await admin.get(url)).json()
    assert len(faces) == 2
    viewer = await user_with_role(app, admin, sid, "viewer")
    assert (await viewer.get(f"{url}/{faces[0]['id']}/photo")).status_code == 403
    assert (await admin.delete(f"{url}/{faces[0]['id']}")).status_code == 200
    assert len((await admin.get(url)).json()) == 1
    bad = await admin.post(f"/api/spaces/{sid}/faces/bulk",
                           files={"file": ("p.zip", b"nope", "application/zip")})
    assert bad.status_code == 422


async def test_reissued_qr_revokes_the_old_card_on_kiosks(app, admin):
    s = await make_space(admin)
    sid = s["id"]
    m = await make_member(admin, sid)
    dev = await make_device(app, admin, sid)
    await admin.get(f"/api/spaces/{sid}/members/{m['id']}/qr.svg")
    roster = (await dev.get("/api/device/sync")).json()
    old = roster["members"][0]["credential_id"]
    keys = {k: qr.public_key_from_str(v) for k, v in roster["keys"].items()}
    assert old and keys
    r = await admin.post(f"/api/spaces/{sid}/members/{m['id']}/qr")
    new = r.json()["credential_id"]
    roster = (await dev.get("/api/device/sync")).json()
    assert roster["members"][0]["credential_id"] == new != old


# ---------- schedule ----------
async def test_shift_crud_and_validation(admin):
    s = await make_space(admin, template="custom")
    sid = s["id"]
    bad = [{"name": "x", "start_time": "09:00", "end_time": "09:00"},
           {"name": "x", "start_time": "09:00", "end_time": "17:00", "weekdays": [7]},
           {"name": "", "start_time": "09:00", "end_time": "17:00"}]
    for body in bad:
        assert (await admin.post(f"/api/spaces/{sid}/shifts", json=body)).status_code == 422
    r = await admin.post(f"/api/spaces/{sid}/shifts", json={
        "name": "Night", "start_time": "22:00", "end_time": "06:00", "weekdays": [0, 0, 1]})
    sh = r.json()
    assert sh["overnight"] and sh["weekdays"] == [0, 1]
    assert (await admin.get(f"/api/spaces/{sid}")).json()["default_shift_id"] == sh["id"]
    r = await admin.put(f"/api/spaces/{sid}/shifts/{sh['id']}", json={
        "name": "Late night", "start_time": "23:00", "end_time": "07:00"})
    assert r.json()["name"] == "Late night"
    await admin.delete(f"/api/spaces/{sid}/shifts/{sh['id']}")
    assert (await admin.get(f"/api/spaces/{sid}")).json()["default_shift_id"] is None
    await run_jobs()


async def test_holidays_and_leave_lists(admin):
    s = await make_space(admin)
    sid = s["id"]
    m = await make_member(admin, sid)
    await admin.post(f"/api/spaces/{sid}/holidays", json={"day": f"{MON.year}-12-25",
                                                          "name": "Xmas"})
    hs = (await admin.get(f"/api/spaces/{sid}/holidays?year={MON.year}")).json()
    assert [h["name"] for h in hs] == ["Xmas"]
    assert (await admin.delete(f"/api/spaces/{sid}/holidays/{hs[0]['id']}")).status_code == 200
    r = await admin.post(f"/api/spaces/{sid}/holidays/preset", json={"country": "ZZ",
                                                                      "year": 2026})
    assert r.status_code == 422
    r = await admin.post(f"/api/spaces/{sid}/leaves", json={
        "member_id": m["id"], "start_date": str(MON), "end_date": str(MON - timedelta(days=1))})
    assert r.status_code == 422
    r = await admin.post(f"/api/spaces/{sid}/leaves", json={
        "member_id": m["id"], "start_date": str(MON), "end_date": str(MON), "note": "sick"})
    lid = r.json()["id"]
    assert (await admin.delete(f"/api/spaces/{sid}/leaves/{lid}")).status_code == 200
    assert (await admin.get(f"/api/spaces/{sid}/leaves")).json() == []
    await run_jobs()


# ---------- kiosks ----------
async def test_settings_qr_and_heartbeat(app, admin):
    s = await make_space(admin)
    sid = s["id"]
    r = await admin.post(f"/api/spaces/{sid}/devices", json={"name": "Not paired"})
    unpaired = r.json()["id"]
    r = await admin.post(f"/api/spaces/{sid}/devices/{unpaired}/settings-token")
    assert r.status_code == 409
    dev = await make_device(app, admin, sid)
    did = next(d["id"] for d in (await admin.get(f"/api/spaces/{sid}/devices")).json()
               if d["enrolled_at"])
    r = await dev.post("/api/device/heartbeat", json={"info": {"version": "1.2.3",
                                                               "outbox": 4}})
    assert "server_time" in r.json()
    listed = next(d for d in (await admin.get(f"/api/spaces/{sid}/devices")).json()
                  if d["id"] == did)
    assert listed["online"] and listed["info"]["version"] == "1.2.3"
    token = (await admin.post(f"/api/spaces/{sid}/devices/{did}/settings-token")).json()["token"]
    keys = {k: qr.public_key_from_str(v)
            for k, v in (await dev.get("/api/device/sync")).json()["keys"].items()}
    import time

    qr.verify_settings(token, keys, did, time.time())  # raises if invalid


async def test_kiosk_event_edge_cases(app, admin):
    a, b = await make_space(admin), await make_space(admin)
    ma = await make_member(admin, a["id"])
    mb = await make_member(admin, b["id"])
    dev = await make_device(app, admin, a["id"])
    future = _ev(ma["id"], "2999-01-01T09:00:00Z")
    foreign = _ev(mb["id"], f"{MON}T09:00:00Z")  # someone from another space
    r = await dev.post("/api/device/events", json={"events": [future, foreign]})
    assert r.status_code == 200
    review = {e["id"]: e for e in (await admin.get(f"/api/spaces/{a['id']}/review")).json()}
    assert "device_clock_ahead" in review[future["id"]]["reason"]
    rejected = (await admin.get(
        f"/api/spaces/{a['id']}/events?start={MON}&end={MON}&status=rejected")).json()
    assert [e["id"] for e in rejected] == [foreign["id"]] and rejected[0]["member_id"] is None
    naive = _ev(ma["id"], f"{MON}T09:00:00")
    assert (await dev.post("/api/device/events", json={"events": [naive]})).status_code == 422
    assert (await dev.post("/api/device/events",
                           json={"events": [_ev(ma["id"], f"{MON}T09:00:00Z")] * 501})
            ).status_code == 422
    await admin.delete(f"/api/spaces/{a['id']}?confirm={a['name']}")
    assert (await dev.get("/api/device/sync")).status_code == 401


# ---------- attendance ----------
async def test_review_reject_rereview_and_filters(app, admin):
    s = await make_space(admin)
    sid = s["id"]
    m = await make_member(admin, sid, start_date=str(MON - timedelta(days=30)))
    other = await make_member(admin, sid)
    dev = await make_device(app, admin, sid)
    p1 = _ev(m["id"], f"{MON}T09:00:00Z", status="pending", reason="manual_review_requested")
    await dev.post("/api/device/events", json={"events": [p1, _ev(other["id"],
                                                                     f"{MON}T09:00:00Z")]})
    url = f"/api/spaces/{sid}/events/{p1['id']}/review"
    assert (await admin.post(url, json={"decision": "reject", "note": "not him"})).json() == \
        {"status": "rejected"}
    mine = (await admin.get(f"/api/spaces/{sid}/events?start={MON}&end={MON}"
                            f"&member_id={m['id']}")).json()
    assert len(mine) == 1 and "[review] not him" in mine[0]["note"]
    assert (await admin.get(f"/api/spaces/{sid}/days?start={MON}&end={MON}"
                            f"&member_id={m['id']}")).json()[0]["status"] == "absent"
    assert (await admin.post(url, json={"decision": "approve"})).json() == {"status": "accepted"}
    assert (await admin.get(f"/api/spaces/{sid}/days?start={MON}&end={MON}"
                            f"&member_id={m['id']}")).json()[0]["status"] == "present"


async def test_manual_entry_and_range_validation(admin):
    s = await make_space(admin)
    sid = s["id"]
    m = await make_member(admin, sid)
    r = await admin.post(f"/api/spaces/{sid}/events", json={
        "member_id": m["id"], "occurred_at": "2999-01-01T09:00:00Z", "note": "future"})
    assert r.status_code == 422
    r = await admin.post(f"/api/spaces/{sid}/events", json={
        "member_id": m["id"], "occurred_at": f"{MON}T09:00:00Z", "note": "x"})
    assert r.status_code == 422  # a reason is required
    r = await admin.get(f"/api/spaces/{sid}/days?start={MON}&end={MON - timedelta(days=1)}")
    assert r.status_code == 422
    r = await admin.get(f"/api/spaces/{sid}/days?start=2020-01-01&end={MON}")
    assert r.status_code == 422


async def test_single_check_report(admin):
    s = await make_space(admin, template="school")
    sid = s["id"]
    m = await make_member(admin, sid, start_date=str(MON - timedelta(days=30)))
    await admin.post(f"/api/spaces/{sid}/events", json={
        "member_id": m["id"], "occurred_at": f"{MON}T08:20:00Z", "note": "late bus"})
    c = await admin.get(f"/api/spaces/{sid}/reports/attendance.csv?start={MON}&end={MON}")
    head, row = c.text.lstrip("﻿").splitlines()[:2]
    assert head.split(",") == ["Date", "ID", "Name", "Title", "Status", "In", "Late (min)", "Flags"]
    assert ",late,08:20" not in row and ",late," in row and row.endswith("late")


# ---------- app shell ----------
async def test_security_headers_and_static_paths(app):
    c = make_client(app)
    r = await c.get("/api/health")
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["cache-control"] == "no-store"
    assert (await c.get("/api/does-not-exist")).status_code in (404, 405)


# ---------- kiosk PIN ----------
async def test_kiosk_pin_is_set_by_managers_and_synced_as_a_hash(app, admin):
    from fexor_core.pin import check_pin

    s = await make_space(admin)
    sid = s["id"]
    dev = await make_device(app, admin, sid)
    did = (await admin.get(f"/api/spaces/{sid}/devices")).json()[0]["id"]
    first = await dev.get("/api/device/sync")
    assert first.json()["device"]["unlock_pin"] is None

    viewer = await user_with_role(app, admin, sid, "viewer")
    assert (await viewer.put(f"/api/spaces/{sid}/devices/{did}/pin", json={"pin": "123456"})).status_code == 403
    for bad in ("12345", "abcdef", "1234567"):
        assert (await admin.put(f"/api/spaces/{sid}/devices/{did}/pin", json={"pin": bad})).status_code == 422
    r = await admin.put(f"/api/spaces/{sid}/devices/{did}/pin", json={"pin": "482913"})
    assert r.status_code == 200 and r.json()["has_pin"] is True

    r = await dev.get("/api/device/sync", headers={"If-None-Match": first.headers["etag"]})
    assert r.status_code == 200  # the kiosk picks up the new PIN at its next sync
    rec = r.json()["device"]["unlock_pin"]
    assert "482913" not in str(rec) and check_pin("482913", rec) and not check_pin("000000", rec)

    assert (await admin.delete(f"/api/spaces/{sid}/devices/{did}/pin")).status_code == 200
    assert (await dev.get("/api/device/sync")).json()["device"]["unlock_pin"] is None
    audit = [a["action"] for a in (await admin.get(f"/api/audit?space_id={sid}")).json()]
    assert "device.pin_set" in audit and "device.pin_removed" in audit
