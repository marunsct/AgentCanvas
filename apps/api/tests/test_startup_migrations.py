"""HARDENING 2a: with AUTO_MIGRATE off, the server refuses to start on a database its migrations have not reached,
and says what to run; at head it starts; a database newer than the code (a rollback) starts with a warning."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine, text

from agentcanvas.api.main import create_app
from agentcanvas.db.session import run_alembic
from agentcanvas.settings import Settings


def _settings(tmp_path, url: str) -> Settings:
    return Settings(env="test", database_url=url, auto_migrate=False, seed_dev_data=False, persistence_url="memory",
                    workspaces_dir=str(tmp_path / "ws"))  # fmt: skip


async def _start(settings: Settings) -> None:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        pass


async def test_a_database_behind_the_code_is_refused(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'behind.db'}"
    await asyncio.to_thread(run_alembic, url, "upgrade", "head")
    await asyncio.to_thread(run_alembic, url, "downgrade", "-1")
    with pytest.raises(RuntimeError, match=r"run `alembic upgrade head`.*AGENTCANVAS_AUTO_MIGRATE=true"):
        await _start(_settings(tmp_path, url))


async def test_a_database_never_migrated_is_refused(tmp_path):
    with pytest.raises(RuntimeError, match="at migration none"):
        await _start(_settings(tmp_path, f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}"))


async def test_a_database_at_head_starts(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'head.db'}"
    await asyncio.to_thread(run_alembic, url, "upgrade", "head")
    await _start(_settings(tmp_path, url))


async def test_a_database_newer_than_the_code_starts(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'newer.db'}"
    await asyncio.to_thread(run_alembic, url, "upgrade", "head")
    engine = create_engine(url.replace("+aiosqlite", ""))
    with engine.begin() as c:  # a migration a later version of the server added
        c.execute(text("UPDATE alembic_version SET version_num = 'ffffffffffff'"))
    engine.dispose()
    await _start(_settings(tmp_path, url))
