"""Playground (S31): chat with a model before building anything, then turn the chat's setup into a workflow.

The playground sends the conversation to a model (the project's default model unless another is picked) with an
optional system prompt; nothing is stored. "Make it a workflow" creates a workflow with one agent step that has the
same instructions and model, so what worked in the playground becomes the start of a real workflow.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.api.v1.workflows import WorkflowOut, _out, service
from agentcanvas.db.repositories import ProjectRepo
from agentcanvas.errors import InvalidInput
from agentcanvas.ids import new_id
from agentcanvas.services.secrets import redact
from agentcanvas.services.workflows import WorkflowService

router = APIRouter(prefix="/projects/{project_id}/playground", tags=["playground"])
TIMEOUT_S = 120
MODEL = r"^[a-z0-9_\-]+:.+$"


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)


class ChatIn(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=100)
    system_prompt: str = Field("", max_length=20_000)
    model: str | None = Field(None, pattern=MODEL, description="provider:model; omitted: the project's default")
    temperature: float | None = Field(None, ge=0, le=2)


class ChatOut(BaseModel):
    reply: str
    model: str
    usage: dict[str, int] = Field(default_factory=dict)


class WorkflowIn(BaseModel):
    name: str = Field("Playground assistant", min_length=1, max_length=200)
    system_prompt: str = Field("", max_length=20_000)
    model: str | None = Field(None, pattern=MODEL)
    temperature: float | None = Field(None, ge=0, le=2)


async def _model(session: AsyncSession, project_id: str, model: str | None) -> tuple[str, dict[str, Any]]:
    """The model to use and its settings (the default model's address and key when it is the default)."""
    project = await ProjectRepo(session).require(project_id)
    default = (project.settings or {}).get("default_model") or {}
    chosen = model or default.get("model")
    if not chosen:
        raise InvalidInput("choose a model, or set the project's default model on the Model page first")
    config = {k: v for k, v in default.items() if k != "model" and v} if chosen == default.get("model") else {}
    return chosen, config


@router.post("/chat", response_model=ChatOut)
async def chat(
    project_id: str, body: ChatIn, request: Request, session: AsyncSession = Depends(get_session)
) -> ChatOut:
    """One answer from the model to the conversation so far (nothing is saved)."""
    model, config = await _model(session, project_id, body.model)
    if body.temperature is not None:
        config["temperature"] = body.temperature
    projects = request.app.state.projects
    factory = getattr(request.app.state, "resources_factory", None)
    secrets = projects.secrets.get(project_id, {})
    resources = (
        factory(f"playground:{project_id}", None)
        if factory
        else projects.adapter.new_resources(secrets=dict(secrets), resources_class=projects.resources_class)
    )
    messages = [{"role": "system", "content": body.system_prompt}] if body.system_prompt.strip() else []
    messages += [m.model_dump() for m in body.messages]
    usage: dict[str, int] = {}
    try:
        reply = await asyncio.wait_for(
            request.app.state.models.chat(resources, model, messages, [], usage=usage, config=config), TIMEOUT_S
        )
    except TimeoutError as exc:
        raise InvalidInput(f"the model did not answer within {TIMEOUT_S} s") from exc
    except Exception as exc:
        hidden = [v for v in secrets.values() if isinstance(v, str) and len(v) >= 6]
        raise InvalidInput(redact(f"the model call failed: {type(exc).__name__}: {exc}", hidden)[:500]) from exc
    return ChatOut(reply=str(reply.get("content") or ""), model=model, usage=usage)


@router.post("/workflow", response_model=WorkflowOut, status_code=201)
async def make_workflow(
    project_id: str,
    body: WorkflowIn,
    response: Response,
    session: AsyncSession = Depends(get_session),
    svc: WorkflowService = Depends(service),
    user: CurrentUser = Depends(current_user),
) -> WorkflowOut:
    """A workflow with one agent step that has the playground's instructions and model."""
    model, config = await _model(session, project_id, body.model)
    conf: dict[str, Any] = {"model": model, **config}
    if body.temperature is not None:
        conf["temperature"] = body.temperature
    ir = {
        "ir_version": "1.2",
        "id": new_id("wf"),
        "name": body.name,
        "description": "Made from the playground.",
        "state": {
            "channels": [{"name": "messages", "type": {"type": "array"}, "reducer": "add_messages", "io": "inout"}]
        },
        "resources": [{"id": "r_model", "type": "model.chat", "name": "model", "config": conf}],
        "nodes": [
            {
                "id": "n_assistant",
                "type": "core.agent",
                "name": "assistant",
                "label": "Assistant",
                "config": {"system_prompt": body.system_prompt},
            },
        ],
        "edges": [
            {"id": "e_start", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_assistant"}},
            {"id": "e_end", "kind": "flow", "source": {"node": "n_assistant"}, "target": {"node": "END"}},
            {
                "id": "w_model",
                "kind": "wiring",
                "source": {"node": "r_model"},
                "target": {"node": "n_assistant", "port": "model"},
            },
        ],
        "metadata": {"made_in": "playground"},
    }
    layout = {"n_assistant": {"x": 240, "y": 120}, "r_model": {"x": 240, "y": 300}}
    view = await svc.create_from_ir(project_id, ir, layout, user.id)
    response.headers["Location"] = f"/v1/workflows/{view.row.id}"
    return _out(view, response)
