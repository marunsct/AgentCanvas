import json
import os
import pathlib
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agentcanvas.db.session import async_url
from agentcanvas.ir.models import Workflow
from agentcanvas.settings import Settings

ROOT = pathlib.Path(__file__).parents[1]
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")  # set in CI to run against Postgres
TABLES = [
    "rate_window",
    "dataset_version",
    "dataset",
    "test_case",
    "event_dedup",
    "event_log",
    "audit_log",
    "interrupt_task",
    "run",
    "thread",
    "trigger",
    "deployment",
    "workflow_version",
    "workflow",
    "secret",
    "connection",
    "project",
    "membership",
    "app_user",
    "organization",
]
RUNTIME_TABLES = [
    "event_dedup",
    "event_log",
    "checkpoints",
    "checkpoint_writes",
    "checkpoint_blobs",
    "store",
]  # LangGraph persistence data


@pytest.fixture
def triage() -> Workflow:
    return Workflow.model_validate(json.loads((ROOT / "examples/support_triage.ir.json").read_text()))


async def _truncate(url: str) -> None:
    engine = create_async_engine(async_url(url))
    async with engine.begin() as conn:
        # only tables that exist: the database may still be at an older revision (the app migrates it)
        present = [t for t in TABLES if (await conn.execute(text(f"SELECT to_regclass('public.{t}')"))).scalar()]
        if present:
            await conn.execute(text(f"TRUNCATE {', '.join(present)} CASCADE"))
        for table in RUNTIME_TABLES:  # thread ids repeat across tests: stale checkpoints would leak in
            if (await conn.execute(text(f"SELECT to_regclass('public.{table}')"))).scalar():
                await conn.execute(text(f"TRUNCATE {table}"))
    await engine.dispose()


@pytest.fixture
async def settings(tmp_path) -> Settings:
    if TEST_DATABASE_URL:
        await _truncate(TEST_DATABASE_URL)
        url = TEST_DATABASE_URL
    else:
        url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    return Settings(env="test", database_url=url, workspaces_dir=str(tmp_path / "workspaces"))


@pytest.fixture
async def app(settings):
    from agentcanvas.api.main import create_app

    application = create_app(settings)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def api_client(app) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
