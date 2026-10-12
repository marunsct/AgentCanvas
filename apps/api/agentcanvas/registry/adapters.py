"""Framework adapter manifests (S20): what each installed framework can run, without importing it.

Each adapter ships a manifest in `registry/adapters/<id>.json`: its abilities beyond the required core (thread
state, time travel, long-term memory, MCP inspection, breakpoints) and how it supports each block type
(`native`, `emulated` or `unsupported`; `"*"` and `"<namespace>.*"` are fallbacks). The validator reads them for the
portability check (P001-P004) and the editor for the framework picker, so neither ever imports a framework.

Which adapters a server offers is `AGENTCANVAS_ADAPTERS` (comma-separated ids, default
`langchain,openai_agents,crewai,google_adk,pydantic_ai,agno`).
"""

from __future__ import annotations

import json
import os
from functools import cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

HERE = Path(__file__).parent / "adapters"
DEFAULT_FRAMEWORK = "langchain"
DEFAULT_ADAPTERS = "langchain,openai_agents,crewai,google_adk,pydantic_ai,agno"  # S25, S27, S29; S32: Pydantic AI, Agno

Ability = Literal["thread_state", "time_travel", "memory_store", "mcp_inspect", "breakpoints", "agent_designs"]
Support = Literal["native", "emulated", "unsupported"]

ABILITY_LABEL: dict[str, str] = {
    "thread_state": "reading and editing a conversation's saved state",
    "time_travel": "run history and replaying from an earlier step",
    "memory_store": "long-term memory",
    "mcp_inspect": "browsing an app connector's tools",
    "breakpoints": "breakpoints in debug runs",
    "agent_designs": "agents designed for other frameworks (mixed flows)",
}


class AdapterManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    label: str
    framework: str  # the framework package(s) it drives
    version: str
    tier: Literal["T1", "T2", "T3", "community"]
    description: str = ""
    languages: list[str] = Field(default_factory=lambda: ["python"])
    abilities: list[Ability] = Field(default_factory=list)
    nodes: dict[str, Support] = Field(default_factory=lambda: {"*": "native"})
    notes: dict[str, str] = Field(default_factory=dict)  # block type -> how it is emulated or why not

    def has(self, ability: str) -> bool:
        return ability in self.abilities

    def support(self, type_: str) -> Support:
        if type_ in self.nodes:
            return self.nodes[type_]
        namespace = type_.split(".", 1)[0]
        return self.nodes.get(f"{namespace}.*", self.nodes.get("*", "unsupported"))


@cache
def all_manifests() -> dict[str, AdapterManifest]:
    found = (AdapterManifest.model_validate(json.loads(p.read_text())) for p in sorted(HERE.glob("*.json")))
    return {m.id: m for m in found}


def installed_ids() -> list[str]:
    wanted = [a.strip() for a in os.environ.get("AGENTCANVAS_ADAPTERS", DEFAULT_ADAPTERS).split(",") if a.strip()]
    known = all_manifests()
    ids = [a for a in wanted if a in known]
    return ids or [DEFAULT_FRAMEWORK]


def installed() -> list[AdapterManifest]:
    known = all_manifests()
    return [known[i] for i in installed_ids()]


def manifest(adapter_id: str) -> AdapterManifest | None:
    return all_manifests().get(adapter_id) if adapter_id in installed_ids() else None


def portability(wf: Any, adapters: list[AdapterManifest] | None = None) -> list[dict[str, Any]]:
    """For each installed framework: can this workflow target it, and which blocks stop or bend it."""
    out = []
    for m in adapters if adapters is not None else installed():
        blockers, emulated = [], []
        for el in [*wf.nodes, *wf.resources]:
            level = m.support(el.type)
            entry = {"id": el.id, "name": el.name, "type": el.type, "note": m.notes.get(el.type)}
            if level == "unsupported":
                blockers.append(entry)
            elif level == "emulated":
                emulated.append(entry)
        out.append({"adapter": m.id, "label": m.label, "ok": not blockers, "blockers": blockers, "emulated": emulated})
    return out
