"""Faces that change (beard, glasses, ageing): kiosks keep learning, safely. And the numbers an
admin needs to set the match threshold from real check-ins instead of guessing."""

import base64
import uuid
from datetime import timedelta

import numpy as np
import pytest
from sqlalchemy import update

from .conftest import make_device, make_member, make_space, recent_monday, user_with_role

MON = recent_monday()


def _unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


def _near(anchor: np.ndarray, cos: float, seed: int = 0) -> np.ndarray:
    """A unit vector with exactly this cosine similarity to `anchor`."""
    r = np.random.default_rng(seed).standard_normal(anchor.shape[0]).astype(np.float32)
    r = _unit(r - np.dot(r, anchor) * anchor)
    return _unit(cos * anchor + np.sqrt(1 - cos * cos) * r)


def _b64(v: np.ndarray) -> str:
    return base64.b64encode(np.asarray(v, "<f4").tobytes()).decode()


GOOD = {"match": 0.6, "liveness": 0.97, "spoof_low_frac": 0.0, "other_best": 0.2,
        "frames": 30, "actions": "blink,turn_left"}


@pytest.fixture
async def person(app, admin, face_jpeg):
    """Space + member with one enrolled photo + kiosk. Returns (sid, member, device, anchor)."""
    s = await make_space(admin)
    m = await make_member(admin, s["id"], start_date=str(MON - timedelta(days=30)))
    r = await admin.post(f"/api/spaces/{s['id']}/members/{m['id']}/faces",
                         files=[("files", ("a.jpg", face_jpeg, "image/jpeg"))])
    assert r.json()[0]["ok"], r.text
    dev = await make_device(app, admin, s["id"])
    roster = (await dev.get("/api/device/sync")).json()
    anchor = np.frombuffer(base64.b64decode(roster["members"][0]["templates"][0]), "<f4")
    return s["id"], m, dev, anchor


async def _check_in(dev, mid, probe, at=None, scores=None, status="accepted", reason="verified",
                    **extra):
    ev = {"id": str(uuid.uuid4()), "member_id": mid, "occurred_at": at or f"{MON}T09:00:00Z",
          "status": status, "reason": reason, "scores": {**GOOD, **(scores or {})},
          "embedding_b64": _b64(probe) if probe is not None else None, **extra}
    r = await dev.post("/api/device/events", json={"events": [ev]})
    assert r.status_code == 200, r.text
    return ev


async def _faces(admin, sid, mid):
    return (await admin.get(f"/api/spaces/{sid}/members/{mid}/faces")).json()


async def _age_auto_faces(days: int):
    """Move time on by `days` for the weekly limit: every template gets that much older (so
    their order stays as it was)."""
    from fexor_server.db import sessionmaker
    from fexor_server.models import FaceTemplate

    async with sessionmaker()() as db:
        await db.execute(update(FaceTemplate).values(
            created_at=FaceTemplate.created_at - timedelta(days=days)))
        await db.commit()


# ---------- learning ----------
async def test_a_confident_live_check_in_teaches_the_kiosks_a_new_look(admin, person):
    sid, m, dev, anchor = person
    etag = (await dev.get("/api/device/sync")).headers["etag"]
    probe = _near(anchor, 0.6)
    await _check_in(dev, m["id"], probe)
    faces = await _faces(admin, sid, m["id"])
    assert [f["source"] for f in faces] == ["enrolled", "auto"]
    assert faces[1]["has_photo"] is False  # only the numbers, no picture of the check-in
    r = await dev.get("/api/device/sync", headers={"If-None-Match": etag})
    assert r.status_code == 200
    learned = [np.frombuffer(base64.b64decode(t), "<f4") for t in r.json()["members"][0]["templates"]]
    assert len(learned) == 2 and np.allclose(learned[1], probe, atol=1e-6)
    log = (await admin.get("/api/audit", params={"space_id": sid})).json()
    assert any(e["action"] == "face.auto_added" for e in log), log
    # The event itself is stored as usual, without the face numbers.
    ev = (await admin.get(f"/api/spaces/{sid}/events?start={MON}&end={MON}")).json()
    assert ev[0]["status"] == "accepted" and "embedding_b64" not in ev[0]["scores"]


async def test_learning_is_at_most_weekly_and_replays_do_nothing(admin, person):
    sid, m, dev, anchor = person
    ev = await _check_in(dev, m["id"], _near(anchor, 0.6, seed=1))
    r = await dev.post("/api/device/events", json={"events": [ev]})  # kiosk resends
    assert r.status_code == 200
    await _check_in(dev, m["id"], _near(anchor, 0.6, seed=2), at=f"{MON}T18:00:00Z")
    assert [f["source"] for f in await _faces(admin, sid, m["id"])] == ["enrolled", "auto"]
    await _age_auto_faces(8)
    await _check_in(dev, m["id"], _near(anchor, 0.6, seed=3),
                    at=f"{MON + timedelta(days=1)}T09:00:00Z")
    assert [f["source"] for f in await _faces(admin, sid, m["id"])] == ["enrolled", "auto", "auto"]


async def test_only_a_few_learned_looks_are_kept_oldest_replaced(admin, person, monkeypatch):
    from fexor_server import services

    monkeypatch.setattr(services, "AUTO_FACES_MAX", 2)
    sid, m, dev, anchor = person
    for i in range(3):
        await _check_in(dev, m["id"], _near(anchor, 0.6, seed=10 + i),
                        at=f"{MON + timedelta(days=i)}T09:00:00Z")
        await _age_auto_faces(8)
    faces = await _faces(admin, sid, m["id"])
    assert [f["source"] for f in faces] == ["enrolled", "auto", "auto"]
    assert faces[0]["source"] == "enrolled"  # an admin's photo is never replaced


@pytest.mark.parametrize("why, kw", [
    ("weak match", {"probe_cos": 0.45}),
    ("almost the same as a stored photo", {"probe_cos": 0.9}),
    ("someone else is too close", {"scores": {"other_best": 0.55}}),
    ("no 1:N cross-check", {"scores": {"other_best": None}}),
    ("liveness not clear", {"scores": {"liveness": 0.7}}),
    ("some frames looked like a photo", {"scores": {"spoof_low_frac": 0.1}}),
    ("no movement check", {"scores": {"actions": ""}}),
    ("rejected", {"status": "rejected", "reason": "face_mismatch"}),
    ("waiting for review", {"status": "pending", "reason": "review_requested:face_mismatch"}),
    ("no face sent", {"probe": None}),
])
async def test_doubtful_check_ins_teach_nothing(admin, person, why, kw):
    sid, m, dev, anchor = person
    probe = kw.pop("probe", _near(anchor, kw.pop("probe_cos", 0.6)))
    await _check_in(dev, m["id"], probe, **kw)
    assert [f["source"] for f in await _faces(admin, sid, m["id"])] == ["enrolled"], why


async def test_a_check_in_held_by_the_shift_window_teaches_nothing(admin, person):
    sid, m, dev, anchor = person
    shift = (await admin.get(f"/api/spaces/{sid}/shifts")).json()[0]  # office: 09:00-18:00
    body = {k: shift[k] for k in ("name", "start_time", "end_time", "weekdays", "grace_minutes",
                                  "early_window_minutes", "late_window_minutes",
                                  "half_day_minutes")}
    r = await admin.put(f"/api/spaces/{sid}/shifts/{shift['id']}",
                        json={**body, "checkin_close_minutes": 30})
    assert r.status_code == 200, r.text
    await _check_in(dev, m["id"], _near(anchor, 0.6), at=f"{MON}T11:00:00Z")  # check-in closed
    ev = (await admin.get(f"/api/spaces/{sid}/events?start={MON}&end={MON}")).json()
    assert ev[0]["status"] == "pending" and ev[0]["reason"].startswith("checkin_closed")
    assert [f["source"] for f in await _faces(admin, sid, m["id"])] == ["enrolled"]


async def test_learning_never_builds_on_a_learned_look(admin, person):
    """Each new look must match the admin's photos, so no chain of small steps can walk a
    template toward someone else."""
    sid, m, dev, anchor = person
    first = _near(anchor, 0.6, seed=4)
    await _check_in(dev, m["id"], first)
    await _age_auto_faces(8)
    # Close to the learned look, far from the enrolled photo.
    drifted = _unit(0.75 * first + 0.66 * _near(first, 0.0, seed=5) - 0.2 * anchor)
    assert np.dot(drifted, first) > 0.6 and np.dot(drifted, anchor) < 0.42
    await _check_in(dev, m["id"], drifted, at=f"{MON + timedelta(days=1)}T09:00:00Z",
                    scores={"match": float(np.dot(drifted, first))})
    assert [f["source"] for f in await _faces(admin, sid, m["id"])] == ["enrolled", "auto"]


@pytest.mark.parametrize("bad", ["not base64!", base64.b64encode(b"\0" * 100).decode(),
                                 _b64(np.full(128, np.nan, np.float32)),
                                 _b64(np.zeros(128, np.float32)), "A" * 2000])
async def test_damaged_face_numbers_are_ignored_not_fatal(admin, person, bad):
    sid, m, dev, _ = person
    ev = {"id": str(uuid.uuid4()), "member_id": m["id"], "occurred_at": f"{MON}T09:00:00Z",
          "status": "accepted", "reason": "verified", "scores": GOOD, "embedding_b64": bad}
    r = await dev.post("/api/device/events", json={"events": [ev]})
    assert r.status_code == 200, r.text  # the check-in itself is never refused for it
    assert [f["source"] for f in await _faces(admin, sid, m["id"])] == ["enrolled"]
    ev_out = (await admin.get(f"/api/spaces/{sid}/events?start={MON}&end={MON}")).json()
    assert [e["status"] for e in ev_out] == ["accepted"]


async def test_learning_can_be_turned_off_per_space(admin, person):
    sid, m, dev, anchor = person
    r = await admin.patch(f"/api/spaces/{sid}", json={"auto_update_faces": False})
    assert r.status_code == 200 and r.json()["auto_update_faces"] is False
    roster = (await dev.get("/api/device/sync")).json()
    assert roster["space"]["auto_update_faces"] is False  # kiosks stop sending faces
    await _check_in(dev, m["id"], _near(anchor, 0.6))
    assert [f["source"] for f in await _faces(admin, sid, m["id"])] == ["enrolled"]


async def test_learned_looks_do_not_use_up_photo_slots(admin, person, face_jpeg, monkeypatch):
    from fexor_server.api import faces

    sid, m, dev, anchor = person
    await _check_in(dev, m["id"], _near(anchor, 0.6))
    monkeypatch.setattr(faces, "MAX_TEMPLATES", 2)
    r = await admin.post(f"/api/spaces/{sid}/members/{m['id']}/faces",
                         files=[("files", ("b.jpg", face_jpeg, "image/jpeg"))])
    assert r.json()[0]["ok"], r.text
    r = await admin.post(f"/api/spaces/{sid}/members/{m['id']}/faces",
                         files=[("files", ("c.jpg", face_jpeg, "image/jpeg"))])
    assert r.json()[0]["error"] == "too_many_photos"


async def test_an_admin_can_remove_a_learned_look(admin, person):
    sid, m, dev, anchor = person
    await _check_in(dev, m["id"], _near(anchor, 0.6))
    auto = [f for f in await _faces(admin, sid, m["id"]) if f["source"] == "auto"][0]
    r = await admin.delete(f"/api/spaces/{sid}/members/{m['id']}/faces/{auto['id']}")
    assert r.status_code == 200
    assert [f["source"] for f in await _faces(admin, sid, m["id"])] == ["enrolled"]


async def test_a_new_space_learns_by_default(admin):
    s = await make_space(admin)
    assert s["auto_update_faces"] is True


# ---------- threshold report ----------
async def _scores_event(dev, mid, status, reason, scores, at):
    ev = {"id": str(uuid.uuid4()), "member_id": mid, "occurred_at": at, "status": status,
          "reason": reason, "scores": scores}
    r = await dev.post("/api/device/events", json={"events": [ev]})
    assert r.status_code == 200, r.text
    return ev


async def test_face_score_report_needs_enough_check_ins_to_suggest(app, admin):
    s = await make_space(admin)
    m = await make_member(admin, s["id"], start_date=str(MON - timedelta(days=30)))
    dev = await make_device(app, admin, s["id"])
    await _scores_event(dev, m["id"], "accepted", "verified", {"match": 0.7, "other_best": 0.2},
                        f"{MON}T09:00:00Z")
    r = await admin.get(f"/api/spaces/{s['id']}/face-scores")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["genuine"]["count"] == 1 and rep["impostor"]["count"] == 1
    assert rep["suggested_threshold"] is None and rep["threshold"] == pytest.approx(0.42)


async def test_face_score_report_finds_honest_people_being_turned_away(app, admin):
    s = await make_space(admin)
    sid = s["id"]
    m = await make_member(admin, sid, start_date=str(MON - timedelta(days=30)))
    dev = await make_device(app, admin, sid)
    rng = np.random.default_rng(3)
    t0 = f"{MON - timedelta(days=5)}T"
    for i in range(40):  # ordinary check-ins: genuine scores, and the nearest other person
        await _scores_event(dev, m["id"], "accepted", "verified",
                            {"match": float(rng.uniform(0.5, 0.8)),
                             "other_best": float(rng.uniform(0.05, 0.3))},
                            f"{t0}{8 + i // 6:02d}:{i % 6 * 10:02d}:00Z")
    # Turned away, then confirmed by an admin: the costly mistakes.
    held = []
    for i, score in enumerate((0.37, 0.39, 0.40)):
        held.append(await _scores_event(
            dev, m["id"], "pending", "review_requested:face_mismatch",
            {"match": score, "frames": 20}, f"{MON - timedelta(days=4)}T09:{i:02d}:00Z"))
    for ev in held:
        r = await admin.post(f"/api/spaces/{sid}/events/{ev['id']}/review",
                             json={"decision": "approve"})
        assert r.status_code == 200
    await _scores_event(dev, m["id"], "rejected", "face_mismatch", {"match": 0.2},
                        f"{MON - timedelta(days=3)}T09:00:00Z")  # nobody knows who this was
    rep = (await admin.get(f"/api/spaces/{sid}/face-scores")).json()
    assert rep["genuine"]["count"] == 43 and rep["confirmed_false_rejects"] == 3
    assert rep["impostor"]["count"] == 40 and rep["impostor"]["max"] <= 0.3
    assert rep["unconfirmed_mismatches"] == 1
    # Above every other person seen, plus a margin; below the turned-away honest scores.
    assert rep["impostor"]["max"] < rep["suggested_threshold"] <= 0.37
    assert rep["genuine_below_suggested"] == 0
    assert rep["genuine_below_current"] == 3


async def test_face_score_report_is_for_managers(app, admin):
    s = await make_space(admin)
    viewer = await user_with_role(app, admin, s["id"], "viewer")
    assert (await viewer.get(f"/api/spaces/{s['id']}/face-scores")).status_code == 403


async def test_face_score_report_reads_only_event_scores(admin):
    """No face template or snapshot is read (or decrypted) to build it."""
    from .test_performance import capture_sql

    s = await make_space(admin)
    with capture_sql() as sql:
        assert (await admin.get(f"/api/spaces/{s['id']}/face-scores")).status_code == 200
    assert not [q for q in sql if "face_template" in q or "attendance_event.snapshot" in q], sql


async def test_non_finite_scores_are_stored_as_empty(app, admin):
    """Postgres JSON has no NaN: such a score made the whole upload fail on the server."""
    import json

    s = await make_space(admin)
    m = await make_member(admin, s["id"])
    dev = await make_device(app, admin, s["id"])
    eid = str(uuid.uuid4())
    body = json.dumps({"events": [{"id": eid, "member_id": m["id"], "status": "rejected",
                                   "occurred_at": f"{MON}T09:00:00Z", "reason": "liveness_failed",
                                   "scores": {"liveness": float("nan"), "match": 0.5}}]})
    r = await dev.post("/api/device/events", content=body,
                       headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    ev = (await admin.get(f"/api/spaces/{s['id']}/events?start={MON}&end={MON}")).json()
    assert ev[0]["scores"] == {"liveness": None, "match": 0.5}


async def test_disconnecting_a_kiosk_forgets_the_faces_it_taught(app, admin, person):
    """A stolen kiosk could send crafted face numbers; what it taught must not outlive it."""
    sid, m, dev, anchor = person
    await _check_in(dev, m["id"], _near(anchor, 0.6))
    other = await make_device(app, admin, sid)  # a second kiosk in the same space
    await _age_auto_faces(8)
    await _check_in(other, m["id"], _near(anchor, 0.6, seed=9), at=f"{MON}T18:00:00Z")
    assert [f["source"] for f in await _faces(admin, sid, m["id"])] == ["enrolled", "auto", "auto"]
    kiosks = (await admin.get(f"/api/spaces/{sid}/devices")).json()
    first = kiosks[0]["id"]
    assert (await admin.delete(f"/api/spaces/{sid}/devices/{first}")).status_code == 200
    faces = await _faces(admin, sid, m["id"])
    assert [f["source"] for f in faces] == ["enrolled", "auto"]
    assert faces[1]["quality"]["device"] == kiosks[1]["id"]
    log = (await admin.get("/api/audit", params={"space_id": sid})).json()
    revoked = next(e for e in log if e["action"] == "device.revoked")
    assert revoked["detail"]["learned_faces_removed"] == 1


async def test_a_space_with_one_person_has_no_impostor_scores(app, admin):
    """With nobody else in the space the kiosk records other_best = -1.0 ("no one to compare
    with"). That is not a score: counted as one, it made the report suggest the most lenient
    threshold for every one-person space."""
    s = await make_space(admin)
    m = await make_member(admin, s["id"], start_date=str(MON - timedelta(days=30)))
    dev = await make_device(app, admin, s["id"])
    for i in range(40):
        await _scores_event(dev, m["id"], "accepted", "verified", {"match": 0.85, "other_best": -1.0},
                            f"{MON - timedelta(days=5)}T{8 + i // 6:02d}:{i % 6 * 10:02d}:00Z")
    rep = (await admin.get(f"/api/spaces/{s['id']}/face-scores")).json()
    assert rep["genuine"]["count"] == 40
    assert rep["impostor"]["count"] == 0 and rep["suggested_threshold"] is None
