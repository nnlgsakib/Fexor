"""First-run setup. Runs before the other API tests (file name order), while no admin exists."""

import httpx


def client(app, host: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(host, 5555)),
                             base_url="http://test")


async def test_first_admin_setup_flow(app):
    from fexor_server.api.setup import setup_code

    remote = client(app, "10.1.2.3")
    s = (await remote.get("/api/setup/status")).json()
    assert s == {"needs_setup": True, "code_required": True}
    body = {"email": "owner@example.com", "name": "Owner", "password": "first-owner-pass"}
    assert (await remote.post("/api/setup", json=body)).status_code == 403
    assert (await remote.post("/api/setup", json={**body, "code": "AAAAA-BBBBB"})).status_code == 403
    # Spoofed forwarding header does not make a request "local".
    spoof = await remote.post("/api/setup", json=body, headers={"x-forwarded-for": "127.0.0.1"})
    assert spoof.status_code == 403

    local = client(app, "127.0.0.1")
    assert (await local.get("/api/setup/status")).json()["code_required"] is False
    code = setup_code().lower().replace("-", "")  # typing it loosely still works
    r = await remote.post("/api/setup", json={**body, "code": code})
    assert r.status_code == 200, r.text
    me = await remote.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["is_superadmin"]

    r = await local.post("/api/setup", json={**body, "email": "second@example.com"})
    assert r.status_code == 409
    assert (await local.get("/api/setup/status")).json()["needs_setup"] is False
