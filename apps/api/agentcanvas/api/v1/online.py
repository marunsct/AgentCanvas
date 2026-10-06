"""/v1 online evaluation (S13 FR-EVAL-06): evaluators on a deployment's traffic, their scores and drift alerts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.models import Deployment, EvalAlert, Evaluator, OnlineEvaluator, Run, RunScore
from agentcanvas.errors import Conflict, InvalidInput, NotFound
from agentcanvas.evaluation import ONLY_IN_EXPERIMENTS
from agentcanvas.ids import new_id
from agentcanvas.services import online as online_svc
from agentcanvas.services.online import buckets

router = APIRouter(tags=["online evaluation"])

RunStatus = Literal["success", "interrupted", "error", "cancelled"]


class FilterIn(BaseModel):
    status: list[RunStatus] | None = Field(default=None, description="only runs that ended so (default: any)")
    metadata: dict[str, str] = Field(default_factory=dict, description="only runs the caller labelled so")


class OnlineIn(BaseModel):
    evaluator_id: str
    sampling: float = Field(default=1.0, ge=0, le=1, description="share of matching runs scored")
    filter: FilterIn = Field(default_factory=FilterIn)
    alert_below: float | None = Field(default=None, ge=0, le=1, description="alert when the window mean is below")
    alert_drop: float | None = Field(
        default=None, gt=0, le=1, description="alert when the window mean is this far below the trailing 7-day mean"
    )
    window_minutes: int = Field(default=60, ge=5, le=1440)
    min_samples: int = Field(default=5, ge=1, le=1000, description="scored runs a window needs before rules apply")


class OnlinePatch(BaseModel):
    sampling: float | None = Field(default=None, ge=0, le=1)
    filter: FilterIn | None = None
    alert_below: float | None = Field(default=None, ge=0, le=1)
    alert_drop: float | None = Field(default=None, gt=0, le=1)
    window_minutes: int | None = Field(default=None, ge=5, le=1440)
    min_samples: int | None = Field(default=None, ge=1, le=1000)


class OnlineOut(OnlineIn):
    id: str
    deployment_id: str
    evaluator: str
    created_at: datetime


class ScoreOut(BaseModel):
    id: str
    run_id: str
    thread_id: str
    evaluator: str
    source: str
    score: float | None
    passed: bool
    comment: str
    judge_tokens: int
    judge_cost_usd: float
    created_by: str | None
    created_at: datetime


class BucketOut(BaseModel):
    start: datetime
    evaluator: str
    runs: int
    scored: int
    mean: float | None
    pass_rate: float
    judge_cost_usd: float


class ScoredRunOut(BaseModel):
    run_id: str
    thread_id: str
    status: str
    started_at: datetime
    scores: list[ScoreOut]


class AlertOut(BaseModel):
    id: str
    deployment_id: str
    evaluator: str
    kind: str
    message: str
    value: float
    baseline: float | None
    created_at: datetime
    resolved_at: datetime | None


def _out(o: OnlineEvaluator, name: str) -> OnlineOut:
    return OnlineOut(
        id=o.id, deployment_id=o.deployment_id, evaluator_id=o.evaluator_id, evaluator=name, sampling=o.sampling,
        filter=FilterIn.model_validate(o.filter or {}), alert_below=o.alert_below, alert_drop=o.alert_drop,
        window_minutes=o.window_minutes, min_samples=o.min_samples, created_at=o.created_at,
    )  # fmt: skip


def score_out(r: RunScore) -> ScoreOut:
    return ScoreOut(
        id=r.id, run_id=r.run_id, thread_id=r.thread_id, evaluator=r.evaluator, source=r.source, score=r.score,
        passed=r.passed, comment=r.comment, judge_tokens=r.judge_tokens, judge_cost_usd=r.judge_cost_usd,
        created_by=r.created_by, created_at=r.created_at,
    )  # fmt: skip


async def _deployment(session: AsyncSession, dep_id: str) -> Deployment:
    dep = await session.get(Deployment, dep_id)
    if dep is None:
        raise NotFound(f"deployment '{dep_id}' not found")
    return dep


async def _online(session: AsyncSession, oev_id: str) -> tuple[OnlineEvaluator, str]:
    row = (
        await session.execute(
            select(OnlineEvaluator, Evaluator.name)
            .join(Evaluator, Evaluator.id == OnlineEvaluator.evaluator_id)
            .where(OnlineEvaluator.id == oev_id)
        )
    ).first()
    if row is None:
        raise NotFound(f"online evaluator '{oev_id}' not found")
    return row[0], row[1]


@router.get("/deployments/{dep_id}/online-evaluators", response_model=list[OnlineOut])
async def list_online(dep_id: str, session: AsyncSession = Depends(get_session)) -> list[OnlineOut]:
    q = (
        select(OnlineEvaluator, Evaluator.name)
        .join(Evaluator, Evaluator.id == OnlineEvaluator.evaluator_id)
        .where(OnlineEvaluator.deployment_id == dep_id)
        .order_by(OnlineEvaluator.created_at, OnlineEvaluator.id)
    )
    return [_out(o, name) for o, name in (await session.execute(q)).all()]


@router.post("/deployments/{dep_id}/online-evaluators", response_model=OnlineOut, status_code=201)
async def add_online(
    dep_id: str, body: OnlineIn, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> OnlineOut:
    """Score a sample of this deployment's finished runs with one of the workflow's evaluators."""
    dep = await _deployment(session, dep_id)
    e = await session.get(Evaluator, body.evaluator_id)
    if e is None or e.workflow_id != dep.workflow_id:  # also keeps other organisations' evaluators out
        raise NotFound(f"evaluator '{body.evaluator_id}' not found")
    if e.kind in ONLY_IN_EXPERIMENTS:
        raise InvalidInput(f"a {e.kind} evaluator compares experiment targets; it cannot score single runs")
    o = OnlineEvaluator(
        id=new_id("oev"), deployment_id=dep.id, evaluator_id=e.id, sampling=body.sampling,
        filter=body.filter.model_dump(), alert_below=body.alert_below, alert_drop=body.alert_drop,
        window_minutes=body.window_minutes, min_samples=body.min_samples, created_by=user.id,
    )  # fmt: skip
    name = e.name
    session.add(o)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise Conflict(f"'{name}' already scores this deployment") from None
    await session.refresh(o)
    return _out(o, name)


@router.patch("/online-evaluators/{oev_id}", response_model=OnlineOut)
async def update_online(oev_id: str, body: OnlinePatch, session: AsyncSession = Depends(get_session)) -> OnlineOut:
    o, name = await _online(session, oev_id)
    for field in body.model_fields_set:
        value: Any = getattr(body, field)
        if field == "filter":
            value = (value or FilterIn()).model_dump()
        elif field in ("sampling", "window_minutes", "min_samples") and value is None:
            continue
        setattr(o, field, value)
    off = tuple(k for k, f in (("below", "alert_below"), ("drop", "alert_drop")) if getattr(o, f) is None)
    if off:  # a rule turned off has nothing left to alert on
        await online_svc.resolve(session, o.deployment_id, name, off)
    await session.commit()
    await session.refresh(o)
    return _out(o, name)


@router.delete("/online-evaluators/{oev_id}", status_code=204)
async def delete_online(oev_id: str, session: AsyncSession = Depends(get_session)) -> Response:
    o, name = await _online(session, oev_id)
    await online_svc.resolve(session, o.deployment_id, name)  # nothing watches them any more
    await session.delete(o)
    await session.commit()
    return Response(status_code=204)


@router.get("/deployments/{dep_id}/scores", response_model=list[BucketOut])
async def score_summary(
    dep_id: str,
    bucket: Literal["hour", "day"] = "hour",
    days: int = Query(default=1, ge=1, le=30),
    session: AsyncSession = Depends(get_session),
) -> list[BucketOut]:
    """Online scores per evaluator, by hour or day, over the last `days` days."""
    await _deployment(session, dep_id)
    since = datetime.now(UTC) - timedelta(days=days)
    q = select(RunScore.created_at, RunScore.evaluator, RunScore.score, RunScore.passed, RunScore.judge_cost_usd).where(
        RunScore.deployment_id == dep_id, RunScore.source == "online", RunScore.created_at >= since
    )
    rows = [tuple(r) for r in (await session.execute(q.limit(100_000))).all()]
    return [BucketOut(**b) for b in buckets(rows, 1 if bucket == "hour" else 24)]  # type: ignore[arg-type]


@router.get("/deployments/{dep_id}/scored-runs", response_model=list[ScoredRunOut])
async def scored_runs(
    dep_id: str, limit: int = Query(default=50, ge=1, le=200), session: AsyncSession = Depends(get_session)
) -> list[ScoredRunOut]:
    """The deployment's latest scored runs, newest first, with their scores."""
    await _deployment(session, dep_id)
    q = (
        select(RunScore)
        .where(RunScore.deployment_id == dep_id)
        .order_by(RunScore.created_at.desc(), RunScore.id.desc())
        .limit(limit * 5)
    )
    by_run: dict[str, list[RunScore]] = {}
    for r in (await session.scalars(q)).all():
        if r.run_id in by_run or len(by_run) < limit:
            by_run.setdefault(r.run_id, []).append(r)
    runs = {r.id: r for r in (await session.scalars(select(Run).where(Run.id.in_(list(by_run))))).all()}
    return [
        ScoredRunOut(
            run_id=rid,
            thread_id=runs[rid].thread_id,
            status=runs[rid].status,
            started_at=runs[rid].started_at,
            scores=[score_out(x) for x in reversed(scores)],
        )
        for rid, scores in by_run.items()
        if rid in runs
    ]


@router.get("/threads/{thread_id}/scores", response_model=list[ScoreOut])
async def thread_scores(thread_id: str, session: AsyncSession = Depends(get_session)) -> list[ScoreOut]:
    """Every score given to the thread's runs (online evaluators, feedback, reviewers), oldest first."""
    q = select(RunScore).where(RunScore.thread_id == thread_id).order_by(RunScore.created_at, RunScore.id)
    return [score_out(r) for r in (await session.scalars(q)).all()]


@router.get("/workflows/{wf_id}/alerts", response_model=list[AlertOut])
async def list_alerts(
    wf_id: str, state: Literal["open", "all"] = "open", session: AsyncSession = Depends(get_session)
) -> list[AlertOut]:
    q = select(EvalAlert).where(EvalAlert.workflow_id == wf_id)
    if state == "open":
        q = q.where(EvalAlert.resolved_at.is_(None))
    rows = (await session.scalars(q.order_by(EvalAlert.created_at.desc()).limit(200))).all()
    return [
        AlertOut(
            id=a.id,
            deployment_id=a.deployment_id,
            evaluator=a.evaluator,
            kind=a.kind,
            message=a.message,
            value=a.value,
            baseline=a.baseline,
            created_at=a.created_at,
            resolved_at=a.resolved_at,
        )
        for a in rows
    ]
