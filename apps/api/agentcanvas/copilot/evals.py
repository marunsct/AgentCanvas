"""Copilot eval harness (S14): build workflows from plain requests with a real model and check the results.

    uv run python -m agentcanvas.copilot.evals --model anthropic:claude-sonnet-5-5 [--suite debug] [--case greeter]
        [--engine deepagents] [--min-pass 0.8] [--out results.json]

Each case starts from an empty workflow, sends the request in Build mode, answers a brief with "Yes, build it",
then checks the proposal: it applies, has no validation errors, and uses the expected blocks. The model's key comes
from the usual environment variable (e.g. ANTHROPIC_API_KEY). CI runs the harness with a scripted model on every
change, and with a real model nightly when the repository has a key (`.github/workflows/copilot-evals.yml`);
`--min-pass` fails a run below a pass rate.
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
    case: dict[str, Any],
    models: Any,
    resources: Any,
    model: ResolvedModel,
    registry: Registry,
    settings: Settings,
    engine: type[CopilotTurn] = CopilotTurn,
) -> CaseResult:
    started = time.monotonic()
    templates = list_templates(settings)
    pack = build_pack(registry, templates)
    empty = case.get("start") or Workflow(id="wf_eval", name=case["id"])

    async def check(wf: Workflow) -> list[Any]:
        return validate(wf, registry)

    history: list[dict[str, Any]] = []
    proposals: list[dict[str, Any]] = []
    usage: dict[str, int] = {}
    brief = None
    result = CaseResult(id=case["id"], passed=False)
    for text in (case["request"], CONFIRM):
        ctx = ToolContext(empty, registry, templates, check, brief=brief)
        turn = engine(
            models, resources, model, pack, ctx, history=history, proposals=proposals, usage=usage,
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
    result.missing_types = [t for t in case.get("types", []) if t not in present]
    result.passed = not result.errors and not result.missing_types
    return result


FIX_REQUEST = "This workflow has a problem. Find it, explain it in one sentence, and fix it."


def debug_cases(settings: Settings, registry: Registry) -> list[dict[str, Any]]:
    """Planted bugs (S15): each starter template broken in up to five ways the validator reports. A case passes
    when the Copilot's fix leaves no validation errors."""
    cases: list[dict[str, Any]] = []
    for name, doc in list_templates(settings).items():
        if name.startswith(("demo", "dev")):
            continue
        wf = Workflow.model_validate({**doc, "id": f"wf_{name}"})
        to_end = next((e for e in wf.edges if e.kind == "flow" and e.target.node == "END"), None)
        wiring = next((e for e in wf.edges if e.kind == "wiring" and e.target.port == "model"), None)
        writer = next((n for n in wf.nodes if isinstance(n.config.get("output"), str)), None)
        router = next((n for n in wf.nodes if n.type == "core.router" and n.config.get("cases")), None)
        prompt = next((n for n in wf.nodes if isinstance(n.config.get("prompt"), str)), None)
        bugs: list[tuple[str, list[dict[str, Any]]]] = []
        if to_end:
            bugs.append(("no_end", [{"op": "Disconnect", "id": to_end.id}]))
        if wiring:
            bugs.append(("no_model", [{"op": "Disconnect", "id": wiring.id}]))
        if writer:
            bugs.append(
                ("bad_output", [{"op": "UpdateNode", "id": writer.id, "patch": {"config": {"output": "nowhere"}}}])
            )
        if router:
            broken = [dict(router.config["cases"][0], when="state. =="), *router.config["cases"][1:]]
            bugs.append(
                ("bad_condition", [{"op": "UpdateNode", "id": router.id, "patch": {"config": {"cases": broken}}}])
            )
        if prompt:
            bad = str(prompt.config["prompt"]) + " {{ state.missing_field }}"
            bugs.append(
                ("unknown_field", [{"op": "UpdateNode", "id": prompt.id, "patch": {"config": {"prompt": bad}}}])
            )
        for bug, commands in bugs:
            try:
                broken_wf, _ = apply_batch(wf, parse_commands(commands))
            except Exception:  # noqa: BLE001 - a mutation that does not fit this template is skipped
                continue
            if any(d.severity == "error" for d in validate(broken_wf, registry)):
                cases.append({"id": f"{name}/{bug}", "request": FIX_REQUEST, "start": broken_wf})
    return cases


def engine_class(name: str) -> type[CopilotTurn]:
    if name == "deepagents":
        from agentcanvas.adapters.langchain.copilot import DeepAgentTurn

        return DeepAgentTurn
    return CopilotTurn


async def run(model: str, only: str | None = None, suite: str = "build", engine: str = "loop") -> list[CaseResult]:
    from agentcanvas.adapters import get_adapter, model_service

    resources = get_adapter().new_resources()
    resolved = ResolvedModel(model=model, origin="eval")
    settings = Settings(env="prod")
    registry = default_registry()
    pool = CASES if suite == "build" else debug_cases(settings, registry)
    cases = [c for c in pool if only in (None, c["id"])]
    turn = engine_class(engine)
    return [await run_case(c, model_service(), resources, resolved, registry, settings, turn) for c in cases]


def main() -> None:
    parser = argparse.ArgumentParser(description="Copilot eval harness")
    parser.add_argument("--model", required=True, help="provider:model")
    parser.add_argument("--case", help="run one case by id")
    parser.add_argument("--suite", choices=["build", "debug"], default="build", help="debug: ~30 planted bugs")
    parser.add_argument("--engine", choices=["loop", "deepagents"], default="loop", help="the Copilot's engine")
    parser.add_argument("--min-pass", type=float, default=0.0, help="exit 1 when fewer cases pass (0-1)")
    parser.add_argument("--out", help="write the results as JSON")
    args = parser.parse_args()
    results = asyncio.run(run(args.model, args.case, args.suite, args.engine))
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        detail = r.problem or "; ".join(r.errors + [f"missing {t}" for t in r.missing_types])
        print(f"{status} {r.id:12} {r.tokens:>7} tok {r.seconds:>5}s {detail}")
    passed = sum(r.passed for r in results)
    print(f"{passed}/{len(results)} passed ({args.engine}, {args.model}, {args.suite})")
    if args.out:
        with open(args.out, "w") as f:
            json.dump([asdict(r) for r in results], f, indent=2)
    if results and passed / len(results) < args.min_pass:
        raise SystemExit(f"pass rate {passed / len(results):.0%} is below {args.min_pass:.0%}")


if __name__ == "__main__":
    main()
