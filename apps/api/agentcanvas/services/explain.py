"""Explain mode (FR-X-17): turn a thread's checkpoint history into a step-by-step narrative.

For every super-step: which nodes ran (in parallel or not), which channels they wrote, and — for routers —
which case matched and why. Router decisions are re-evaluated with the same CEL evaluator the runtime uses,
on the state *before* the router ran, so the explanation cannot drift from the real behaviour.
"""

from __future__ import annotations

from typing import Any

from agentcanvas.expr import ExpressionError, eval_condition
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry
from agentcanvas.runtime.types import CheckpointInfo

MAX_VALUE = 160


def _summary(value: Any) -> str:
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= MAX_VALUE else text[: MAX_VALUE - 1] + "…"


def _router_decision(wf: Workflow, node_id: str, state: dict[str, Any]) -> dict[str, Any]:
    node = wf.require_node(node_id)
    cases = node.config.get("cases", []) if isinstance(node.config.get("cases"), list) else []
    route, reason, when = node.config.get("default", "default"), "no case matched, so the default route was taken", None
    for case in cases:
        try:
            if eval_condition(case["when"], state):
                route, when = case["route"], case["when"]
                reason = f"`{when}` is true"
                break
        except (ExpressionError, KeyError, TypeError) as exc:
            return {"route": None, "reason": f"condition could not be evaluated: {exc}"}
    edge = next((e for e in wf.flow_edges() if e.source.node == node_id and e.source.route == route), None)
    target = edge.target.node if edge else None
    target_name = target if target in ("END", None) else wf.require_node(target).name
    return {"route": route, "condition": when, "reason": reason, "target": target, "target_name": target_name}


def explain(wf: Workflow, history_newest_first: list[CheckpointInfo]) -> list[dict[str, Any]]:
    registry = default_registry()
    steps: list[dict[str, Any]] = []
    for cp in reversed(history_newest_first):  # chronological
        ran = []
        for node_id in cp.next:
            node = wf.node(node_id)
            if node is None:
                continue
            manifest = registry.get(node.type)
            wrote = (cp.writes or {}).get(node_id)
            item: dict[str, Any] = {
                "node_id": node_id,
                "name": node.name,
                "label": node.label or node.name,
                "type": node.type,
                "completed": wrote is not None,
                "wrote": {k: _summary(v) for k, v in wrote.items()} if isinstance(wrote, dict) else {},
            }
            if manifest and manifest.dynamic_outputs == "routes" and cp.values is not None:
                item["decision"] = _router_decision(wf, node_id, cp.values)
            ran.append(item)
        if not ran:
            continue
        names = ", ".join(r["label"] for r in ran)
        text = f"Step {len(steps) + 1}: {names} {'ran in parallel' if len(ran) > 1 else 'ran'}."
        for r in ran:
            if r["wrote"]:
                text += f" {r['label']} wrote {', '.join(r['wrote'])}."
            if "decision" in r and r["decision"].get("route"):
                d = r["decision"]
                text += f" {r['label']} chose route '{d['route']}' → {d['target_name']} because {d['reason']}."
            if not r["completed"]:
                text += f" {r['label']} is waiting (paused)."
        steps.append(
            {
                "step": len(steps) + 1,
                "checkpoint_id": cp.checkpoint_id,
                "parallel": len(ran) > 1,
                "ran": ran,
                "text": text,
            }
        )
    return steps
