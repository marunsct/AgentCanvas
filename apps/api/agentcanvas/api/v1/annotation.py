"""/v1 annotation queues (S13): reviewers score flagged runs per rubric criterion and turn them into examples."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.api.v1.online import ScoreOut, score_out
from agentcanvas.db.models import (
    Annotation,
    AnnotationItem,
    AnnotationQueue,
    Dataset,
    Run,
    RunScore,
    Thread,
    WorkflowRow,
)
from agentcanvas.errors import Conflict, InvalidInput, NotFound
from agentcanvas.ids import new_id
from agentcanvas.services import annotation, gates
from agentcanvas.services.datasets import DatasetService, Example

router = APIRouter(tags=["annotation queues"])


class Criterion(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = ""
    scale: int = Field(default=5, ge=2, le=10, description="points; 2 is a yes/no")
    pass_at: float = Field(default=0.5, ge=0, le=1, description="normalised score (0-1) that passes")


class Rules(BaseModel):
    failed: bool = Field(default=False, description="production runs that failed")
    thumbs_down: bool = Field(default=False, description="runs an end user rated down through the deployment API")
    scores_below: dict[str, float] = Field(
        default_factory=dict, description="online evaluator name -> runs scoring below this"
    )


class QueueIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    instructions: str = ""
    rubric: list[Criterion] = Field(min_length=1, max_length=20)
    rules: Rules = Field(default_factory=Rules)
    reviewers_per_item: int = Field(default=1, ge=1, le=10)

    @field_validator("rubric")
    @classmethod
    def _unique(cls, v: list[Criterion]) -> list[Criterion]:
        if len({c.name for c in v}) != len(v):
            raise ValueError("criterion names must be unique")
        return v


class QueuePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    instructions: str | None = None
    rules: Rules | None = None
    reviewers_per_item: int | None = Field(default=None, ge=1, le=10)


class Progress(BaseModel):
    total: int
    pending: int
    done: int


class QueueOut(QueueIn):
    id: str
    workflow_id: str
    created_at: datetime
    progress: Progress
    agreement: dict[str, float | None] = Field(
        default_factory=dict, description="per criterion, 0-1, over items two or more people reviewed"
    )


class AnnotationOut(BaseModel):
    reviewer: str
    scores: dict[str, float]
    comment: str
    created_at: datetime


class ItemOut(BaseModel):
    id: str
    queue_id: str
    run_id: str
    thread_id: str
    reason: str
    status: str
    reviewed_by_me: bool
    reviews: int
    created_at: datetime


class ItemDetail(ItemOut):
    run_status: str
    error: str | None
    input: dict[str, Any] | None
    output: dict[str, Any] | None
    annotations: list[AnnotationOut]
    scores: list[ScoreOut] = Field(description="every score the run has: online evaluators, feedback, reviewers")


class ItemsIn(BaseModel):
    run_ids: list[str] = Field(min_length=1, max_length=100)


class AddToDataset(BaseModel):
    dataset_id: str
    reference: dict[str, Any] = Field(description="the corrected output: channel -> expected value")


class AnnotateIn(BaseModel):
    scores: dict[str, float] = Field(description="criterion -> points (1..scale)")
    comment: str = Field(default="", max_length=4000)
    add_to_dataset: AddToDataset | None = None


async def _queue(s: AsyncSession, aq_id: str) -> AnnotationQueue:
    q = await s.get(AnnotationQueue, aq_id)
    if q is None:
        raise NotFound(f"annotation queue '{aq_id}' not found")
    return q


async def _item(s: AsyncSession, item_id: str) -> AnnotationItem:
    i = await s.get(AnnotationItem, item_id)
    if i is None:
        raise NotFound(f"annotation item '{item_id}' not found")
    return i


async def _progress(s: AsyncSession, queue_ids: list[str]) -> dict[str, Progress]:
    q = (
        select(AnnotationItem.queue_id, AnnotationItem.status, func.count())
        .where(AnnotationItem.queue_id.in_(queue_ids))
        .group_by(AnnotationItem.queue_id, AnnotationItem.status)
    )
    counts: dict[str, dict[str, int]] = {}
    for qid, status, n in (await s.execute(q)).all():
        counts.setdefault(qid, {})[status] = n
    return {
        qid: Progress(
            total=sum(counts.get(qid, {}).values()),
            pending=counts.get(qid, {}).get("pending", 0),
            done=counts.get(qid, {}).get("done", 0),
        )
        for qid in queue_ids
    }


async def _out(s: AsyncSession, q: AnnotationQueue, progress: Progress, with_agreement: bool = False) -> QueueOut:
    agreement: dict[str, float | None] = {}
    if with_agreement:
        rows = (
            await s.execute(
                select(Annotation.item_id, Annotation.scores)
                .join(AnnotationItem, AnnotationItem.id == Annotation.item_id)
                .where(AnnotationItem.queue_id == q.id)
            )
        ).all()
        by_item: dict[str, list[dict[str, Any]]] = {}
        for item_id, scores in rows:
            by_item.setdefault(item_id, []).append(scores)
        agreement = annotation.agreement(q.rubric, by_item)
    return QueueOut(
        id=q.id, workflow_id=q.workflow_id, name=q.name, instructions=q.instructions,
        rubric=[Criterion.model_validate(c) for c in q.rubric], rules=Rules.model_validate(q.rules or {}),
        reviewers_per_item=q.reviewers_per_item, created_at=q.created_at, progress=progress, agreement=agreement,
    )  # fmt: skip


@router.get("/workflows/{wf_id}/annotation-queues", response_model=list[QueueOut])
async def list_queues(wf_id: str, s: AsyncSession = Depends(get_session)) -> list[QueueOut]:
    queues = (
        await s.scalars(
            select(AnnotationQueue).where(AnnotationQueue.workflow_id == wf_id).order_by(AnnotationQueue.name)
        )
    ).all()
    progress = await _progress(s, [q.id for q in queues])
    return [await _out(s, q, progress[q.id]) for q in queues]


@router.post("/workflows/{wf_id}/annotation-queues", response_model=QueueOut, status_code=201)
async def create_queue(
    wf_id: str, body: QueueIn, s: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> QueueOut:
    if await s.get(WorkflowRow, wf_id) is None:
        raise NotFound(f"workflow '{wf_id}' not found")
    q = AnnotationQueue(
        id=new_id("aq"), workflow_id=wf_id, name=body.name, instructions=body.instructions,
        rubric=[c.model_dump() for c in body.rubric], rules=body.rules.model_dump(),
        reviewers_per_item=body.reviewers_per_item, created_by=user.id,
    )  # fmt: skip
    s.add(q)
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
        raise Conflict(f"a queue named '{body.name}' already exists") from None
    await s.refresh(q)
    return await _out(s, q, Progress(total=0, pending=0, done=0))


@router.get("/annotation-queues/{aq_id}", response_model=QueueOut)
async def get_queue(aq_id: str, s: AsyncSession = Depends(get_session)) -> QueueOut:
    q = await _queue(s, aq_id)
    return await _out(s, q, (await _progress(s, [q.id]))[q.id], with_agreement=True)


@router.patch("/annotation-queues/{aq_id}", response_model=QueueOut)
async def update_queue(aq_id: str, body: QueuePatch, s: AsyncSession = Depends(get_session)) -> QueueOut:
    """The rubric stays as it was created: reviews already given are scored against it."""
    q = await _queue(s, aq_id)
    for field in body.model_fields_set:
        value = getattr(body, field)
        if value is None:
            continue
        setattr(q, field, value.model_dump() if isinstance(value, Rules) else value)
    try:
        await s.commit()
    except IntegrityError:
        await s.rollback()
        raise Conflict(f"a queue named '{body.name}' already exists") from None
    await s.refresh(q)
    return await _out(s, q, (await _progress(s, [q.id]))[q.id], with_agreement=True)


@router.delete("/annotation-queues/{aq_id}", status_code=204)
async def delete_queue(aq_id: str, s: AsyncSession = Depends(get_session)) -> Response:
    await s.delete(await _queue(s, aq_id))
    await s.commit()
    return Response(status_code=204)


def _item_out(i: AnnotationItem, reviewers: list[str], me: str) -> ItemOut:
    return ItemOut(
        id=i.id, queue_id=i.queue_id, run_id=i.run_id, thread_id=i.thread_id, reason=i.reason, status=i.status,
        reviewed_by_me=me in reviewers, reviews=len(reviewers), created_at=i.created_at,
    )  # fmt: skip


@router.get("/annotation-queues/{aq_id}/items", response_model=list[ItemOut])
async def list_items(
    aq_id: str,
    status: Literal["pending", "done", "all"] = "pending",
    limit: int = Query(default=100, ge=1, le=500),
    s: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> list[ItemOut]:
    """Oldest first, so reviewers work through the queue in order."""
    await _queue(s, aq_id)
    q = select(AnnotationItem).where(AnnotationItem.queue_id == aq_id)
    if status != "all":
        q = q.where(AnnotationItem.status == status)
    items = (await s.scalars(q.order_by(AnnotationItem.created_at, AnnotationItem.id).limit(limit))).all()
    return await _items_out(s, items, user.id)


async def _items_out(s: AsyncSession, items: Sequence[AnnotationItem], me: str) -> list[ItemOut]:
    """Items with who reviewed them."""
    reviewers: dict[str, list[str]] = {}
    rows = await s.execute(
        select(Annotation.item_id, Annotation.reviewer).where(Annotation.item_id.in_([i.id for i in items]))
    )
    for item_id, reviewer in rows.all():
        reviewers.setdefault(item_id, []).append(reviewer)
    return [_item_out(i, reviewers.get(i.id, []), me) for i in items]


@router.post("/annotation-queues/{aq_id}/items", response_model=list[ItemOut])
async def add_items(
    aq_id: str, body: ItemsIn, s: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> list[ItemOut]:
    """Add runs of the queue's workflow by hand (runs already in the queue are skipped)."""
    queue = await _queue(s, aq_id)
    q = select(Run.id, Run.thread_id).join(Thread, Thread.id == Run.thread_id)
    runs = dict((await s.execute(q.where(Run.id.in_(body.run_ids), Thread.workflow_id == queue.workflow_id))).all())
    if missing := [r for r in body.run_ids if r not in runs]:
        raise NotFound(f"run '{missing[0]}' not found in this workflow")
    for run_id in dict.fromkeys(body.run_ids):
        await annotation.enqueue(s, aq_id, run_id, runs[run_id], "manual", user.id)
    items = (
        await s.scalars(
            select(AnnotationItem).where(AnnotationItem.queue_id == aq_id, AnnotationItem.run_id.in_(body.run_ids))
        )
    ).all()
    return await _items_out(s, items, user.id)  # a run already in the queue may have been reviewed


async def _detail(s: AsyncSession, item: AnnotationItem, me: str) -> ItemDetail:
    run = await s.get(Run, item.run_id)
    notes = (
        await s.scalars(select(Annotation).where(Annotation.item_id == item.id).order_by(Annotation.created_at))
    ).all()
    scores = (
        await s.scalars(select(RunScore).where(RunScore.run_id == item.run_id).order_by(RunScore.created_at))
    ).all()
    recording = (run.recording if run else None) or {}
    base = _item_out(item, [a.reviewer for a in notes], me)
    return ItemDetail(
        **base.model_dump(), run_status=run.status if run else "deleted", error=run.error if run else None,
        input=recording.get("input"), output=recording.get("output"),
        annotations=[
            AnnotationOut(reviewer=a.reviewer, scores=a.scores, comment=a.comment, created_at=a.created_at)
            for a in notes
        ],
        scores=[score_out(x) for x in scores],
    )  # fmt: skip


@router.get("/annotation-items/{item_id}", response_model=ItemDetail)
async def get_item(
    item_id: str, s: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> ItemDetail:
    """The run's input, output and scores, and the reviews so far."""
    return await _detail(s, await _item(s, item_id), user.id)


@router.post("/annotation-items/{item_id}/annotations", response_model=ItemDetail)
async def annotate(
    item_id: str, body: AnnotateIn, s: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> ItemDetail:
    """Score the run on every rubric criterion, with a comment; optionally add it, with a corrected reference, to
    one of the workflow's datasets (a new dataset version) in the same step."""
    item = await _item(s, item_id)
    queue = await _queue(s, item.queue_id)
    annotation.check_scores(queue.rubric, body.scores)
    example: tuple[str, Example, str] | None = None
    if body.add_to_dataset is not None:
        ds = await s.get(Dataset, body.add_to_dataset.dataset_id)
        if ds is None or ds.workflow_id != queue.workflow_id:
            raise NotFound(f"dataset '{body.add_to_dataset.dataset_id}' not found")
        await gates.guard(s, user.role, dataset_id=ds.id)
        run = await s.get(Run, item.run_id)
        if run is None or not run.recording or "input" not in run.recording:
            raise InvalidInput("this run has no recorded input to turn into an example")
        meta = {"run_id": item.run_id, "annotation_queue": queue.name, "reviewer": user.id}
        corrected = Example(input=run.recording["input"], reference=body.add_to_dataset.reference, metadata=meta)
        await DatasetService(s).check_new(ds.id, [corrected])  # refused before the review is saved, not after
        example = (ds.id, corrected, f"corrected run {item.run_id} ({queue.name})")
    thread = await s.get(Thread, item.thread_id)
    await annotation.submit(
        s, queue, item, thread.deployment_id if thread else None, user.id, body.scores, body.comment
    )
    if example is not None:  # after the review is saved: a refused review never leaves an example behind
        await DatasetService(s).add(example[0], [example[1]], example[2], user.id)
    return await _detail(s, item, user.id)


@router.delete("/annotation-items/{item_id}", status_code=204)
async def remove_item(item_id: str, s: AsyncSession = Depends(get_session)) -> Response:
    await s.delete(await _item(s, item_id))
    await s.commit()
    return Response(status_code=204)
