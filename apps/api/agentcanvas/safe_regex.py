"""Regular expressions written by users, searched with a time limit.

Python's `re` holds the GIL for the whole match, so a catastrophic pattern such as `(a+)+$` would freeze the event
loop (and its heartbeats) for minutes; a thread does not help. Searches run in a small pool of worker processes
instead, and a search that runs out of time terminates the pool.
"""

from __future__ import annotations

import asyncio
import atexit
import multiprocessing
import re
import threading
from multiprocessing.pool import Pool

TIMEOUT_S = 2.0
MAX_TEXT = 100_000  # characters searched; longer text is cut

_state: dict[str, Pool] = {}
_lock = threading.Lock()


def _search(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


def _get_pool() -> Pool:
    with _lock:
        if "pool" not in _state:
            _state["pool"] = multiprocessing.get_context("spawn").Pool(2)
            atexit.register(_state["pool"].terminate)
        return _state["pool"]


def _discard(pool: Pool) -> None:
    with _lock:
        if _state.get("pool") is pool:
            del _state["pool"]
    pool.terminate()


async def search(pattern: str, text: str, timeout: float | None = None) -> bool:
    """Whether `pattern` is found in `text`; TimeoutError when the search takes longer than `timeout` seconds."""
    timeout = TIMEOUT_S if timeout is None else timeout
    pool = _get_pool()
    pending = pool.apply_async(_search, (pattern, text[:MAX_TEXT]))
    try:
        return await asyncio.to_thread(pending.get, timeout)
    except multiprocessing.TimeoutError:
        _discard(pool)
        raise TimeoutError(f"the pattern took longer than {timeout:g} s") from None
