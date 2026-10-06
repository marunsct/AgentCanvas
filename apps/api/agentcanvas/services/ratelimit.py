"""Rate limits on the public API (S12, closes the S30 gap left in S11).

nginx limits each client address. These limits follow the caller's identity instead: a deployment's API key, a
webhook trigger, and the project they belong to. Requests are counted in fixed one-minute windows in the control
database, so the limits hold across API instances without a separate store. A request over a limit answers 429
with `Retry-After` (the seconds left in the window) and is not counted against the other keys.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from agentcanvas.db.models import RateWindow
from agentcanvas.errors import TooManyRequests

WINDOW_S = 60


class RateLimiter:
    def __init__(self, db: Any, window_s: int = WINDOW_S):
        self.db, self.window_s = db, window_s
        self._swept = -1

    async def hit(self, *limits: tuple[str, int], now: float | None = None) -> None:
        """Count one request for each `(key, limit)`; raise `TooManyRequests` when one is over its limit.
        A limit of 0 (or less) is not checked."""
        limits = tuple((k, n) for k, n in limits if n > 0)
        if not limits:
            return
        now = time.time() if now is None else now
        window = int(now // self.window_s)
        insert = pg_insert if self.db.engine.dialect.name == "postgresql" else sqlite_insert
        async with self.db.sessionmaker() as s:
            if window != self._swept:  # once per window and instance: drop finished windows
                await s.execute(delete(RateWindow).where(RateWindow.slot < window))
                self._swept = window
            counts = []
            for key, _ in limits:
                stmt = insert(RateWindow).values(key=key, slot=window, count=1)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[RateWindow.key, RateWindow.slot], set_={"count": RateWindow.count + 1}
                ).returning(RateWindow.count)
                counts.append((await s.execute(stmt)).scalar_one())
            over = [key for (key, limit), count in zip(limits, counts, strict=True) if count > limit]
            if over:
                await s.rollback()  # a refused request does not use up anyone's budget
                retry = max(1, int((window + 1) * self.window_s - now) + 1)
                kind = over[0].split(":", 1)[0]
                raise TooManyRequests(
                    f"too many requests for this {kind}; retry in {retry} s", retry_after=retry, limit=over[0]
                )
            await s.commit()
