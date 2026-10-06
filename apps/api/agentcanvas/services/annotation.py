"""Annotation queues (S13): people review the runs that evaluators, failures and end users flag.

A queue belongs to a workflow and has a rubric: criteria scored on a small scale (2 points is a yes/no). Its rules
route production runs into it as they finish: a failed run, a thumbs-down from the deployment API, or an online
evaluator score below a threshold. Anyone with the reviewer role can also add runs by hand. Each reviewer scores
an item once (a later submission replaces theirs); an item is done when `reviewers_per_item` people reviewed it.
Reviewers' scores are also stored as run scores (source "annotation"), so the run's timeline shows them.

**Agreement.** For every criterion, over the items at least two people scored: the mean, over pairs of reviewers,
of 1 - |a - b| / (scale - 1). 1 is perfect agreement; 0 is opposite ends of the scale every time.
"""

from __future__ import annotations

from itertools import combinations
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import Annotation, AnnotationItem, AnnotationQueue, RunScore
from agentcanvas.errors import Conflict, InvalidInput
from agentcanvas.ids import new_id
from agentcanvas.log import get_logger

log = get_logger(__name__)

SOURCE = "annotation"


def normalised(points: float, scale: int) -> float:
    """Points 1..scale as 0..1."""
    return round((points - 1) / (scale - 1), 4)


def reasons(rules: dict[str, Any], *, status: str, thumbs_down: bool, scores: dict[str, float | None]) -> list[str]:
    """Why a production run belongs in a queue with these rules (empty: it does not)."""
    out = []
    if rules.get("failed") and status == "error":
        out.append("failed")
    if rules.get("thumbs_down") and thumbs_down:
        out.append("thumbs_down")
    for evaluator, below in (rules.get("scores_below") or {}).items():
        value = scores.get(evaluator)
        if value is not None and value < below:
            out.append(f"score:{evaluator}")
    return out


async def enqueue(s: AsyncSession, queue_id: str, run_id: str, thread_id: str, reason: str, by: str | None) -> bool:
    """Add a run to a queue; False when it is already there. Commits."""
    if await s.scalar(
        select(AnnotationItem.id).where(AnnotationItem.queue_id == queue_id, AnnotationItem.run_id == run_id)
    ):
        return False
    s.add(
        AnnotationItem(
            id=new_id("aqi"),
            queue_id=queue_id,
            run_id=run_id,
            thread_id=thread_id,
            reason=reason,
            status="pending",
            added_by=by,
        )
    )
    try:
        await s.commit()
    except IntegrityError:  # another instance routed it at the same moment
        await s.rollback()
        return False
    return True


async def route(
    s: AsyncSession,
    workflow_id: str,
    run_id: str,
    thread_id: str,
    *,
    status: str = "success",
    thumbs_down: bool = False,
    scores: dict[str, float | None] | None = None,
) -> list[str]:
    """Put a finished production run into every queue of its workflow whose rules it meets; the queues' ids."""
    queues = (await s.scalars(select(AnnotationQueue).where(AnnotationQueue.workflow_id == workflow_id))).all()
    added = []
    for q in queues:
        why = reasons(q.rules or {}, status=status, thumbs_down=thumbs_down, scores=scores or {})
        if why and await enqueue(s, q.id, run_id, thread_id, why[0], None):
            added.append(q.id)
            log.info("annotation.routed", queue_id=q.id, run_id=run_id, reason=why[0])
    return added


def check_scores(rubric: list[dict[str, Any]], scores: dict[str, float]) -> None:
    criteria = {c["name"]: c for c in rubric}
    if unknown := sorted(set(scores) - set(criteria)):
        raise InvalidInput(f"not in the rubric: {', '.join(unknown)}")
    if missing := sorted(set(criteria) - set(scores)):
        raise InvalidInput(f"score every criterion; missing: {', '.join(missing)}")
    for name, points in scores.items():
        scale = criteria[name]["scale"]
        if not 1 <= points <= scale or points != int(points):
            raise InvalidInput(f"{name}: a whole number of points from 1 to {scale}")


async def submit(
    s: AsyncSession,
    queue: AnnotationQueue,
    item: AnnotationItem,
    workflow_deployment: str | None,
    reviewer: str,
    scores: dict[str, float],
    comment: str,
) -> Annotation:
    """Record (or replace) a reviewer's annotation, mirror it as run scores and mark the item done when enough
    people reviewed it. Commits."""
    check_scores(queue.rubric, scores)
    # reviews of one item are counted one at a time, so two at once cannot both see themselves alone
    await s.execute(select(AnnotationItem.id).where(AnnotationItem.id == item.id).with_for_update())
    a = await s.scalar(select(Annotation).where(Annotation.item_id == item.id, Annotation.reviewer == reviewer))
    if a is None:
        a = Annotation(id=new_id("ann"), item_id=item.id, reviewer=reviewer, scores=scores, comment=comment)
        s.add(a)
    else:
        a.scores, a.comment = scores, comment
    await s.execute(
        delete(RunScore).where(
            RunScore.run_id == item.run_id,
            RunScore.source == SOURCE,
            RunScore.created_by == reviewer,
            RunScore.evaluator.in_([c["name"] for c in queue.rubric]),
        )
    )
    for c in queue.rubric:
        value = normalised(scores[c["name"]], c["scale"])
        s.add(
            RunScore(
                id=new_id("rsc"),
                run_id=item.run_id,
                thread_id=item.thread_id,
                workflow_id=queue.workflow_id,
                deployment_id=workflow_deployment,
                evaluator=c["name"],
                source=SOURCE,
                score=value,
                passed=value >= c.get("pass_at", 0.5),
                comment=comment,
                created_by=reviewer,
            )
        )
    try:
        await s.flush()
    except IntegrityError:  # the same reviewer, twice at once
        await s.rollback()
        raise Conflict("this review is being saved already; reload the item") from None
    reviewed = await s.scalar(select(func.count()).select_from(Annotation).where(Annotation.item_id == item.id))
    item.status = "done" if (reviewed or 0) >= queue.reviewers_per_item else "pending"
    await s.commit()
    return a


def agreement(rubric: list[dict[str, Any]], by_item: dict[str, list[dict[str, Any]]]) -> dict[str, float | None]:
    """Per criterion: mean pairwise agreement over items at least two reviewers scored (None: no such item)."""
    out: dict[str, float | None] = {}
    for c in rubric:
        span = c["scale"] - 1
        pairs = [
            1 - abs(a[c["name"]] - b[c["name"]]) / span
            for scores in by_item.values()
            for a, b in combinations([x for x in scores if c["name"] in x], 2)
        ]
        out[c["name"]] = round(sum(pairs) / len(pairs), 4) if pairs else None
    return out
