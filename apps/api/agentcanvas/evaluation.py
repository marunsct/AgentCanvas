"""Evaluators (FR-EVAL-03): score one run of a workflow against an example.

Each evaluator turns an `Observation` (the example's input and reference, and what the run produced) into a
`Score` between 0 and 1, which passes when it reaches the evaluator's threshold.

| kind | config | score |
|---|---|---|
| `exact` | `channels` (default: every reference channel) | share of channels equal to the reference |
| `regex` | `channel`, `pattern` | 1 when the pattern is found in the channel's text |
| `json_schema` | `channel`, `schema` | 1 when the channel's value fits the schema |
| `llm_judge` | `model`, `rubric`, `channels` | the judge model's 0-1 grade, with its reasoning |
| `trajectory` | `mode`: strict / unordered / subset | 1 when the run's tool calls match the example's |
| `code` | `function`: `scripts.<module>:<fn>` | what the workspace function returns |
| `composite` | `evaluators`, `mode`: weighted / all / any, `weights` | their weighted mean, or 1 when all / any pass |
| `pairwise` | `model`, `rubric`, `channels` | experiments only: each target against the first, see `pairwise` |

A composite scores its parts itself (their specs are resolved into its config by `spec_of`), so it needs no
other evaluator to run first; parts that cannot score are left out of a weighted mean.

Evaluators never raise: a broken evaluator scores None with the reason in its comment, so one bad evaluator
cannot fail a whole experiment. The model call and the workspace function come from the framework adapter.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, Field, ValidationError

from agentcanvas import safe_regex
from agentcanvas.errors import InvalidInput
from agentcanvas.registry import default_registry
from agentcanvas.workspace.bundle import collect

Kind = Literal["exact", "regex", "json_schema", "llm_judge", "trajectory", "code", "composite", "pairwise"]
DEFAULT_THRESHOLD: dict[str, float] = {"llm_judge": 0.7, "composite": 0.7, "pairwise": 0.5}
ONLY_IN_EXPERIMENTS = ("pairwise",)
JUDGE_TIMEOUT_S = 60.0


class ExactConfig(BaseModel):
    channels: list[str] | None = None


class RegexConfig(BaseModel):
    channel: str
    pattern: str


class JsonSchemaConfig(BaseModel):
    channel: str
    schema_: dict[str, Any] = Field(alias="schema")


class JudgeConfig(BaseModel):
    model: str
    rubric: str = Field(min_length=1)
    channels: list[str] | None = Field(default=None, description="output channels the judge sees (default: all)")
    include_reference: bool = True


class TrajectoryConfig(BaseModel):
    mode: Literal["strict", "unordered", "subset"] = "strict"


class CodeConfig(BaseModel):
    function: str = Field(description="scripts.<module>:<function>, (sample) -> {score, comment}")
    pinned: str | None = Field(
        default=None, description="a release gate's copy: the code_hash it was saved with (set by the server)"
    )


class CompositeConfig(BaseModel):
    evaluators: list[str] = Field(min_length=1, max_length=20, description="the workflow's evaluators it combines")
    mode: Literal["weighted", "all", "any"] = "weighted"
    weights: dict[str, float] = Field(default_factory=dict, description="weighted: name -> weight (default 1)")
    parts: list[dict[str, Any]] | None = Field(default=None, description="resolved specs (set by the server)")


class PairwiseConfig(BaseModel):
    model: str
    rubric: str = Field(min_length=1, description="what makes one output better than the other")
    channels: list[str] | None = Field(default=None, description="output channels the judge sees (default: all)")


CONFIGS: dict[str, type[BaseModel]] = {
    "exact": ExactConfig,
    "regex": RegexConfig,
    "json_schema": JsonSchemaConfig,
    "llm_judge": JudgeConfig,
    "trajectory": TrajectoryConfig,
    "code": CodeConfig,
    "composite": CompositeConfig,
    "pairwise": PairwiseConfig,
}


def check_config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """The config, validated for its kind (422 otherwise)."""
    try:
        parsed = CONFIGS[kind].model_validate(config)
    except ValidationError as exc:
        raise InvalidInput(f"{kind} evaluator: {exc.errors()[0]['loc'][0]}: {exc.errors()[0]['msg']}") from None
    if isinstance(parsed, RegexConfig):
        try:
            re.compile(parsed.pattern)
        except re.error as exc:
            raise InvalidInput(f"regex evaluator: invalid pattern: {exc}") from None
    elif isinstance(parsed, JsonSchemaConfig):
        try:
            Draft202012Validator.check_schema(parsed.schema_)
        except SchemaError as exc:
            raise InvalidInput(f"json_schema evaluator: invalid schema: {exc.message}") from None
    elif isinstance(parsed, JudgeConfig | PairwiseConfig) and not default_registry().usable_model(parsed.model):
        raise InvalidInput(f"{kind} evaluator: '{parsed.model}' is not a catalog model or a known provider's model")
    elif isinstance(parsed, CompositeConfig):
        parsed.parts = None
        if len(set(parsed.evaluators)) != len(parsed.evaluators):
            raise InvalidInput("composite evaluator: each evaluator once")
        if extra := sorted(set(parsed.weights) - set(parsed.evaluators)):
            raise InvalidInput(f"composite evaluator: weights for evaluators it does not combine: {extra}")
        if any(w < 0 for w in parsed.weights.values()) or not any(
            parsed.weights.get(n, 1.0) > 0 for n in parsed.evaluators
        ):
            raise InvalidInput("composite evaluator: weights are positive (or 0 to leave one out)")
    elif isinstance(parsed, CodeConfig):
        parsed.pinned = None
        if not re.fullmatch(r"scripts(\.\w+)+:\w+", parsed.function):
            raise InvalidInput("code evaluator: the function must be 'scripts.<module>:<function>' in the workspace")
    return parsed.model_dump(by_alias=True, exclude_none=True)


class Observation(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    reference: dict[str, Any] = Field(default_factory=dict)
    trajectory: list[str] = Field(default_factory=list, description="tool calls the run made, in order")
    expected_trajectory: list[str] | None = None
    status: str = "success"
    error: str | None = None


class Score(BaseModel):
    evaluator: str
    score: float | None
    passed: bool
    comment: str = ""


class Spec(BaseModel):
    name: str
    kind: Kind
    config: dict[str, Any] = Field(default_factory=dict)
    threshold: float | None = None


def spec_of(e: Any, siblings: Any = ()) -> Spec:
    """The spec of evaluator `e` (anything with name, kind, config, threshold); a composite gets its parts' specs,
    looked up by name among `siblings` (the workflow's evaluators), so it scores on its own."""
    config = dict(e.config or {})
    if e.kind == "composite":
        by_name = {x.name: x for x in siblings}
        config["parts"] = [spec_of(by_name[n]).model_dump() for n in config.get("evaluators", []) if n in by_name]
    return Spec(name=e.name, kind=e.kind, config=config, threshold=e.threshold)


# (system, prompt, model) -> reply text;  (import path) -> async (sample) -> dict;  (import path) -> code_hash
Complete = Callable[[str, str, str], Awaitable[str]]
Script = Callable[[str], Callable[[dict[str, Any]], Awaitable[Any]]]
Fingerprint = Callable[[str], str]
CODE_CHANGED = "its code changed since the release gate was saved: an admin saves the gate again to accept that"


def code_hash(workspace: Path | None, function: str) -> str:
    """What a code evaluator runs: its function's module and every workspace module that imports, hashed. A
    release gate keeps this, so the code it measures with cannot change behind its back (S13)."""
    h = hashlib.sha256(function.encode())
    if workspace is not None:
        for rel in collect(workspace, "evaluator", function).files:
            if rel.endswith(".py"):  # the code it imports (not a workflow folder's data files)
                h.update(f"\0{rel}\0".encode() + (workspace / rel).read_bytes())
    return h.hexdigest()


def pinned_code(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """The code evaluator specs in `spec` (itself, or a composite's parts)."""
    if spec["kind"] == "composite":
        return [p for p in spec["config"].get("parts") or [] if p["kind"] == "code"]
    return [spec] if spec["kind"] == "code" else []


JUDGE_SYSTEM = (
    "You grade the output of an AI workflow against a rubric. Reply with JSON only: "
    '{"score": <number from 0 to 1>, "reasoning": "<one or two sentences>"}.'
)


def trajectory_score(mode: str, actual: list[str], expected: list[str]) -> float:
    if mode == "strict":
        return float(actual == expected)
    if mode == "unordered":
        return float(Counter(actual) == Counter(expected))
    return float(not (Counter(expected) - Counter(actual)))  # subset: every expected call was made


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)


def _grade(reply: str) -> tuple[float, str]:
    """The judge's score and reasoning from its reply (JSON, or a number somewhere in the text)."""
    match = re.search(r"\{.*\}", reply, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            return float(data["score"]), str(data.get("reasoning", ""))
        except (ValueError, KeyError, TypeError):
            pass
    number = re.search(r"\b(0(?:\.\d+)?|1(?:\.0+)?)\b", reply)
    if number is None:
        raise ValueError(f"the judge's reply has no score: {reply[:200]!r}")
    return float(number.group(1)), reply.strip()[:500]


async def score(
    spec: Spec,
    obs: Observation,
    complete: Complete | None = None,
    script: Script | None = None,
    fingerprint: Fingerprint | None = None,
) -> Score:
    threshold = spec.threshold if spec.threshold is not None else DEFAULT_THRESHOLD.get(spec.kind, 1.0)

    def done(value: float | None, comment: str = "") -> Score:
        if value is not None and not math.isfinite(value):  # max/min would turn NaN into a perfect 1.0
            value, comment = None, f"not a finite score: {value}"
        value = None if value is None else max(0.0, min(1.0, value))
        return Score(evaluator=spec.name, score=value, passed=value is not None and value >= threshold, comment=comment)

    try:
        cfg = CONFIGS[spec.kind].model_validate(spec.config)
        if isinstance(cfg, ExactConfig):
            channels = cfg.channels or list(obs.reference)
            if not channels:
                return done(None, "the example has no reference output")
            wrong = [c for c in channels if c not in obs.reference or obs.output.get(c) != obs.reference[c]]
            return done(1 - len(wrong) / len(channels), f"differs: {', '.join(wrong)}" if wrong else "")
        if isinstance(cfg, RegexConfig):
            if cfg.channel not in obs.output:
                return done(0.0, f"the run did not produce '{cfg.channel}'")
            found = await safe_regex.search(cfg.pattern, _text(obs.output[cfg.channel]))
            return done(float(found), "" if found else f"/{cfg.pattern}/ not found in '{cfg.channel}'")
        if isinstance(cfg, JsonSchemaConfig):
            if cfg.channel not in obs.output:
                return done(0.0, f"the run did not produce '{cfg.channel}'")
            errors = list(Draft202012Validator(cfg.schema_).iter_errors(obs.output[cfg.channel]))
            return done(float(not errors), errors[0].message if errors else "")
        if isinstance(cfg, TrajectoryConfig):
            if obs.expected_trajectory is None:
                return done(None, "the example has no reference trajectory")
            value = trajectory_score(cfg.mode, obs.trajectory, obs.expected_trajectory)
            return done(value, "" if value else f"tool calls {obs.trajectory} vs expected {obs.expected_trajectory}")
        if isinstance(cfg, JudgeConfig):
            if complete is None:
                return done(None, "no model available for the judge")
            shown = {k: v for k, v in obs.output.items() if cfg.channels is None or k in cfg.channels}
            prompt = f"Rubric:\n{cfg.rubric}\n\nInput:\n{_text(obs.input)}\n\nOutput:\n{_text(shown)}"
            if cfg.include_reference and obs.reference:
                prompt += f"\n\nReference output:\n{_text(obs.reference)}"
            if obs.status != "success":
                prompt += f"\n\nThe run ended with status '{obs.status}'" + (f": {obs.error}" if obs.error else "")
            reply = await asyncio.wait_for(complete(JUDGE_SYSTEM, prompt, cfg.model), JUDGE_TIMEOUT_S)
            value, reasoning = _grade(reply)
            return done(value, reasoning)
        if isinstance(cfg, CompositeConfig):
            return await _composite(cfg, obs, complete, script, fingerprint, done)
        if isinstance(cfg, PairwiseConfig):
            return done(None, "a pairwise judge compares two experiment targets: it scores in experiments only")
        assert isinstance(cfg, CodeConfig)
        if script is None:
            return done(None, "workspace code is not available here")
        if cfg.pinned is not None and (
            fingerprint is None or await asyncio.to_thread(fingerprint, cfg.function) != cfg.pinned
        ):
            return done(None, CODE_CHANGED)
        out = await asyncio.wait_for(script(cfg.function)(obs.model_dump()), JUDGE_TIMEOUT_S)
        if isinstance(out, bool | int | float):
            return done(float(out))
        return done(None if out.get("score") is None else float(out["score"]), str(out.get("comment") or ""))
    except Exception as exc:  # noqa: BLE001 - an evaluator that breaks scores None, it never stops an experiment
        return done(None, f"{type(exc).__name__}: {exc}"[:500])


async def _composite(
    cfg: CompositeConfig,
    obs: Observation,
    complete: Complete | None,
    script: Script | None,
    fingerprint: Fingerprint | None,
    done: Any,
) -> Score:
    parts = {p["name"]: Spec(**p) for p in cfg.parts or []}
    if missing := [n for n in cfg.evaluators if n not in parts]:
        return done(None, f"no longer an evaluator of the workflow: {', '.join(missing)}")
    if any(p.kind in ("composite", *ONLY_IN_EXPERIMENTS) for p in parts.values()):
        return done(None, "a composite combines plain evaluators only")
    scores = {n: await score(parts[n], obs, complete, script, fingerprint) for n in cfg.evaluators}
    summary = ", ".join(f"{n} {'—' if s.score is None else f'{s.score:.2f}'}" for n, s in scores.items())
    if cfg.mode == "all":
        return done(float(all(s.passed for s in scores.values())), summary)
    if cfg.mode == "any":
        return done(float(any(s.passed for s in scores.values())), summary)
    weighted = [(cfg.weights.get(n, 1.0), s.score) for n, s in scores.items() if s.score is not None]
    total = sum(w for w, _ in weighted)
    if not total:
        return done(None, f"none of its evaluators scored ({summary})")
    return done(sum(w * v for w, v in weighted) / total, summary)


PAIRWISE_SYSTEM = (
    "You compare two outputs of an AI workflow for the same input against a rubric. Reply with JSON only: "
    '{"winner": "A" | "B" | "tie", "reasoning": "<one or two sentences>"}.'
)


async def pairwise(spec: Spec, input: dict[str, Any], a: Observation, b: Observation, complete: Complete) -> Score:
    """How `b` fares against `a` (1 better, 0.5 as good, 0 worse), judged twice with the outputs in swapped places so
    a judge's preference for a position cancels out: the two verdicts are averaged. A run that failed loses to one
    that succeeded without asking the judge."""

    def verdict(value: float | None, comment: str) -> Score:
        return Score(evaluator=spec.name, score=value, passed=value is not None and value >= 0.5, comment=comment)

    ok_a, ok_b = a.status == "success", b.status == "success"
    if not (ok_a and ok_b):
        value = 0.5 if ok_a == ok_b else float(ok_b)
        return verdict(value, "both runs failed" if not (ok_a or ok_b) else "only one run succeeded")
    try:
        cfg = PairwiseConfig.model_validate(spec.config)

        def shown(o: Observation) -> str:
            return _text({k: v for k, v in o.output.items() if cfg.channels is None or k in cfg.channels})

        async def ask(first: Observation, second: Observation) -> tuple[str, str]:
            prompt = (
                f"Rubric:\n{cfg.rubric}\n\nInput:\n{_text(input)}\n\n"
                f"Output A:\n{shown(first)}\n\nOutput B:\n{shown(second)}"
            )
            reply = await asyncio.wait_for(complete(PAIRWISE_SYSTEM, prompt, cfg.model), JUDGE_TIMEOUT_S)
            match = re.search(r"\{.*\}", reply, re.S)
            data = json.loads(match.group(0)) if match else {}
            winner = str(data.get("winner", "")).strip().upper()
            if winner not in ("A", "B", "TIE"):
                raise ValueError(f"the judge named no winner: {reply[:200]!r}")
            return winner, str(data.get("reasoning", ""))

        (w1, r1), (w2, r2) = await asyncio.gather(ask(a, b), ask(b, a))
        v1 = {"B": 1.0, "A": 0.0}.get(w1, 0.5)  # b was in place B
        v2 = {"A": 1.0, "B": 0.0}.get(w2, 0.5)  # b was in place A
        note = "" if v1 == v2 else "the verdicts disagreed when the outputs swapped places; "
        return verdict((v1 + v2) / 2, f"{note}{r1}".strip()[:500] or r2[:500])
    except Exception as exc:  # noqa: BLE001 - like every evaluator: None with the reason
        return verdict(None, f"{type(exc).__name__}: {exc}"[:500])


def trajectory_of(events: list[dict[str, Any]]) -> list[str]:
    """The tool calls a run's models asked for, in order (from its `ac.model_reply` events)."""
    calls = []
    for ev in events:
        if ev.get("type") == "CUSTOM" and ev.get("name") == "ac.model_reply":
            reply = (ev.get("value") or {}).get("reply")
            if isinstance(reply, dict):
                calls += [c["name"] for c in reply.get("tool_calls") or []]
    return calls
