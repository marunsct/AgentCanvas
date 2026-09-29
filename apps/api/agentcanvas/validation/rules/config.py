"""Config rules: JSON Schema validation, expressions and templates, secrets."""

from __future__ import annotations

import re
from collections.abc import Iterator
from functools import cache

from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from jsonschema import Draft202012Validator

from agentcanvas.expr import ExpressionError, check_condition
from agentcanvas.validation.analysis import iter_formatted, iter_strings
from agentcanvas.validation.engine import Diagnostic, ValidationContext, error, rule

_jinja = SandboxedEnvironment()
SECRET_PATTERNS = [
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}"),  # OpenAI / Anthropic style keys
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),  # GitHub tokens
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack tokens
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),  # Google API key
]


@cache
def _validator(schema_json: str) -> Draft202012Validator:
    import json

    return Draft202012Validator(json.loads(schema_json))


def _schema_key(schema: dict) -> str:
    import json

    return json.dumps(schema, sort_keys=True)


@rule
def config_matches_schema(ctx: ValidationContext) -> Iterator[Diagnostic]:
    for el in [*ctx.wf.nodes, *ctx.wf.resources]:
        m = ctx.manifest(el)
        if m is None:
            continue
        v = _validator(_schema_key(m.config_schema))
        for err in sorted(v.iter_errors(el.config), key=lambda e: list(e.absolute_path)):
            path = "config" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in err.absolute_path)
            yield error("E004", f"'{el.name}': {err.message}", node_id=el.id, path=path)


@rule
def embedded_schemas(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """Config values with format `json_schema` (structured output, input forms) must be valid JSON Schemas."""
    from jsonschema.exceptions import SchemaError

    for el in [*ctx.wf.nodes, *ctx.wf.resources]:
        m = ctx.manifest(el)
        if m is None:
            continue
        for key, prop in m.config_schema.get("properties", {}).items():
            value = el.config.get(key)
            if prop.get("format") != "json_schema" or not isinstance(value, dict) or not value:
                continue
            try:
                Draft202012Validator.check_schema(value)
            except SchemaError as exc:
                yield error(
                    "E004",
                    f"'{el.name}': {key} is not a valid JSON Schema: {exc.message}",
                    node_id=el.id,
                    path=f"config.{key}",
                )
            if key == "response_format" and value.get("type", "object") != "object":
                yield error(
                    "E004",
                    f"'{el.name}': structured output must be an object schema",
                    node_id=el.id,
                    path=f"config.{key}",
                )


@rule
def expressions_compile(ctx: ValidationContext) -> Iterator[Diagnostic]:
    for el in [*ctx.wf.nodes, *ctx.wf.resources]:
        m = ctx.manifest(el)
        if m is None:
            continue
        for path, fmt, value in iter_formatted(m.config_schema, el.config):
            if fmt == "cel":
                try:
                    check_condition(value)
                except ExpressionError as exc:
                    yield error("E013", f"'{el.name}': {exc}", node_id=el.id, path=path)
            elif fmt in ("prompt", "template"):
                try:
                    _jinja.parse(value)
                except TemplateSyntaxError as exc:
                    yield error("E014", f"'{el.name}': template error: {exc.message}", node_id=el.id, path=path)


@rule
def no_secret_literals(ctx: ValidationContext) -> Iterator[Diagnostic]:
    for el in [*ctx.wf.nodes, *ctx.wf.resources]:
        for path, s in iter_strings(el.config):
            if any(p.search(s) for p in SECRET_PATTERNS):
                yield error(
                    "E050",
                    f"'{el.name}': a secret-looking value is written inline",
                    node_id=el.id,
                    path=path,
                    hint='store it as a project secret and reference it as {"$secret": "NAME"}',
                )
