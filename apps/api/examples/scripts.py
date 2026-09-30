"""Example script-node bodies used by tests and demos (dev only: `examples.*` is not importable in prod)."""

import asyncio
from collections import Counter

from langgraph.types import interrupt

CALLS: Counter[str] = Counter()  # test instrumentation


def shout(state: dict) -> dict:
    """A sync script: runs in a worker thread (ADR 0015)."""
    return {"out": str(state.get("text", "")).upper()}


def ask_human(state: dict) -> dict:
    """A sync script that pauses for a human; resume value becomes `out`."""
    return {"out": interrupt({"question": state.get("text")})}


async def slow(state: dict) -> dict:
    await asyncio.sleep(float(state.get("seconds") or 5))
    return {"out": "slept"}


def flaky(state: dict) -> dict:
    """Fails on the first two attempts of each run (retry policy demo)."""
    CALLS["flaky"] += 1
    if CALLS["flaky"] % 3:
        raise ConnectionError(f"transient failure #{CALLS['flaky']}")
    return {"out": "finally"}


def counted(state: dict) -> dict:
    CALLS["counted"] += 1
    return {"out": f"call {CALLS['counted']}"}


def whoami(state: dict, runtime) -> dict:
    """Reads the run context (e.g. the authenticated user injected by the runner)."""
    return {"out": f"user={runtime.context['user_id']}"}


def bump(state: dict) -> dict:
    return {"n": int(state.get("n") or 0) + 1}
