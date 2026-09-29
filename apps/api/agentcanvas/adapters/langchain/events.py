"""Translate LangGraph v2 stream parts into canonical run events (AG-UI based, spec doc 12 §C.3).

The UI only understands these events, never raw LangGraph output (ADR 0008). Adding another
framework (or LangGraph's v3 event streaming, review V-04) means writing another translator.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from agentcanvas.ir.models import Workflow


def node_index(wf: Workflow) -> dict[str, str]:
    """LangGraph node name -> canvas node id."""
    return {n.name: n.id for n in wf.nodes}


def nested_index(wf: Workflow, path: tuple[str, ...] = ()) -> dict[tuple[str, ...], dict[str, str]]:
    """Subgraph path (outer node names) -> {inner node name: canvas node id}, for nested activity on the canvas."""
    out: dict[tuple[str, ...], dict[str, str]] = {}
    for n in wf.nodes:
        if n.type == "core.subgraph":
            try:
                inner = wf.subworkflow(n)
            except Exception:  # noqa: BLE001 - invalid subgraphs never run (E018)
                continue
            out[(*path, n.name)] = node_index(inner)
            out |= nested_index(inner, (*path, n.name))
    return out


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


def translate(
    part: Any, names: dict[str, str], nested: dict[tuple[str, ...], dict[str, str]] | None = None
) -> Iterator[dict[str, Any]]:
    kind, ns, data = part["type"], part["ns"], part["data"]
    top_level = len(ns) == 0
    path = tuple(seg.split(":")[0] for seg in ns)
    if kind == "tasks" and not top_level and nested and path in nested:
        names = nested[path]  # a step inside a subgraph: report it for the inner node (drill-in view)
        top_level = True
    if kind == "tasks" and top_level:
        node_id = names.get(data["name"])
        if node_id is None:
            return
        if "input" in data:
            yield {"type": "STEP_STARTED", "stepName": data["name"], "canvas_node_id": node_id, "task_id": data["id"]}
        else:
            error = data.get("error")
            status = "error" if error else ("interrupted" if data.get("interrupts") else "ok")
            ev = {
                "type": "STEP_FINISHED",
                "stepName": data["name"],
                "canvas_node_id": node_id,
                "status": status,
                "task_id": data["id"],
            }
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


class UsageTracker(BaseCallbackHandler):
    """Collects token usage per model call and attributes it to the top-level canvas node (M4-T11).

    Nested calls (an agent's model inside its subgraph, a Deep Agent's subagent) count for the node on the
    canvas: the first segment of the checkpoint namespace. Pricing happens in the framework-neutral runner.
    """

    run_inline = True  # plain list appends; no executor hop

    def __init__(self, names: dict[str, str]):
        self.names = names
        self._calls: dict[UUID, tuple[str | None, str | None]] = {}
        self._resources: dict[UUID, str] = {}
        self._records: list[dict[str, Any]] = []
        self._replies: list[dict[str, Any]] = []  # FR-X-13: what each model resource answered, in order

    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: UUID, **kw: Any) -> None:
        md = kw.get("metadata") or {}
        ns = str(md.get("checkpoint_ns") or md.get("langgraph_checkpoint_ns") or "")
        node = ns.split("|")[0].split(":")[0] or md.get("langgraph_node")
        model = md.get("ac_model") or (
            f"{md['ls_provider']}:{md['ls_model_name']}" if md.get("ls_provider") and md.get("ls_model_name") else None
        )
        self._calls[run_id] = (self.names.get(str(node)), model)
        if md.get("ac_resource"):
            self._resources[run_id] = str(md["ac_resource"])

    def on_llm_end(self, response: Any, *, run_id: UUID, **kw: Any) -> None:
        node_id, model = self._calls.pop(run_id, (None, None))
        resource = self._resources.pop(run_id, None)
        message = next((getattr(g, "message", None) for gens in response.generations for g in gens), None)
        if resource and message is not None:
            content = message.content if isinstance(message.content, str) else str(message.content)
            calls = [{"name": c["name"], "args": c.get("args", {})} for c in getattr(message, "tool_calls", []) or []]
            reply = {"content": content, "tool_calls": calls} if calls else content
            self._replies.append({"resource": resource, "canvas_node_id": node_id, "reply": reply})
        tokens_in = tokens_out = 0
        for gens in response.generations:
            for g in gens:
                usage = getattr(getattr(g, "message", None), "usage_metadata", None) or {}
                tokens_in += int(usage.get("input_tokens") or 0)
                tokens_out += int(usage.get("output_tokens") or 0)
        if not (tokens_in or tokens_out):
            legacy = (response.llm_output or {}).get("token_usage") or {}
            tokens_in, tokens_out = int(legacy.get("prompt_tokens") or 0), int(legacy.get("completion_tokens") or 0)
        if tokens_in or tokens_out:
            self._records.append(
                {"canvas_node_id": node_id, "model": model, "input_tokens": tokens_in, "output_tokens": tokens_out}
            )

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kw: Any) -> None:
        self._calls.pop(run_id, None)
        self._resources.pop(run_id, None)

    def drain(self) -> list[dict[str, Any]]:
        out, self._records = self._records, []
        return out

    def drain_replies(self) -> list[dict[str, Any]]:
        out, self._replies = self._replies, []
        return out
