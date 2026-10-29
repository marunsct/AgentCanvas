"""A run's events, kept with the run (G-10): what the trajectory view, run search and fine-tuning exports read once
the run's in-memory events are gone.

Token deltas are joined into one text per step (and helper agent), state updates are left out (the checkpoints
have them), long strings and lists are cut, and a run keeps at most `MAX_EVENTS` of them: the first ones and its
last (terminal) event. Each event carries `t`, milliseconds since the run started. Secrets are already masked in
run events, so nothing here masks again.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

MAX_EVENTS = 2000
MAX_TEXT = 8000  # characters of one string
MAX_ITEMS = 200  # items of one list
KEPT = {"ac.model_reply", "ac.tool_io", "ac.agent", "ac.interrupt", "ac.usage", "ac.dry_run", "ac.warning"}
TERMINAL = ("RUN_FINISHED", "RUN_ERROR")


def clip(value: Any, depth: int = 0) -> Any:
    """`value` with long strings and lists cut (and anything nested deeper than 12 levels summarised)."""
    if isinstance(value, str):
        return value if len(value) <= MAX_TEXT else value[:MAX_TEXT] + "…"
    if depth > 12:
        return "…"
    if isinstance(value, dict):
        return {str(k): clip(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clip(v, depth + 1) for v in value[:MAX_ITEMS]]
    return value


def _one(ev: dict[str, Any], t: int) -> dict[str, Any] | None:
    kind = ev.get("type")
    node = ev.get("canvas_node_id")
    if kind in ("STEP_STARTED", "STEP_FINISHED"):
        out = {"type": kind, "t": t, "node": node, "task": ev.get("task_id"), "name": ev.get("stepName")}
        for k in ("status", "duration_ms", "error"):
            if ev.get(k) is not None:
                out[k] = clip(ev[k])
        return out
    if kind == "CUSTOM" and ev.get("name") in KEPT:
        return {"type": ev["name"], "t": t, "node": node, "value": clip(ev.get("value"))}
    if kind == "RUN_STARTED":
        return {"type": kind, "t": t, "mode": ev.get("mode"), "adapter": ev.get("adapter")}
    if kind == "RUN_FINISHED":
        return {"type": kind, "t": t, "status": ev.get("status"), "duration_ms": ev.get("duration_ms"),
                "usage": ev.get("usage"), "output": clip(ev.get("output") or {})}  # fmt: skip
    if kind == "RUN_ERROR":
        return {"type": kind, "t": t, "code": ev.get("code"), "message": clip(ev.get("message"))}
    return None


def compact(events: Sequence[dict[str, Any]], times: Sequence[int], now_ns: int) -> list[dict[str, Any]]:
    """The run's events as the run keeps them. `times`: the wall clock (ns) of each event, as far as known (the run's
    last events are not published yet when it is saved: they get `now_ns`)."""
    start = times[0] if times else now_ns
    out: list[dict[str, Any]] = []
    for i, ev in enumerate(events):
        t = max(0, round(((times[i] if i < len(times) else now_ns) - start) / 1_000_000))
        if ev.get("type") == "TEXT_MESSAGE_CONTENT":
            delta, node, agent = str(ev.get("delta") or ""), ev.get("canvas_node_id"), ev.get("agent")
            last = out[-1] if out else None
            if last and last["type"] == "TEXT" and last.get("node") == node and last.get("agent") == agent:
                last["text"] = clip(last["text"] + delta)
                continue
            item: dict[str, Any] | None = {"type": "TEXT", "t": t, "node": node, "text": clip(delta)}
            if agent:
                item = {**item, "agent": agent}
        else:
            item = _one(ev, t)
        if item is not None:
            out.append(item)
    if len(out) > MAX_EVENTS:
        last = [e for e in out[-3:] if e["type"] in TERMINAL]
        out = [*out[: MAX_EVENTS - 1 - len(last)], {"type": "ac.truncated", "t": out[-1]["t"]}, *last]
    return out
