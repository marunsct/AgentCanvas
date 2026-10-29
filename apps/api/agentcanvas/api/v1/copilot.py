"""Copilot endpoints (S14): chat about a workflow, get its changes as proposals, and the Copilot's model setting.

`POST /workflows/{wf_id}/copilot/messages` streams the answer as Server-Sent Events: `start`, `text`, `tool`,
`brief`, `usage`, `proposal`, `error`, and always `done` last. A proposal is applied by the editor through the normal
`POST /workflows/{wf_id}/commands` (so it is one undo step), then reported here as accepted or rejected.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import RANK, CurrentUser, current_user, get_session, get_settings_dep
from agentcanvas.api.v1.model_choices import ModelOption, ready_chat_models
from agentcanvas.api.v1.runs import _context as run_context
from agentcanvas.api.v1.workspace import _off_loop
from agentcanvas.copilot import settings as copilot_settings
from agentcanvas.copilot.pack import Pack, build_pack
from agentcanvas.copilot.runs import RunAccess
from agentcanvas.copilot.service import CopilotTurn, diff
from agentcanvas.copilot.settings import CopilotSettings, ResolvedModel
from agentcanvas.copilot.tools import ToolContext, carry_pending
from agentcanvas.db.models import CopilotConversation
from agentcanvas.db.repositories import ProjectRepo, WorkflowRepo
from agentcanvas.errors import Conflict, Forbidden, InvalidInput, NotFound, TooManyRequests
from agentcanvas.ids import new_id
from agentcanvas.ir.commands import CommandError, apply_batch, parse_commands
from agentcanvas.ir.models import Workflow
from agentcanvas.observability import counted_stream
from agentcanvas.registry import Registry, billed_tokens, default_registry
from agentcanvas.runtime.runner import usage_price
from agentcanvas.services.testcases import Expect, TestCaseService
from agentcanvas.services.workflows import WorkflowService, WorkflowView, list_templates
from agentcanvas.settings import Settings
from agentcanvas.validation.policies import Policy

router = APIRouter(tags=["copilot"])
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
LEASE = timedelta(minutes=15)  # a turn holds its conversation this long at most (a crashed server's lease expires)
_packs: dict[str, Pack] = {}  # registry + templates fingerprint -> pack


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    mode: Literal["build", "ask"] = "build"
    allow_real_runs: bool = Field(False, description="the person allows test runs that call real models")


class ProposalDecision(BaseModel):
    status: Literal["accepted", "rejected"]
    commands: list[int] | None = Field(
        None, description="accepted: the indices of the changes kept (the editor applied those); null = all"
    )
    tests: list[int] | None = Field(None, description="accepted: the indices of the tests to save; null = all")
    files: list[int] | None = Field(None, description="accepted: the indices of the files to write; null = all")
    notes: bool = Field(True, description="accepted: add the proposal's notes to the project's Copilot notes")


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
    chosen_model: str | None = Field(None, description="the model chosen for this conversation; null: the project's")
    cost_usd: float | None = Field(
        None,
        description="what the conversation's answers cost, where the model's price is known (each answer's own "
        "`usage` and `cost_usd` are on its message)",
    )
    token_cap: int
    model: ModelInfoOut
    pack_tokens: int


class SettingsOut(CopilotSettings):
    resolved: ModelInfoOut | None = None


def _engine(settings: Settings) -> type[CopilotTurn]:
    """The loop (default) or the Deep Agents harness (AGENTCANVAS_COPILOT_ENGINE=deepagents)."""
    if settings.copilot_engine == "deepagents":
        from agentcanvas.adapters.langchain.copilot import DeepAgentTurn

        return DeepAgentTurn
    return CopilotTurn


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
        CopilotConversation.workflow_id == wf_id,
        CopilotConversation.user_id == user_id,
        CopilotConversation.archived_at.is_(None),
    )
    return await session.scalar(q.with_for_update() if lock else q)


def _aware(t: datetime | None) -> datetime | None:
    return t.replace(tzinfo=UTC) if t is not None and t.tzinfo is None else t


async def _model(
    svc: WorkflowService,
    view: WorkflowView,
    project_settings: dict[str, Any],
    cs: CopilotSettings,
    mode: str = "build",
    override: str | None = None,
) -> ResolvedModel:
    registry = svc.registry(view.row.project_id) or default_registry()
    policies = await svc.policies(view.row.project_id)
    return copilot_settings.resolve(cs, project_settings, view.workflow, registry, policies, mode, override)


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
        m = await _model(svc, view, project.settings or {}, cs, override=conv.model if conv else None)
        model = ModelInfoOut(model=m.model, origin=m.origin)
    except InvalidInput as exc:
        model = ModelInfoOut(problem=str(exc))
    registry = svc.registry(view.row.project_id) or default_registry()
    return ConversationOut(
        id=conv.id if conv else None,
        enabled=cs.enabled,
        messages=conv.messages if conv else [],
        brief=conv.brief if conv else None,
        proposals=[_listed(p) for p in (conv.proposals if conv else [])],
        pending=_pending(conv, view),
        usage=conv.usage if conv else {},
        chosen_model=conv.model if conv else None,
        cost_usd=_spent(conv.messages if conv else []),
        token_cap=cs.token_cap,
        model=model,
        pack_tokens=_pack(registry, list_templates(settings)).tokens,
    )


def _spent(messages: list[dict[str, Any]]) -> float | None:
    costs = [m["cost_usd"] for m in messages if isinstance(m.get("cost_usd"), int | float)]
    return round(sum(costs), 6) if costs else None


def _listed(p: dict[str, Any]) -> dict[str, Any]:
    """A past proposal as the conversation lists it: what it was, without its commands or files' contents."""
    out = {k: v for k, v in p.items() if k not in ("commands", "files")}
    if p.get("files"):
        out["files"] = [f["path"] for f in p["files"]]
    return out


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
    leased: list[str] = []
    try:
        return await _start_turn(wf_id, body, request, session, settings, user, leased)
    except BaseException:
        # the turn never started streaming: an uncommitted lease goes with the rollback, which also lets go of the
        # row lock before the release below (on Postgres it would otherwise wait for this session for ever)
        await session.rollback()
        if leased:
            await _release(request.app.state.db, leased[0])
        raise


async def _release(db: Any, conv_id: str) -> None:
    async with db.sessionmaker() as s:
        row = await s.get(CopilotConversation, conv_id, with_for_update=True)
        if row is not None:
            row.busy_until = None
            await s.commit()


async def _start_turn(
    wf_id: str,
    body: MessageIn,
    request: Request,
    session: AsyncSession,
    settings: Settings,
    user: CurrentUser,
    leased: list[str],
) -> StreamingResponse:
    svc = _service(request, session, settings)
    view = await svc.get(wf_id)
    project_id = view.row.project_id
    project = await ProjectRepo(session).require(project_id)
    cs = copilot_settings.load(project.settings)
    if not cs.enabled:
        raise Forbidden("the Copilot is switched off for this project")
    now = datetime.now(UTC)
    conv = await _conversation(session, wf_id, user.id, lock=True)  # the lease: one turn at a time, on any replica
    model = await _model(svc, view, project.settings or {}, cs, body.mode, conv.model if conv else None)
    if conv is None:
        conv = CopilotConversation(id=new_id("cpc"), workflow_id=wf_id, user_id=user.id, messages=[], proposals=[])
        conv.usage = {}
        session.add(conv)
    elif (until := _aware(conv.busy_until)) is not None and until > now:
        raise Conflict("the Copilot is still answering your last message")
    conv.busy_until = now + LEASE
    try:
        await session.flush()
    except IntegrityError as exc:  # the same person's first message from another tab won
        raise Conflict("the Copilot is still answering your last message") from exc
    leased.append(conv.id)
    usage: dict[str, int] = dict(conv.usage or {})
    if billed_tokens(usage) >= cs.token_cap:
        raise TooManyRequests("this conversation reached its token limit; start a new chat")
    registry = svc.registry(project_id) or default_registry()
    templates = list_templates(settings)

    async def validate(wf: Workflow) -> list[Any]:
        return await svc._validate(wf, project_id)

    runs = RunAccess(
        request.app.state.runner, request.app.state.db.sessionmaker, view.workflow,
        lambda wf, given: run_context(wf, given, user), user.id,
    )  # fmt: skip
    ctx = ToolContext(
        workflow=view.workflow, registry=registry, templates=templates, validate=validate, brief=conv.brief,
        runs=runs, secret_names=sorted(request.app.state.projects.secrets.get(project_id, {})),
        allow_real_runs=body.allow_real_runs, workspace=request.app.state.projects.workspace(project_id),
    )  # fmt: skip
    carry_pending(ctx, list(conv.proposals or []))
    factory = getattr(request.app.state, "resources_factory", None)
    resources = (
        factory(f"copilot:{conv.id}", view.workflow)
        if factory
        else request.app.state.projects.for_workflow(view.workflow)
    )
    turn = _engine(settings)(
        request.app.state.models, resources, model, _pack(registry, templates), ctx,
        history=list(conv.messages or []), proposals=[dict(p) for p in conv.proposals or []], usage=usage,
        token_cap=cs.token_cap, base_version=view.row.version, project_notes=cs.notes, saved=view.workflow,
    )  # fmt: skip
    conv_id = conv.id
    seen_messages = len(turn.history)
    usage_before = dict(usage)
    spent_before = _spent(list(conv.messages or []))  # the earlier answers' costs, as GET /copilot sums them
    await session.commit()  # the stream outlives this request's session (it is closed when the response starts)
    db = request.app.state.db

    async def save() -> None:
        """Merge this turn into the row as it is now: decisions taken meanwhile (another tab) are kept."""
        async with db.sessionmaker() as s:
            row = await s.get(CopilotConversation, conv_id, with_for_update=True)
            if row is None:  # "New chat" while answering
                return
            new = [dict(m) for m in turn.history[seen_messages:]]
            spent = {k: v - usage_before.get(k, 0) for k, v in turn.usage.items() if v > usage_before.get(k, 0)}
            answer = next((m for m in reversed(new) if m.get("role") == "assistant"), None)
            if answer is not None and spent:  # what this answer cost, shown under it and summed for the conversation
                cost = usage_price(model.model, spent)
                answer.update(usage=spent, model=model.model, cost_usd=cost)
            row.messages = [*(row.messages or []), *new]
            row.busy_until = None
            if not row.title:  # the first question names the conversation in the thread list
                first = next((m.get("content", "") for m in row.messages if m.get("role") == "user"), "")
                row.title = " ".join(first.split())[:80] or None
            row.proposals = merge_proposals(list(row.proposals or []), turn.proposals)
            row.brief = ctx.brief
            now = dict(row.usage or {})
            for k, v in turn.usage.items():
                now[k] = now.get(k, 0) + v - usage_before.get(k, 0)
            row.usage = now
            await s.commit()

    async def stream() -> AsyncIterator[str]:
        try:
            async for raw in turn.run(body.text.strip(), body.mode):
                event = raw
                if event["type"] in ("usage", "done"):  # what the conversation has cost so far, when the price is known
                    used = event["usage"]
                    turn_used = {k: v - usage_before.get(k, 0) for k, v in used.items() if isinstance(v, int)}
                    now = usage_price(model.model, turn_used)  # this turn, on top of the earlier answers (as a reload)
                    total = None if now is None and spent_before is None else (spent_before or 0) + (now or 0)
                    event = {**event, "cost_usd": total}
                if event["type"] == "proposal":  # older open proposals are replaced by the new one
                    for p in turn.proposals[:-1]:
                        if p.get("status") == "pending":
                            p["status"] = "superseded"
                yield f"data: {json.dumps(event, default=str)}\n\n"
        finally:
            await asyncio.shield(save())

    return StreamingResponse(counted_stream(stream()), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/workflows/{wf_id}/copilot/proposals/{proposal_id}", status_code=204)
async def decide_proposal(
    wf_id: str,
    proposal_id: str,
    body: ProposalDecision,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> Response:
    """Record what the person did with a proposal (the editor applies accepted commands itself; tests, notes and
    workspace files are saved here)."""
    conv = await _conversation(session, wf_id, user.id, lock=True)  # two tabs cannot both accept it
    proposals = [dict(p) for p in (conv.proposals if conv else [])]
    match = next((p for p in proposals if p["id"] == proposal_id), None)
    if conv is None or match is None:
        raise NotFound(f"proposal '{proposal_id}' not found")
    if match["status"] != "pending":
        raise Conflict(f"proposal '{proposal_id}' is already {match['status']}")
    match["status"] = body.status
    match["decided_by"], match["decided_at"] = user.id, datetime.now(UTC).isoformat()  # a version credits it
    if body.status == "accepted" and body.commands is not None:  # partly accepted: what was kept, for the record
        total = len(match.get("commands") or [])
        if any(not 0 <= i < total for i in body.commands):
            raise InvalidInput(f"the proposal has {total} changes; keep indices from 0 to {total - 1}")
        match["accepted_commands"] = sorted(set(body.commands))
    kept_tests = list(match.get("tests") or [])
    if body.tests is not None:
        kept_tests = [t for i, t in enumerate(kept_tests) if i in set(body.tests)]
    kept_files = list(match.get("files") or [])
    if body.files is not None:
        kept_files = [f for i, f in enumerate(kept_files) if i in set(body.files)]
        match["accepted_files"] = sorted({f["path"] for f in kept_files})
    conv.proposals = proposals
    if body.status == "accepted" and kept_files:  # one commit per file, credited to the person who accepted
        row = await WorkflowRepo(session).require(wf_id)
        ws = request.app.state.projects.workspace(row.project_id)
        who = user.email or user.id
        for f in kept_files:  # every file is checked before any is written
            await _off_loop(ws, lambda f=f: ws.check(f["path"], f["content"]))
        for f in kept_files:
            message = f"Copilot: {match.get('summary') or 'change'} ({f['path']}, accepted by {who})"[:300]
            await _off_loop(ws, lambda f=f, message=message: ws.write(f["path"], f["content"], message))
    if body.status == "accepted":  # the editor applied the commands; tests and notes are saved here
        tests = TestCaseService(session)
        for t in kept_tests:
            await tests.add(
                wf_id, name=t["name"], input=t["input"], expect=Expect.model_validate(t.get("expect") or {}),
                mocks=t.get("mocks") or {}, created_by=user.id,
            )  # fmt: skip
        if body.notes and (notes := match.get("notes")):
            row = await WorkflowRepo(session).require(wf_id)
            project = await ProjectRepo(session).require(row.project_id)
            cs = copilot_settings.load(project.settings)
            merged = "\n".join([cs.notes, *(f"- {n}" for n in notes)]).strip()[-4000:]
            project.settings = {
                **(project.settings or {}),
                "copilot": {**cs.model_dump(exclude_none=True), "notes": merged},
            }
    await session.commit()
    return Response(status_code=204)


@router.delete("/workflows/{wf_id}/copilot", status_code=204)
async def new_chat(
    wf_id: str, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> Response:
    """Start over: the open conversation is archived (listed under the person's conversations), or dropped when it
    has no messages."""
    conv = await _conversation(session, wf_id, user.id, lock=True)
    if conv is not None:
        if (until := _aware(conv.busy_until)) is not None and until > datetime.now(UTC):
            raise Conflict("the Copilot is still answering; stop it first")
        if conv.messages:
            conv.archived_at = datetime.now(UTC)
        else:
            await session.delete(conv)
        await session.commit()
    return Response(status_code=204)


class CopilotThreadOut(BaseModel):
    id: str
    title: str | None
    messages: int
    open: bool = Field(description="the conversation the panel shows; the others are archived")
    created_at: datetime
    updated_at: datetime


async def _thread(session: AsyncSession, wf_id: str, user_id: str, conv_id: str) -> CopilotConversation:
    row = await session.get(CopilotConversation, conv_id, with_for_update=True)
    if row is None or row.workflow_id != wf_id or row.user_id != user_id:
        raise NotFound(f"conversation '{conv_id}' not found")
    return row


@router.get("/workflows/{wf_id}/copilot/threads", response_model=list[CopilotThreadOut])
async def list_threads(
    wf_id: str, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> list[CopilotThreadOut]:
    """The person's Copilot conversations about this workflow, newest first."""
    rows = await session.scalars(
        select(CopilotConversation)
        .where(CopilotConversation.workflow_id == wf_id, CopilotConversation.user_id == user.id)
        .order_by(CopilotConversation.updated_at.desc(), CopilotConversation.id.desc())
    )
    return [
        CopilotThreadOut(
            id=r.id,
            title=r.title,
            messages=len(r.messages or []),
            open=r.archived_at is None,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]


@router.post("/workflows/{wf_id}/copilot/threads/{conv_id}/open", status_code=204)
async def open_thread(
    wf_id: str, conv_id: str, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> Response:
    """Reopen an archived conversation; the open one is archived (or dropped when empty)."""
    target = await _thread(session, wf_id, user.id, conv_id)
    current = await _conversation(session, wf_id, user.id, lock=True)
    if current is not None and current.id != target.id:
        if (until := _aware(current.busy_until)) is not None and until > datetime.now(UTC):
            raise Conflict("the Copilot is still answering; stop it first")
        if current.messages:
            current.archived_at = datetime.now(UTC)
        else:
            await session.delete(current)
        await session.flush()
    target.archived_at = None
    await session.commit()
    return Response(status_code=204)


class ChatModelIn(BaseModel):
    model: str | None = Field(
        None, pattern=r"^[a-z0-9_\-]+:.+$", description="provider:model for this conversation; null: the project's"
    )


async def _ready_models(
    request: Request,
    project_id: str,
    project_settings: dict[str, Any],
    cs: CopilotSettings,
    wf: Workflow,
    registry: Registry,
    policies: list[Policy],
) -> list[ModelOption]:
    """The models set up to answer here (`model_choices`), with the Copilot's own and the workflow's among them, that
    pass the Copilot's checks (tool calling, local-only, policies)."""
    default = (project_settings.get("default_model") or {}).get("model")
    configured = [m for m in (cs.model, cs.ask_model, default) if m] + [
        str(r.config["model"])
        for r in wf.resources
        if r.type == "model.chat" and isinstance(r.config.get("model"), str)
    ]

    def base_url(model: str) -> Any:
        return copilot_settings.endpoint_for(model, cs, project_settings).get("base_url")

    ready = await ready_chat_models(
        request, project_id, project_settings, registry, configured, lambda pid: base_url(f"{pid}:-")
    )
    options = []
    for option in ready:
        url = base_url(option.id)
        try:
            copilot_settings.check_model(option.id, cs, policies, registry, url if isinstance(url, str) else None)
        except InvalidInput:
            continue
        options.append(option)
    return options


@router.get("/workflows/{wf_id}/copilot/models", response_model=list[ModelOption])
async def chat_models(
    wf_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> list[ModelOption]:
    """The models the conversation can switch to (PLAN §2.3): only those set up to answer here, so the menu does not
    offer a model that would fail for want of a key or a running server."""
    svc = _service(request, session, settings)
    view = await svc.get(wf_id)
    project_id = view.row.project_id
    project = await ProjectRepo(session).require(project_id)
    registry = svc.registry(project_id) or default_registry()
    return await _ready_models(
        request, project_id, project.settings or {}, copilot_settings.load(project.settings), view.workflow,
        registry, await svc.policies(project_id),
    )  # fmt: skip


@router.put("/workflows/{wf_id}/copilot/model", response_model=ModelInfoOut)
async def choose_chat_model(
    wf_id: str,
    body: ChatModelIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    user: CurrentUser = Depends(current_user),
) -> ModelInfoOut:
    """Switch the model of the open conversation (PLAN §2.3). It must pass the project's model policies and privacy
    setting; "New chat" goes back to the project's."""
    svc = _service(request, session, settings)
    view = await svc.get(wf_id)
    project = await ProjectRepo(session).require(view.row.project_id)
    cs = copilot_settings.load(project.settings)
    chosen = await _model(svc, view, project.settings or {}, cs, override=body.model)  # 422 when not allowed
    conv = await _conversation(session, wf_id, user.id, lock=True)
    if conv is None:
        conv = CopilotConversation(id=new_id("cpc"), workflow_id=wf_id, user_id=user.id, messages=[], proposals=[])
        conv.usage = {}
        session.add(conv)
    conv.model = body.model
    await session.commit()
    return ModelInfoOut(model=chosen.model, origin=chosen.origin)


class CopilotThreadPatch(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@router.patch("/workflows/{wf_id}/copilot/threads/{conv_id}", response_model=CopilotThreadOut)
async def rename_thread(
    wf_id: str,
    conv_id: str,
    body: CopilotThreadPatch,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> CopilotThreadOut:
    row = await _thread(session, wf_id, user.id, conv_id)
    row.title = body.title.strip()
    await session.commit()
    await session.refresh(row)  # updated_at comes from the database
    return CopilotThreadOut(id=row.id, title=row.title, messages=len(row.messages or []), open=row.archived_at is None,
                     created_at=row.created_at, updated_at=row.updated_at)  # fmt: skip


@router.delete("/workflows/{wf_id}/copilot/threads/{conv_id}", status_code=204)
async def delete_thread(
    wf_id: str, conv_id: str, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> Response:
    row = await _thread(session, wf_id, user.id, conv_id)
    if (until := _aware(row.busy_until)) is not None and until > datetime.now(UTC):
        raise Conflict("the Copilot is still answering; stop it first")
    await session.delete(row)
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
    """Turn the Copilot on or off, pick its model (the project's or a separate one) and its token limit. Fields left
    out keep their saved value (an explicit null clears one), so a client that does not know a field cannot erase it."""
    project = await ProjectRepo(session).require(project_id)
    saved = copilot_settings.load(project.settings).model_dump()
    body = CopilotSettings.model_validate({**saved, **body.model_dump(include=body.model_fields_set)})
    if body.source == "separate" and not body.model:
        raise InvalidInput("choose the Copilot's model, or use the workflow's model")
    chosen = [m for m in (body.model if body.source == "separate" else None, body.ask_model) if m]
    if chosen:  # the question model too: a bad one would only fail later, on every question
        registry = request.app.state.projects.registry(project_id) or default_registry()
        policies = await WorkflowService(session, settings).policies(project_id)
        for m in chosen:
            copilot_settings.check_model(m, body, policies, registry, body.base_url if m == body.model else None)
    project.settings = {**(project.settings or {}), "copilot": body.model_dump(exclude_none=True)}
    await session.commit()
    return await get_copilot_settings(project_id, session)


# ------------------------------------------------------------------ code help (S17) ----
CODE_KINDS = {
    "scripts": "a Script step: `def run(state: dict) -> dict` (or async) that reads workflow fields with "
    "`state.get(...)` and returns only the fields to update; an optional second parameter `runtime` gives the "
    "LangGraph runtime",
    "tools": "a tool the AI can call: a function decorated with `@tool` from `langchain_core.tools`, with typed "
    "arguments and a docstring the model reads to decide when to call it; it returns a string or JSON-able value",
    "nodes": "a custom block: a function `(state: dict, cfg: dict) -> dict` decorated with "
    "`@canvas_node(name='acme.my_block', label=..., writes=[...])` from `agentcanvas.sdk`",
    "events": "an event handler: `@on('type', filter=..., dedup_key=...)` from `agentcanvas.sdk` on "
    "`def handler(event, act)`, which may call `act.start_workflow(name, input=...)`",
}
CODE_SYSTEM = """You help a beginner write Python code for AgentCanvas, a visual agent-workflow builder.
The file is {path}. It is {kind}.
Secrets come from `agentcanvas.sdk.secret("NAME")`, never from literals. Keep code short, plain and commented
where a beginner would wonder why. Use only the standard library and packages the file already imports, unless asked.
Answer in two parts: first a few plain sentences for the person; then, unless you are only explaining, the WHOLE
updated file in one ```python fenced block (nothing after it). Never include more than one code block."""
FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)


class CodeHelpIn(BaseModel):
    action: Literal["write", "explain", "fix"]
    path: str = Field(min_length=1, max_length=500)
    content: str = Field("", max_length=200_000, description="the file as it is in the editor, unsaved edits included")
    selection: str | None = Field(None, max_length=50_000, description="the selected lines, when any")
    instruction: str = Field("", max_length=4000, description="what to write, or what to change")
    problems: list[str] = Field(default_factory=list, max_length=50, description="ruff findings shown for the file")


class CodeHelpOut(BaseModel):
    text: str = Field(description="the Copilot's explanation, in plain words")
    code: str | None = Field(None, description="the whole proposed file, to show as a diff; never saved here")
    model: str
    usage: dict[str, int] = Field(default_factory=dict)


def _kind(path: str) -> str:
    parts = path.strip("/").split("/")
    folder = parts[2] if parts[0] == "flows" and len(parts) > 3 else parts[0]
    return CODE_KINDS.get(folder, "a helper module other workspace code imports")


def code_prompt(body: CodeHelpIn) -> list[dict[str, Any]]:
    """The model's messages for one code-help request (pure, so it is testable without a model)."""
    task = {
        "write": f"Write or extend the code as asked: {body.instruction or 'a useful starting point for this file'}",
        "explain": "Explain what this code does, step by step, for a beginner. Do not change it; no code block.",
        "fix": "Fix the problems below (and any bug you are sure of), changing as little as possible."
        + (f" Also: {body.instruction}" if body.instruction else ""),
    }[body.action]
    parts = [task, f"Current file `{body.path}`:\n```python\n{body.content}\n```"]
    if body.selection:
        parts.append(f"Focus on this selected part:\n```python\n{body.selection}\n```")
    if body.problems:
        parts.append("Problems the linter reports:\n" + "\n".join(f"- {p}" for p in body.problems))
    system = CODE_SYSTEM.format(path=body.path, kind=_kind(body.path))
    return [{"role": "system", "content": system, "cache": True}, {"role": "user", "content": "\n\n".join(parts)}]


def split_reply(content: str, action: str) -> tuple[str, str | None]:
    """(explanation, whole proposed file or None) from the model's answer."""
    blocks = FENCE.findall(content)
    if action == "explain" or not blocks:
        return content.strip(), None
    code = blocks[-1]
    text = FENCE.sub("", content).strip()
    return text or "Here is the updated file.", code if code.endswith("\n") else code + "\n"


@router.post("/workflows/{wf_id}/copilot/code", response_model=CodeHelpOut)
async def code_help(
    wf_id: str,
    body: CodeHelpIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    user: CurrentUser = Depends(current_user),
) -> CodeHelpOut:
    """Copilot help in the code editor (S17): write code from a description, explain it, or fix it. The answer is a
    proposal the editor shows as a diff; nothing is written to the workspace here."""
    if body.action != "explain" and RANK.get(user.role, -1) < RANK["builder"]:
        raise Forbidden("only builders can have the Copilot write code; Explain works for every role")
    svc = _service(request, session, settings)
    view = await svc.get(wf_id)
    project = await ProjectRepo(session).require(view.row.project_id)
    cs = copilot_settings.load(project.settings)
    if not cs.enabled:
        raise Forbidden("the Copilot is switched off for this project")
    model = await _model(svc, view, project.settings or {}, cs, "ask" if body.action == "explain" else "build")
    factory = getattr(request.app.state, "resources_factory", None)
    resources = (
        factory(f"copilot-code:{wf_id}", view.workflow)
        if factory
        else request.app.state.projects.for_workflow(view.workflow)
    )
    usage: dict[str, int] = {}
    try:
        reply = await request.app.state.models.chat(
            resources, model.model, code_prompt(body), [], usage=usage, config=model.config
        )
    except Exception as exc:
        raise InvalidInput(f"the model call failed: {type(exc).__name__}: {exc}"[:500]) from exc
    text, code = split_reply(str(reply.get("content") or ""), body.action)
    return CodeHelpOut(text=text, code=code, model=model.model, usage=usage)
