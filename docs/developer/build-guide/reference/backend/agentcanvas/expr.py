"""Safe expression evaluation. NEVER use Python eval() on user input.

- Conditions (routers):   CEL  (https://cel.dev) via `cel-python`, e.g.  state.urgency == "high"
- Text templates (prompts): sandboxed Jinja2, e.g.  "Classify: {{ state.ticket }}"
"""

from __future__ import annotations

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


def render_template(source: str, state: dict[str, Any]) -> str:
    try:
        return _jinja.from_string(source).render(state=state)
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
