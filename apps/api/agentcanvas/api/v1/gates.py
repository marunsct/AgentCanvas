"""/v1 release gates (FR-EVAL-05): what an environment requires of an evaluation before a publish."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.models import Dataset, Evaluator, ReleaseGate
from agentcanvas.errors import InvalidInput, NotFound
from agentcanvas.ids import new_id

router = APIRouter(tags=["release gates"])
ENVIRONMENT = r"^[a-z][a-z0-9-]{0,30}$"


class GateIn(BaseModel):
    dataset_id: str
    min_scores: dict[str, float] = Field(
        default_factory=dict, description="evaluator name -> minimum mean score (none: each evaluator's threshold)"
    )
    max_regression: float | None = Field(
        default=None, ge=0, le=1, description="largest allowed drop in pass rate against the live version"
    )


class GateOut(GateIn):
    id: str
    workflow_id: str
    environment: str
    updated_at: datetime


def _out(g: ReleaseGate) -> GateOut:
    return GateOut(
        id=g.id, workflow_id=g.workflow_id, environment=g.environment, dataset_id=g.dataset_id,
        min_scores=g.min_scores, max_regression=g.max_regression, updated_at=g.updated_at,
    )  # fmt: skip


async def gate_for(session: AsyncSession, wf_id: str, environment: str) -> ReleaseGate | None:
    q = select(ReleaseGate).where(ReleaseGate.workflow_id == wf_id, ReleaseGate.environment == environment)
    return await session.scalar(q)


@router.get("/workflows/{wf_id}/gates", response_model=list[GateOut])
async def list_gates(wf_id: str, session: AsyncSession = Depends(get_session)) -> list[GateOut]:
    q = select(ReleaseGate).where(ReleaseGate.workflow_id == wf_id).order_by(ReleaseGate.environment)
    return [_out(g) for g in (await session.scalars(q)).all()]


@router.put("/workflows/{wf_id}/gates/{environment}", response_model=GateOut)
async def set_gate(
    wf_id: str,
    environment: str,
    body: GateIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> GateOut:
    """Require an evaluation before publishing to `environment` (admins only)."""
    import re

    if not re.fullmatch(ENVIRONMENT, environment):
        raise InvalidInput(f"'{environment}' is not an environment name")
    ds = await session.get(Dataset, body.dataset_id)
    if ds is None or ds.workflow_id != wf_id:
        raise NotFound(f"dataset '{body.dataset_id}' not found")
    names = set((await session.scalars(select(Evaluator.name).where(Evaluator.workflow_id == wf_id))).all())
    if unknown := sorted(set(body.min_scores) - names):
        raise InvalidInput(f"'{unknown[0]}' is not an evaluator of this workflow")
    if not body.min_scores and not names:
        raise InvalidInput("add an evaluator to the workflow before gating a release on it")
    gate = await gate_for(session, wf_id, environment)
    if gate is None:
        gate = ReleaseGate(id=new_id("gate"), workflow_id=wf_id, environment=environment)
        session.add(gate)
    gate.dataset_id, gate.min_scores, gate.max_regression = body.dataset_id, body.min_scores, body.max_regression
    gate.updated_by, gate.updated_at = user.id, datetime.now(UTC)
    await session.commit()
    await session.refresh(gate)
    return _out(gate)


@router.delete("/workflows/{wf_id}/gates/{environment}", status_code=204)
async def delete_gate(wf_id: str, environment: str, session: AsyncSession = Depends(get_session)) -> Response:
    gate = await gate_for(session, wf_id, environment)
    if gate is None:
        raise NotFound(f"no release gate for '{environment}'")
    await session.delete(gate)
    await session.commit()
    return Response(status_code=204)
