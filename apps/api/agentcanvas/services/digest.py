"""Weekly run digests (F-11): what a project's runs did in a UTC week (Monday 00:00 to Monday 00:00).

A digest counts the week's runs (people's runs only: test and experiment runs are left out, as in run search),
how many failed and why, grouped the way experiments group failures (the error with numbers and quoted values taken
out, so alike errors group), what they spent, how long they took (median and 95th percentile), the busiest
workflows and the runs most often flagged (failing evaluator scores, thumbs-down feedback, review queue items). It
is computed from the database alone: no model is asked.

Every API server runs the job. Each tick it looks for projects that had runs in the week that just ended and have
no digest for it, computes one and inserts it; the unique key (project, week) is the claim, so whatever the number
of servers each digest is made once, and a server that loses the race drops its copy. Digests older than a year are
deleted. A digest holds no inputs or answers, only counts, workflow names and errors with their values taken out.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, delete, extract, func, select
from sqlalchemy.exc import IntegrityError

from agentcanvas import observability
from agentcanvas.db.models import AnnotationItem, AnnotationQueue, Run, RunDigest, RunScore, Thread, WorkflowRow
from agentcanvas.ids import new_id
from agentcanvas.log import get_logger
from agentcanvas.services.experiments import failure_reason
from agentcanvas.services.run_search import PEOPLES
from agentcanvas.services.secrets import hide_secrets

log = get_logger(__name__)

WEEK = timedelta(days=7)
KEEP = timedelta(days=366)  # digests older than this are deleted
BATCH = 20  # projects one tick makes digests for; the rest wait for the next tick
FAILURES_READ = 5000  # newest failed runs of a week grouped by their error
GROUPS = 5
FLAGGED = 5
WORKFLOWS = 5
DURATIONS_READ = 50_000  # finished runs whose duration a SQLite database reads (Postgres computes it in SQL)


def week_of(t: datetime) -> datetime:
    """The Monday 00:00 UTC that starts the week holding `t`."""
    t = t.astimezone(UTC)
    return datetime(t.year, t.month, t.day, tzinfo=UTC) - timedelta(days=t.weekday())


def _percentile(values: list[float], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(q * len(ordered)) - 1)])


async def compute(s: Any, project_id: str, start: datetime, end: datetime, secrets: dict[str, str]) -> dict[str, Any]:
    """The digest of a project's runs that started in [start, end)."""
    week = (
        WorkflowRow.project_id == project_id,
        Run.started_at >= start,
        Run.started_at < end,
        PEOPLES,
    )

    def runs_q(*columns: Any) -> Any:
        q = select(*columns).select_from(Run).join(Thread, Thread.id == Run.thread_id)
        return q.join(WorkflowRow, WorkflowRow.id == Thread.workflow_id).where(*week)

    failed = Run.status == "error"
    failures = func.coalesce(func.sum(case((failed, 1), else_=0)), 0)
    total, n_failed, cost, tokens = (
        await s.execute(
            runs_q(
                func.count(),
                failures,
                func.coalesce(func.sum(Run.cost_usd), 0),
                func.coalesce(func.sum(Run.tokens_in + Run.tokens_out), 0),
            )
        )
    ).one()
    total, n_failed = int(total or 0), int(n_failed or 0)

    # how long they took: in SQL on Postgres; SQLite (one server, small) reads the durations
    finished = (Run.ended_at.is_not(None), Run.status != "running")
    if s.bind.dialect.name == "postgresql":
        ms = extract("epoch", Run.ended_at - Run.started_at) * 1000
        p50, p95 = (
            await s.execute(
                runs_q(
                    func.percentile_disc(0.5).within_group(ms),
                    func.percentile_disc(0.95).within_group(ms),
                ).where(*finished)
            )
        ).one()
        p50 = round(float(p50)) if p50 is not None else None
        p95 = round(float(p95)) if p95 is not None else None
    else:
        ms = (func.julianday(Run.ended_at) - func.julianday(Run.started_at)) * 86_400_000
        took = [float(v) for v in (await s.scalars(runs_q(ms).where(*finished).limit(DURATIONS_READ))).all()]
        p50, p95 = _percentile(took, 0.5), _percentile(took, 0.95)

    # why runs failed, grouped like an experiment's failures
    groups: dict[str, dict[str, Any]] = {}
    rows = await s.execute(
        runs_q(Run.id, Run.thread_id, Run.error, WorkflowRow.id, WorkflowRow.name)
        .where(failed)
        .order_by(Run.started_at.desc(), Run.id.desc())
        .limit(FAILURES_READ)
    )
    for run_id, thread_id, error, wf_id, wf_name in rows.all():
        reason = failure_reason(hide_secrets(error or "failed", secrets))
        g = groups.setdefault(
            reason,
            {"reason": reason, "runs": 0, "workflows": [], "example": {"run_id": run_id, "thread_id": thread_id,
                                                                     "workflow_id": wf_id}},
        )  # fmt: skip
        g["runs"] += 1
        if wf_name not in g["workflows"] and len(g["workflows"]) < 5:
            g["workflows"].append(wf_name)
    failure_groups = sorted(groups.values(), key=lambda g: (-g["runs"], g["reason"]))[:GROUPS]

    # the busiest workflows
    by_wf = await s.execute(
        runs_q(
            WorkflowRow.id, WorkflowRow.name, func.count(), failures, func.coalesce(func.sum(Run.cost_usd), 0),
        )
        .group_by(WorkflowRow.id, WorkflowRow.name)
        .order_by(func.count().desc(), WorkflowRow.id)
        .limit(WORKFLOWS)
    )  # fmt: skip
    workflows = [
        {"id": i, "name": name, "runs": int(n), "failed": int(f or 0), "cost_usd": round(float(c or 0), 6)}
        for i, name, n, f, c in by_wf.all()
    ]

    # the runs flagged most: failing scores (evaluators, thumbs-down) and review queue items
    flags: dict[str, dict[str, Any]] = {}
    scored = await s.execute(
        runs_q(Run.id, Run.thread_id, WorkflowRow.id, WorkflowRow.name, RunScore.evaluator)
        .join(RunScore, RunScore.run_id == Run.id)
        .where(RunScore.passed.is_(False))
    )
    queued = await s.execute(
        runs_q(Run.id, Run.thread_id, WorkflowRow.id, WorkflowRow.name, AnnotationQueue.name)
        .join(AnnotationItem, AnnotationItem.run_id == Run.id)
        .join(AnnotationQueue, AnnotationQueue.id == AnnotationItem.queue_id)
    )
    reasons = [tuple(r) for r in scored.all()] + [(*r[:4], f"review: {r[4]}") for r in queued.all()]
    for run_id, thread_id, wf_id, wf_name, why in reasons:
        f = flags.setdefault(run_id, {"run_id": run_id, "thread_id": thread_id, "workflow_id": wf_id,
                                      "workflow_name": wf_name, "flags": 0, "why": []})  # fmt: skip
        f["flags"] += 1
        if why not in f["why"] and len(f["why"]) < 5:
            f["why"].append(why)
    flagged = sorted(flags.values(), key=lambda f: (-f["flags"], f["run_id"]))[:FLAGGED]

    return {
        "runs": total, "failed": n_failed, "failure_rate": round(n_failed / total, 4) if total else None,
        "cost_usd": round(float(cost or 0), 6), "tokens": int(tokens or 0),
        "latency_p50_ms": p50, "latency_p95_ms": p95,
        "failure_groups": failure_groups, "workflows": workflows, "flagged": flagged,
    }  # fmt: skip


class DigestJob:
    def __init__(
        self,
        db: Any,
        interval_s: float,
        secrets: Callable[[str], dict[str, str]] = lambda _: {},
    ):
        self.db, self.interval_s, self.secrets = db, interval_s, secrets
        self.made_hooks: list[Callable[[Any, RunDigest], Any]] = []  # called in the claiming transaction
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self.interval_s <= 0:
            return
        observability.job_started("run_digest")
        self._task = asyncio.create_task(self._loop(), name="run-digest")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                with observability.job_round("run_digest"):
                    await self.tick(datetime.now(UTC))
            except Exception:
                log.exception("run_digest.tick_failed")
            await asyncio.sleep(self.interval_s)

    async def tick(self, now: datetime) -> int:
        """Make the digests of the week that just ended for the projects that ran something in it and have none yet;
        returns how many this server made."""
        end = week_of(now)
        start = end - WEEK
        async with self.db.sessionmaker() as s:
            done = select(RunDigest.project_id).where(RunDigest.week_start == start)
            q = (
                select(WorkflowRow.project_id)
                .join(Thread, Thread.workflow_id == WorkflowRow.id)
                .join(Run, Run.thread_id == Thread.id)
                .where(Run.started_at >= start, Run.started_at < end, PEOPLES, WorkflowRow.project_id.not_in(done))
                .group_by(WorkflowRow.project_id)
                .order_by(WorkflowRow.project_id)
                .limit(BATCH)
            )
            projects = list((await s.scalars(q)).all())
            await s.execute(delete(RunDigest).where(RunDigest.week_start < start - KEEP))
            await s.commit()
        made = 0
        for project_id in projects:
            try:
                made += await self.make(project_id, start)
            except Exception:  # one project's failure must not stop the others
                log.exception("run_digest.failed", project_id=project_id, week=start.date().isoformat())
        return made

    async def make(self, project_id: str, start: datetime) -> bool:
        """Compute and insert one digest; False when another server inserted it first."""
        async with self.db.sessionmaker() as s:
            summary = await compute(s, project_id, start, start + WEEK, self.secrets(project_id))
        async with self.db.sessionmaker() as s:
            row = RunDigest(id=new_id("rdg"), project_id=project_id, week_start=start, summary=summary)
            s.add(row)
            try:
                await s.flush()
                for hook in self.made_hooks:
                    await hook(s, row)
                await s.commit()
            except IntegrityError:
                await s.rollback()
                return False  # another server made it
        log.info("run_digest.made", project_id=project_id, week=start.date().isoformat(), runs=summary["runs"])
        return True
