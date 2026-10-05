"""Workflow endpoints: CRUD with optimistic locking, commands, layout, validation, code (M1-T3…T5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.adapters import get_adapter
from agentcanvas.api.deps import CurrentUser, current_user, get_session, get_settings_dep, if_match
from agentcanvas.ir.commands import Command
from agentcanvas.services.workflows import WorkflowService, WorkflowView
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
    response: Response,
    svc: WorkflowService = Depends(service),
    user: CurrentUser = Depends(current_user),
) -> WorkflowOut:
    view = await svc.create(project_id, body.name, body.template, user.id)
    response.headers["Location"] = f"/v1/workflows/{view.row.id}"
    return _out(view, response)


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


@router.delete("/workflows/{wf_id}", status_code=204)
async def delete_workflow(wf_id: str, svc: WorkflowService = Depends(service)) -> Response:
    await svc.delete(wf_id)
    return Response(status_code=204)


@router.post("/workflows/{wf_id}/validate", response_model=list[Diagnostic])
async def validate_workflow(wf_id: str, svc: WorkflowService = Depends(service)) -> list[Diagnostic]:
    return (await svc.get(wf_id)).diagnostics


@router.get("/workflows/{wf_id}/code", response_class=PlainTextResponse)
async def get_code(wf_id: str, svc: WorkflowService = Depends(service)) -> str:
    view = await svc.get(wf_id)
    return get_adapter(view.workflow.framework).generate(view.workflow, svc.registry(view.row.project_id))


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
