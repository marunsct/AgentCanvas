"""The Copilot's skills (product-plan-2026-10.md §2.2): `skills/<name>/SKILL.md`, which the Deep Agents engine
lists by name and description and reads when one applies.

The `build-for-<framework>` skills say what a framework runs on the canvas. Their advice is written by hand; their
facts are filled in here from the adapters' manifests (`registry/adapters/*.json`), the agent frameworks
(`designs.AGENT_FRAMEWORKS`) and the templates, where the file has the `SUPPORT` marker. So a skill says what the
validator checks (P001, P002, P005, P006, P007) and cannot drift from it when a manifest changes.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from agentcanvas import designs
from agentcanvas.registry import Registry, default_registry
from agentcanvas.registry import adapters as adapter_manifests
from agentcanvas.services.workflows import TEMPLATES
from agentcanvas.validation.rules.portability import POLICY_FRAMEWORKS

SKILLS = Path(__file__).with_name("skills")  # one folder per skill, each with a SKILL.md
SUPPORT = "<!-- support: filled in from the adapter manifests -->"
# one skill per framework family the product exports to: the frameworks it covers, by adapter or agent framework id
FAMILIES: dict[str, tuple[str, ...]] = {
    "build-for-openai-agents": ("openai_agents",),
    "build-for-crewai": ("crewai",),
    "build-for-google-adk": ("google_adk",),
    "build-for-pydantic-ai-agno": ("pydantic_ai", "agno"),
    "build-for-microsoft-agent-framework": ("microsoft_agent_framework",),
    "build-for-strands-llamaindex": ("strands", "llamaindex"),
    "build-for-mastra": ("mastra",),
    "build-for-claude-agent-sdk": ("claude_agent_sdk",),
}
NOT_TARGETS = {"langchain", "echo"}  # the default framework (the knowledge pack covers it) and the demo engine


def _blocks(types: list[str], registry: Registry) -> str:
    def name(t: str) -> str:
        m = registry.get(t)
        return f"{m.label} (`{t}`)" if m else f"`{t}`"

    return ", ".join(name(t) for t in types)


def support(framework: str, templates: dict[str, str] | None = None, registry: Registry | None = None) -> str:
    """What `framework` runs on the canvas, in Markdown: from its manifest when it is a workflow framework, and from
    its agent framework entry when an Agent block can be designed for it."""
    registry = registry or default_registry()
    m = adapter_manifests.all_manifests().get(framework)
    fw = designs.AGENT_FRAMEWORKS.get(framework)
    parts: list[str] = []
    if m is not None:
        types = [b.type for b in registry.all()]
        native = [t for t in types if m.support(t) == "native"]
        emulated = [t for t in types if m.support(t) == "emulated"]
        refused = [t for t in types if m.support(t) == "unsupported"]
        parts += [
            f"## {m.label} as the workflow's framework (`{m.id}`)",
            m.description,
            f"- Runs natively: {_blocks(native, registry)}.",
            "- Emulated (it works, differently; the validator warns with P002):",
            *(f"  - {_blocks([t], registry)}: {m.notes.get(t, 'it behaves differently')}" for t in emulated),
            f"- Refused (P001 error; remove or replace them first): {_blocks(refused, registry) or 'none'}.",
        ]
        if lacks := [label for ability, label in adapter_manifests.ABILITY_LABEL.items() if not m.has(ability)]:
            parts.append(f"- Not available on {m.label}: {'; '.join(lacks)}.")
        if m.id not in POLICY_FRAMEWORKS:
            parts.append(
                f"- {m.label} does not apply node policies (retry, timeout, cache, error route): P007 warns; a step "
                "runs once and an error ends the run."
            )
        if starters := sorted(n for n, fw_id in (templates or {}).items() if fw_id == m.id):
            parts.append(f"- Starter templates for it (`get_template`): {', '.join(starters)}.")
        parts.append(
            f'- To move a workflow here: `{{"op": "UpdateMeta", "framework": "{m.id}"}}` in `propose_changes`; first '
            f'`export_plan` with `framework: "{m.id}"` lists what it cannot run or only emulates.'
        )
    if fw is not None:
        output = "can give" if fw.structured_output else "cannot give"
        parts += [
            f"## An AI agent designed for {fw.label} (`engine: {fw.id}`)",
            f"An AI agent block (`core.agent`) in a LangGraph workflow can set its `engine` to `{fw.id}`: it then "
            f"runs as {fw.label}'s own code, still with the workflow's model, and exports as a {fw.label} project "
            f"({', '.join(fw.packages)}).",
            "- It cannot use add-ons (middleware) and can use only Python, web API and MCP tools (P005 error); it "
            f"{output} structured output.",
            f"- A server without {fw.label} installed cannot run it (P006 warning: `make framework FW={fw.id}`).",
        ]
        if fw.own_env:
            parts.append(
                f"- {fw.label}'s packages clash with LangGraph's, so it runs as a part in a Python environment of its "
                "own, in exports too."
            )
    return "\n".join(parts)


@cache
def _templates() -> dict[str, str]:
    """Each starter template's framework, by template name."""
    return {
        p.name.removesuffix(".ir.json"): json.loads(p.read_text()).get("framework", "langchain")
        for p in sorted(TEMPLATES.glob("*.ir.json"))
    }


def skills() -> dict[str, str]:
    """Every skill's SKILL.md by folder name, a framework skill's facts filled in."""
    out: dict[str, str] = {}
    for path in sorted(SKILLS.glob("*/SKILL.md")):
        text = path.read_text()
        if SUPPORT in text:
            facts = "\n\n".join(support(f, _templates()) for f in FAMILIES.get(path.parent.name, ()))
            text = text.replace(SUPPORT, facts)
        out[path.parent.name] = text
    return out
