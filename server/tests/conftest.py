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
