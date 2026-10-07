"""Copilot eval harness (S14): build workflows from plain requests with a real model and check the results.

    uv run python -m agentcanvas.copilot.evals --model anthropic:claude-sonnet-5-5 [--case greeter] [--out r.json]

Each case starts from an empty workflow, sends the request in Build mode, answers a brief with "Yes, build it",
then checks the proposal: it applies, has no validation errors, and uses the expected blocks. The model's key comes
from the usual environment variable (e.g. ANTHROPIC_API_KEY). CI runs the harness with a scripted model only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from agentcanvas.copilot.pack import build_pack
from agentcanvas.copilot.service import CopilotTurn
from agentcanvas.copilot.settings import ResolvedModel
from agentcanvas.copilot.tools import ToolContext
from agentcanvas.ir.commands import apply_batch, parse_commands
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import Registry, default_registry
from agentcanvas.services.workflows import list_templates
from agentcanvas.settings import Settings
from agentcanvas.validation import validate

CASES: list[dict[str, Any]] = [
    {"id": "greeter", "request": "Take a person's name and write them a friendly one-line greeting.",
     "types": ["core.llm", "model.chat"]},
    {"id": "triage", "request": "Classify support tickets as urgent or not; urgent ones need a manager's approval, "
     "the others get a drafted reply.", "types": ["core.llm", "core.router", "core.approval"]},
    {"id": "chat_tools", "request": "A chat assistant that can look up the weather with a web API "
     "(GET https://api.weather.example/v1?city=...).", "types": ["core.agent", "tool.http"]},
    {"id": "rag", "request": "Answer questions only from our knowledge base stored in memory space 'docs'.",
     "types": ["core.memory_recall", "core.llm"]},
    {"id": "extract", "request": "Turn an invoice email into fields: vendor, total, due date.",
     "types": ["model.chat"]},
    {"id": "per_topic", "request": "For each topic in a list, write a short paragraph in parallel, then combine "
     "them into one summary.", "types": ["core.map", "core.join"]},
    {"id": "sentiment", "request": "Detect the sentiment of a review; thank happy customers, apologise to unhappy "
     "ones.", "types": ["core.router"]},
    {"id": "form", "request": "Draft a blog post, then ask a person for the title they want before finishing.",
     "types": ["core.human_input"]},
    {"id": "remember", "request": "Chat with the user and remember their preferences between conversations.",
     "types": ["core.agent"]},
    {"id": "mcp", "request": "An agent that manages GitHub issues through an MCP server at "
     "https://mcp.example.com/github.", "types": ["core.agent", "mcp.server"]},
]  # fmt: skip
CONFIRM = "Yes, build it."


@dataclass
class CaseResult:
    id: str
    passed: bool
    applied: bool = False
    errors: list[str] = field(default_factory=list)
    missing_types: list[str] = field(default_factory=list)
    turns: int = 0
    tokens: int = 0
    seconds: float = 0.0
    problem: str | None = None


async def run_case(
    case: dict[str, Any], adapter: Any, resources: Any, model: ResolvedModel, registry: Registry, settings: Settings
) -> CaseResult:
    started = time.monotonic()
    templates = list_templates(settings)
    pack = build_pack(registry, templates)
    empty = Workflow(id="wf_eval", name=case["id"])

    async def check(wf: Workflow) -> list[Any]:
        return validate(wf, registry)

    history: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    brief = None
    result = CaseResult(id=case["id"], passed=False)
    for text in (case["request"], CONFIRM):
        ctx = ToolContext(empty, registry, templates, check, brief=brief)
        turn = CopilotTurn(
            adapter, resources, model, pack, ctx, history=history, proposals=proposals, usage=usage,
            token_cap=2_000_000, base_version=1,
        )  # fmt: skip
        async for event in turn.run(text, "build"):
            if event["type"] == "error":
                result.problem = event["message"]
        result.turns += 1
        brief = ctx.brief
        if proposals:
            break
    result.tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    result.seconds = round(time.monotonic() - started, 1)
    if not proposals:
        result.problem = result.problem or "no proposal"
        return result
    try:
        built, _ = apply_batch(empty, parse_commands(proposals[-1]["commands"]))
    except Exception as exc:  # noqa: BLE001 - reported per case
        result.problem = f"proposal does not apply: {exc}"
        return result
    result.applied = True
    result.errors = [f"{d.code} {d.message}" for d in validate(built, registry) if d.severity == "error"]
    present = {n.type for n in built.nodes} | {r.type for r in built.resources}
    result.missing_types = [t for t in case["types"] if t not in present]
    result.passed = not result.errors and not result.missing_types
    return result


async def run(model: str, only: str | None = None) -> list[CaseResult]:
    from agentcanvas.adapters import get_adapter

    adapter = get_adapter()
    resources = adapter.new_resources()
    resolved = ResolvedModel(model=model, origin="eval")
    cases = [c for c in CASES if only in (None, c["id"])]
    settings = Settings(env="prod")
    return [await run_case(c, adapter, resources, resolved, default_registry(), settings) for c in cases]


def main() -> None:
    parser = argparse.ArgumentParser(description="Copilot eval harness")
    parser.add_argument("--model", required=True, help="provider:model")
    parser.add_argument("--case", help="run one case by id")
    parser.add_argument("--out", help="write the results as JSON")
    args = parser.parse_args()
    results = asyncio.run(run(args.model, args.case))
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        detail = r.problem or "; ".join(r.errors + [f"missing {t}" for t in r.missing_types])
        print(f"{status} {r.id:12} {r.tokens:>7} tok {r.seconds:>5}s {detail}")
    print(f"{sum(r.passed for r in results)}/{len(results)} passed")
    if args.out:
        with open(args.out, "w") as f:
            json.dump([asdict(r) for r in results], f, indent=2)


if __name__ == "__main__":
    main()
