"""Graph IR (Intermediate Representation) — the single source of truth for a workflow.

Every screen (canvas, code view, API) reads and writes these models. Keep them
framework-neutral: no LangChain/LangGraph imports in this file.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

IDENT = re.compile(r"^[a-z][a-z0-9_]{0,62}$")  # names become Python identifiers


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")  # typos in JSON fail loudly


Reducer = Literal["replace", "append", "add_messages", "sum", "merge"]


class Channel(_Base):
    """One field of the shared graph state."""

    name: str
    type: dict[str, Any] = Field(default_factory=lambda: {"type": "string"})  # JSON Schema
    reducer: Reducer = "replace"
    default: Any = None

    @field_validator("name")
    @classmethod
    def _ident(cls, v: str) -> str:
        if not IDENT.match(v):
            raise ValueError(f"channel name '{v}' must be snake_case (a-z, 0-9, _)")
        return v


class StateSchema(_Base):
    channels: list[Channel] = Field(default_factory=list)

    def channel(self, name: str) -> Channel | None:
        return next((c for c in self.channels if c.name == name), None)


class Resource(_Base):
    """Constructed once and injected into nodes (models, tools...)."""

    id: str
    type: Literal["model.chat", "tool.python"]
    name: str
    config: dict[str, Any] = Field(default_factory=dict)


class Node(_Base):
    """A runtime step in the graph."""

    id: str
    type: Literal["core.llm", "core.agent", "core.router", "core.approval", "core.set", "core.script"]
    name: str
    label: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _ident(cls, v: str) -> str:
        if not IDENT.match(v):
            raise ValueError(f"node name '{v}' must be snake_case (a-z, 0-9, _)")
        return v


class Endpoint(_Base):
    node: str  # node or resource id, or the special ids "START" / "END"
    port: str | None = None

    @property
    def route(self) -> str:
        """Route name for router outputs: port 'route:high' -> 'high'."""
        return (self.port or "").removeprefix("route:")


class Edge(_Base):
    id: str
    kind: Literal["flow", "wiring"]
    source: Endpoint
    target: Endpoint


class Workflow(_Base):
    ir_version: Literal["1.0"] = "1.0"
    id: str
    name: str
    state: StateSchema = Field(default_factory=StateSchema)
    resources: list[Resource] = Field(default_factory=list)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)

    # ---- small helpers used by validator, interpreter and compiler ----
    def node(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.id == node_id), None)

    def require_node(self, node_id: str) -> Node:
        node = self.node(node_id)
        if node is None:
            raise KeyError(f"unknown node id '{node_id}'")
        return node

    def resource(self, res_id: str) -> Resource | None:
        return next((r for r in self.resources if r.id == res_id), None)

    def flow_edges(self) -> list[Edge]:
        return [e for e in self.edges if e.kind == "flow"]

    def wired_into(self, node_id: str, port: str) -> list[Resource]:
        """Resources connected to `node_id.port` by wiring edges, in edge order."""
        out = []
        for e in self.edges:
            if e.kind == "wiring" and e.target.node == node_id and e.target.port == port:
                r = self.resource(e.source.node)
                if r:
                    out.append(r)
        return out
