"""Whole workflows on other frameworks through Open Agent Spec (S22, preview): what carries over, and the export."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas import bridges
from agentcanvas.api.deps import get_session
from agentcanvas.api.v1.versions import _export_source
from agentcanvas.api.v1.workflows import service
from agentcanvas.errors import NotFound
from agentcanvas.services.workflows import WorkflowService

router = APIRouter(tags=["workflows"])


class GapOut(BaseModel):
    blocks: bool  # the flow cannot run there until this is dealt with
    text: str


class BridgeOut(BaseModel):
    id: str
    label: str
    summary: str
    packages: list[str]
    preview: bool = True
    runs: bool  # nothing blocks it
    gaps: list[GapOut]


class NotOfferedOut(BaseModel):
    label: str
    why: str


class BridgesOut(BaseModel):
    targets: list[BridgeOut]
    not_offered: list[NotOfferedOut]


@router.get("/workflows/{wf_id}/bridges", response_model=BridgesOut)
async def list_bridges(
    wf_id: str,
    version: str | None = None,
    session: AsyncSession = Depends(get_session),
    svc: WorkflowService = Depends(service),
) -> BridgesOut:
    """The other frameworks a whole workflow can be exported to through Agent Spec, and what does not carry over."""
    _, wf, _ = await _export_source(session, svc, wf_id, version)
    out = []
    for t in bridges.TARGETS.values():
        found = bridges.gaps(wf, t.id)
        out.append(BridgeOut(
            id=t.id, label=t.label, summary=t.summary, packages=list(t.packages),
            runs=not any(g.blocks for g in found), gaps=[GapOut(blocks=g.blocks, text=g.text) for g in found],
        ))  # fmt: skip
    return BridgesOut(targets=out, not_offered=[NotOfferedOut(label=k, why=v) for k, v in bridges.NOT_OFFERED.items()])


@router.get("/workflows/{wf_id}/bridges/{target}/export.zip", response_class=Response)
async def bridge_export(
    wf_id: str,
    target: str,
    version: str | None = None,
    session: AsyncSession = Depends(get_session),
    svc: WorkflowService = Depends(service),
) -> Response:
    """The workflow as a project for `target` (preview): flow.json, a runner, steps.py, README, GAPS.md and tests."""
    if target not in bridges.TARGETS:
        raise NotFound(f"no export to '{target}' (one of {', '.join(bridges.TARGETS)})")
    _, wf, _ = await _export_source(session, svc, wf_id, version)
    name, data = bridges.export_zip(wf, target)
    return Response(
        data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )
