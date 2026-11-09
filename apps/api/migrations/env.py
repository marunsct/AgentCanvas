"""Alembic environment (async). Run via `uv run alembic upgrade head` or at API startup."""

from __future__ import annotations

import asyncio
import os
import time

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from agentcanvas.db.models import Base
from agentcanvas.db.rls import connect_options
from agentcanvas.db.session import async_url

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or async_url(
        os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agentcanvas.db")
    )


MIGRATION_LOCK = 0x6163_6D69_6772  # Postgres advisory lock id: one process migrates at a time
LOCK_POLL_S = 0.5  # how often a replica waiting for another's migration tries the lock again
DDL_LOCK_TIMEOUT = (
    "60s"  # an ALTER TABLE queued behind a long transaction blocks every query behind it: give up instead
)


def _serialize(connection) -> None:
    """Postgres: replicas that start together take turns (the second finds nothing left to do), and no statement
    waits for a table lock for ever. The lock is the session's, polled outside any transaction, and held until the
    end: a migration that builds an index CONCURRENTLY commits midway, and its build waits for every open
    transaction, so a replica waiting for the lock inside one would deadlock it."""
    if connection.dialect.name != "postgresql":
        return
    while not connection.exec_driver_sql(f"SELECT pg_try_advisory_lock({MIGRATION_LOCK})").scalar():
        connection.commit()
        time.sleep(LOCK_POLL_S)
    connection.exec_driver_sql(f"SET lock_timeout = '{DDL_LOCK_TIMEOUT}'")
    connection.commit()


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite needs batch mode for ALTER
        compare_type=True,
    )
    _serialize(connection)
    try:
        with context.begin_transaction():
            context.run_migrations()
    finally:
        if connection.dialect.name == "postgresql":
            connection.rollback()  # whatever a failure left open, then the lock goes
            connection.exec_driver_sql(f"SELECT pg_advisory_unlock({MIGRATION_LOCK})")
            connection.commit()


async def run_async() -> None:
    url = _url()
    engine = create_async_engine(url, connect_args=connect_options(url))  # data migrations see every organisation
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_async())
