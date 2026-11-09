"""Config rules: JSON Schema validation, expressions and templates, secrets."""

from __future__ import annotations

from collections.abc import Iterator
from functools import cache

from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from jsonschema import Draft202012Validator

from agentcanvas.expr import ExpressionError, check_condition
from agentcanvas.ir.models import SECRET_NAME
from agentcanvas.validation.analysis import SECRET_PATTERNS, iter_formatted, iter_strings, plain_secrets
from agentcanvas.validation.engine import Diagnostic, ValidationContext, error, rule, warning

_jinja = SandboxedEnvironment()


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
            elif fmt in ("prompt", "template", "url_template"):
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


@rule
def plain_text_keys(ctx: ValidationContext) -> Iterator[Diagnostic]:
    """W021: a header or environment value that looks like a key, typed in: runs work, exports refuse it."""
    for el in [*ctx.wf.nodes, *ctx.wf.resources]:
        for key, name in plain_secrets(el.config):
            what = "header" if key == "headers" else "environment variable"
            yield warning(
                "W021",
                f"'{el.name}': the {what} {name} is typed in as plain text; an export would carry it, so exports "
                "refuse it",
                node_id=el.id,
                path=f"config.{key}.{name}",
                hint='store it as a project secret and reference it as {"$secret": "NAME"}',
            )


def _secret_refs(value: object, path: str = "config") -> Iterator[tuple[str, object]]:
    if isinstance(value, dict):
        if "$secret" in value:
            yield path, value["$secret"]
            return
        for k, v in value.items():
            yield from _secret_refs(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _secret_refs(v, f"{path}[{i}]")


@rule
def secret_names(ctx: ValidationContext) -> Iterator[Diagnostic]:
    for el in [*ctx.wf.nodes, *ctx.wf.resources]:
        for path, name in _secret_refs(el.config):
            if not isinstance(name, str) or not SECRET_NAME.fullmatch(name):
                yield error(
                    "E051",
                    f"'{el.name}': secret name {name!r} is not a variable name",
                    node_id=el.id,
                    path=path,
                    hint="use letters, digits and _ (e.g. OPENAI_API_KEY): exports read secrets from the environment",
                )
