"""Translate LangGraph v2 stream parts into canonical run events (AG-UI based, spec doc 12 §C.3).

The UI only understands these events, never raw LangGraph output (ADR 0008). Adding another
framework (or LangGraph's v3 event streaming, review V-04) means writing another translator.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from agentcanvas.ir.models import Workflow


def node_index(wf: Workflow) -> dict[str, str]:
    """LangGraph node name -> canvas node id."""
    return {n.name: n.id for n in wf.nodes}


def jsonable(v: Any) -> Any:
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if hasattr(v, "model_dump"):
        return jsonable(v.model_dump(exclude_none=True))
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def translate(part: Any, names: dict[str, str]) -> Iterator[dict[str, Any]]:
    kind, ns, data = part["type"], part["ns"], part["data"]
    top_level = len(ns) == 0
    if kind == "tasks" and top_level:
        node_id = names.get(data["name"])
        if node_id is None:
            return
        if "input" in data:
            yield {"type": "STEP_STARTED", "stepName": data["name"], "canvas_node_id": node_id}
        else:
            error = data.get("error")
            status = "error" if error else ("interrupted" if data.get("interrupts") else "ok")
            ev = {"type": "STEP_FINISHED", "stepName": data["name"], "canvas_node_id": node_id, "status": status}
            if error:
                ev["error"] = str(error)
            yield ev
    elif kind == "updates" and top_level:
        if "__interrupt__" in data:
            return  # interrupts are reported once, with their node, in RUN_FINISHED / CUSTOM ac.interrupt
        for node_name, update in data.items():
            if isinstance(update, dict) and update:
                yield {"type": "STATE_DELTA", "canvas_node_id": names.get(node_name), "delta": jsonable(update)}
    elif kind == "messages":
        chunk, meta = data
        text = getattr(chunk, "content", "")
        if text and isinstance(text, str) and getattr(chunk, "type", "") in ("ai", "AIMessageChunk"):
            parent = ns[0].split(":")[0] if ns else meta.get("langgraph_node")
            yield {"type": "TEXT_MESSAGE_CONTENT", "canvas_node_id": names.get(parent), "delta": text}
    elif kind == "custom":
        yield {"type": "CUSTOM", "name": "ac.custom", "value": jsonable(data)}
