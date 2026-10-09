"""First-run setup: pick the project's default model and its API key or local server (one form, any provider).

The default model is stored in the project's settings; workflows created from templates afterwards use it instead
of the template's own models. An API key typed here becomes the project secret named after the provider
(e.g. OPENAI_API_KEY), which chat models of that provider use when their config names no key.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.repositories import ProjectRepo
from agentcanvas.errors import InvalidInput
from agentcanvas.registry import ProviderInfo, default_registry
from agentcanvas.services.secrets import SecretService, redact

router = APIRouter(prefix="/projects/{project_id}/setup", tags=["setup"])
COMPATIBLE_KEY = "OPENAI_COMPATIBLE_API_KEY"  # the secret for an OpenAI-compatible API that needs a key
TEST_TIMEOUT_S = 60


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
    return p.key_env or (COMPATIBLE_KEY if p.base_url_required else None)


def _default_model(body: SetupIn, p: ProviderInfo, key_given: bool) -> DefaultModel:
    if p.base_url_required and not body.base_url:
        raise InvalidInput(f"{p.label} needs a base URL")
    base_url = body.base_url if body.base_url and body.base_url != p.base_url else None
    api_key = {"$secret": COMPATIBLE_KEY} if p.base_url_required and key_given else None
    return DefaultModel(model=body.model, base_url=base_url, api_key=api_key)


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
    dm = _default_model(body, p, bool(key) or _key_set(request, project_id, COMPATIBLE_KEY))
    name = _key_secret(p)
    if key and name:
        await SecretService(session, request.app.state.keyring).put(project_id, name, key, user.id)
        request.app.state.projects.secrets.setdefault(project_id, {})[name] = key  # runs see it right away
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
    await ProjectRepo(session).require(project_id)
    p = _provider(body.model)
    projects = request.app.state.projects
    secrets = dict(projects.secrets.get(project_id, {}))
    name = _key_secret(p)
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
