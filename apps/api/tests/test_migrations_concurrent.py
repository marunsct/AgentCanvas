"""Several API replicas starting together must not collide in Alembic (each runs `upgrade head` at startup)."""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from agentcanvas.db.session import async_url

URL = os.getenv("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not URL.startswith("postgres"), reason="advisory locks need Postgres")


async def test_replicas_migrating_at_once_take_turns():
    admin = create_async_engine(async_url(URL), isolation_level="AUTOCOMMIT")
    name = f"mig_{uuid.uuid4().hex[:10]}"
    async with admin.connect() as c:
        await c.execute(text(f"CREATE DATABASE {name}"))
    fresh = async_url(URL).rsplit("/", 1)[0] + f"/{name}"
    try:
        env = {**os.environ, "DATABASE_URL": fresh}
        replicas = [  # separate processes, as replicas are
            await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "alembic",
                "upgrade",
                "head",
                cwd=pathlib.Path(__file__).parents[1],
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            for _ in range(4)
        ]
        outputs = await asyncio.wait_for(asyncio.gather(*(p.communicate() for p in replicas)), 120)
        failed = [out.decode()[-600:] for p, (out, _) in zip(replicas, outputs, strict=True) if p.returncode]
        assert failed == [], failed
        engine = create_async_engine(fresh)
        async with engine.connect() as c:
            assert (await c.execute(text("SELECT count(*) FROM alembic_version"))).scalar() == 1
            assert (await c.execute(text("SELECT to_regclass('public.run')"))).scalar() == "run"
        await engine.dispose()
    finally:
        async with admin.connect() as c:
            await c.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        await admin.dispose()
