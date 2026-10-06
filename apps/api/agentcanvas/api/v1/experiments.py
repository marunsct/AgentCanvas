"""/v1 experiments (FR-EVAL-04): run workflow versions and variants over a dataset version, scored by evaluators."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.api.v1.versions import workspace_commit
from agentcanvas.api.v1.workflows import service
from agentcanvas.db.models import Evaluator, Experiment, ExperimentResult
from agentcanvas.errors import InvalidInput, NotFound, WorkflowHasErrors
from agentcanvas.evaluation import Score, spec_of
from agentcanvas.ir.models import Workflow
from agentcanvas.services.datasets import DatasetService
from agentcanvas.services.experiments import (
    MAX_CONCURRENCY,
    ExperimentService,
    TargetIn,
    TargetSummary,
    apply_overrides,
    summary,
)
from agentcanvas.services.projects import PROJECT_OF
from agentcanvas.services.versions import VersionService
from agentcanvas.services.workflows import WorkflowService
from agentcanvas.validation.policies import CODES as POLICY_CODES

router = APIRouter(tags=["experiments"])


class ExperimentIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    dataset_id: str
    dataset_version: int | None = Field(default=None, description="default: the latest version")
    split: str | None = Field(default=None, description="only the examples of this split")
    targets: list[TargetIn] = Field(default_factory=lambda: [TargetIn()], min_length=1, max_length=10)
    evaluator_ids: list[str] | None = Field(default=None, description="default: every evaluator of the workflow")
    concurrency: int = Field(default=4, ge=1, le=MAX_CONCURRENCY)


class TargetOut(BaseModel):
    label: str
    version: str | None
    overrides: dict[str, dict[str, Any]]


class ExperimentOut(BaseModel):
    id: str
    workflow_id: str
    name: str
    dataset_id: str
    dataset_version: int
    split: str | None
    targets: list[TargetOut]
    evaluators: list[str]
    pairwise: list[str] = Field(
        default_factory=list, description="pairwise judges: each target's mean is its win rate against the first target"
    )
    concurrency: int
    status: str
    error: str | None
    created_at: datetime
    ended_at: datetime | None
    summary: list[TargetSummary]


class ExperimentResultOut(BaseModel):
    id: str
    target: int
    example_id: str
    position: int
    status: str
    run_id: str | None
    thread_id: str | None
    run_status: str | None
    error: str | None
    output: dict[str, Any] | None
    trajectory: list[str] | None
    scores: list[Score]
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: int | None


def _out(exp: Experiment, results: list[ExperimentResult]) -> ExperimentOut:
    return ExperimentOut(
        id=exp.id, workflow_id=exp.workflow_id, name=exp.name, dataset_id=exp.dataset_id,
        dataset_version=exp.dataset_version, split=exp.split,
        targets=[TargetOut(label=t["label"], version=t["version"], overrides=t["overrides"]) for t in exp.targets],
        evaluators=[e["name"] for e in exp.evaluators],
        pairwise=[e["name"] for e in exp.evaluators if e["kind"] == "pairwise"], concurrency=exp.concurrency,
        status=exp.status,
        error=exp.error, created_at=exp.created_at, ended_at=exp.ended_at, summary=summary(exp, results),
    )  # fmt: skip


def _result(r: ExperimentResult) -> ExperimentResultOut:
    return ExperimentResultOut(
        id=r.id, target=r.target, example_id=r.example_id, position=r.position, status=r.status, run_id=r.run_id,
        thread_id=r.thread_id, run_status=r.run_status, error=r.error, output=r.output, trajectory=r.trajectory,
        scores=[Score.model_validate(s) for s in r.scores or []], tokens_in=r.tokens_in, tokens_out=r.tokens_out,
        cost_usd=r.cost_usd, latency_ms=r.latency_ms,
    )  # fmt: skip


async def _target(
    session: AsyncSession, svc: WorkflowService, wf_id: str, project_id: str, t: TargetIn, i: int
) -> dict[str, Any]:
    versions = VersionService(session)
    if t.version is None:
        wf, version_id, semver = await versions.workflow_at(wf_id, "draft"), None, None
    else:
        v = await versions.get(wf_id, t.version)
        wf, version_id, semver = Workflow.model_validate(v.ir), v.id, v.semver
    wf = apply_overrides(wf, t.overrides)
    label = t.label or (f"v{semver}" if semver else "draft") + (" (variant)" if t.overrides else "")
    errors = [
        d.model_dump()
        for d in await svc._validate(wf, project_id)
        if d.severity == "error" and d.code not in POLICY_CODES  # policies apply at publish, not to experiments
    ]
    if errors:
        raise WorkflowHasErrors(f"target {i + 1} ({label}) has {len(errors)} error(s)", errors=errors)
    return {
        "label": label, "version": semver, "version_id": version_id, "overrides": t.overrides,
        "ir": wf.model_dump(mode="json"),
    }  # fmt: skip


@router.post("/workflows/{wf_id}/experiments", response_model=ExperimentOut, status_code=202)
async def start_experiment(
    wf_id: str,
    body: ExperimentIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    svc: WorkflowService = Depends(service),
    user: CurrentUser = Depends(current_user),
) -> ExperimentOut:
    """Start an experiment in the background; follow it with GET /v1/experiments/{id}."""
    view = await svc.get(wf_id)
    PROJECT_OF[wf_id] = view.row.project_id
    datasets = DatasetService(session)
    ds = await datasets.get(body.dataset_id)
    if ds.workflow_id != wf_id:  # also keeps other organisations' datasets out
        raise NotFound(f"dataset '{body.dataset_id}' not found")
    version = await datasets.version(ds.id, body.dataset_version)
    q = select(Evaluator).where(Evaluator.workflow_id == wf_id).order_by(Evaluator.created_at, Evaluator.id)
    evaluators = everything = list((await session.scalars(q)).all())
    if body.evaluator_ids is not None:
        known = {e.id: e for e in evaluators}
        if missing := [i for i in body.evaluator_ids if i not in known]:
            raise InvalidInput(f"evaluator '{missing[0]}' is not an evaluator of this workflow")
        evaluators = [known[i] for i in body.evaluator_ids]
    targets = [await _target(session, svc, wf_id, view.row.project_id, t, i) for i, t in enumerate(body.targets)]
    exp = await ExperimentService(session).create(
        wf_id,
        name=body.name or f"{ds.name} v{version.number}",
        dataset_id=ds.id,
        dataset_version=version.number,
        split=body.split,
        examples=version.examples,
        targets=targets,
        evaluators=[spec_of(e, everything) for e in evaluators],
        concurrency=body.concurrency,
        created_by=user.id,
        workspace_commit=await workspace_commit(request, session, wf_id),
    )
    results = await ExperimentService(session).results(exp.id)
    out = _out(exp, results)
    request.app.state.experiments.launch(exp.id)
    return out


@router.get("/workflows/{wf_id}/experiments", response_model=list[ExperimentOut])
async def list_experiments(wf_id: str, session: AsyncSession = Depends(get_session)) -> list[ExperimentOut]:
    svc = ExperimentService(session)
    return [_out(e, await svc.results(e.id)) for e in await svc.list(wf_id)]


@router.get("/experiments/{exp_id}", response_model=ExperimentOut)
async def get_experiment(exp_id: str, session: AsyncSession = Depends(get_session)) -> ExperimentOut:
    svc = ExperimentService(session)
    return _out(await svc.get(exp_id), await svc.results(exp_id))


@router.get("/experiments/{exp_id}/results", response_model=list[ExperimentResultOut])
async def experiment_results(exp_id: str, session: AsyncSession = Depends(get_session)) -> list[ExperimentResultOut]:
    svc = ExperimentService(session)
    await svc.get(exp_id)
    return [_result(r) for r in await svc.results(exp_id)]


@router.post("/experiments/{exp_id}/cancel", response_model=ExperimentOut)
async def cancel_experiment(
    exp_id: str, request: Request, session: AsyncSession = Depends(get_session)
) -> ExperimentOut:
    svc = ExperimentService(session)
    await svc.get(exp_id)
    await request.app.state.experiments.cancel(exp_id)
    session.expire_all()
    return _out(await svc.get(exp_id), await svc.results(exp_id))


@router.delete("/experiments/{exp_id}", status_code=204)
async def delete_experiment(exp_id: str, session: AsyncSession = Depends(get_session)) -> Response:
    await ExperimentService(session).delete(exp_id)
    return Response(status_code=204)
