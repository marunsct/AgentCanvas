"""Bring the database up to this release's migrations: what the Helm migration Job and the compose `migrate` service
run, before `python -m agentcanvas.db.app_role`:

    python -m agentcanvas.db.migrate

It is `alembic upgrade head`, except on a database a later release already migrated (the server was rolled back to
an older image): there Alembic fails on the revision it does not know, and since the API waits for this step, the
rolled-back API never started. That database is left as it is, with a `db.newer_than_code` warning: the migrations
are additive, so the older code runs on it.
"""

from __future__ import annotations

import asyncio
import os

from agentcanvas.db.session import Database
from agentcanvas.log import configure_logging


async def _migrate(url: str) -> None:
    db = Database(url)
    try:
        await db.migrate()
    finally:
        await db.dispose()


def main() -> int:
    configure_logging(json=os.getenv("AGENTCANVAS_LOG_JSON", "").lower() in ("1", "true", "yes", "on"))
    asyncio.run(_migrate(os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./agentcanvas.db")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
