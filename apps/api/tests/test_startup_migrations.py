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


def _newer(url: str) -> None:
    run_alembic(url, "upgrade", "head")
    engine = create_engine(url.replace("+aiosqlite", ""))
    with engine.begin() as c:  # a migration a later version of the server added
        c.execute(text("UPDATE alembic_version SET version_num = 'ffffffffffff'"))
    engine.dispose()


async def test_a_rolled_back_server_that_migrates_itself_starts_on_the_newer_database(tmp_path):
    """Rolling back = the previous image on the same database. `alembic upgrade head` does not know the newer revision
    ("Can't locate revision"), so with AUTO_MIGRATE on (the default) the old server never started."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'newer.db'}"
    await asyncio.to_thread(_newer, url)
    await _start(_settings(tmp_path, url).model_copy(update={"auto_migrate": True}))


def test_the_migration_job_leaves_a_newer_database_alone_and_migrates_an_older_one(tmp_path, monkeypatch, capsys):
    """`python -m agentcanvas.db.migrate` (the Helm Job, the compose `migrate` service): its failure kept a rolled-back
    API from starting, since the API waits for it."""
    from agentcanvas.db import migrate

    url = f"sqlite+aiosqlite:///{tmp_path / 'newer.db'}"
    _newer(url)
    monkeypatch.setenv("DATABASE_URL", url)
    assert migrate.main() == 0
    assert "db.newer_than_code" in capsys.readouterr().out
    engine = create_engine(url.replace("+aiosqlite", ""))
    with engine.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "ffffffffffff"  # untouched
    engine.dispose()

    older = f"sqlite+aiosqlite:///{tmp_path / 'older.db'}"
    run_alembic(older, "upgrade", "head")
    run_alembic(older, "downgrade", "-1")
    monkeypatch.setenv("DATABASE_URL", older)
    assert migrate.main() == 0
    engine = create_engine(older.replace("+aiosqlite", ""))
    with engine.connect() as c:
        current = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
    engine.dispose()
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from agentcanvas.db.session import MIGRATIONS

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    assert current == ScriptDirectory.from_config(cfg).get_current_head()
