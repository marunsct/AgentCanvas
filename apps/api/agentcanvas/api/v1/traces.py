"""Trace export settings (S15): where a project's runs are sent as OpenTelemetry traces."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import get_session
from agentcanvas.db.repositories import ProjectRepo
from agentcanvas.errors import InvalidInput
from agentcanvas.services import trace_export
from agentcanvas.services.trace_export import TraceExport

router = APIRouter(tags=["trace export"])


class TraceExportOut(BaseModel):
    config: TraceExport | None
    guides: dict[str, str]
    defaults: dict[str, str]
    missing_secrets: list[str]


def _missing(cfg: TraceExport | None, request: Request, project_id: str) -> list[str]:
    known = set(request.app.state.projects.secrets.get(project_id, {}))
    return sorted(cfg.secrets() - known) if cfg else []


@router.get("/projects/{project_id}/trace-export", response_model=TraceExportOut)
async def get_trace_export(
    project_id: str, request: Request, session: AsyncSession = Depends(get_session)
) -> TraceExportOut:
    project = await ProjectRepo(session).require(project_id)
    cfg = trace_export.load(project.settings)
    return TraceExportOut(
        config=cfg, guides=trace_export.GUIDE, defaults=trace_export.DEFAULTS,
        missing_secrets=_missing(cfg, request, project_id),
    )  # fmt: skip


@router.put("/projects/{project_id}/trace-export", response_model=TraceExportOut)
async def put_trace_export(
    project_id: str, body: TraceExport, request: Request, session: AsyncSession = Depends(get_session)
) -> TraceExportOut:
    """Send this project's runs to Langfuse, Phoenix, Datadog or an OTLP endpoint (admins: traces leave the
    server, with inputs and outputs unless `include_content` is off)."""
    if body.kind == "langfuse" and not (body.public_key and body.secret_key):
        raise InvalidInput("Langfuse needs its public and secret keys, each stored as a project secret")
    if body.kind == "otlp" and not body.endpoint:
        raise InvalidInput("enter the OTLP traces URL, e.g. https://collector.example.com/v1/traces")
    project = await ProjectRepo(session).require(project_id, lock=True)
    project.settings = {**(project.settings or {}), "trace_export": body.model_dump(exclude_none=True)}
    await session.commit()
    request.app.state.trace_export.forget(project_id)
    return await get_trace_export(project_id, request, session)


@router.delete("/projects/{project_id}/trace-export", response_model=TraceExportOut)
async def delete_trace_export(
    project_id: str, request: Request, session: AsyncSession = Depends(get_session)
) -> TraceExportOut:
    project = await ProjectRepo(session).require(project_id, lock=True)
    project.settings = {k: v for k, v in (project.settings or {}).items() if k != "trace_export"}
    await session.commit()
    request.app.state.trace_export.forget(project_id)
    return await get_trace_export(project_id, request, session)


class TestOut(BaseModel):
    ok: bool
    detail: str


@router.post("/projects/{project_id}/trace-export/test", response_model=TestOut)
async def test_trace_export(project_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> TestOut:
    """Send one test span to the saved destination now."""
    cfg = trace_export.load((await ProjectRepo(session).require(project_id)).settings)
    if cfg is None:
        raise InvalidInput("save a trace destination first")
    try:
        url, headers = cfg.target(request.app.state.projects.secrets.get(project_id, {}))
    except KeyError as exc:
        return TestOut(ok=False, detail=f"missing {exc.args[0]}: add it under Secrets")
    problem = await asyncio.to_thread(trace_export.test_send, url, headers)
    return TestOut(ok=problem is None, detail=problem or f"{url} accepted a test trace")
