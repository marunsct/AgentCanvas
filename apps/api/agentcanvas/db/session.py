"""Async engine/session management and startup migrations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

MIGRATIONS = Path(__file__).parents[2] / "migrations"


def async_url(url: str) -> str:
    """Normalise a database URL to an async SQLAlchemy driver URL."""
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.split("://", 1)[1]
    if url.startswith("sqlite:///"):
        return "sqlite+aiosqlite:///" + url.removeprefix("sqlite:///")
    return url


def langgraph_url(url: str) -> str:
    """The same database as a plain psycopg connection string (for LangGraph's Postgres saver/store)."""
    return "postgresql://" + url.split("://", 1)[1] if url.startswith("postgresql+") else url


class Database:
    def __init__(self, url: str):
        self.url = async_url(url)
        self.engine: AsyncEngine = create_async_engine(self.url, pool_pre_ping=True)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False)

    @property
    def is_postgres(self) -> bool:
        return self.engine.dialect.name == "postgresql"

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as s:
            yield s

    async def migrate(self) -> None:
        """Alembic `upgrade head` (runs in a worker thread because env.py drives its own event loop)."""
        await asyncio.to_thread(run_alembic, self.url, "upgrade", "head")

    async def dispose(self) -> None:
        await self.engine.dispose()


def run_alembic(url: str, action: str, revision: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", async_url(url).replace("%", "%%"))
    getattr(command, action)(cfg, revision)
