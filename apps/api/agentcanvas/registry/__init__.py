"""Node-type registry: one manifest per node/resource type (spec doc 04 §12, ticket M1-T7).

Manifests drive the palette, the node renderers (ports), the inspector forms (config JSON Schema)
and the validator (port types, required ports, config validation, channel writes, side effects).
Built-in manifests live in `registry/nodes/*.json`; workspace custom nodes are added in Sprint 7.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from functools import cache
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentcanvas.sampling import SAMPLING, without_sampling  # noqa: F401 - part of the registry's API

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
    price_in_per_mtok: float | None = None  # USD per 1M input tokens; None = unknown (AGENTCANVAS_MODEL_PRICES)
    price_out_per_mtok: float | None = None
    # prompt caching: reading cached input, and writing it (5-minute cache); None = charged as ordinary input
    price_cache_read_per_mtok: float | None = None
    price_cache_write_per_mtok: float | None = None
    # takes temperature / top_p / top_k: current Claude models refuse non-default values (400) and reasoning models
    # such as GPT-5 take the default only, so a run leaves them out for a model marked False
    sampling: bool = True


def token_counts(meta: Any) -> dict[str, int]:
    """A model reply's token counts from LangChain's `usage_metadata` (or any mapping shaped like it): input and
    output, and the part of the input read from or written to the provider's prompt cache. `input_tokens`
    includes the cached part, as LangChain reports it."""
    meta = meta or {}
    details = meta.get("input_token_details") or {}
    return {
        "input_tokens": int(meta.get("input_tokens") or 0),
        "output_tokens": int(meta.get("output_tokens") or 0),
        "cache_read_tokens": int(details.get("cache_read") or 0),
        "cache_write_tokens": int(details.get("cache_creation") or 0),
    }


def billed_tokens(usage: dict[str, Any]) -> int:
    """Tokens a token cap counts: what was sent and received, less what was read from the prompt cache (a tenth of
    the price or less; the Copilot re-sends its whole knowledge pack on every call, so counting it in full ended a
    chat after one or two builds)."""
    return (
        int(usage.get("input_tokens", 0)) - int(usage.get("cache_read_tokens", 0)) + int(usage.get("output_tokens", 0))
    )


class ProviderInfo(BaseModel):
    """A model provider: the `provider:` prefix of a model id, its integration and where its API key comes from."""

    model_config = ConfigDict(extra="forbid")
    id: str  # the model id prefix, e.g. "openai" in "openai:gpt-5.5"
    label: str
    package: str  # the integration distribution, e.g. langchain-openai
    module: str  # its import name, used to tell whether it is installed
    key_env: str | None = None  # server env variable / project secret holding the API key; None = no key needed
    # a key the server may or may not need (an LM Studio token, vLLM/llama.cpp --api-key, a compatible API's key):
    # the project secret / server variable it is read from when set
    optional_key_env: str | None = None
    local: bool = False  # runs on your own machine (Ollama, LM Studio, vLLM, llama.cpp)
    base_url: str | None = None  # default server address
    base_url_required: bool = False
    example: str = ""
    notes: str = ""
    installed: bool = True  # filled in by `installed_providers()`


class Registry:
    def __init__(self, manifests: list[NodeManifest], models: list[ModelInfo], providers: Sequence[ProviderInfo] = ()):
        self._by_type = {m.type: m for m in manifests}
        self._models = {m.id: m for m in models}
        self._providers = {p.id: p for p in providers}

    def get(self, type_: str) -> NodeManifest | None:
        return self._by_type.get(type_)

    def all(self) -> list[NodeManifest]:
        return sorted(self._by_type.values(), key=lambda m: (m.category, m.label))

    def model(self, model_id: str) -> ModelInfo | None:
        return self._models.get(model_id)

    def models(self) -> list[ModelInfo]:
        return list(self._models.values())

    def usable_model(self, model_id: str) -> bool:
        """A model id that can be created from its id alone: in the catalog, or of a known provider that needs no
        base URL in the config (judges and simulated users take only a model id)."""
        if model_id in self._models:
            return True
        provider, sep, name = model_id.partition(":")
        p = self._providers.get(provider)
        return bool(sep and name and p and not p.base_url_required)

    def provider(self, provider_id: str) -> ProviderInfo | None:
        return self._providers.get(provider_id)

    def providers(self) -> list[ProviderInfo]:
        return list(self._providers.values())

    def with_manifests(self, extra: list[NodeManifest]) -> Registry:
        """A registry that also contains workspace/custom manifests (Sprint 7)."""
        return Registry([*self._by_type.values(), *extra], list(self._models.values()), list(self._providers.values()))


def load_manifests(directory: Path = HERE / "nodes") -> list[NodeManifest]:
    return [NodeManifest.model_validate(json.loads(p.read_text())) for p in sorted(directory.glob("*.json"))]


def priced(models: list[ModelInfo], raw: str | None) -> list[ModelInfo]:
    """Apply AGENTCANVAS_MODEL_PRICES, `{"openai:gpt-5.5": [in, out], ...}` in USD per million tokens: the catalog
    prices only the models whose list price was checked, and an operator knows their own (or negotiated) prices.
    A malformed value or an unknown model is refused, so a budget never silently counts nothing."""
    if not raw:
        return models
    try:
        table = json.loads(raw)
        prices = {str(k): tuple(float(x) for x in v) for k, v in table.items()}
        if any(len(v) not in (2, 4) for v in prices.values()):
            raise ValueError("each model takes [in, out] or [in, out, cache read, cache write]")
    except (ValueError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError(
            f'AGENTCANVAS_MODEL_PRICES must be JSON like {{"provider:model": [in, out]}} (or [in, out, cache read, '
            f"cache write]): {exc}"
        ) from None
    known = {m.id for m in models}
    if unknown := sorted(set(prices) - known):
        raise ValueError(f"AGENTCANVAS_MODEL_PRICES names models not in the catalog: {', '.join(unknown)}")
    if any(x < 0 for pair in prices.values() for x in pair):
        raise ValueError("AGENTCANVAS_MODEL_PRICES: prices cannot be negative")
    fields = ("price_in_per_mtok", "price_out_per_mtok", "price_cache_read_per_mtok", "price_cache_write_per_mtok")
    return [m.model_copy(update=dict(zip(fields, prices[m.id], strict=False))) if m.id in prices else m for m in models]


@cache
def default_registry() -> Registry:
    catalog = [ModelInfo.model_validate(m) for m in json.loads((HERE / "models.json").read_text())]
    models = priced(catalog, os.environ.get("AGENTCANVAS_MODEL_PRICES"))
    providers = [ProviderInfo.model_validate(p) for p in json.loads((HERE / "providers.json").read_text())]
    return Registry(load_manifests(), models, providers)


def provider_installed(provider: ProviderInfo) -> bool:
    """Whether this server can create the provider's models (its integration package is importable)."""
    return find_spec(provider.module) is not None


def installed_providers() -> list[ProviderInfo]:
    """The providers with `installed` filled in, and local servers' default address as this server reaches them
    (AGENTCANVAS_LOCAL_MODELS_HOST, e.g. host.docker.internal when the API runs in a container)."""
    host = os.environ.get("AGENTCANVAS_LOCAL_MODELS_HOST")

    def effective(p: ProviderInfo) -> ProviderInfo:
        base_url = p.base_url.replace("localhost", host, 1) if host and p.local and p.base_url else p.base_url
        return p.model_copy(update={"installed": provider_installed(p), "base_url": base_url})

    return [effective(p) for p in default_registry().providers()]


@cache
def load_gallery() -> dict[str, Any]:
    """The curated MCP connector gallery (S15): `{"version": 1, "connectors": [...]}`."""
    path = HERE / "mcp_gallery.json"
    return json.loads(path.read_text()) if path.exists() else {"version": 1, "connectors": []}
