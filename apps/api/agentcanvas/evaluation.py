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

Evaluators never raise: a broken evaluator scores None with the reason in its comment, so one bad evaluator
cannot fail a whole experiment. The model call and the workspace function come from the framework adapter.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, Field, ValidationError

from agentcanvas import safe_regex
from agentcanvas.errors import InvalidInput
from agentcanvas.registry import default_registry

Kind = Literal["exact", "regex", "json_schema", "llm_judge", "trajectory", "code"]
DEFAULT_THRESHOLD: dict[str, float] = {"llm_judge": 0.7}
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


CONFIGS: dict[str, type[BaseModel]] = {
    "exact": ExactConfig,
    "regex": RegexConfig,
    "json_schema": JsonSchemaConfig,
    "llm_judge": JudgeConfig,
    "trajectory": TrajectoryConfig,
    "code": CodeConfig,
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
    elif isinstance(parsed, JudgeConfig) and default_registry().model(parsed.model) is None:
        raise InvalidInput(f"llm_judge evaluator: '{parsed.model}' is not a model in the catalog")
    elif isinstance(parsed, CodeConfig) and not re.fullmatch(r"scripts(\.\w+)+:\w+", parsed.function):
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


# (system, prompt, model) -> reply text;  (import path) -> async (sample) -> dict
Complete = Callable[[str, str, str], Awaitable[str]]
Script = Callable[[str], Callable[[dict[str, Any]], Awaitable[Any]]]

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


async def score(spec: Spec, obs: Observation, complete: Complete | None = None, script: Script | None = None) -> Score:
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
        assert isinstance(cfg, CodeConfig)
        if script is None:
            return done(None, "workspace code is not available here")
        out = await asyncio.wait_for(script(cfg.function)(obs.model_dump()), JUDGE_TIMEOUT_S)
        if isinstance(out, bool | int | float):
            return done(float(out))
        return done(None if out.get("score") is None else float(out["score"]), str(out.get("comment") or ""))
    except Exception as exc:  # noqa: BLE001 - an evaluator that breaks scores None, it never stops an experiment
        return done(None, f"{type(exc).__name__}: {exc}"[:500])


def trajectory_of(events: list[dict[str, Any]]) -> list[str]:
    """The tool calls a run's models asked for, in order (from its `ac.model_reply` events)."""
    calls = []
    for ev in events:
        if ev.get("type") == "CUSTOM" and ev.get("name") == "ac.model_reply":
            reply = (ev.get("value") or {}).get("reply")
            if isinstance(reply, dict):
                calls += [c["name"] for c in reply.get("tool_calls") or []]
    return calls
