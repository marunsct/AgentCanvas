"""First-run setup: pick the project's default model and its API key or local server (one form, any provider).

The default model is stored in the project's settings; workflows created from templates afterwards use it instead
of the template's own models. An API key typed here becomes the project secret named after the provider
(e.g. OPENAI_API_KEY), which chat models of that provider use when their config names no key. Local servers (LM Studio,
vLLM, llama.cpp) and OpenAI-compatible APIs take an optional key (LMSTUDIO_API_KEY, …) for servers that require one, and
`server-models` lists the models such a server offers.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.repositories import ProjectRepo
from agentcanvas.errors import InvalidInput
from agentcanvas.registry import ProviderInfo, default_registry, installed_providers
from agentcanvas.services.secrets import SecretService, redact

router = APIRouter(prefix="/projects/{project_id}/setup", tags=["setup"])
COMPATIBLE_KEY = "OPENAI_COMPATIBLE_API_KEY"  # the secret for an OpenAI-compatible API that needs a key
TEST_TIMEOUT_S = 60
LIST_TIMEOUT_S = 10
MAX_SERVER_MODELS = 500


class DefaultModel(BaseModel):
    model: str
    base_url: str | None = None
    api_key: dict[str, str] | None = None  # {"$secret": NAME}


class SetupOut(BaseModel):
    done: bool
    default_model: DefaultModel | None = None
    key_secret: str | None = Field(None, description="the project secret the default model's key is read from")
    key_set: bool = Field(False, description="that secret exists, or the server has the variable")


class SetupIn(BaseModel):
    model: str = Field(pattern=r"^[a-z0-9_\-]+:.+$", description="provider:model, e.g. ollama:llama3.3")
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(None, max_length=4000, description="stored as a project secret; omit to keep")


class ServerModelsIn(BaseModel):
    provider: str = Field(pattern=r"^[a-z0-9_\-]+$")
    base_url: str | None = Field(None, max_length=500)
    api_key: str | None = Field(
        None, max_length=4000, description="used for this request only; omit to use the saved key"
    )


class ServerModelsOut(BaseModel):
    ok: bool
    models: list[str] = Field(default_factory=list, description="model names, without the provider prefix")
    error: str | None = None


class TestOut(BaseModel):
    ok: bool
    reply: str | None = None
    error: str | None = None


def _provider(model: str) -> ProviderInfo:
    p = default_registry().provider(model.partition(":")[0])
    if p is None:
        known = ", ".join(x.id for x in default_registry().providers())
        raise InvalidInput(f"unknown model provider in '{model}'; use one of: {known}")
    return p


def _key_secret(p: ProviderInfo) -> str | None:
    """The project secret a provider's key is stored in: its required key, or the optional one of a local server or
    OpenAI-compatible API (LMSTUDIO_API_KEY, OPENAI_COMPATIBLE_API_KEY, …)."""
    return p.key_env or p.optional_key_env or (COMPATIBLE_KEY if p.base_url_required else None)


def _default_model(body: SetupIn, p: ProviderInfo, key_given: bool) -> DefaultModel:
    if p.base_url_required and not body.base_url:
        raise InvalidInput(f"{p.label} needs a base URL")
    base_url = body.base_url if body.base_url and body.base_url != p.base_url else None
    optional = p.optional_key_env or (COMPATIBLE_KEY if p.base_url_required else None)
    api_key = {"$secret": optional} if optional and not p.key_env and key_given else None
    return DefaultModel(model=body.model, base_url=base_url, api_key=api_key)


def _same(a: str | None, b: str | None) -> bool:
    return bool(a and b) and a.strip().rstrip("/").lower() == b.strip().rstrip("/").lower()  # type: ignore[union-attr]


def stored_key_may_go(project_settings: dict[str, Any], p: ProviderInfo, base_url: str | None) -> bool:
    """Whether a key stored for provider `p` (project secret or server variable) may be sent to `base_url`: its own
    address (none given, or the provider's), or the server the project saved with it in setup. Anywhere else the key
    must be given with the request: a builder could otherwise point a model at a server they run and read the
    project's key from the request it receives (2026-10-29 review, S-4)."""
    if not base_url or _same(base_url, p.base_url):
        return True
    saved = (project_settings or {}).get("default_model") or {}
    return str(saved.get("model", "")).partition(":")[0] == p.id and _same(base_url, saved.get("base_url"))


def _key_set(request: Request, project_id: str, name: str | None) -> bool:
    if name is None:
        return False
    return name in request.app.state.projects.secrets.get(project_id, {}) or bool(os.environ.get(name))


@router.get("", response_model=SetupOut)
async def get_setup(project_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> SetupOut:
    project = await ProjectRepo(session).require(project_id)
    settings = project.settings or {}
    dm = DefaultModel.model_validate(settings["default_model"]) if settings.get("default_model") else None
    p = default_registry().provider(dm.model.partition(":")[0]) if dm else None
    name = _key_secret(p) if p else None
    return SetupOut(
        done=bool(settings.get("setup_done")), default_model=dm, key_secret=name,
        key_set=_key_set(request, project_id, name),
    )  # fmt: skip


@router.put("", response_model=SetupOut)
async def put_setup(
    project_id: str,
    body: SetupIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> SetupOut:
    """Save the default model (and its key as a project secret) and mark setup as done."""
    project = await ProjectRepo(session).require(project_id)
    p = _provider(body.model)
    key = (body.api_key or "").strip()
    name = _key_secret(p)
    dm = _default_model(body, p, bool(key) or _key_set(request, project_id, name))
    if key and name:
        await SecretService(session, request.app.state.keyring).put(project_id, name, key, user.id)
        await request.app.state.secrets.refresh(project_id)  # runs here see it right away, other instances soon
    project.settings = {
        **(project.settings or {}),
        "default_model": dm.model_dump(exclude_none=True),
        "setup_done": True,
    }
    await session.commit()
    return await get_setup(project_id, request, session)


@router.post("/skip", response_model=SetupOut)
async def skip_setup(project_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> SetupOut:
    """Hide the setup prompt without choosing a model (templates keep their own models)."""
    project = await ProjectRepo(session).require(project_id)
    project.settings = {**(project.settings or {}), "setup_done": True}
    await session.commit()
    return await get_setup(project_id, request, session)


@router.post("/test", response_model=TestOut)
async def test_model(
    project_id: str, body: SetupIn, request: Request, session: AsyncSession = Depends(get_session)
) -> TestOut:
    """Send one short message to the model with these settings (nothing is saved)."""
    project = await ProjectRepo(session).require(project_id)
    p = _provider(body.model)
    projects = request.app.state.projects
    secrets = dict(projects.secrets.get(project_id, {}))
    name = _key_secret(p)
    if name and not stored_key_may_go(project.settings or {}, p, body.base_url):
        secrets.pop(name, None)  # another server: only a key typed here goes there
    if body.api_key and body.api_key.strip() and name:
        secrets[name] = body.api_key.strip()
    try:
        dm = _default_model(body, p, bool(name and name in secrets))
    except InvalidInput as exc:
        return TestOut(ok=False, error=str(exc))
    config: dict[str, Any] = dm.model_dump(exclude_none=True, exclude={"model"})
    resources = projects.adapter.new_resources(secrets=secrets, resources_class=projects.resources_class)
    try:
        reply = await asyncio.wait_for(
            request.app.state.models.complete(
                resources, body.model, "You are a connection test.", "Reply with: OK", config=config
            ),
            TEST_TIMEOUT_S,
        )
    except TimeoutError:
        return TestOut(ok=False, error=f"no answer within {TEST_TIMEOUT_S} s")
    except Exception as exc:  # noqa: BLE001 - any provider error is the answer to "does this work?"
        hidden = [v for v in secrets.values() if len(v) >= 6]
        return TestOut(ok=False, error=redact(f"{type(exc).__name__}: {exc}", hidden)[:300])
    return TestOut(ok=True, reply=reply[:200])


@router.post("/server-models", response_model=ServerModelsOut)
async def server_models(project_id: str, body: ServerModelsIn, request: Request,
                        session: AsyncSession = Depends(get_session)) -> ServerModelsOut:  # fmt: skip
    """The models a local server (Ollama, LM Studio, vLLM, llama.cpp) or an OpenAI-compatible API offers, read from
    its model list (`/api/tags` for Ollama, `/models` otherwise), so the model name can be picked instead of typed."""
    project = await ProjectRepo(session).require(project_id)
    p = next((x for x in installed_providers() if x.id == body.provider), None)
    if p is None or not (p.local or p.base_url_required):
        raise InvalidInput(f"'{body.provider}' is not a local server or an OpenAI-compatible API")
    base = (body.base_url or "").strip() or p.base_url  # installed_providers() maps localhost for Docker
    if not base:
        return ServerModelsOut(ok=False, error=f"{p.label} needs a base URL")
    name = _key_secret(p)
    key = (body.api_key or "").strip() or None
    if not key and name and stored_key_may_go(project.settings or {}, p, base):  # another server gets a typed key only
        key = request.app.state.projects.secrets.get(project_id, {}).get(name) or os.environ.get(name)
    return await list_server_models(request, p, base, key)


async def list_server_models(
    request: Request, p: ProviderInfo, base: str, key: str | None, timeout: float = LIST_TIMEOUT_S
) -> ServerModelsOut:
    """Read a local server's or OpenAI-compatible API's model list; any connection or format error is the answer."""
    ollama = p.id == "ollama"
    url = base.rstrip("/") + ("/api/tags" if ollama else "/models")
    try:
        transport = getattr(request.app.state, "model_list_transport", None)  # tests serve a fake server here
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=transport) as client:
            r = await client.get(url, headers={"Authorization": f"Bearer {key}"} if key else {})
        if r.status_code in (401, 403):
            return ServerModelsOut(
                ok=False, error=f"the server refused the request ({r.status_code}): check the API key"
            )
        r.raise_for_status()
        data = r.json()
        items = data.get("models" if ollama else "data") if isinstance(data, dict) else None
        ids = [str(m.get("name" if ollama else "id")) for m in items or [] if isinstance(m, dict)]
    except Exception as exc:  # noqa: BLE001 - any connection or format error is the answer
        hidden = [key] if key and len(key) >= 6 else []
        return ServerModelsOut(ok=False, error=redact(f"{type(exc).__name__}: {exc}", hidden)[:300])
    return ServerModelsOut(ok=True, models=sorted({i for i in ids if i and i != "None"})[:MAX_SERVER_MODELS])
