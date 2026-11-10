"""Running a short write transaction again when Postgres ends it to break a deadlock or a serialization conflict.

Postgres aborts one of two transactions that wait on each other's row locks (SQLSTATE 40P01, deadlock detected) and,
at REPEATABLE READ or above, one of two that change the same rows (40001, serialization failure). Both may simply
run again from the start: nothing of the aborted transaction was written. Deletes are where it shows: a
conversation, workflow or project deleted the moment a run ends races what writes under it in another order (the
run's finish hooks and its search indexing, the notifier, another server saving the run it just handed over).

`retrying` is for transactions that are safe to repeat as a whole and short: each attempt reads again what it needs.
SQLite has neither error (one writer at a time), so there it runs once.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

from prometheus_client import Counter
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.errors import Unavailable
from agentcanvas.log import get_logger

log = get_logger(__name__)

RETRYABLE = {"40P01": "deadlock", "40001": "serialization"}  # SQLSTATE -> the metric's reason
ATTEMPTS = 4  # the first try and three more: about a second in all at most
BASE_S = 0.1
CAP_S = 1.0
RETRY_AFTER_S = 2

RETRIES = Counter(
    "agentcanvas_db_retries_total",
    "Write transactions run again after Postgres ended them as a deadlock's victim or a serialization conflict",
    ["what", "reason"],
)


def retryable(exc: BaseException) -> str | None:
    """'deadlock' or 'serialization' when `exc` is Postgres ending a transaction that may run again, else None."""
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None)
    return RETRYABLE.get(code) if isinstance(code, str) else None


def backoff(attempt: int) -> float:
    """Seconds to wait before attempt `attempt + 1`: doubling from BASE_S up to CAP_S, the second half of it at
    random, so two servers that collided do not collide again in step."""
    full = min(CAP_S, BASE_S * 2 ** (attempt - 1))
    return full / 2 + random.uniform(0, full / 2)


async def retrying[T](
    session: AsyncSession, work: Callable[[], Awaitable[T]], *, what: str, attempts: int | None = None
) -> T:
    """Run `work`, which writes in `session` and commits, and when Postgres ends its transaction as a deadlock's
    victim or a serialization conflict, roll back and run it again: up to `attempts` (ATTEMPTS) times in all. Then
    it fails closed: 503 with a Retry-After and the reason, nothing written. Any other error is raised at once."""
    tries = attempts or ATTEMPTS
    attempt = 0
    while True:
        attempt += 1
        try:
            return await work()
        except DBAPIError as exc:
            reason = retryable(exc)
            if reason is None:
                raise
            await session.rollback()
            RETRIES.labels(what, reason).inc()
            if attempt >= tries:
                log.warning("db.retry_gave_up", what=what, reason=reason, attempts=attempt)
                raise Unavailable(
                    f"the {what} met other work writing the same rows ({reason}) {attempt} times in a row; nothing "
                    "was changed, try again in a moment",
                    retry_after=RETRY_AFTER_S,
                ) from exc
            log.info("db.retry", what=what, reason=reason, attempt=attempt)
            await asyncio.sleep(backoff(attempt))
