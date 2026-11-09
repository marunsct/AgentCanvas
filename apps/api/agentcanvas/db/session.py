"""Async engine/session management and startup migrations."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from agentcanvas.db.rls import TenantSession, connect_options
from agentcanvas.log import get_logger

log = get_logger(__name__)

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
    def __init__(self, url: str, pool_size: int | None = None, max_overflow: int | None = None):
        self.url = async_url(url)
        pool: dict[str, int] = {}
        if self.url.startswith("postgresql") and pool_size is not None:  # connections per API process
            pool = {"pool_size": pool_size, "max_overflow": max_overflow or 0}
        # every connection starts in all-tenants mode; a Matura request narrows its session (db/rls.py)
        self.engine: AsyncEngine = create_async_engine(
            self.url, pool_pre_ping=True, connect_args=connect_options(self.url), **pool
        )
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine.sync_engine, "connect", _sqlite_foreign_keys)
        self.sessionmaker = async_sessionmaker(self.engine, expire_on_commit=False, sync_session_class=TenantSession)

    @property
    def is_postgres(self) -> bool:
        return self.engine.dialect.name == "postgresql"

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessionmaker() as s:
            yield s

    async def migrate(self) -> None:
        """Alembic `upgrade head` (runs in a worker thread because env.py drives its own event loop)."""
        await asyncio.to_thread(run_alembic, self.url, "upgrade", "head")

    async def revisions(self) -> tuple[set[str], set[str]]:
        """(the database's Alembic revisions, the code's heads); the first is empty when it was never migrated."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError

        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS))
        heads = set(ScriptDirectory.from_config(cfg).get_heads())
        try:
            async with self.engine.connect() as c:
                current = set((await c.execute(text("SELECT version_num FROM alembic_version"))).scalars().all())
        except DBAPIError:  # no alembic_version table: never migrated
            current = set()
        return current, heads

    async def check_migrated(self) -> None:
        """With AUTO_MIGRATE off, refuse to start on a database the migrations have not reached (its queries would
        fail on missing columns, request by request). A revision this code does not know is a newer database (a
        rollback of the server): it is logged, and the server starts."""
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        current, heads = await self.revisions()
        if current == heads:
            return
        cfg = Config()
        cfg.set_main_option("script_location", str(MIGRATIONS))
        script = ScriptDirectory.from_config(cfg)
        known = {r.revision for r in script.walk_revisions()}
        if current and not current <= known:
            log.warning("db.newer_than_code", database=sorted(current), code=sorted(heads))
            return
        raise RuntimeError(
            f"the database is at migration {', '.join(sorted(current)) or 'none'} and this server needs "
            f"{', '.join(sorted(heads))}: run `alembic upgrade head` (the image: `python -m alembic upgrade head`), "
            "or set AGENTCANVAS_AUTO_MIGRATE=true"
        )

    async def dispose(self) -> None:
        await self.engine.dispose()


def _sqlite_foreign_keys(dbapi_connection, _record) -> None:
    """SQLite enforces foreign keys (and so the schema's ON DELETE CASCADE) only when each connection asks: without it
    a deleted workflow left its versions, threads and deployments behind, and a published endpoint kept answering.
    Only the app's connections ask; migrations run on their own engine, where a batch table rebuild must not cascade."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def run_alembic(url: str, action: str, revision: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    cfg.set_main_option("sqlalchemy.url", async_url(url).replace("%", "%%"))
    getattr(command, action)(cfg, revision)
