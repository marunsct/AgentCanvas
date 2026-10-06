"""/v1 evaluators (FR-EVAL-03): how a workflow's runs are scored in experiments and at the release gate."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.models import Evaluator, WorkflowRow
from agentcanvas.errors import Conflict, NotFound
from agentcanvas.evaluation import DEFAULT_THRESHOLD, Kind, Observation, Score, Spec, check_config, score
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.services.experiments import scorers

router = APIRouter(tags=["evaluators"])


class EvaluatorIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Kind
    config: dict[str, Any] = Field(default_factory=dict)
    threshold: float | None = Field(default=None, ge=0, le=1, description="passes at score >= threshold")


class EvaluatorPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    config: dict[str, Any] | None = None
    threshold: float | None = Field(default=None, ge=0, le=1)


class EvaluatorOut(EvaluatorIn):
    id: str
    workflow_id: str
    effective_threshold: float
    created_at: datetime


def _out(e: Evaluator) -> EvaluatorOut:
    return EvaluatorOut(
        id=e.id, workflow_id=e.workflow_id, name=e.name, kind=e.kind, config=e.config, threshold=e.threshold,  # type: ignore[arg-type]
        effective_threshold=e.threshold if e.threshold is not None else DEFAULT_THRESHOLD.get(e.kind, 1.0),
        created_at=e.created_at,
    )  # fmt: skip


def spec(e: Evaluator) -> Spec:
    return Spec(name=e.name, kind=e.kind, config=e.config, threshold=e.threshold)  # type: ignore[arg-type]


async def _get(session: AsyncSession, evaluator_id: str) -> Evaluator:
    e = await session.get(Evaluator, evaluator_id)
    if e is None:
        raise NotFound(f"evaluator '{evaluator_id}' not found")
    return e


async def _commit(session: AsyncSession, name: str) -> None:
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise Conflict(f"this workflow already has an evaluator named '{name}'") from None


@router.get("/workflows/{wf_id}/evaluators", response_model=list[EvaluatorOut])
async def list_evaluators(wf_id: str, session: AsyncSession = Depends(get_session)) -> list[EvaluatorOut]:
    q = select(Evaluator).where(Evaluator.workflow_id == wf_id).order_by(Evaluator.created_at, Evaluator.id)
    return [_out(e) for e in (await session.scalars(q)).all()]


@router.post("/workflows/{wf_id}/evaluators", response_model=EvaluatorOut, status_code=201)
async def create_evaluator(
    wf_id: str,
    body: EvaluatorIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> EvaluatorOut:
    e = Evaluator(
        id=new_id("evl"), workflow_id=wf_id, name=body.name, kind=body.kind,
        config=check_config(body.kind, body.config), threshold=body.threshold, created_by=user.id,
    )  # fmt: skip
    session.add(e)
    await _commit(session, body.name)
    return _out(e)


@router.patch("/evaluators/{evaluator_id}", response_model=EvaluatorOut)
async def update_evaluator(
    evaluator_id: str, body: EvaluatorPatch, session: AsyncSession = Depends(get_session)
) -> EvaluatorOut:
    e = await _get(session, evaluator_id)
    if body.name is not None:
        e.name = body.name
    if body.config is not None:
        e.config = check_config(e.kind, body.config)
    if "threshold" in body.model_fields_set:
        e.threshold = body.threshold
    await _commit(session, e.name)
    return _out(e)


@router.delete("/evaluators/{evaluator_id}", status_code=204)
async def delete_evaluator(evaluator_id: str, session: AsyncSession = Depends(get_session)) -> Response:
    await session.delete(await _get(session, evaluator_id))
    await session.commit()
    return Response(status_code=204)


@router.post("/evaluators/{evaluator_id}/try", response_model=Score)
async def try_evaluator(
    evaluator_id: str, body: Observation, request: Request, session: AsyncSession = Depends(get_session)
) -> Score:
    """Score a hand-written observation (input, output, reference, trajectory) without running anything."""
    e = await _get(session, evaluator_id)
    row = await session.get(WorkflowRow, e.workflow_id)
    assert row is not None
    wf = Workflow.model_validate(row.draft_ir)
    await session.close()  # no connection held while a judge model answers
    return await score(spec(e), body, **scorers(request.app.state.runner, wf))
