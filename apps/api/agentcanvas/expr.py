"""Safe expression evaluation. NEVER use Python eval() on user input.

- Conditions (routers):   CEL  (https://cel.dev) via `cel-python`, e.g.  state.urgency == "high"
- Text templates (prompts): sandboxed Jinja2, e.g.  "Classify: {{ state.ticket }}"
- A "Decide with AI" step's question and how its model's answer is read: here because every framework's runtime and
  every export carries this module, so all of them decide alike.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Any

import celpy
from celpy.adapter import json_to_cel
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

_cel_env = celpy.Environment()
_jinja = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)


class ExpressionError(ValueError):
    pass


@lru_cache(maxsize=1024)
def _compile_cel(source: str):
    try:
        return _cel_env.program(_cel_env.compile(source))
    except Exception as exc:  # celpy raises several error types
        raise ExpressionError(f"invalid condition '{source}': {exc}") from exc


def check_condition(source: str) -> None:
    """Raise ExpressionError if the condition does not compile (used by the validator)."""
    _compile_cel(source)


def eval_condition(source: str, state: dict[str, Any]) -> bool:
    program = _compile_cel(source)
    result = program.evaluate({"state": json_to_cel(_jsonable(state))})
    return bool(result)


def eval_expression(source: str, variables: dict[str, Any]) -> Any:
    """Evaluate CEL over named variables, e.g. `event.data.amount > 100` with {"event": {...}} (event filters)."""
    program = _compile_cel(source)
    try:
        return program.evaluate({k: json_to_cel(_jsonable(v)) for k, v in variables.items()})
    except Exception as exc:  # missing fields, type errors
        raise ExpressionError(f"cannot evaluate '{source}': {exc}") from exc


def to_cel(value: Any) -> Any:
    """Convert a value for CEL once, to evaluate many expressions over it with `eval_cel`."""
    return json_to_cel(_jsonable(value))


def eval_cel(source: str, variables: dict[str, Any]) -> Any:
    """`eval_expression` over variables already converted with `to_cel` (no per-call conversion)."""
    program = _compile_cel(source)
    try:
        return program.evaluate(variables)
    except Exception as exc:  # missing fields, type errors
        raise ExpressionError(f"cannot evaluate '{source}': {exc}") from exc


def render_template(source: str, state: dict[str, Any], **names: Any) -> str:
    """Render a sandboxed Jinja template with `state` (and extra names, e.g. `args` for HTTP tool URLs)."""
    try:
        return _jinja.from_string(source).render(state=state, **names)
    except Exception as exc:
        raise ExpressionError(f"template error in '{source[:40]}…': {exc}") from exc


def _jsonable(value: Any) -> Any:
    """Convert LangChain messages and other objects to plain JSON for CEL."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# ------------------------------------------------------------- Decide with AI ----
DECIDE_INSTRUCTIONS = (
    "Choose exactly one of these options:\n{options}\n\nAnswer with JSON only, no other text: "
    '{{"choice": "<one option name>", "confidence": <0 to 1, how sure you are>, "reason": "<one short sentence>"}}'
)


def decision(text: str, choices: list[str] | dict[str, str]) -> dict[str, Any]:
    """The decision in a model's answer: {choice, confidence, reason}; `choice` is None when the answer names no
    option (no JSON, or a name that is not one of `choices`)."""
    start, end = text.find("{"), text.rfind("}")
    try:
        found = json.loads(text[start : end + 1]) if 0 <= start < end else {}
    except ValueError:
        found = {}
    found = found if isinstance(found, dict) else {}
    choice = str(found.get("choice", "")).strip()
    raw = found.get("confidence")
    try:
        value = float(raw) if isinstance(raw, int | float | str) else math.nan
    except ValueError:
        value = math.nan
    confidence = min(1.0, max(0.0, value)) if math.isfinite(value) else None
    return {"choice": choice if choice in choices else None, "confidence": confidence,
            "reason": str(found.get("reason") or "")[:500]}  # fmt: skip


def chosen(made: Any, default: str, min_confidence: float | None) -> str:
    """The path a decision takes: its choice, or `default` when it made none or is less sure than the minimum."""
    made = made if isinstance(made, dict) else {}
    confidence = made.get("confidence")
    unsure = min_confidence is not None and (not isinstance(confidence, int | float) or confidence < min_confidence)
    return default if unsure or not made.get("choice") else str(made["choice"])


def decide_prompt(prompt: str, choices: dict[str, str]) -> str:
    """What a Decide with AI step asks its model: the rendered question, the options and the answer's shape."""
    options = "\n".join(f"- {route}: {when}" for route, when in choices.items())
    return f"{prompt}\n\n" + DECIDE_INSTRUCTIONS.format(options=options)
