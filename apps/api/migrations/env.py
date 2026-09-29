"""Alembic environment (async). Run via `uv run alembic upgrade head` or at API startup."""

from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from agentcanvas.db.models import Base
from agentcanvas.db.session import async_url

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or async_url(
        os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agentcanvas.db")
    )


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",  # SQLite needs batch mode for ALTER
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_async())
