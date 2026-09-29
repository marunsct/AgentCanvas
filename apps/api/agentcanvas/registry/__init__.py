"""Node-type registry: one manifest per node/resource type (spec doc 04 §12, ticket M1-T7).

Manifests drive the palette, the node renderers (ports), the inspector forms (config JSON Schema)
and the validator (port types, required ports, config validation, channel writes, side effects).
Built-in manifests live in `registry/nodes/*.json`; workspace custom nodes are added in Sprint 7.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

HERE = Path(__file__).parent

SideEffect = Literal["external_write", "message_send", "code_exec", "spend", "pii_read", "host_exec"]


class PortSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    type: str  # ChatModel, Tool, Middleware, Backend, Subagent, Schema, Store, Retriever, Embeddings, Flow
    required: bool = False
    multi: bool = False  # accepts several wiring edges
    ordered: bool = False  # order of wiring edges matters (edge.order)
    description: str | None = None


class NodeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    version: str = "1.0.0"
    kind: Literal["runtime", "resource", "middleware", "trigger"]
    label: str
    category: str
    icon: str = "box"
    color: str | None = None
    description: str = ""
    inputs: list[PortSpec] = Field(default_factory=list)
    outputs: list[PortSpec] = Field(default_factory=list)
    dynamic_outputs: Literal["routes"] | None = None  # routers: one output port per route
    config_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    # channel bookkeeping for the validator (reads/writes inference)
    reads: list[str] = Field(default_factory=list)  # always-read channels
    writes: list[str] = Field(default_factory=list)  # always-written channels
    channel_fields: list[str] = Field(default_factory=list)  # config keys naming a written channel
    read_fields: list[str] = Field(default_factory=list)  # config keys naming a read channel
    channel_map_fields: list[str] = Field(default_factory=list)  # config keys whose dict keys are written channels
    list_write_fields: list[str] = Field(default_factory=list)  # config keys holding a list of written channels
    list_read_fields: list[str] = Field(default_factory=list)  # config keys holding a list of read channels
    conditional_writes: dict[str, list[str]] = Field(default_factory=dict)  # config key set -> channels written
    template_fields: list[str] = Field(default_factory=list)  # Jinja templates (may read state.*)
    builtin_tools: list[str] = Field(default_factory=list)  # tools the node brings itself (valid in approve_tools)
    interrupts: bool = False  # may pause for a human
    side_effects: list[SideEffect] = Field(default_factory=list)
    hosted_forbidden: bool = False  # E041: only allowed in the local/dev edition
    stability: Literal["stable", "beta", "experimental"] = "stable"
    targets: dict[str, Literal["full", "partial", "none"]] = Field(default_factory=lambda: {"python": "full"})
    impl: str | None = None  # workspace custom nodes: "nodes.<module>:<function>" (M5-T7)
    provenance: Literal["builtin", "workspace"] = "builtin"
    docs: str | None = None

    def input(self, name: str) -> PortSpec | None:
        return next((p for p in self.inputs if p.name == name), None)

    def output(self, name: str) -> PortSpec | None:
        return next((p for p in self.outputs if p.name == name), None)


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str  # provider:model, as accepted by init_chat_model
    label: str
    provider: str
    tools: bool = True
    structured_output: bool = True
    vision: bool = False
    reasoning: bool = False
    context_window: int | None = None
    price_in_per_mtok: float | None = None  # USD per 1M input tokens; None = unknown (fill from provider pricing)
    price_out_per_mtok: float | None = None


class Registry:
    def __init__(self, manifests: list[NodeManifest], models: list[ModelInfo]):
        self._by_type = {m.type: m for m in manifests}
        self._models = {m.id: m for m in models}

    def get(self, type_: str) -> NodeManifest | None:
        return self._by_type.get(type_)

    def all(self) -> list[NodeManifest]:
        return sorted(self._by_type.values(), key=lambda m: (m.category, m.label))

    def model(self, model_id: str) -> ModelInfo | None:
        return self._models.get(model_id)

    def models(self) -> list[ModelInfo]:
        return list(self._models.values())

    def with_manifests(self, extra: list[NodeManifest]) -> Registry:
        """A registry that also contains workspace/custom manifests (Sprint 7)."""
        return Registry([*self._by_type.values(), *extra], list(self._models.values()))


def load_manifests(directory: Path = HERE / "nodes") -> list[NodeManifest]:
    return [NodeManifest.model_validate(json.loads(p.read_text())) for p in sorted(directory.glob("*.json"))]


@cache
def default_registry() -> Registry:
    models = [ModelInfo.model_validate(m) for m in json.loads((HERE / "models.json").read_text())]
    return Registry(load_manifests(), models)
