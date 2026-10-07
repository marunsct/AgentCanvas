"""The Copilot's product knowledge (S14): a pack generated from the running product, sent as a cached prompt prefix.

The pack is built from the same sources the editor uses (the registry's block manifests, including a project's
workspace blocks, and the starter templates) plus a short hand-written guide, so it cannot drift from the product.
It is small enough to send whole on every turn; providers that support prompt caching cache it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentcanvas.registry import NodeManifest, Registry

GUIDE = Path(__file__).parent / "knowledge" / "guide.md"
MAX_DESCRIPTION = 220
EXAMPLE_TEMPLATES = ("ticket_triage", "chat_assistant")  # shown whole as few-shot examples


@dataclass(frozen=True)
class Pack:
    text: str
    hash: str

    @property
    def tokens(self) -> int:
        """A rough size estimate (4 characters per token)."""
        return len(self.text) // 4


def _short(text: str, limit: int = MAX_DESCRIPTION) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _schema_summary(schema: dict[str, Any]) -> str:
    """`name: type (required) - description` per config property, one line each."""
    props: dict[str, Any] = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    lines = []
    for name, spec in props.items():
        if not isinstance(spec, dict):
            continue
        kind = spec.get("type") or ("enum" if "enum" in spec else "any")
        if isinstance(kind, list):
            kind = "|".join(str(k) for k in kind)
        extra = f" one of {spec['enum']}" if "enum" in spec else ""
        default = f" default {json.dumps(spec['default'])}" if "default" in spec else ""
        desc = f" - {_short(spec['description'], 140)}" if spec.get("description") else ""
        lines.append(f"    - {name}: {kind}{' (required)' if name in required else ''}{extra}{default}{desc}")
    return "\n".join(lines)


def block_summary(m: NodeManifest, *, full: bool = False) -> str:
    ports_in = ", ".join(f"{p.name}:{p.type}{'*' if p.required else ''}{'[]' if p.multi else ''}" for p in m.inputs)
    ports_out = "routes (one per case + default)" if m.dynamic_outputs else ", ".join(p.name for p in m.outputs)
    head = f'- `{m.type}` ({m.kind}) "{m.label}", {m.category}: {_short(m.description, 400 if full else 220)}'
    lines = [head, f"    in: {ports_in or '-'}; out: {ports_out or '-'}"]
    if m.interrupts:
        lines.append("    pauses for a human")
    config = _schema_summary(m.config_schema)
    if config:
        lines.append("    config:\n" + config)
    if full and m.docs:
        lines.append("    docs: " + _short(m.docs, 1500))
    return "\n".join(lines)


def _compact(doc: dict[str, Any]) -> str:
    return json.dumps({k: v for k, v in doc.items() if k not in ("id", "ir_version")}, separators=(",", ":"))


def build_pack(registry: Registry, templates: dict[str, dict[str, Any]]) -> Pack:
    parts = [GUIDE.read_text()]
    parts.append("# Blocks\n\n`*` = required port, `[]` = accepts several.\n")
    for kind in ("runtime", "resource", "middleware", "trigger"):
        blocks = sorted((m for m in registry.all() if m.kind == kind), key=lambda m: m.type)
        parts.extend(block_summary(m) for m in blocks)
    parts.append("\n# Starter templates\n")
    for name, doc in templates.items():
        parts.append(f"- `{name}`: {doc.get('name', name)}. {_short(doc.get('description', ''))}")
    for name in EXAMPLE_TEMPLATES:
        if name in templates:
            parts.append(f"\nExample, the `{name}` template as IR:\n```json\n{_compact(templates[name])}\n```")
    text = "\n".join(parts)
    return Pack(text=text, hash=hashlib.sha256(text.encode()).hexdigest()[:16])
