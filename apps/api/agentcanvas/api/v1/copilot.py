"""Copilot endpoints (S14): chat about a workflow, get its changes as proposals, and the Copilot's model setting.

`POST /workflows/{wf_id}/copilot/messages` streams the answer as Server-Sent Events: `start`, `text`, `tool`,
`brief`, `usage`, `proposal`, `error`, and always `done` last. A proposal is applied by the editor through the normal
`POST /workflows/{wf_id}/commands` (so it is one undo step), then reported here as accepted or rejected.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import RANK, CurrentUser, current_user, get_session, get_settings_dep
from agentcanvas.copilot import settings as copilot_settings
from agentcanvas.copilot.pack import Pack, build_pack
from agentcanvas.copilot.service import CopilotTurn, diff
from agentcanvas.copilot.settings import CopilotSettings, ResolvedModel
from agentcanvas.copilot.tools import ToolContext
from agentcanvas.db.models import CopilotConversation
from agentcanvas.db.repositories import ProjectRepo
from agentcanvas.errors import Conflict, Forbidden, InvalidInput, NotFound, TooManyRequests
from agentcanvas.ids import new_id
from agentcanvas.ir.commands import CommandError, apply_batch, parse_commands
from agentcanvas.ir.models import Workflow
from agentcanvas.observability import counted_stream
from agentcanvas.registry import Registry, default_registry
from agentcanvas.services.workflows import WorkflowService, WorkflowView, list_templates
from agentcanvas.settings import Settings

router = APIRouter(tags=["copilot"])
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
_busy: set[tuple[str, str]] = set()  # (workflow, person) answering right now: one turn at a time per conversation
_packs: dict[str, Pack] = {}  # registry + templates fingerprint -> pack


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    mode: Literal["build", "ask"] = "build"


class ProposalDecision(BaseModel):
    status: Literal["accepted", "rejected"]


class ModelInfoOut(BaseModel):
    model: str | None = None
    origin: str | None = None
    problem: str | None = Field(default=None, description="why the Copilot cannot answer yet, in plain words")


class ConversationOut(BaseModel):
    id: str | None
    enabled: bool
    messages: list[dict[str, Any]]
    brief: dict[str, Any] | None
    proposals: list[dict[str, Any]]
    pending: dict[str, Any] | None = Field(default=None, description="the open proposal with its diff and preview")
    usage: dict[str, int]
    token_cap: int
    model: ModelInfoOut
    pack_tokens: int


class SettingsOut(CopilotSettings):
    resolved: ModelInfoOut | None = None


def _service(request: Request, session: AsyncSession, settings: Settings) -> WorkflowService:
    return WorkflowService(session, settings, request.app.state.projects.registry)


def _pack(registry: Registry, templates: dict[str, dict[str, Any]]) -> Pack:
    key = json.dumps([[m.type, m.version, m.provenance] for m in registry.all()] + sorted(templates))
    if key not in _packs:
        _packs.clear()  # one live entry per registry shape is enough
        _packs[key] = build_pack(registry, templates)
    return _packs[key]


async def _conversation(
    session: AsyncSession, wf_id: str, user_id: str, lock: bool = False
) -> CopilotConversation | None:
    q = select(CopilotConversation).where(
        CopilotConversation.workflow_id == wf_id, CopilotConversation.user_id == user_id
    )
    return await session.scalar(q.with_for_update() if lock else q)


async def _model(
    svc: WorkflowService, view: WorkflowView, project_settings: dict[str, Any], cs: CopilotSettings
) -> ResolvedModel:
    registry = svc.registry(view.row.project_id) or default_registry()
    policies = await svc.policies(view.row.project_id)
    return copilot_settings.resolve(cs, project_settings, view.workflow, registry, policies)


def _pending(conv: CopilotConversation | None, view: WorkflowView) -> dict[str, Any] | None:
    """The latest open proposal, previewed on the current draft (it may no longer apply after other edits)."""
    open_ = [p for p in (conv.proposals if conv else []) if p.get("status") == "pending"]
    if not open_:
        return None
    p = open_[-1]
    try:
        after, _ = apply_batch(view.workflow, parse_commands(p["commands"]))
    except (CommandError, ValueError) as exc:
        return {**p, "stale": True, "problem": f"the workflow changed since: {exc}"}
    return {**p, "stale": False, "diff": diff(view.workflow, after), "preview": after.model_dump(mode="json")}


@router.get("/workflows/{wf_id}/copilot", response_model=ConversationOut)
async def get_conversation(
    wf_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    user: CurrentUser = Depends(current_user),
) -> ConversationOut:
    svc = _service(request, session, settings)
    view = await svc.get(wf_id)
    project = await ProjectRepo(session).require(view.row.project_id)
    cs = copilot_settings.load(project.settings)
    conv = await _conversation(session, wf_id, user.id)
    try:
        m = await _model(svc, view, project.settings or {}, cs)
        model = ModelInfoOut(model=m.model, origin=m.origin)
    except InvalidInput as exc:
        model = ModelInfoOut(problem=str(exc))
    registry = svc.registry(view.row.project_id) or default_registry()
    return ConversationOut(
        id=conv.id if conv else None,
        enabled=cs.enabled,
        messages=conv.messages if conv else [],
        brief=conv.brief if conv else None,
        proposals=[{k: v for k, v in p.items() if k != "commands"} for p in (conv.proposals if conv else [])],
        pending=_pending(conv, view),
        usage=conv.usage if conv else {},
        token_cap=cs.token_cap,
        model=model,
        pack_tokens=_pack(registry, list_templates(settings)).tokens,
    )


DECIDED = ("accepted", "rejected")


def merge_proposals(stored: list[dict[str, Any]], turn: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The turn's proposals over the stored ones, except that a decision recorded meanwhile wins."""
    by_id = {p.get("id"): p for p in stored}
    out = []
    for p in turn:
        old = by_id.pop(p.get("id"), None)
        out.append(old if old is not None and old.get("status") in DECIDED else p)
    merged = [*by_id.values(), *out]
    # two turns that overlapped (another API process) each left one pending: only the newest stays open
    pending = [p for p in merged if p.get("status") == "pending"]
    return [{**p, "status": "superseded"} if p in pending[:-1] else p for p in merged]


@router.post("/workflows/{wf_id}/copilot/messages", response_class=StreamingResponse)
async def send_message(
    wf_id: str,
    body: MessageIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    user: CurrentUser = Depends(current_user),
) -> StreamingResponse:
    """Ask the Copilot (`ask`: questions only, any role) or have it build (`build`: builders) — an SSE stream."""
    if body.mode == "build" and RANK.get(user.role, -1) < RANK["builder"]:
        raise Forbidden("only builders can have the Copilot change workflows; use Ask mode for questions")
    key = (wf_id, user.id)
    if key in _busy:  # checked and taken with no await in between
        raise Conflict("the Copilot is still answering your last message")
    _busy.add(key)
    try:
        return await _start_turn(wf_id, body, request, session, settings, user, key)
    except BaseException:
        _busy.discard(key)
        raise


async def _start_turn(
    wf_id: str,
    body: MessageIn,
    request: Request,
    session: AsyncSession,
    settings: Settings,
    user: CurrentUser,
    key: tuple[str, str],
) -> StreamingResponse:
    svc = _service(request, session, settings)
    view = await svc.get(wf_id)
    project_id = view.row.project_id
    project = await ProjectRepo(session).require(project_id)
    cs = copilot_settings.load(project.settings)
    if not cs.enabled:
        raise Forbidden("the Copilot is switched off for this project")
    model = await _model(svc, view, project.settings or {}, cs)
    conv = await _conversation(session, wf_id, user.id)
    if conv is None:
        conv = CopilotConversation(id=new_id("cpc"), workflow_id=wf_id, user_id=user.id, messages=[], proposals=[])
        conv.usage = {}
        session.add(conv)
        try:
            await session.flush()
        except IntegrityError as exc:  # the same person's first message from another tab won
            raise Conflict("the Copilot is still answering your last message") from exc
    usage: dict[str, int] = dict(conv.usage or {})
    if usage.get("input_tokens", 0) + usage.get("output_tokens", 0) >= cs.token_cap:
        raise TooManyRequests("this conversation reached its token limit; start a new chat")
    registry = svc.registry(project_id) or default_registry()
    templates = list_templates(settings)

    async def validate(wf: Workflow) -> list[Any]:
        return await svc._validate(wf, project_id)

    ctx = ToolContext(
        workflow=view.workflow, registry=registry, templates=templates, validate=validate, brief=conv.brief
    )
    factory = getattr(request.app.state, "resources_factory", None)
    resources = (
        factory(f"copilot:{conv.id}", view.workflow)
        if factory
        else request.app.state.projects.for_workflow(view.workflow)
    )
    turn = CopilotTurn(
        request.app.state.projects.adapter, resources, model, _pack(registry, templates), ctx,
        history=list(conv.messages or []), proposals=[dict(p) for p in conv.proposals or []], usage=usage,
        token_cap=cs.token_cap, base_version=view.row.version,
    )  # fmt: skip
    conv_id = conv.id
    seen_messages = len(turn.history)
    usage_before = dict(usage)
    await session.commit()  # the stream outlives this request's session (it is closed when the response starts)
    db = request.app.state.db

    async def save() -> None:
        """Merge this turn into the row as it is now: decisions taken meanwhile (another tab) are kept."""
        async with db.sessionmaker() as s:
            row = await s.get(CopilotConversation, conv_id, with_for_update=True)
            if row is None:  # "New chat" while answering
                return
            row.messages = [*(row.messages or []), *turn.history[seen_messages:]]
            row.proposals = merge_proposals(list(row.proposals or []), turn.proposals)
            row.brief = ctx.brief
            now = dict(row.usage or {})
            for k, v in turn.usage.items():
                now[k] = now.get(k, 0) + v - usage_before.get(k, 0)
            row.usage = now
            await s.commit()

    async def stream() -> AsyncIterator[str]:
        try:
            async for event in turn.run(body.text.strip(), body.mode):
                if event["type"] == "proposal":  # older open proposals are replaced by the new one
                    for p in turn.proposals[:-1]:
                        if p.get("status") == "pending":
                            p["status"] = "superseded"
                yield f"data: {json.dumps(event, default=str)}\n\n"
        finally:
            try:
                await asyncio.shield(save())
            finally:
                _busy.discard(key)

    return StreamingResponse(counted_stream(stream()), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/workflows/{wf_id}/copilot/proposals/{proposal_id}", status_code=204)
async def decide_proposal(
    wf_id: str,
    proposal_id: str,
    body: ProposalDecision,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> Response:
    """Record what the person did with a proposal (the editor applies accepted commands itself)."""
    conv = await _conversation(session, wf_id, user.id, lock=True)  # two tabs cannot both accept it
    proposals = [dict(p) for p in (conv.proposals if conv else [])]
    match = next((p for p in proposals if p["id"] == proposal_id), None)
    if conv is None or match is None:
        raise NotFound(f"proposal '{proposal_id}' not found")
    if match["status"] != "pending":
        raise Conflict(f"proposal '{proposal_id}' is already {match['status']}")
    match["status"] = body.status
    conv.proposals = proposals
    await session.commit()
    return Response(status_code=204)


@router.delete("/workflows/{wf_id}/copilot", status_code=204)
async def new_chat(
    wf_id: str, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> Response:
    """Forget this conversation (brief, messages and proposals) and start over."""
    await session.execute(
        delete(CopilotConversation).where(
            CopilotConversation.workflow_id == wf_id, CopilotConversation.user_id == user.id
        )
    )
    await session.commit()
    return Response(status_code=204)


@router.get("/projects/{project_id}/copilot-settings", response_model=SettingsOut)
async def get_copilot_settings(project_id: str, session: AsyncSession = Depends(get_session)) -> SettingsOut:
    project = await ProjectRepo(session).require(project_id)
    cs = copilot_settings.load(project.settings)
    resolved = None
    if cs.source == "separate" and cs.model:
        resolved = ModelInfoOut(model=cs.model, origin="Copilot settings")
    elif default := (project.settings or {}).get("default_model"):
        resolved = ModelInfoOut(model=default.get("model"), origin="project default")
    return SettingsOut(**cs.model_dump(), resolved=resolved)


@router.put("/projects/{project_id}/copilot-settings", response_model=SettingsOut)
async def put_copilot_settings(
    project_id: str,
    body: CopilotSettings,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> SettingsOut:
    """Turn the Copilot on or off, pick its model (the workflow's own or a separate one) and its token limit."""
    project = await ProjectRepo(session).require(project_id)
    if body.source == "separate":
        if not body.model:
            raise InvalidInput("choose the Copilot's model, or use the workflow's model")
        registry = request.app.state.projects.registry(project_id) or default_registry()
        policies = await WorkflowService(session, settings).policies(project_id)
        copilot_settings.check_model_policies(body.model, policies, registry)
        info = registry.model(body.model)
        if info is not None and not info.tools:
            raise InvalidInput(f"{body.model} cannot call tools, which the Copilot needs; choose another model")
    project.settings = {**(project.settings or {}), "copilot": body.model_dump(exclude_none=True)}
    await session.commit()
    return await get_copilot_settings(project_id, session)
