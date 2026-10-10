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
from collections.abc import Callable
from multiprocessing.pool import Pool
from typing import Any

TIMEOUT_S = 2.0
MAX_TEXT = 100_000  # characters searched; longer text is cut
MAX_MATCHES = 1_000  # spans returned by `spans`

_state: dict[str, Pool] = {}
_lock = threading.Lock()


def _search(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


def _spans(pattern: str, text: str) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    for m in re.finditer(pattern, text):
        if m.end() > m.start():
            found.append(m.span())
            if len(found) >= MAX_MATCHES:
                break
    return found


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


async def _run(fn: Callable[..., Any], args: tuple[Any, ...], timeout: float | None) -> Any:
    timeout = TIMEOUT_S if timeout is None else timeout
    pool = _get_pool()
    pending = pool.apply_async(fn, args)
    try:
        return await asyncio.to_thread(pending.get, timeout)
    except multiprocessing.TimeoutError:
        _discard(pool)
        raise TimeoutError(f"the pattern took longer than {timeout:g} s") from None


async def search(pattern: str, text: str, timeout: float | None = None) -> bool:
    """Whether `pattern` is found in `text`; TimeoutError when the search takes longer than `timeout` seconds."""
    return await _run(_search, (pattern, text[:MAX_TEXT]), timeout)


async def spans(pattern: str, text: str, timeout: float | None = None) -> list[tuple[int, int]]:
    """The (start, end) of each non-empty match of `pattern` in `text` (at most MAX_MATCHES, within the first
    MAX_TEXT characters); TimeoutError when the search takes longer than `timeout` seconds."""
    return await _run(_spans, (pattern, text[:MAX_TEXT]), timeout)
