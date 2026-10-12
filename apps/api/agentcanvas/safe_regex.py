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
MAX_TEXT = 100_000  # characters searched by `search` (longer text is cut), and per window by `spans`
OVERLAP = 10_000  # characters each `spans` window shares with the next: matches up to this long are found whole
MAX_MATCHES = 1_000  # spans returned by `spans`

_state: dict[str, Pool] = {}
_lock = threading.Lock()


def _search(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


def _spans(pattern: str, text: str) -> list[tuple[int, int]]:
    """Every match in the whole text, searched in overlapping windows of MAX_TEXT characters: the cost of a
    pattern that is slow on long input stays bounded per window, and nothing after the first window is missed
    (redaction must see all of the text). A match starting in a window's overlap is left to the next window."""
    found: list[tuple[int, int]] = []
    step = MAX_TEXT - OVERLAP
    start = 0
    while True:
        last = start + MAX_TEXT >= len(text)
        for m in re.finditer(pattern, text[start : start + MAX_TEXT]):
            if m.end() > m.start() and (last or m.start() < step):
                found.append((start + m.start(), start + m.end()))
                if len(found) >= MAX_MATCHES:
                    return found
        if last:
            return found
        start += step


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
    """The (start, end) of each non-empty match of `pattern` in `text` (at most MAX_MATCHES), all of the text
    searched; TimeoutError when the search takes longer than `timeout` seconds."""
    return await _run(_spans, (pattern, text), timeout)
