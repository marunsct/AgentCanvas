"""Which models are set up to answer here, for every place that offers a choice (Settings > Models by purpose, the
Copilot's model menu, knowledge bases): a model is offered only when it can answer, so no menu offers a model that
fails for want of a key or a running server.

- a hosted model when its provider's key is a project secret or a server variable;
- a model in your own cloud account (AWS Bedrock, Google Vertex AI) when it has credentials and the server a default
  region;
- a local model (Ollama, LM Studio, vLLM, llama.cpp) when its server lists it right now;
- the project's OpenAI-compatible model when it has a base URL.

Local servers do not say which of their models embed, so a model's name decides (`EMBEDDING_NAME`): embedding models
are offered for knowledge bases and left out of chat choices. The demo server answers every model, so it offers the
whole catalogue.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Iterable
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas import cloudmodels
from agentcanvas.api.deps import get_session
from agentcanvas.api.v1.setup import list_server_models
from agentcanvas.db.repositories import ProjectRepo
from agentcanvas.registry import ProviderInfo, Registry, default_registry, installed_providers
from agentcanvas.services import model_defaults

router = APIRouter(tags=["catalog"])
LOCAL_LIST_TIMEOUT_S = 3.0  # a local model server that does not answer this fast is left out


class ModelOption(BaseModel):
    id: str = Field(description="provider:model, or 'builtin' for the built-in embedding")
    label: str
    provider: str = Field(description="the provider's name, e.g. LM Studio (local)")
    local: bool = False


BaseUrlFor = Callable[[str], Any]  # provider id -> the base URL the project reaches it at, if it has its own


def project_base_url(project_settings: dict[str, Any]) -> BaseUrlFor:
    """The project model's base URL for its own provider (an LM Studio model beside the project's LM Studio model is
    on the same server)."""
    default = project_settings.get("default_model") or {}
    return lambda pid: default.get("base_url") if str(default.get("model", "")).partition(":")[0] == pid else None


def _providers() -> dict[str, ProviderInfo]:
    return {p.id: p for p in installed_providers() if p.installed}


def _has_key(p: ProviderInfo, secrets: dict[str, str]) -> bool:
    return bool(p.key_env and (p.key_env in secrets or os.environ.get(p.key_env)))


def _cloud_ready(model: str, secrets: dict[str, str], hosted: bool) -> bool:
    """A model in your own cloud account answers with credentials (the project's, or the server's identity outside
    the hosted edition) and the server's default region (a menu sets no region of its own)."""
    return not cloudmodels.credentials_problem(model, secrets, hosted=hosted) and not cloudmodels.region_problem(
        model, {}
    )


async def _served(
    request: Request, providers: dict[str, ProviderInfo], secrets: dict[str, str], base_url_for: BaseUrlFor
) -> list[tuple[ProviderInfo, str]]:
    """Every model the local servers list right now, asked in parallel."""

    async def listed(p: ProviderInfo) -> list[tuple[ProviderInfo, str]]:
        name = p.optional_key_env
        key = (secrets.get(name) or os.environ.get(name)) if name else None
        base = str(base_url_for(p.id) or p.base_url)
        found = await list_server_models(request, p, base, key, timeout=LOCAL_LIST_TIMEOUT_S)
        return [(p, m) for m in found.models] if found.ok else []

    lists = await asyncio.gather(*(listed(p) for p in providers.values() if p.local and p.base_url))
    return [item for found in lists for item in found]


async def ready_chat_models(
    request: Request,
    project_id: str,
    project_settings: dict[str, Any],
    registry: Registry | None = None,
    configured: Iterable[str] = (),
    base_url_for: BaseUrlFor | None = None,
) -> list[ModelOption]:
    """Chat models set up to answer: the catalogue's and the `configured` ones (the project's, the Copilot's, a
    workflow's), and what local servers list."""
    registry = registry or default_registry()
    base_url_for = base_url_for or project_base_url(project_settings)
    providers = _providers()
    secrets = request.app.state.projects.secrets.get(project_id, {})
    demo = bool(getattr(request.app.state, "demo_models", False))
    hosted = request.app.state.settings.edition == "hosted"
    ready: list[str] = []
    listed = [m.id for m in registry.models() if m.retires is None]  # a retiring model is not offered for new work
    for model in dict.fromkeys(listed + [m for m in configured if m]):
        p = providers.get(model.partition(":")[0])
        if p is None or (p.local and not demo):
            continue
        if cloudmodels.cloud_of(model):
            if demo or _cloud_ready(model, secrets, hosted):
                ready.append(model)
        elif demo or _has_key(p, secrets) or (p.base_url_required and base_url_for(p.id)):
            ready.append(model)
    if not demo:
        served = await _served(request, providers, secrets, base_url_for)
        ready += [f"{p.id}:{m}" for p, m in served if not model_defaults.EMBEDDING_NAME.search(m)]
    options = []
    for model in dict.fromkeys(ready):
        info, p = registry.model(model), providers[model.partition(":")[0]]
        label = info.label if info else model.partition(":")[2]
        options.append(ModelOption(id=model, label=label, provider=p.label, local=p.local))
    return options


async def ready_embedding_models(
    request: Request, project_id: str, project_settings: dict[str, Any]
) -> list[ModelOption]:
    """Embedding models set up to answer: the built-in offline one, hosted ones whose key is set, and the embedding
    models local servers list."""
    providers = _providers()
    secrets = request.app.state.projects.secrets.get(project_id, {})
    out = [ModelOption(id=model_defaults.BUILTIN_EMBEDDING, label="Built-in (offline)", provider="AgentCanvas",
                       local=True)]  # fmt: skip
    for model, label in model_defaults.HOSTED_EMBEDDINGS.items():
        p = providers.get(model.partition(":")[0])
        if p and _has_key(p, secrets):
            out.append(ModelOption(id=model, label=label, provider=p.label))
    served = await _served(request, providers, secrets, project_base_url(project_settings))
    out += [
        ModelOption(id=f"{p.id}:{m}", label=m, provider=p.label, local=True)
        for p, m in served
        if model_defaults.EMBEDDING_NAME.search(m)
    ]
    return out


@router.get("/projects/{project_id}/chat-models", response_model=list[ModelOption])
async def chat_models(
    project_id: str, request: Request, session: AsyncSession = Depends(get_session)
) -> list[ModelOption]:
    """The chat models set up to answer in this project (judges, simulated users, AI blocks)."""
    settings = (await ProjectRepo(session).require(project_id)).settings or {}
    default = (settings.get("default_model") or {}).get("model")
    return await ready_chat_models(request, project_id, settings, configured=[default] if default else [])


@router.get("/projects/{project_id}/embedding-models", response_model=list[ModelOption])
async def embedding_models(
    project_id: str, request: Request, session: AsyncSession = Depends(get_session)
) -> list[ModelOption]:
    """The embedding models a knowledge base can use here (chat models cannot embed, so they are left out)."""
    settings = (await ProjectRepo(session).require(project_id)).settings or {}
    return await ready_embedding_models(request, project_id, settings)
