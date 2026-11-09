"""Copilot eval harness (S14): build workflows from plain requests with a real model and check the results.

    uv run python -m agentcanvas.copilot.evals --model anthropic:claude-sonnet-5-5 [--case greeter]
        [--suite build|debug|both] [--engine deepagents] [--min-pass 0.8] [--out results.json] [--fallback-to-demo]

Each case starts from an empty workflow, sends the request in Build mode, answers a brief with "Yes, build it",
then checks the proposal: it applies, has no validation errors, and uses the expected blocks. The model's key comes
from the usual environment variable (e.g. ANTHROPIC_API_KEY). CI runs the harness with a scripted model on every
change, and with a real model nightly when the repository has a key (`.github/workflows/copilot-evals.yml`);
`--min-pass` fails a run below a pass rate. `make copilot-evals` runs both suites on your computer (Ollama by default,
the offline demo Copilot when no model can be reached).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import Callable
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
    removed: list[str] = field(default_factory=list)  # debug suite: steps of the workflow the fix deleted
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
    return judge(case, built, registry, result)


def judge(case: dict[str, Any], built: Workflow, registry: Registry, result: CaseResult) -> CaseResult:
    """Whether the workflow the proposal built passes the case: no validation errors, the blocks a build case asks
    for, and (a debug case) every step of the broken workflow still there: deleting the broken step silences the
    validator without fixing anything."""
    result.errors = [f"{d.code} {d.message}" for d in validate(built, registry) if d.severity == "error"]
    present = {n.type for n in built.nodes} | {r.type for r in built.resources}
    result.missing_types = [t for t in case.get("types", []) if t not in present]
    kept = {n.id for n in built.nodes}
    result.removed = [n for n in case.get("keep", []) if n not in kept]
    result.passed = not result.errors and not result.missing_types and not result.removed
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
                keep = [n.id for n in broken_wf.nodes]  # a fix repairs the steps, it does not delete them
                cases.append({"id": f"{name}/{bug}", "request": FIX_REQUEST, "start": broken_wf, "keep": keep})
    return cases


def engine_class(name: str) -> type[CopilotTurn]:
    if name == "deepagents":
        from agentcanvas.adapters.langchain.copilot import DeepAgentTurn

        return DeepAgentTurn
    return CopilotTurn


DEMO = "demo"  # the key-less demo Copilot: it proves the harness runs, it says nothing about a real model


def _ollama_up() -> bool:
    import urllib.request

    host = os.environ.get("AGENTCANVAS_LOCAL_MODELS_HOST") or "localhost"
    try:
        with urllib.request.urlopen(f"http://{host}:11434/api/tags", timeout=2):
            return True
    except OSError:
        return False


def unusable(
    model: str, registry: Registry | None = None, *, ollama_up: Callable[[], bool] | None = None
) -> str | None:
    """Why `model` cannot be used from here (no server running, no API key), or None when it can."""
    if model == DEMO:
        return None
    provider = model.partition(":")[0]
    info = (registry or default_registry()).provider(provider)
    if info is None:
        return f"'{provider}' is not a model provider this AgentCanvas knows"
    if provider == "ollama":
        return (
            None if (ollama_up or _ollama_up)() else "Ollama is not running on this computer (http://localhost:11434)"
        )
    if info.local:
        return None  # another local server: the run reports its own connection error
    if info.key_env and not os.environ.get(info.key_env):
        return f"{info.key_env} is not set"
    return None


async def run(
    model: str, only: str | None = None, suite: str = "build", engine: str = "loop", settings: Settings | None = None
) -> list[CaseResult]:
    from agentcanvas.adapters import get_adapter, model_service

    if model == DEMO:
        from agentcanvas.adapters.langchain.testing import DemoResources

        resources = get_adapter().new_resources(resources_class=DemoResources)
    else:
        resources = get_adapter().new_resources()
    resolved = ResolvedModel(model=model, origin="eval")
    settings = settings or Settings(env="prod")
    registry = default_registry()
    pool = CASES if suite == "build" else debug_cases(settings, registry)
    cases = [c for c in pool if only in (None, c["id"])]
    turn = engine_class(engine)
    return [await run_case(c, model_service(), resources, resolved, registry, settings, turn) for c in cases]


def report(results: list[CaseResult], engine: str, model: str, suite: str) -> int:
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        detail = r.problem or "; ".join(
            r.errors + [f"missing {t}" for t in r.missing_types] + [f"deleted step {n}" for n in r.removed]
        )
        print(f"{status} {r.id:12} {r.tokens:>7} tok {r.seconds:>5}s {detail}")
    passed = sum(r.passed for r in results)
    print(f"{passed}/{len(results)} passed ({engine}, {model}, {suite})")
    return passed


def main() -> None:
    parser = argparse.ArgumentParser(description="Copilot eval harness")
    parser.add_argument("--model", required=True, help=f"provider:model, or {DEMO} (the key-less demo Copilot)")
    parser.add_argument("--case", help="run one case by id")
    parser.add_argument(
        "--suite", choices=["build", "debug", "both"], default="build", help="debug: ~100 planted bugs; both: the two"
    )
    parser.add_argument("--engine", choices=["loop", "deepagents"], default="loop", help="the Copilot's engine")
    parser.add_argument("--min-pass", type=float, default=0.0, help="exit 1 when fewer cases pass (0-1)")
    parser.add_argument("--out", help="write the results as JSON (both suites: one object with a list for each)")
    parser.add_argument(
        "--fallback-to-demo", action="store_true",
        help="when the model cannot be reached (no Ollama running, no API key), say so and use the demo Copilot",
    )  # fmt: skip
    args = parser.parse_args()
    model = args.model
    if why := unusable(model):
        if not args.fallback_to_demo:
            raise SystemExit(f"cannot use {model}: {why}")
        print(f"NOTE: {model} is not available ({why}); running the offline demo Copilot instead.")
        model = DEMO
    if model == DEMO:
        print(
            "NOTE: the demo Copilot only builds a one-step greeter, so most cases fail by design. It shows the harness "
            "runs end to end and says nothing about a real model; set MODEL=provider:model for a score."
        )
    suites = ["build", "debug"] if args.suite == "both" else [args.suite]
    done: dict[str, list[CaseResult]] = {}
    low: list[str] = []
    for suite in suites:
        done[suite] = results = asyncio.run(run(model, args.case, suite, args.engine))
        if not results and not args.case:
            raise SystemExit(f"the {suite} suite has no cases")
        passed = report(results, args.engine, model, suite)
        if results and passed / len(results) < args.min_pass:
            low.append(f"{suite}: {passed / len(results):.0%}")
    if args.case and not any(done.values()):  # a typo would otherwise "pass" with 0/0 and exit 0
        raise SystemExit(f"no case '{args.case}' in the {args.suite} suite(s)")
    if args.out:
        body: Any = {"model": model, "engine": args.engine, **{s: [asdict(r) for r in rs] for s, rs in done.items()}}
        if args.suite != "both":
            body = body[suites[0]]  # one suite keeps the list the nightly job has always written
        with open(args.out, "w") as f:
            json.dump(body, f, indent=2)
        print(f"results written to {args.out}")
    if low:
        raise SystemExit(f"pass rate below {args.min_pass:.0%} ({'; '.join(low)})")


if __name__ == "__main__":
    main()
