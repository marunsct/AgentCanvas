"""Workflow endpoints: CRUD with optimistic locking, commands, layout, validation, code (M1-T3…T5)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, ValidationError, model_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas import agentspec, importers
from agentcanvas.adapters import framework_adapter
from agentcanvas.api.deps import CurrentUser, current_user, get_session, get_settings_dep, if_match
from agentcanvas.api.v1.workspace import _off_loop
from agentcanvas.db.models import CostAlert, Run, SpendArchive, Thread, WorkflowRow
from agentcanvas.db.retry import retrying
from agentcanvas.errors import InvalidInput
from agentcanvas.ids import new_id
from agentcanvas.ir.commands import Command
from agentcanvas.registry.adapters import portability
from agentcanvas.runtime.runner import periods
from agentcanvas.services import template_kit
from agentcanvas.services.workflows import WorkflowService, WorkflowView, list_templates
from agentcanvas.settings import Settings
from agentcanvas.validation import Diagnostic
from agentcanvas.validation.blast import BlastRadius, blast_radius

router = APIRouter(tags=["workflows"])


def service(
    request: Request, session: AsyncSession = Depends(get_session), settings: Settings = Depends(get_settings_dep)
):
    return WorkflowService(session, settings, request.app.state.projects.registry)


class WorkflowOut(BaseModel):
    id: str
    project_id: str
    name: str
    version: int
    ir_hash: str
    ir: dict[str, Any]
    layout: dict[str, Any]
    diagnostics: list[Diagnostic]
    created_at: datetime
    updated_at: datetime
    kit: template_kit.KitOut | None = Field(
        default=None, description="created from a template: the files, dataset, evaluators, tests and schedule it added"
    )


class WorkflowSummary(BaseModel):
    id: str
    name: str
    version: int
    updated_at: datetime


class WorkflowPage(BaseModel):
    items: list[WorkflowSummary]
    next_cursor: str | None


class CreateWorkflow(BaseModel):
    name: str = Field("Untitled workflow", min_length=1, max_length=200)
    template: str | None = None


class ReplaceIR(BaseModel):
    ir: dict[str, Any]


class CommandBatch(BaseModel):
    commands: list[Command] = Field(min_length=1)


class CommandResult(WorkflowOut):
    inverse: list[Command]


class LayoutUpdate(BaseModel):
    positions: dict[str, dict[str, float]]
    replace: bool = False


def _out(v: WorkflowView, response: Response | None = None) -> WorkflowOut:
    if response is not None:
        response.headers["ETag"] = f'"{v.row.version}"'
    return WorkflowOut(
        id=v.row.id,
        project_id=v.row.project_id,
        name=v.row.name,
        version=v.row.version,
        ir_hash=v.row.ir_hash,
        ir=v.workflow.model_dump(mode="json"),
        layout=v.row.layout or {},
        diagnostics=v.diagnostics,
        created_at=v.row.created_at,
        updated_at=v.row.updated_at,
    )


@router.post("/projects/{project_id}/workflows", response_model=WorkflowOut, status_code=201)
async def create_workflow(
    project_id: str,
    body: CreateWorkflow,
    request: Request,
    response: Response,
    svc: WorkflowService = Depends(service),
    user: CurrentUser = Depends(current_user),
) -> WorkflowOut:
    """An empty workflow, or one from a template with what the template brings beside it (`kit`)."""
    doc = list_templates(svc.settings).get(body.template) if body.template else None
    kit = template_kit.kit_of(doc)
    template_kit.check(kit)
    wf_id = new_id("wf")
    added = template_kit.KitOut()
    if kit.files:  # before the workflow, so its custom blocks are known when it is checked
        await svc.projects.require(project_id)
        ws = request.app.state.projects.workspace(project_id)
        added = await _off_loop(ws, lambda: template_kit.write_files(ws, kit, wf_id, str(body.template)))
    try:
        view = await svc.create(project_id, body.name, body.template, user.id, wf_id=wf_id)
        if kit.dataset or kit.evaluators or kit.tests or kit.triggers:
            added = await template_kit.add_records(svc.repo.s, kit, wf_id, user.id, added)
    except Exception:  # the kit's services commit as they go: undo all of it, never a workflow with half a kit
        await svc.repo.s.rollback()
        if await svc.repo.s.get(WorkflowRow, wf_id) is not None:
            await svc.delete(wf_id)  # its dataset, evaluators, tests and schedule go with it
        if written := added.files_written:
            here = request.app.state.projects.workspace(project_id)
            await _off_loop(here, lambda: [here.delete(rel) for rel in written])
        raise
    response.headers["Location"] = f"/v1/workflows/{view.row.id}"
    out = _out(view, response)
    return out.model_copy(update={"kit": added}) if kit != template_kit.Kit() else out


@router.get("/projects/{project_id}/workflows", response_model=WorkflowPage)
async def list_workflows(
    project_id: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
    svc: WorkflowService = Depends(service),
) -> WorkflowPage:
    rows = await svc.list(project_id, limit + 1, cursor)
    items = [WorkflowSummary(id=r.id, name=r.name, version=r.version, updated_at=r.updated_at) for r in rows[:limit]]
    return WorkflowPage(items=items, next_cursor=items[-1].id if len(rows) > limit else None)


@router.get("/workflows/{wf_id}", response_model=WorkflowOut)
async def get_workflow(wf_id: str, response: Response, svc: WorkflowService = Depends(service)) -> WorkflowOut:
    return _out(await svc.get(wf_id), response)


@router.put("/workflows/{wf_id}", response_model=WorkflowOut)
async def replace_workflow(
    wf_id: str,
    body: ReplaceIR,
    response: Response,
    version: int = Depends(if_match),
    svc: WorkflowService = Depends(service),
) -> WorkflowOut:
    return _out(await svc.replace(wf_id, body.ir, version), response)


@router.post("/workflows/{wf_id}/commands", response_model=CommandResult)
async def apply_commands(
    wf_id: str,
    body: CommandBatch,
    response: Response,
    version: int = Depends(if_match),
    svc: WorkflowService = Depends(service),
) -> CommandResult:
    view, inverse = await svc.apply_commands(wf_id, body.commands, version)
    return CommandResult(**_out(view, response).model_dump(), inverse=inverse)


@router.put("/workflows/{wf_id}/layout")
async def set_layout(wf_id: str, body: LayoutUpdate, svc: WorkflowService = Depends(service)) -> dict[str, Any]:
    return {"layout": await svc.set_layout(wf_id, body.positions, body.replace)}


class WorkflowPatch(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.patch("/workflows/{wf_id}", response_model=WorkflowOut)
async def rename_workflow(
    wf_id: str, body: WorkflowPatch, response: Response, svc: WorkflowService = Depends(service)
) -> WorkflowOut:
    """Rename the workflow (a new draft version; the editor of another tab is told it changed)."""
    if not body.name.strip():
        raise InvalidInput("a workflow needs a name")
    return _out(await svc.rename(wf_id, body.name.strip()), response)


class DuplicateIn(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200, description="default: '<name> (copy)'")


@router.post("/workflows/{wf_id}/duplicate", response_model=WorkflowOut, status_code=201)
async def duplicate_workflow(
    wf_id: str,
    body: DuplicateIn,
    response: Response,
    svc: WorkflowService = Depends(service),
    user: CurrentUser = Depends(current_user),
) -> WorkflowOut:
    """A copy of the draft and its layout in the same project (not its versions, runs or deployments)."""
    view = await svc.duplicate(wf_id, body.name.strip() if body.name else None, user.id)
    response.headers["Location"] = f"/v1/workflows/{view.row.id}"
    return _out(view, response)


@router.delete("/workflows/{wf_id}", status_code=204)
async def delete_workflow(wf_id: str, request: Request, svc: WorkflowService = Depends(service)) -> Response:
    """Delete a workflow with its versions, deployments and conversations, the conversations' saved state too."""
    await svc.repo.require(wf_id)
    await request.app.state.runner.forget_conversations(svc.repo.s, Thread.workflow_id == wf_id)
    await retrying(svc.repo.s, lambda: svc.delete(wf_id), what="workflow delete")  # runs ending write under it
    return Response(status_code=204)


@router.post("/workflows/{wf_id}/validate", response_model=list[Diagnostic])
async def validate_workflow(wf_id: str, svc: WorkflowService = Depends(service)) -> list[Diagnostic]:
    return (await svc.get(wf_id)).diagnostics


@router.get("/workflows/{wf_id}/code", response_class=PlainTextResponse)
async def get_code(wf_id: str, framework: str | None = None, svc: WorkflowService = Depends(service)) -> str:
    """The workflow's generated code; `?framework=` previews it converted to another installed framework (S25)."""
    view = await svc.get(wf_id)
    wf = view.workflow
    if framework and framework != wf.framework:
        wf = wf.model_copy(update={"framework": framework})
    return framework_adapter(wf.framework).generate(wf, svc.registry(view.row.project_id))


class PortabilityOut(BaseModel):
    adapter: str
    label: str
    ok: bool
    blockers: list[dict[str, Any]]
    emulated: list[dict[str, Any]]


@router.get("/workflows/{wf_id}/portability", response_model=list[PortabilityOut])
async def get_portability(wf_id: str, svc: WorkflowService = Depends(service)) -> list[dict[str, Any]]:
    """For each installed framework (S20): can this workflow move to it, and which blocks stop or bend it."""
    view = await svc.get(wf_id)
    return portability(view.workflow)


# ------------------------------------------------------------------ spending (S26) ----
class SpendPeriod(BaseModel):
    period: str  # "day" | "month" (UTC)
    period_start: str
    spent_usd: float
    limit_usd: float | None
    runs: int
    tokens: int
    cache_hits: int  # model answers taken from the answer cache
    fallbacks: int  # model calls a backup model answered


class CostAlertOut(BaseModel):
    id: str
    period: str
    period_start: str
    share: float
    spent_usd: float
    limit_usd: float
    message: str
    run_id: str | None
    created_at: datetime


class SpendOut(BaseModel):
    day: SpendPeriod
    month: SpendPeriod
    alerts: list[CostAlertOut]


@router.get("/workflows/{wf_id}/spend", response_model=SpendOut)
async def get_spend(
    wf_id: str, svc: WorkflowService = Depends(service), session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    """What the workflow's runs spent today and this month (UTC), against its day and month budgets, and its latest
    cost alerts (S26)."""
    view = await svc.get(wf_id)
    budget = view.workflow.settings.budget
    limits = {"day": budget.daily_max_cost_usd if budget else None,
              "month": budget.monthly_max_cost_usd if budget else None}  # fmt: skip
    out: dict[str, Any] = {}
    for period, (label, start) in periods().items():
        q = (
            select(
                func.count(Run.id), func.coalesce(func.sum(Run.tokens_in + Run.tokens_out), 0),
                func.coalesce(func.sum(Run.cost_usd), 0), func.coalesce(func.sum(Run.cache_hits), 0),
                func.coalesce(func.sum(Run.fallbacks), 0),
            )
            .join(Thread, Thread.id == Run.thread_id)
            .where(Thread.workflow_id == wf_id, Thread.updated_at >= start - timedelta(days=1))  # as workflow_spend
            .where(Run.started_at >= start)
        )  # fmt: skip
        runs, tokens, cost, hits, fallbacks = (await session.execute(q)).one()
        gone = select(  # what conversations deleted since spent (as workflow_spend)
            func.coalesce(func.sum(SpendArchive.runs), 0),
            func.coalesce(func.sum(SpendArchive.tokens_in + SpendArchive.tokens_out), 0),
            func.coalesce(func.sum(SpendArchive.cost_usd), 0), func.coalesce(func.sum(SpendArchive.cache_hits), 0),
            func.coalesce(func.sum(SpendArchive.fallbacks), 0),
        ).where(SpendArchive.workflow_id == wf_id, SpendArchive.hour >= start)  # fmt: skip
        g_runs, g_tokens, g_cost, g_hits, g_fallbacks = (await session.execute(gone)).one()
        runs, tokens, hits, fallbacks = runs + g_runs, tokens + g_tokens, hits + g_hits, fallbacks + g_fallbacks
        cost = float(cost) + float(g_cost)
        out[period] = SpendPeriod(
            period=period, period_start=label, spent_usd=round(float(cost), 6), limit_usd=limits[period],
            runs=int(runs), tokens=int(tokens), cache_hits=int(hits), fallbacks=int(fallbacks),
        )  # fmt: skip
    alerts = await session.scalars(
        select(CostAlert).where(CostAlert.workflow_id == wf_id).order_by(CostAlert.created_at.desc()).limit(20)
    )
    out["alerts"] = [CostAlertOut.model_validate(a, from_attributes=True) for a in alerts]
    return out


# ------------------------------------------------------------------ Agent Spec (S16) ----
class AgentSpecExport(BaseModel):
    spec: dict[str, Any]
    report: list[str]


class AgentSpecImport(BaseModel):
    spec: dict[str, Any]
    name: str | None = Field(None, min_length=1, max_length=200)


class AgentSpecImported(WorkflowOut):
    report: list[str]


@router.get("/workflows/{wf_id}/agent-spec", response_model=AgentSpecExport)
async def export_agent_spec(wf_id: str, svc: WorkflowService = Depends(service)) -> AgentSpecExport:
    """The workflow as an Open Agent Spec flow, with what Agent Spec cannot express."""
    view = await svc.get(wf_id)
    spec, report = agentspec.export_flow(view.workflow, view.row.layout)
    return AgentSpecExport(spec=spec, report=report)


@router.post("/projects/{project_id}/workflows/agent-spec", response_model=AgentSpecImported, status_code=201)
async def import_agent_spec(
    project_id: str,
    body: AgentSpecImport,
    response: Response,
    svc: WorkflowService = Depends(service),
    user: CurrentUser = Depends(current_user),
) -> AgentSpecImported:
    """A new workflow from an Open Agent Spec flow or agent."""
    try:  # off the event loop, as the import route: a large or strange file must not stop the server answering
        ir, report, layout = await asyncio.to_thread(
            importers.import_agentspec, body.spec, wf_id=new_id("wf"), name=body.name
        )
    except importers.ImportError_ as exc:
        raise InvalidInput(str(exc)) from exc
    view = await svc.create_from_ir(project_id, ir, layout, user.id)
    response.headers["Location"] = f"/v1/workflows/{view.row.id}"
    return AgentSpecImported(**_out(view, response).model_dump(), report=report)


# ------------------------------------------------------------------ importers (S31) ----
class ImportFile(BaseModel):
    file: dict[str, Any] | None = Field(
        None, description="The JSON a Langflow, Flowise, n8n or Agent Spec export downloads"
    )
    code: str | None = Field(
        None, max_length=500_000, description="OpenAI Agents SDK Python (an Agent Builder export, or written by hand)"
    )
    name: str | None = Field(None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _one(self) -> ImportFile:
        if (self.file is None) == (self.code is None):
            raise ValueError("send either `file` (an exported JSON file) or `code` (OpenAI Agents SDK Python)")
        return self


class Imported(WorkflowOut):
    format: str = Field(description="langflow, flowise, n8n, agentspec or openai_agents")
    report: list[str]


@router.post("/projects/{project_id}/workflows/import", response_model=Imported, status_code=201)
async def import_file(
    project_id: str,
    body: ImportFile,
    request: Request,
    response: Response,
    svc: WorkflowService = Depends(service),
    user: CurrentUser = Depends(current_user),
) -> Imported:
    """A new workflow from another builder's file (Langflow, Flowise, n8n or Open Agent Spec) or OpenAI Agents SDK
    code, with what changed."""
    wf_id = new_id("wf")
    try:  # off the event loop: a large or strange file must not stop the server answering others
        kind, ir, report, layout = await asyncio.to_thread(
            importers.import_document, body.file if body.file is not None else body.code, wf_id=wf_id, name=body.name
        )
    except importers.ImportError_ as exc:
        raise InvalidInput(str(exc)) from exc
    except RecursionError as exc:
        raise InvalidInput("the file is nested too deeply to import") from exc
    kit = template_kit.Kit()
    if kind == "openai_agents":  # the one importer that brings code, read from the code itself; never a file's kit
        try:
            kit = template_kit.kit_of(ir)
        except ValidationError as exc:
            raise InvalidInput(f"the imported code's workspace files are not valid: {exc}") from exc
    if isinstance(ir.get("metadata"), dict):
        ir["metadata"].pop(template_kit.KIT, None)
    written: list[str] = []
    if kit.files:  # code the workflow calls (an Agents SDK file's function tools): into the workspace first
        await svc.projects.require(project_id)
        ws = request.app.state.projects.workspace(project_id)
        added = await _off_loop(ws, lambda: template_kit.write_files(ws, kit, wf_id, f"import of {kind}"))
        written = added.files_written
        report = [*report, *(f"Added {p} to the workspace." for p in added.files_written)]
        report += [f"Kept your own {p}; the imported code needs its functions." for p in added.files_kept]
    try:
        view = await svc.create_from_ir(project_id, ir, layout, user.id)
    except Exception:  # a refused import leaves nothing behind in the workspace either
        if written:
            here = request.app.state.projects.workspace(project_id)
            await _off_loop(here, lambda: [here.delete(rel) for rel in written])
        raise
    response.headers["Location"] = f"/v1/workflows/{view.row.id}"
    return Imported(**_out(view, response).model_dump(), format=kind, report=report)


# ------------------------------------------------------------------ refactorings (M2-T13) ----
class SubgraphRequest(BaseModel):
    node_ids: list[str] = Field(min_length=1)
    name: str | None = None


class InlineRequest(BaseModel):
    node_id: str


class RefactorOut(BaseModel):
    """Commands the editor dispatches like its own edits (optimistic, autosaved, undoable)."""

    commands: list[dict[str, Any]]


@router.post("/workflows/{wf_id}/refactor/subgraph", response_model=RefactorOut)
async def refactor_to_subgraph(
    wf_id: str, body: SubgraphRequest, svc: WorkflowService = Depends(service)
) -> RefactorOut:
    from agentcanvas.services.refactor import to_subgraph

    wf = (await svc.get(wf_id)).workflow
    return RefactorOut(commands=[c.model_dump(mode="json") for c in to_subgraph(wf, body.node_ids, body.name)])


@router.post("/workflows/{wf_id}/refactor/inline", response_model=RefactorOut)
async def refactor_inline(wf_id: str, body: InlineRequest, svc: WorkflowService = Depends(service)) -> RefactorOut:
    from agentcanvas.services.refactor import inline_subgraph

    wf = (await svc.get(wf_id)).workflow
    return RefactorOut(commands=[c.model_dump(mode="json") for c in inline_subgraph(wf, body.node_id)])


@router.get("/workflows/{wf_id}/blast-radius", response_model=BlastRadius)
async def get_blast_radius(wf_id: str, svc: WorkflowService = Depends(service)) -> BlastRadius:
    """What each step can do to the outside world and whether a human gates it (FR-X-02)."""
    view = await svc.get(wf_id)
    return blast_radius(view.workflow, svc.registry(view.row.project_id))
