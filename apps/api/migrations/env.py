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


def _take_turn(connection) -> None:
    """Postgres: replicas that start together take turns (the second finds nothing left to do). The lock is the
    session's, not a transaction's: a migration that builds an index concurrently (`autocommit_block`) commits in
    the middle. A replica waiting for it tries again every half second outside any transaction, rather than wait in
    a statement: a concurrent index build waits for every older snapshot to end, a waiting statement's too."""
    level = connection.get_isolation_level()
    connection.execution_options(isolation_level="AUTOCOMMIT")
    try:
        while not connection.exec_driver_sql(f"SELECT pg_try_advisory_lock({MIGRATION_LOCK})").scalar():
            connection.commit()
            time.sleep(LOCK_POLL_S)
    finally:
        connection.commit()
        connection.execution_options(isolation_level=level)


def _serialize(connection) -> None:
    """Postgres: no statement waits for a table lock for ever (for the whole session: see `_take_turn`)."""
    if connection.dialect.name != "postgresql":
        return
    connection.exec_driver_sql(f"SET lock_timeout = '{DDL_LOCK_TIMEOUT}'")


def _run(connection) -> None:
    postgres = connection.dialect.name == "postgresql"
    if postgres:
        _take_turn(connection)
    try:
        _migrate(connection)
    finally:
        if postgres:  # also when it failed: the next replica tries
            connection.rollback()
            connection.exec_driver_sql(f"SELECT pg_advisory_unlock({MIGRATION_LOCK})")
            connection.commit()


def _migrate(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite needs batch mode for ALTER
        compare_type=True,
    )
    _serialize(connection)
    with context.begin_transaction():  # the lock is released by `_run`, also when this fails
        context.run_migrations()


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
