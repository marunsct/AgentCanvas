"""Static analysis helpers shared by rules (and later by the blast-radius map, FR-X-02)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from pydantic import ValidationError

from agentcanvas.errors import InvalidInput
from agentcanvas.ir.models import Node, Workflow
from agentcanvas.registry import NodeManifest

STATE_REF = re.compile(r"\bstate\.([a-z][a-z0-9_]*)\b(?!\s*\()")  # `state.get(…)` is a method, not a channel
JINJA_BLOCK = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.S)
TEMPLATE_FORMATS = frozenset({"prompt", "template", "url_template"})  # text with {{ … }}: only those parts run
SECRET_PATTERNS = [
    re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}"),  # OpenAI / Anthropic style keys
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),  # GitHub tokens
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack tokens
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),  # Google API key
]
PLAIN_VALUE_MAPS = ("headers", "env")  # config maps whose values may be keys typed in directly
_KEY_NAME = re.compile(r"auth|token|key|secret|passw|cookie|session|credential|signature", re.IGNORECASE)
_KEY_VALUE = re.compile(r"^\s*(?:bearer|basic|digest|token)\s+\S", re.IGNORECASE)


def iter_strings(value: Any, path: str = "config") -> Iterator[tuple[str, str]]:
    """Yield (path, string) for every string nested in a config value."""
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from iter_strings(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from iter_strings(v, f"{path}[{i}]")


def iter_typed_strings(schema: Any, value: Any, path: str = "config") -> Iterator[tuple[str, str | None, str]]:
    """Yield (path, format or None, string) for every string nested in a config value."""
    schema = schema if isinstance(schema, dict) else {}
    if isinstance(value, str):
        fmt = schema.get("format")
        yield path, fmt if isinstance(fmt, str) else None, value
    elif isinstance(value, dict):
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        for k, v in value.items():
            yield from iter_typed_strings(props.get(k) if k in props else extra, v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from iter_typed_strings(schema.get("items"), v, f"{path}[{i}]")


def state_refs(text: str, fmt: str | None = None) -> set[str]:
    """The channels a config string reads as `state.<name>`: in a template or prompt only inside `{{ … }}` and
    `{% … %}` (the rest is prose that never runs); in an expression or any other string, anywhere."""
    if fmt in TEMPLATE_FORMATS:
        text = " ".join(JINJA_BLOCK.findall(text))
    return set(STATE_REF.findall(text))


def iter_formatted(schema: dict[str, Any], value: Any, path: str = "config") -> Iterator[tuple[str, str, str]]:
    """Yield (path, format, string) for config strings whose JSON Schema declares a `format`."""
    fmt = schema.get("format")
    if isinstance(value, str) and isinstance(fmt, str):
        yield path, fmt, value
    if isinstance(value, dict):
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        for k, v in value.items():
            sub = props.get(k) if k in props else (extra if isinstance(extra, dict) else None)
            if sub:
                yield from iter_formatted(sub, v, f"{path}.{k}")
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, v in enumerate(value):
            yield from iter_formatted(schema["items"], v, f"{path}[{i}]")


def node_writes(node: Node, m: NodeManifest) -> set[str]:
    cfg = node.config
    out = set(m.writes)
    for f in m.channel_fields:
        if isinstance(cfg.get(f), str) and cfg[f]:
            out.add(cfg[f])
    for f in m.channel_map_fields:
        if isinstance(cfg.get(f), dict):
            out.update(cfg[f])
    for f in m.list_write_fields:
        if isinstance(cfg.get(f), list):
            out.update(x for x in cfg[f] if isinstance(x, str))
    for f, channels in m.conditional_writes.items():
        if cfg.get(f):
            out.update(channels)
    return out


def node_reads(node: Node, m: NodeManifest) -> set[str]:
    out = set(m.reads)
    for f in m.read_fields:
        if isinstance(node.config.get(f), str) and node.config[f]:
            out.add(node.config[f])
    for f in m.list_read_fields:
        if isinstance(node.config.get(f), list):
            out.update(x for x in node.config[f] if isinstance(x, str))
    config = {k: v for k, v in node.config.items() if k != "workflow"}  # a subgraph's own state is not ours
    for _, fmt, s in iter_typed_strings(m.config_schema, config):
        out.update(state_refs(s, fmt))
    return out


def node_side_effects(node: Node, m: NodeManifest) -> set[str]:
    declared = node.config.get("side_effects")
    extra = {x for x in declared if isinstance(x, str)} if isinstance(declared, list) else set()
    return set(m.side_effects) | extra


def plain_secrets(config: dict[str, Any]) -> list[tuple[str, str]]:
    """(`headers` or `env`, entry name) of each value in a block's settings that looks like a key typed in as plain
    text: named like one (Authorization, X-API-Key, GITHUB_TOKEN…) or holding one (`Bearer …`, `sk-…`). A secret
    reference (`{"$secret": NAME}`) names a secret without holding it and is never one."""
    found = []
    for key in PLAIN_VALUE_MAPS:
        values = config.get(key)
        for name, value in values.items() if isinstance(values, dict) else ():
            if not isinstance(value, str) or not value.strip():
                continue
            if _KEY_NAME.search(str(name)) or _KEY_VALUE.match(value) or any(p.search(value) for p in SECRET_PATTERNS):
                found.append((key, str(name)))
    return found


def secrets_in_plain_text(wf: Workflow) -> list[tuple[str, str, str]]:
    """(block, `headers` or `env`, entry name) of every `plain_secrets` value of the workflow, subgraphs included
    (their blocks as `subgraph / block`)."""
    found = [(el.name, key, name) for el in [*wf.nodes, *wf.resources] for key, name in plain_secrets(el.config)]
    for n in wf.nodes:
        if n.type == "core.subgraph":
            try:
                inner = wf.subworkflow(n)
            except ValidationError:
                continue  # E018 says so, and an export refuses it
            found += [(f"{n.name} / {where}", key, name) for where, key, name in secrets_in_plain_text(inner)]
    return found


def refuse_plain_secrets(wf: Workflow) -> None:
    """Raise InvalidInput naming each key typed in as plain text: an export writes settings into its code and into
    its copy of the workflow, so the key would go wherever the project goes."""
    found = secrets_in_plain_text(wf)
    if found:
        what = {"headers": "header", "env": "variable"}
        named = ", ".join(f"'{where}' ({what[key]} {name})" for where, key, name in found)
        raise InvalidInput(
            f"keys typed in as plain text would be written into the export: {named}; store each as a project secret "
            'and use {"$secret": "NAME"} in its place, then export again'
        )
