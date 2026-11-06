import asyncio

from alembic import context
from fexor_server.config import get_settings
from fexor_server.models import Base
from sqlalchemy.ext.asyncio import create_async_engine

target_metadata = Base.metadata


def run(connection):
    context.configure(connection=connection, target_metadata=target_metadata,
                      compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def main():
    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as conn:
        await conn.run_sync(run)
    await engine.dispose()


asyncio.run(main())
