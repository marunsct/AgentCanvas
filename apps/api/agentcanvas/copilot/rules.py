"""Best-practice suggestions the Copilot reads with `get_problems` (FR-COP-04).

The validator already says what is wrong (errors) or risky (warnings: a tool that changes things without a person's
approval W002, a loop with no way out W001, a key typed into a setting E050). These are judgement calls a reviewer
would mention and the editor should not nag about: a router with no default path, a model with no backup, an AI step
whose instructions say very little. Each has what, why and how to fix it, like the diagnostics.
"""

from __future__ import annotations

from typing import Any

from agentcanvas.ir.models import Workflow

THIN = 40  # characters of instructions below which a step says too little about its task

# code -> (what, why, fix)
TEXT = {
    "S001": (
        "This router has no default path.",
        "An input no case matches stops the run with an error.",
        "Set `default` to the route that should take everything else (often a person or a fallback step).",
    ),
    "S002": (
        "This model has no backup model.",
        "When its provider is down or rate-limits, every step using it fails.",
        "Add a backup model from another provider in `fallbacks` (it is tried when this one fails).",
    ),
    "S003": (
        "This AI step's instructions say very little.",
        "A short prompt leaves the model to guess the task, the input and the shape of the answer.",
        "Say what to do, include the input with {{ state.field }}, and say how to answer (format, length).",
    ),
    "S004": (
        "This agent has little or no system prompt.",
        "Without one it does not know its role, when to use its tools, or when it is done.",
        "Write who the agent is, what it may and may not do with its tools, and what a finished answer is.",
    ),
}


def suggestions(wf: Workflow) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def add(code: str, element: Any) -> None:
        what, why, fix = TEXT[code]
        out.append(
            {"code": code, "node_id": element.id, "name": element.label or element.name, "what": what, "why": why,
             "fix": fix}
        )  # fmt: skip

    for n in wf.nodes:
        cfg = n.config or {}
        if n.type == "core.router" and not cfg.get("default"):
            add("S001", n)
        if n.type == "core.llm" and len(str(cfg.get("prompt") or "").strip()) < THIN:
            add("S003", n)
        if n.type == "core.agent" and len(str(cfg.get("system_prompt") or "").strip()) < THIN:
            add("S004", n)
    used = {e.source.node for e in wf.edges if e.kind == "wiring"}
    for r in wf.resources:
        if r.type == "model.chat" and r.id in used and not (r.config or {}).get("fallbacks"):
            add("S002", r)
    return out
