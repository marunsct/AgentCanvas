"""Safe expression evaluation. NEVER use Python eval() on user input.

- Conditions (routers):   CEL  (https://cel.dev) via `cel-python`, e.g.  state.urgency == "high"
- Text templates (prompts): sandboxed Jinja2, e.g.  "Classify: {{ state.ticket }}"
- A "Decide with AI" step's question and how its model's answer is read: here because every framework's runtime and
  every export carries this module, so all of them decide alike.
"""

from __future__ import annotations

import contextvars
import functools
import json
import math
import re
import string
import time
from collections.abc import Callable
from functools import lru_cache
from typing import Any

import celpy
from celpy.adapter import json_to_cel
from jinja2 import Environment, StrictUndefined, nodes
from jinja2.nodes import EvalContext
from jinja2.runtime import Context
from jinja2.sandbox import SandboxedEnvironment
from jinja2.visitor import NodeTransformer

_cel_env = celpy.Environment()


class ExpressionError(ValueError):
    pass


# ------------------------------------------------------------- bounded templates ----
# A template renders inside the server process, on its event loop. The sandbox stops escapes but not a template that
# loops for hours or builds a string of gigabytes, so every render has a time limit (checked at each loop step, call,
# attribute and operator) and every way to build something big is checked before it allocates.
RENDER_SECONDS = 1.0  # a template that takes longer stops with an error
MAX_TEXT = 10_000_000  # characters a template may output, or build into one string
MAX_ITEMS = 1_000_000  # items a template may build into one list
MAX_INT_BITS = 100_000  # an integer a template computes (about 30,000 digits)
MAX_JSON_INDENT = 100

_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar("template_deadline", default=None)


class TemplateTooBig(ExpressionError):
    pass


def _tick() -> None:
    deadline = _deadline.get()
    if deadline is not None and time.monotonic() > deadline:
        raise TemplateTooBig(f"the template took longer than {RENDER_SECONDS:g} s")


def _limit(value: Any) -> int:
    return MAX_TEXT if isinstance(value, str) else MAX_ITEMS


def _refuse_size(size: int, limit: int) -> None:
    if size > limit:
        raise TemplateTooBig(f"the template builds something too big ({size:,} > {limit:,})")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


_PERCENT_SPEC = re.compile(r"%(?:\([^)]*\))?[-#0 +]*(\*|\d+)?(?:\.(\*|\d+))?")


def _check_percent(fmt: str, args: Any) -> None:
    """`fmt % args`: widths and precisions, written or given by `*`, stay within MAX_TEXT."""
    star = False
    for m in _PERCENT_SPEC.finditer(fmt):
        for part in m.groups():
            star = star or part == "*"
            if part and part != "*":
                _refuse_size(int(part), MAX_TEXT)
    if star:
        for v in args if isinstance(args, tuple) else (args,):
            if _is_int(v):
                _refuse_size(v, MAX_TEXT)


def _check_format(fmt: str, args: Any, kwargs: Any) -> None:
    """`fmt.format(...)`: widths in the format specs (and nested ones given as arguments) stay within MAX_TEXT."""
    try:
        specs = [spec or "" for _, _, spec, _ in string.Formatter().parse(fmt)]
    except ValueError:
        return  # formatting raises the error itself
    for spec in specs:
        for digits in re.findall(r"\d+", spec):
            _refuse_size(int(digits), MAX_TEXT)
    if any("{" in spec for spec in specs):
        for v in [*args, *(kwargs.values() if isinstance(kwargs, dict) else ())]:
            if _is_int(v):
                _refuse_size(v, MAX_TEXT)


def _check_replace(s: str, old: Any, new: Any, count: Any = -1) -> None:
    if isinstance(old, str) and isinstance(new, str):
        found = s.count(old) if old else len(s) + 1
        if _is_int(count) and count >= 0:
            found = min(found, count)
        _refuse_size(len(s) + found * max(0, len(new) - len(old)), MAX_TEXT)


def _check_str_call(name: str, s: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, ...]:
    """A call of a string method that can build a big string: refused when its result would be too big."""
    given = [*args, *kwargs.values()]
    if name in ("center", "ljust", "rjust", "zfill") and given and _is_int(given[0]):
        _refuse_size(given[0], MAX_TEXT)
    elif name == "expandtabs" and given and _is_int(given[0]):
        _refuse_size(len(s) + s.count("\t") * max(0, given[0]), MAX_TEXT)
    elif name == "replace" and len(args) >= 2:
        _check_replace(s, *args[:3])
    elif name == "join" and len(args) == 1:
        items = list(args[0])
        _refuse_size(sum(len(x) if isinstance(x, str) else 0 for x in items) + len(s) * len(items), MAX_TEXT)
        return (items,)
    return args


def _filter(check: Callable[..., Any]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Wrap one of Jinja's filters: `check` sees its arguments (and may return the value, materialised) first."""

    def wrap(f: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(f)  # keeps Jinja's pass_context / pass_eval_context markers
        def bounded(*args: Any, **kwargs: Any) -> Any:
            _tick()
            got = check(*args, **kwargs)
            return f(*got, **kwargs) if got is not None else f(*args, **kwargs)

        return bounded

    return wrap


def _skip_context(args: tuple[Any, ...]) -> tuple[Any, ...]:
    """The filter's own arguments, past the context Jinja passes to some filters first."""
    return args[1:] if args and isinstance(args[0], EvalContext | Context | Environment) else args


def _width_check(*args: Any, **kwargs: Any) -> None:  # center(value, width), batch(value, n), slice(value, n)
    own = [*_skip_context(args)[1:], *kwargs.values()]
    if own and _is_int(own[0]):
        _refuse_size(own[0], MAX_ITEMS if not isinstance(_skip_context(args)[0], str) else MAX_TEXT)


def _indent_check(*args: Any, **kwargs: Any) -> None:
    own = _skip_context(args)
    width = own[1] if len(own) > 1 else kwargs.get("width", 4)
    w = width if _is_int(width) else len(str(width))
    text = str(own[0]) if own else ""
    _refuse_size(len(text) + w * (text.count("\n") + 1), MAX_TEXT)


def _join_check(*args: Any, **kwargs: Any) -> tuple[Any, ...] | None:
    own = _skip_context(args)
    if len(own) == 0:
        return None
    sep = own[1] if len(own) > 1 else kwargs.get("d", "")
    items = list(own[0]) if not isinstance(own[0], str | dict) else own[0]
    if isinstance(items, list):
        known = sum(len(x) for x in items if isinstance(x, str))
        _refuse_size(known + len(str(sep)) * len(items), MAX_TEXT)
    return (*args[: len(args) - len(own)], items, *own[1:])


def _replace_check(*args: Any, **kwargs: Any) -> None:
    own = [*_skip_context(args), *kwargs.values()]
    if len(own) >= 3:
        _check_replace(str(own[0]), *own[1:4])


def _format_check(*args: Any, **kwargs: Any) -> None:
    own = _skip_context(args)
    if own:
        _check_percent(str(own[0]), tuple(own[1:]) if own[1:] else kwargs)


def _sum_check(*args: Any, **kwargs: Any) -> tuple[Any, ...] | None:
    own = _skip_context(args)
    if len(own) == 0 or isinstance(own[0], str | dict):
        return None
    items = list(own[0])
    sizes = [len(x) for x in items if isinstance(x, list | tuple)]
    _refuse_size(sum(sizes), MAX_ITEMS)
    _refuse_size(len(sizes) * sum(sizes), MAX_ITEMS * 100)  # adding lists one by one copies them again each time
    return (*args[: len(args) - len(own)], items, *own[1:])


def _tojson_check(*args: Any, **kwargs: Any) -> None:
    own = [*_skip_context(args)[1:], *kwargs.values()]
    if own and _is_int(own[0]):
        _refuse_size(own[0], MAX_JSON_INDENT)


_FILTER_CHECKS: dict[str, Callable[..., Any]] = {
    "center": _width_check, "batch": _width_check, "slice": _width_check, "indent": _indent_check,
    "join": _join_check, "replace": _replace_check, "format": _format_check, "sum": _sum_check,
    "tojson": _tojson_check,
}  # fmt: skip


class _Steps(NodeTransformer):
    """Every `for` loop steps through a counter that checks the time; every `~` checks the size it builds."""

    def visit_For(self, node: nodes.For) -> nodes.Node:
        self.generic_visit(node)
        node.iter = nodes.Filter(node.iter, "_ac_steps", [], [], None, None, lineno=node.lineno)
        return node

    def visit_Concat(self, node: nodes.Concat) -> nodes.Node:
        self.generic_visit(node)
        joined = nodes.List(node.nodes, lineno=node.lineno)
        return nodes.Filter(joined, "_ac_concat", [], [], None, None, lineno=node.lineno)


def _steps(iterable: Any) -> Any:
    for item in iterable:
        _tick()
        yield item


def _concat(parts: list[Any]) -> str:
    texts = [str(p) for p in parts]
    _refuse_size(sum(map(len, texts)), MAX_TEXT)
    return "".join(texts)


def _finalize(value: Any) -> Any:
    _tick()
    if isinstance(value, str):
        _refuse_size(len(value), MAX_TEXT)
    return value


class BoundedEnvironment(SandboxedEnvironment):
    """The sandbox, with a time limit and size checks: see the section comment."""

    intercepted_binops = frozenset({"*", "**", "+", "%"})

    def __init__(self, **options: Any) -> None:
        super().__init__(finalize=_finalize, **options)
        self.globals.pop("lipsum", None)  # placeholder text of any length: nothing a workflow needs
        for name, check in _FILTER_CHECKS.items():
            self.filters[name] = _filter(check)(self.filters[name])
        self.filters["_ac_steps"] = _steps
        self.filters["_ac_concat"] = _concat

    def _parse(self, source: str, name: str | None, filename: str | None) -> nodes.Template:
        return _Steps().visit(super()._parse(source, name, filename))

    def call_binop(self, context: Any, operator: str, left: Any, right: Any) -> Any:
        _tick()
        if operator == "*":
            for seq, n in ((left, right), (right, left)):
                if isinstance(seq, str | list | tuple) and _is_int(n):
                    _refuse_size(len(seq) * n, _limit(seq))
            if _is_int(left) and _is_int(right):
                _refuse_size(left.bit_length() + right.bit_length(), MAX_INT_BITS)
        elif operator == "**" and _is_int(left) and _is_int(right) and right > 0 and abs(left) > 1:
            _refuse_size(right * left.bit_length(), MAX_INT_BITS)
        elif operator == "+" and isinstance(left, str | list | tuple) and isinstance(right, str | list | tuple):
            _refuse_size(len(left) + len(right), _limit(left))
        elif operator == "%" and isinstance(left, str):
            _check_percent(left, right)
        return super().call_binop(context, operator, left, right)

    def call(__self, __context: Any, __obj: Any, *args: Any, **kwargs: Any) -> Any:
        _tick()
        owner = getattr(__obj, "__self__", None)
        if isinstance(owner, str):
            args = _check_str_call(getattr(__obj, "__name__", ""), owner, args, kwargs)
        elif isinstance(owner, list) and getattr(__obj, "__name__", "") == "extend" and args:
            items = list(args[0])
            _refuse_size(len(owner) + len(items), MAX_ITEMS)
            args = (items,)
        return super().call(__context, __obj, *args, **kwargs)

    def getattr(self, obj: Any, attribute: str) -> Any:
        _tick()
        return super().getattr(obj, attribute)

    def getitem(self, obj: Any, argument: Any) -> Any:
        _tick()
        return super().getitem(obj, argument)

    def wrap_str_format(self, value: Any) -> Callable[..., str] | None:
        wrapped = super().wrap_str_format(value)
        if wrapped is None:
            return None
        fmt, is_map = value.__self__, value.__name__ == "format_map"

        def checked(*args: Any, **kwargs: Any) -> str:
            _check_format(fmt, () if is_map else args, args[0] if is_map and args else kwargs)
            return wrapped(*args, **kwargs)

        return checked


_jinja = BoundedEnvironment(undefined=StrictUndefined, autoescape=False)


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
    token = _deadline.set(time.monotonic() + RENDER_SECONDS)
    try:
        out: list[str] = []
        size = 0
        for chunk in _jinja.from_string(source).generate(state=state, **names):
            size += len(chunk)
            _refuse_size(size, MAX_TEXT)
            out.append(chunk)
        return "".join(out)
    except Exception as exc:  # MemoryError and RecursionError included: a template must never stop the server
        raise ExpressionError(f"template error in '{source[:40]}…': {exc}") from exc
    finally:
        _deadline.reset(token)


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


MAX_OBJECTS = 100  # `{` the answer is read from at most: a decision is near the start, not after pages of code
_NUMBER = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?")  # a number as JSON writes it


def _reject(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")  # NaN and Infinity: TypeScript's JSON.parse refuses them too


_DECODER = json.JSONDecoder(parse_constant=_reject)


def _decision_object(text: str) -> dict[str, Any]:
    """The JSON object in a model's answer that holds a `choice` (else the first object): read at each `{` in turn,
    so reasoning, a code fence or braces around the JSON do not hide it."""
    first: dict[str, Any] | None = None
    start = text.find("{")
    for _ in range(MAX_OBJECTS):
        if start < 0:
            break
        try:
            found, _end = _DECODER.raw_decode(text, start)
        except ValueError:
            found = None
        if isinstance(found, dict):
            if "choice" in found:
                return found
            first = found if first is None else first
        start = text.find("{", start + 1)
    return first or {}


def decision(text: str, choices: list[str] | dict[str, str]) -> dict[str, Any]:
    """The decision in a model's answer: {choice, confidence, reason}; `choice` is None when the answer names no
    option (no JSON, or a name that is none of `choices`, in any case). The TypeScript twin (Mastra's flow.ts) reads
    every answer the same way."""
    found = _decision_object(text)
    raw = found.get("choice")
    choice = raw.strip() if isinstance(raw, str) else ""
    names = list(choices)
    folded = {n.lower(): n for n in reversed(names)}  # the first option of a name, whatever its case
    name = choice if choice in names else folded.get(choice.lower()) if choice else None
    given = found.get("confidence")
    value = math.nan
    if isinstance(given, int | float):  # a bool too: true is 1
        value = float(given) if not isinstance(given, int) or abs(given) < 2**1000 else math.inf
    elif isinstance(given, str) and _NUMBER.fullmatch(given.strip()):
        value = float(given.strip())
    confidence = min(1.0, max(0.0, value)) if math.isfinite(value) else None
    reason = found.get("reason")
    return {"choice": name, "confidence": confidence, "reason": reason[:500] if isinstance(reason, str) else ""}


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
