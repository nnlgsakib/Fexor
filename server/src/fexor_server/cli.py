"""fexor-server CLI: serve, worker, migrate, create-admin, gen-key."""

import argparse
import asyncio
import getpass
import os
import sys
from pathlib import Path


def _alembic_cfg():
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    # The server image ships compiled migrations only (no .py source): load .pyc files too.
    cfg.set_main_option("sourceless", "true")
    return cfg


def migrate() -> None:
    from alembic import command

    command.upgrade(_alembic_cfg(), "head")


async def _create_admin(email: str, name: str, password: str) -> None:
    from sqlalchemy import func, select

    from .db import sessionmaker
    from .models import AdminUser
    from .security import hash_password

    async with sessionmaker()() as db:
        if (await db.execute(select(AdminUser).where(func.lower(AdminUser.email) == email.lower()))).first():
            sys.exit(f"admin {email} already exists")
        db.add(AdminUser(email=email.lower(), name=name, password_hash=hash_password(password),
                         is_superadmin=True))
        await db.commit()
    print(f"superadmin {email} created")


async def _print_setup_code() -> None:
    from .api.setup import needs_setup, setup_code
    from .db import sessionmaker

    async with sessionmaker()() as db:
        print(setup_code() if await needs_setup(db) else "")


def main() -> None:
    p = argparse.ArgumentParser(prog="fexor-server")
    sub = p.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the API + admin panel")
    serve.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container default
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--workers", type=int, default=1)
    sub.add_parser("worker", help="run background jobs")
    sub.add_parser("migrate", help="apply database migrations")
    ca = sub.add_parser("create-admin", help="create a superadmin")
    ca.add_argument("--email", required=True)
    ca.add_argument("--name", default="Administrator")
    sub.add_parser("gen-key", help="print a new FEXOR_DATA_KEY")
    sub.add_parser("models", help="download and verify face models")
    sub.add_parser("setup-code", help="print the first-run setup code (only while no admin exists)")
    a = p.parse_args()

    if a.cmd == "gen-key":
        from fexor_core.crypto import new_key_b64

        print(new_key_b64())
    elif a.cmd == "migrate":
        migrate()
    elif a.cmd == "serve":
        import uvicorn

        migrate()
        uvicorn.run("fexor_server.main:app", host=a.host, port=a.port, workers=a.workers,
                    proxy_headers=True, forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "*"))
    elif a.cmd == "worker":
        from .worker import main as worker_main

        asyncio.run(worker_main())
    elif a.cmd == "create-admin":
        pw = os.environ.get("FEXOR_ADMIN_PASSWORD") or getpass.getpass("Password (min 10 chars): ")
        asyncio.run(_create_admin(a.email, a.name, pw))
    elif a.cmd == "setup-code":
        asyncio.run(_print_setup_code())
    elif a.cmd == "models":
        from fexor_core.models import ensure_all

        for n, path in ensure_all().items():
            print(f"{n}: {path}")


if __name__ == "__main__":
    main()
