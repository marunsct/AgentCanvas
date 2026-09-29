"""Example script-node bodies used by tests and the fake server (dev only)."""

from langgraph.types import interrupt


def shout(state: dict) -> dict:
    """A sync script: runs in a worker thread (ADR 0015)."""
    return {"out": str(state.get("text", "")).upper()}


def ask_human(state: dict) -> dict:
    """A sync script that pauses for a human; resume value becomes `out`."""
    return {"out": interrupt({"question": state.get("text")})}
