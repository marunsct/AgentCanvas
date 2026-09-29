"""Static analysis helpers shared by rules (and later by the blast-radius map, FR-X-02)."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from agentcanvas.ir.models import Node
from agentcanvas.registry import NodeManifest

STATE_REF = re.compile(r"\bstate\.([a-z][a-z0-9_]*)")


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
    return out


def node_reads(node: Node, m: NodeManifest) -> set[str]:
    out = set(m.reads)
    for f in m.read_fields:
        if isinstance(node.config.get(f), str) and node.config[f]:
            out.add(node.config[f])
    for _, s in iter_strings(node.config):
        out.update(STATE_REF.findall(s))
    return out


def node_side_effects(node: Node, m: NodeManifest) -> set[str]:
    declared = node.config.get("side_effects")
    extra = {x for x in declared if isinstance(x, str)} if isinstance(declared, list) else set()
    return set(m.side_effects) | extra
