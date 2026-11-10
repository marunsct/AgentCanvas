"""Playground (S31): chat with a model before building anything, then turn the chat's setup into a workflow.

The playground sends the conversation to a model (the project's default model unless another is picked) with an
optional system prompt; nothing is stored. "Make it a workflow" creates a workflow with one agent step that has the
same instructions and model, so what worked in the playground becomes the start of a real workflow.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.api.v1.workflows import WorkflowOut, _out, service
from agentcanvas.db.repositories import ProjectRepo
from agentcanvas.errors import InvalidInput
from agentcanvas.ids import new_id
from agentcanvas.observability import counted_stream
from agentcanvas.runtime.runner import tokens_of
from agentcanvas.sampling import without_sampling
from agentcanvas.services.secrets import redact
from agentcanvas.services.workflows import WorkflowService

router = APIRouter(prefix="/projects/{project_id}/playground", tags=["playground"])
TIMEOUT_S = 120
MODEL = r"^[a-z0-9_\-]+:.+$"
MAX_TOOLS = 20
MAX_TOOL_ROUNDS = 8  # rounds of tool calls before the model must answer
MAX_RESULT = 20_000  # characters of one tool result the model (and the page) gets
Tool = Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+:[A-Za-z_][A-Za-z0-9_]*$")]


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)


class ChatIn(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=100)
    system_prompt: str = Field("", max_length=20_000)
    model: str | None = Field(None, pattern=MODEL, description="provider:model; omitted: the project's default")
    temperature: float | None = Field(None, ge=0, le=2)
    tools: list[Tool] = Field(
        default_factory=list, max_length=MAX_TOOLS,
        description="workspace tools the model may call, by `import_path` (GET .../workspace/catalog)",
    )  # fmt: skip


class ToolUse(BaseModel):
    name: str
    args: dict[str, Any]
    result: str


class ChatOut(BaseModel):
    reply: str
    model: str
    usage: dict[str, int] = Field(default_factory=dict)
    tools: list[ToolUse] = Field(default_factory=list, description="the tool calls made for this answer, in order")


class WorkflowIn(BaseModel):
    name: str = Field("Playground assistant", min_length=1, max_length=200)
    system_prompt: str = Field("", max_length=20_000)
    model: str | None = Field(None, pattern=MODEL)
    temperature: float | None = Field(None, ge=0, le=2)
    tools: list[Tool] = Field(default_factory=list, max_length=MAX_TOOLS, description="become Python tool blocks")


def _picked(request: Request, project_id: str, paths: list[str]) -> list[dict[str, Any]]:
    """The workspace catalog's entries for the picked tools ({name, import_path, ...}), each once."""
    found = {t["import_path"]: t for t in request.app.state.projects.catalog(project_id).tools}
    for path in paths:
        if path not in found:
            raise InvalidInput(f"'{path}' is not a tool in the project's workspace")
    return [found[p] for p in dict.fromkeys(paths)]


async def _model(session: AsyncSession, project_id: str, model: str | None) -> tuple[str, dict[str, Any]]:
    """The model to use and its settings (the default model's address and key when it is the default)."""
    project = await ProjectRepo(session).require(project_id)
    default = (project.settings or {}).get("default_model") or {}
    chosen = model or default.get("model")
    if not chosen:
        raise InvalidInput("choose a model, or set the project's model in Settings > Models first")
    config = {k: v for k, v in default.items() if k != "model" and v} if chosen == default.get("model") else {}
    return chosen, config


async def _prepare(
    project_id: str, body: ChatIn, request: Request, session: AsyncSession
) -> tuple[str, dict[str, Any], Any, list[dict[str, Any]], dict[str, str], dict[str, Any]]:
    model, config = await _model(session, project_id, body.model)
    if body.temperature is not None:
        config["temperature"] = body.temperature
    config, _ = without_sampling(model, config)  # W020: a model that refuses a temperature is not sent one
    projects = request.app.state.projects
    tools: dict[str, Any] = {}  # picked workspace tools by the name the model calls them
    for entry in _picked(request, project_id, body.tools):
        # the project's own resources: they run workspace code where the project's sandbox says
        tool = projects.resources(project_id).tool(entry["name"], {"import_path": entry["import_path"]})
        tools[request.app.state.models.tool_spec(tool)["name"]] = tool
    factory = getattr(request.app.state, "resources_factory", None)
    secrets = projects.secrets.get(project_id, {})
    resources = (
        factory(f"playground:{project_id}", None)
        if factory
        else projects.adapter.new_resources(secrets=dict(secrets), resources_class=projects.resources_class)
    )
    messages = [{"role": "system", "content": body.system_prompt}] if body.system_prompt.strip() else []
    messages += [m.model_dump() for m in body.messages]
    return model, config, resources, messages, secrets, tools


async def _answer(
    request: Request,
    resources: Any,
    model: str,
    messages: list[dict[str, Any]],
    tools: dict[str, Any],
    usage: dict[str, int],
    config: dict[str, Any],
) -> AsyncIterator[dict[str, Any]]:
    """The model's answer when it may call `tools`: a `tool` event {name, args, result} for each call, then a
    `reply` event. Up to MAX_TOOL_ROUNDS rounds; the last one is asked for an answer without tools."""
    models = request.app.state.models
    specs = [{**models.tool_spec(t)} for t in tools.values()]
    messages = list(messages)
    for round_ in range(MAX_TOOL_ROUNDS + 1):
        reply = await models.chat(resources, model, messages, specs if round_ < MAX_TOOL_ROUNDS else [], usage, config)
        calls = reply.get("tool_calls") or []
        if not calls:
            yield {"type": "reply", "text": str(reply.get("content") or "")}
            return
        messages.append({"role": "assistant", "content": reply.get("content") or "", "tool_calls": calls})
        for call in calls:
            tool = tools.get(call["name"])
            try:
                if tool is None:
                    raise KeyError(f"no tool named {call['name']}")
                result = (await models.call_tool(tool, call["args"]))[:MAX_RESULT]
            except Exception as exc:  # noqa: BLE001 - the model reads the failure and may try again
                result = f"error: {type(exc).__name__}: {exc}"[:MAX_RESULT]
            yield {"type": "tool", "name": call["name"], "args": call["args"], "result": result}
            messages.append({"role": "tool", "content": result, "tool_call_id": call["id"]})


def _hide(text: str, secrets: dict[str, str]) -> str:
    return redact(text, [v for v in secrets.values() if isinstance(v, str) and len(v) >= 6])


def _failure(exc: Exception, secrets: dict[str, str]) -> str:
    return _hide(f"the model call failed: {type(exc).__name__}: {exc}", secrets)[:500]


@router.post("/chat", response_model=ChatOut)
async def chat(
    project_id: str, body: ChatIn, request: Request, session: AsyncSession = Depends(get_session)
) -> ChatOut:
    """One answer from the model to the conversation so far (nothing is saved)."""
    model, config, resources, messages, secrets, tools = await _prepare(project_id, body, request, session)
    usage: dict[str, int] = {}
    used: list[ToolUse] = []
    reply = ""
    try:
        async with asyncio.timeout(TIMEOUT_S):
            async for e in _answer(request, resources, model, messages, tools, usage, config):
                if e["type"] == "tool":
                    used.append(ToolUse(name=e["name"], args=e["args"], result=_hide(e["result"], secrets)))
                else:
                    reply = e["text"]
    except TimeoutError as exc:
        raise InvalidInput(f"the model did not answer within {TIMEOUT_S} s") from exc
    except Exception as exc:
        raise InvalidInput(_failure(exc, secrets)) from exc
    return ChatOut(reply=reply, model=model, usage=tokens_of(usage), tools=used)


@router.post("/chat/stream", response_class=StreamingResponse)
async def chat_stream(
    project_id: str, body: ChatIn, request: Request, session: AsyncSession = Depends(get_session)
) -> StreamingResponse:
    """The answer as it is generated, as server-sent events: `delta` {text} pieces, then `done` {reply, model,
    usage}, or `error` {message}. Nothing is saved. With `tools` the model answers in whole after its tool calls:
    a `tool` {name, args, result} event for each call, then the answer as one `delta`."""
    model, config, resources, messages, secrets, tools = await _prepare(project_id, body, request, session)
    await session.close()  # no connection held while the model answers

    async def events() -> AsyncIterator[str]:
        usage: dict[str, int] = {}
        reply = ""
        try:
            async with asyncio.timeout(TIMEOUT_S):
                if tools:
                    async for e in _answer(request, resources, model, messages, tools, usage, config):
                        if e["type"] == "tool":
                            yield f"data: {json.dumps({**e, 'result': _hide(e['result'], secrets)})}\n\n"
                        else:
                            reply = e["text"]
                            yield f"data: {json.dumps({'type': 'delta', 'text': reply})}\n\n"
                else:
                    async for piece in request.app.state.models.stream(resources, model, messages, usage, config):
                        reply += piece
                        yield f"data: {json.dumps({'type': 'delta', 'text': piece})}\n\n"
        except TimeoutError:
            late = f"the model did not answer within {TIMEOUT_S} s"
            yield f"data: {json.dumps({'type': 'error', 'message': late})}\n\n"
            return
        except Exception as exc:  # noqa: BLE001 - the stream has started: the failure is an event, keys redacted
            yield f"data: {json.dumps({'type': 'error', 'message': _failure(exc, secrets)})}\n\n"
            return
        yield f"data: {json.dumps({'type': 'done', 'reply': reply, 'model': model, 'usage': tokens_of(usage)})}\n\n"

    return StreamingResponse(
        counted_stream(events()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/workflow", response_model=WorkflowOut, status_code=201)
async def make_workflow(
    project_id: str,
    body: WorkflowIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    svc: WorkflowService = Depends(service),
    user: CurrentUser = Depends(current_user),
) -> WorkflowOut:
    """A workflow with one agent step that has the playground's instructions, model and tools."""
    model, config = await _model(session, project_id, body.model)
    conf: dict[str, Any] = {"model": model, **config}
    if body.temperature is not None:
        conf["temperature"] = body.temperature
    conf, _ = without_sampling(model, conf)  # W020: a model that refuses a temperature does not get one in the IR
    picked = _picked(request, project_id, body.tools)
    names: list[str] = []  # the function's name, made unique when two modules have one of that name
    for t in picked:
        fn = t["import_path"].partition(":")[2]
        names.append(fn if fn not in names else f"{fn}_{len(names)}")
    ir = {
        "ir_version": "1.3",
        "id": new_id("wf"),
        "name": body.name,
        "description": "Made from the playground.",
        "state": {
            "channels": [{"name": "messages", "type": {"type": "array"}, "reducer": "add_messages", "io": "inout"}]
        },
        "resources": [
            {"id": "r_model", "type": "model.chat", "name": "model", "config": conf},
            *(
                {
                    "id": f"r_tool{i}",
                    "type": "tool.python",
                    "name": names[i],
                    "config": {"import_path": t["import_path"]},
                }
                for i, t in enumerate(picked)
            ),
        ],
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
            *(
                {
                    "id": f"w_tool{i}",
                    "kind": "wiring",
                    "source": {"node": f"r_tool{i}"},
                    "target": {"node": "n_assistant", "port": "tools"},
                }
                for i in range(len(picked))
            ),
        ],
        "metadata": {"made_in": "playground"},
    }
    layout = {"n_assistant": {"x": 240, "y": 120}, "r_model": {"x": 240, "y": 300}}
    layout |= {f"r_tool{i}": {"x": 440 + 200 * i, "y": 300} for i in range(len(picked))}
    view = await svc.create_from_ir(project_id, ir, layout, user.id)
    response.headers["Location"] = f"/v1/workflows/{view.row.id}"
    return _out(view, response)
