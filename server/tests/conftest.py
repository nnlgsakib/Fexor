import os
import urllib.request

import pytest
from fexor_core.crypto import new_key_b64

BASE_URL = os.environ.get("FEXOR_TEST_DB_ADMIN", "postgresql://fexor:fexor@localhost:5432/fexor")
TEST_DB = "fexor_test"
os.environ["FEXOR_DATABASE_URL"] = (
    BASE_URL.rsplit("/", 1)[0].replace("postgresql://", "postgresql+asyncpg://") + f"/{TEST_DB}"
)
os.environ.setdefault("FEXOR_DATA_KEY", new_key_b64())
os.environ["FEXOR_SECURE_COOKIES"] = "false"


def _reset_db() -> None:
    import asyncio

    import asyncpg

    async def go():
        conn = await asyncpg.connect(BASE_URL)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
            await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
        finally:
            await conn.close()

    asyncio.run(go())


@pytest.fixture(scope="session")
def migrated():
    try:
        _reset_db()
    except OSError as e:
        pytest.skip(f"postgres not reachable: {e}")
    from fexor_server.cli import migrate

    migrate()


@pytest.fixture(scope="session")
async def app(migrated):
    from fexor_server.main import create_app

    return create_app()


def make_client(app):
    import httpx

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture(scope="session")
async def admin(app):
    """Logged-in superadmin client with CSRF header set."""
    from fexor_server.cli import _create_admin

    await _create_admin("root@example.com", "Root", "correct-horse-battery")
    c = make_client(app)
    r = await c.post("/api/auth/login", json={"email": "root@example.com",
                                              "password": "correct-horse-battery"})
    assert r.status_code == 200, r.text
    me = (await c.get("/api/auth/me")).json()
    c.headers["X-CSRF-Token"] = me["csrf"]
    yield c
    await c.aclose()


@pytest.fixture(scope="session")
def face_jpeg() -> bytes:
    from fexor_core.models import default_model_dir

    path = default_model_dir() / "test_lena.jpg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(  # noqa: S310
            "https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/lena.jpg", path)
    return path.read_bytes()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Many tests log in; the per-IP login limit must not leak between them."""
    from fexor_server.api import auth

    auth._attempts.clear()
    yield
    auth._attempts.clear()


def recent_monday():
    """A Monday 7-13 days ago: its shifts are closed, and it stays within kiosk event limits."""
    from datetime import date, timedelta

    d = date.today() - timedelta(days=7)
    return d - timedelta(days=d.weekday())


async def login(app, email: str, password: str):
    c = make_client(app)
    r = await c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    c.headers["X-CSRF-Token"] = (await c.get("/api/auth/me")).json()["csrf"]
    return c


async def user_with_role(app, admin, space_id: str, role: str):
    """New admin with one space role; returns a logged-in client."""
    import uuid

    email = f"{role}-{uuid.uuid4().hex[:8]}@example.com"
    r = await admin.post("/api/users", json={"email": email, "name": role.title(),
                                             "password": "role-password-123"})
    assert r.status_code == 201, r.text
    r = await admin.put(f"/api/users/{r.json()['id']}/roles", json=[{"space_id": space_id,
                                                                     "role": role}])
    assert r.status_code == 200, r.text
    return await login(app, email, "role-password-123")


async def make_space(admin, name: str | None = None, template: str = "office", tz: str = "UTC"):
    import uuid

    r = await admin.post("/api/spaces", json={"name": name or f"Space {uuid.uuid4().hex[:6]}",
                                              "template": template, "timezone": tz})
    assert r.status_code == 201, r.text
    return r.json()


async def make_member(admin, sid: str, ext: str | None = None, name: str = "Jane Doe",
                      consent: bool = True, **extra):
    import uuid

    body = {"full_name": name, "external_id": ext or uuid.uuid4().hex[:8], "consent": consent,
            **extra}
    r = await admin.post(f"/api/spaces/{sid}/members", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def make_device(app, admin, sid: str):
    r = await admin.post(f"/api/spaces/{sid}/devices", json={"name": "Lobby kiosk"})
    assert r.status_code == 201, r.text
    dev = make_client(app)
    r = await dev.post("/api/device/enroll", json={"code": r.json()["enroll_code"]})
    assert r.status_code == 200, r.text
    dev.headers["Authorization"] = f"Bearer {r.json()['token']}"
    return dev


async def run_jobs():
    from fexor_server.worker import run_one

    while await run_one():
        pass
