"""Graph IR (Intermediate Representation) — the single source of truth for a workflow (ADR 0001).

Every screen (canvas, code view, API, Copilot) reads and writes these models. Keep them
framework-neutral: no LangChain/LangGraph imports in this package (ADR 0011).

IR versions
-----------
* ``1.0`` — walking skeleton (6 node types, flow/wiring edges).
* ``1.1`` — adds channel ``storage``/``io``, a run ``context`` schema, node ``policies``,
  workflow ``settings``, ``data`` edges, edge ``order`` and open (registry-checked) node types.
  Documents with ``ir_version: "1.0"`` are upgraded on load (1.0 is a strict subset).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

IR_VERSION = "1.1"
IDENT = re.compile(r"^[a-z][a-z0-9_]{0,62}$")  # names become Python identifiers
TYPE_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")  # e.g. core.llm, model.chat, acme.upsert
RESERVED_NAMES = {"start", "end", "__start__", "__end__"}


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")  # typos in JSON fail loudly


def _check_ident(kind: str, v: str) -> str:
    if not IDENT.match(v):
        raise ValueError(f"{kind} name '{v}' must be snake_case (a-z, 0-9, _), max 63 chars")
    return v


# ------------------------------------------------------------------ state ----
Reducer = Literal["replace", "append", "add_messages", "sum", "merge"]
Storage = Literal["full", "delta"]  # delta = LangGraph DeltaChannel (ADR 0016)
ChannelIO = Literal["inout", "in", "out", "private"]


class Channel(_Base):
    """One field of the shared graph state."""

    name: str
    type: dict[str, Any] = Field(default_factory=lambda: {"type": "string"})  # JSON Schema
    reducer: Reducer = "replace"
    default: Any = None
    storage: Storage = "full"
    io: ChannelIO = "inout"
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _ident(cls, v: str) -> str:
        return _check_ident("channel", v)

    @model_validator(mode="after")
    def _delta_needs_batch_safe_reducer(self) -> Channel:
        if self.storage == "delta" and self.reducer not in ("append", "add_messages"):
            raise ValueError(
                f"channel '{self.name}': storage 'delta' requires reducer 'append' or 'add_messages' (ADR 0016)"
            )
        return self


class StateSchema(_Base):
    channels: list[Channel] = Field(default_factory=list)

    def channel(self, name: str) -> Channel | None:
        return next((c for c in self.channels if c.name == name), None)


class ContextField(_Base):
    """Run-scoped, immutable value (LangGraph ``Runtime.context``), e.g. user_id, tenant_id.

    ``source="user"`` means the runner fills it from the authenticated identity — never from
    model output or client input (spec doc 08 rule 2).
    """

    name: str
    type: dict[str, Any] = Field(default_factory=lambda: {"type": "string"})
    required: bool = False
    source: Literal["input", "user", "tenant"] = "input"
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _ident(cls, v: str) -> str:
        return _check_ident("context field", v)


class ContextSchema(_Base):
    fields: list[ContextField] = Field(default_factory=list)

    def field(self, name: str) -> ContextField | None:
        return next((f for f in self.fields if f.name == name), None)


# --------------------------------------------------------------- policies ----
class RetryPolicySpec(_Base):
    max_attempts: int = Field(default=3, ge=1, le=20)
    initial_interval: float = Field(default=0.5, ge=0)
    backoff_factor: float = Field(default=2.0, ge=1)
    max_interval: float = Field(default=128.0, ge=0)
    jitter: bool = True
    retry_on: list[str] = Field(default_factory=list)  # exception class names; empty = LangGraph default


class TimeoutPolicySpec(_Base):
    run_timeout: float | None = Field(default=None, gt=0)
    idle_timeout: float | None = Field(default=None, gt=0)
    refresh_on: Literal["auto", "heartbeat"] = "auto"


class CachePolicySpec(_Base):
    ttl: int | None = Field(default=None, ge=1)  # seconds; None = never expires


TracePolicy = Literal["full", "omit_inputs", "omit_outputs", "omit"]  # FR-X-07


class NodePolicies(_Base):
    retry: RetryPolicySpec | None = None
    timeout: TimeoutPolicySpec | None = None
    cache: CachePolicySpec | None = None
    error_route: str | None = None  # node id that receives langgraph.errors.NodeError
    trace: TracePolicy = "full"


# ---------------------------------------------------------- nodes & edges ----
class Resource(_Base):
    """Constructed once and injected into nodes by wiring edges (models, tools, middleware...)."""

    id: str
    type: str  # registry type, e.g. "model.chat"
    name: str
    label: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if not TYPE_NAME.match(v):
            raise ValueError(f"type '{v}' must look like 'namespace.kind'")
        return v

    @field_validator("name")
    @classmethod
    def _ident(cls, v: str) -> str:
        return _check_ident("resource", v)


class Node(_Base):
    """A runtime step in the graph."""

    id: str
    type: str  # registry type, e.g. "core.llm"
    name: str
    label: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    policies: NodePolicies | None = None
    disabled: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if not TYPE_NAME.match(v):
            raise ValueError(f"type '{v}' must look like 'namespace.kind'")
        return v

    @field_validator("name")
    @classmethod
    def _ident(cls, v: str) -> str:
        _check_ident("node", v)
        if v in RESERVED_NAMES:
            raise ValueError(f"node name '{v}' is reserved")
        return v


class Endpoint(_Base):
    node: str  # node or resource id, or the special ids "START" / "END"; data edges may target a channel
    port: str | None = None

    @property
    def route(self) -> str:
        """Route name for router outputs: port 'route:high' -> 'high'."""
        return (self.port or "").removeprefix("route:")


EdgeKind = Literal["flow", "wiring", "data"]


class Edge(_Base):
    id: str
    kind: EdgeKind
    source: Endpoint
    target: Endpoint
    order: int | None = None  # ordered multi-ports (e.g. middleware); lower first
    label: str | None = None


# -------------------------------------------------------------- workflow ----
class Budget(_Base):
    """Spending limits (FR-X-15), enforced by the runner from the models' reported usage."""

    max_tokens: int | None = Field(default=None, ge=1)  # per run, input + output
    max_cost_usd: float | None = Field(default=None, gt=0)  # per run; needs catalog prices
    max_seconds: float | None = Field(default=None, gt=0)  # per run, wall clock
    thread_max_tokens: int | None = Field(default=None, ge=1)  # all runs of a thread together
    thread_max_cost_usd: float | None = Field(default=None, gt=0)


class WorkflowSettings(_Base):
    recursion_limit: int = Field(default=50, ge=1, le=10_000)
    durability: Literal["exit", "async", "sync"] = "async"
    node_defaults: NodePolicies | None = None  # compiles to StateGraph.set_node_defaults(...)
    input_channels: list[str] | None = None  # None = all non-private channels
    output_channels: list[str] | None = None  # None = all non-private channels
    experimental: bool = False  # allow upstream experimental features (D16)
    budget: Budget | None = None  # FR-X-15


class Workflow(_Base):
    ir_version: Literal["1.0", "1.1"] = IR_VERSION
    id: str
    name: str
    description: str = ""
    kind: Literal["workflow", "component"] = "workflow"
    framework: str = "langchain"
    state: StateSchema = Field(default_factory=StateSchema)
    context: ContextSchema = Field(default_factory=ContextSchema)
    resources: list[Resource] = Field(default_factory=list)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    settings: WorkflowSettings = Field(default_factory=WorkflowSettings)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _upgrade(self) -> Workflow:
        self.ir_version = IR_VERSION  # 1.0 documents are a strict subset of 1.1
        return self

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

    def element(self, element_id: str) -> Node | Resource | None:
        return self.node(element_id) or self.resource(element_id)

    def flow_edges(self) -> list[Edge]:
        return [e for e in self.edges if e.kind == "flow"]

    def wiring_into(self, node_id: str, port: str) -> list[Edge]:
        """Wiring edges into `node_id.port`, ordered by `order` then list position."""
        edges = [e for e in self.edges if e.kind == "wiring" and e.target.node == node_id and e.target.port == port]
        return sorted(edges, key=lambda e: (e.order is None, e.order or 0))

    def wired_into(self, node_id: str, port: str) -> list[Resource]:
        """Resources connected to `node_id.port` by wiring edges, in edge order."""
        out = []
        for e in self.wiring_into(node_id, port):
            r = self.resource(e.source.node)
            if r:
                out.append(r)
        return out
