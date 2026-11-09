"""Online evaluators (S13 FR-EVAL-06): score a sample of a deployment's finished runs, watch for drift.

An online evaluator attaches one of the workflow's evaluators to a deployment with a sampling rate and a filter
(run status, the labels the API caller sent). When a deployment run finishes, the runner's finish hook only
*schedules* the scoring, so the caller's response never waits for it. The scoring task builds the same
observation an experiment does (input, output, tool trajectory, status), scores it, and stores a `RunScore` with
what the judge itself cost, kept apart from the run's cost.

**Drift.** After each score the evaluator's rules are checked: the mean over the last `window_minutes` below
`alert_below`, or more than `alert_drop` below the trailing 7-day mean (before the window). A broken rule opens an
alert (once; it stays open while the rule holds) and resolves it when the rule holds no longer. Open alerts are
shown in the app and exported as `agentcanvas_eval_alert_open`: every instance refreshes that gauge from the
database once a minute, so all pods agree and Prometheus can alert on it (`infra/observability/alerts.yaml`).
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.exc import IntegrityError

from agentcanvas.db.models import EvalAlert, Evaluator, OnlineEvaluator, Run, RunScore, WorkflowRow, WorkflowVersion
from agentcanvas.evaluation import Observation, score, spec_of, trajectory_of
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.log import get_logger
from agentcanvas.runtime.runner import price

log = get_logger(__name__)

BASELINE_DAYS = 7
CONCURRENCY = 4  # scoring tasks at once per instance: judges must not crowd out runs
DRIFT_EVERY_S = 10.0  # each instance checks an evaluator's drift rules at most this often (a window is minutes long)


def sampled(run_id: str, online_id: str, rate: float) -> bool:
    """Whether a run is in the sample: stable for a run (a retry of the hook scores the same runs)."""
    if rate >= 1:
        return True
    digest = hashlib.sha256(f"{online_id}:{run_id}".encode()).digest()
    return int.from_bytes(digest[:4], "big") / 2**32 < rate


def matches(flt: dict[str, Any], status: str, labels: dict[str, str]) -> bool:
    statuses = flt.get("status")
    if statuses and status not in statuses:
        return False
    return all(labels.get(k) == v for k, v in (flt.get("metadata") or {}).items())


def observation(h: Any, status: str, error: str | None, input: dict[str, Any] | None = None) -> Observation:
    output: dict[str, Any] = {}
    for ev in h.events:
        if ev["type"] == "RUN_FINISHED":
            output = ev.get("output") or {}
    return Observation(
        input=input if input is not None else h.input or {},
        output=output,
        trajectory=trajectory_of(h.events),
        status=status,
        error=error,
    )


def judge_cost(usage: dict[str, dict[str, int]]) -> tuple[int, float]:
    tokens, cost = 0, 0.0
    for model, u in usage.items():
        t_in, t_out = u.get("input_tokens", 0), u.get("output_tokens", 0)
        tokens += t_in + t_out
        cost += price(model, t_in, t_out) or 0.0
    return tokens, round(cost, 6)


def unpriced(usage: dict[str, dict[str, int]]) -> list[str]:
    """The judge models that were called but have no catalog price (`judge_cost` counts them as free)."""
    return sorted(m for m, u in usage.items() if any(u.values()) and price(m, 1_000_000, 1_000_000) is None)


async def resolve(s: Any, deployment_id: str, evaluator: str, kinds: tuple[str, ...] = ("below", "drop")) -> None:
    """Resolve the open alerts of these rules (their online evaluator was removed, or the rule turned off)."""
    await s.execute(
        update(EvalAlert)
        .where(
            EvalAlert.deployment_id == deployment_id, EvalAlert.evaluator == evaluator, EvalAlert.kind.in_(kinds),
            EvalAlert.resolved_at.is_(None),
        )
        .values(resolved_at=datetime.now(UTC))
    )  # fmt: skip


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)  # SQLite returns naive times


class OnlineScorer:
    """Scores deployment runs in the background (a runner finish hook) and keeps drift alerts current."""

    def __init__(self, db: Any, runner: Any, interval_s: float = 60.0, drift_every_s: float = DRIFT_EVERY_S):
        self.db, self.runner, self.interval_s, self.drift_every_s = db, runner, interval_s, drift_every_s
        self.tasks: set[asyncio.Task] = set()
        self._gate = asyncio.Semaphore(CONCURRENCY)
        self._loop_task: asyncio.Task | None = None
        self._reported: set[tuple[str, str, str]] = set()
        self._workflows: dict[str, Workflow] = {}  # published versions never change: parsed once
        self._checked: dict[tuple[str, str], float] = {}  # (deployment, evaluator) -> when drift was last checked

    def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop(), name="online-metrics")

    async def stop(self) -> None:
        if self._loop_task:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
        await self.drain()

    async def _loop(self) -> None:
        while True:
            try:
                await self.report()
            except Exception:
                log.exception("online.report_failed")
            await asyncio.sleep(self.interval_s)

    async def report(self) -> set[tuple[str, str, str]]:
        """Set `agentcanvas_eval_alert_open` from the database: 1 for each open alert, 0 for those resolved."""
        from agentcanvas import observability

        async with self.db.sessionmaker() as s:
            await self._resolve_orphans(s)
            await self._recheck_open(s)
            q = select(EvalAlert.deployment_id, EvalAlert.evaluator, EvalAlert.kind).where(
                EvalAlert.resolved_at.is_(None)
            )
            open_now = {(d, e, k) for d, e, k in (await s.execute(q)).all()}
        for d, e, k in self._reported | open_now:
            observability.EVAL_ALERT_OPEN.labels(deployment=d, evaluator=e, kind=k).set(
                1 if (d, e, k) in open_now else 0
            )
        self._reported |= open_now
        return open_now

    async def _recheck_open(self, s: Any) -> None:
        """Check the rules of open alerts again: a scored run checks them, so with no traffic they never would."""
        q = (
            select(OnlineEvaluator, Evaluator.name, EvalAlert.workflow_id)
            .join(Evaluator, Evaluator.id == OnlineEvaluator.evaluator_id)
            .join(
                EvalAlert,
                (EvalAlert.deployment_id == OnlineEvaluator.deployment_id) & (EvalAlert.evaluator == Evaluator.name),
            )
            .where(EvalAlert.resolved_at.is_(None))
        )
        for online, name, workflow_id in {(o, n, w) for o, n, w in (await s.execute(q)).all()}:
            await self.check_drift(s, online, name, workflow_id)
        await s.commit()

    async def _resolve_orphans(self, s: Any) -> None:
        """Resolve open alerts no online evaluator watches any more (its evaluator was renamed or deleted)."""
        watched = select(OnlineEvaluator.deployment_id, Evaluator.name).join(
            Evaluator, Evaluator.id == OnlineEvaluator.evaluator_id
        )
        pairs = {(d, n) for d, n in (await s.execute(watched)).all()}
        q = select(EvalAlert).where(EvalAlert.resolved_at.is_(None))
        orphans = [a for a in (await s.scalars(q)).all() if (a.deployment_id, a.evaluator) not in pairs]
        for a in orphans:
            a.resolved_at = datetime.now(UTC)
        if orphans:
            await s.commit()

    async def on_run_finished(self, h: Any, status: str, error: str | None, _interrupts: list[dict[str, Any]]) -> None:
        """Runner finish hook: schedule the scoring and return at once."""
        if h.deployment_id is None or h.origin == "test" or h.drained or h.superseded:
            return  # a run stopped for a hand-over is scored once, when its continuation finishes
        task = asyncio.create_task(self._score(h, status, error), name=f"online-score:{h.run_id}")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def drain(self, timeout: float = 10.0) -> None:
        """Shutting down: give scoring in progress a moment to finish."""
        if self.tasks:
            await asyncio.wait(list(self.tasks), timeout=timeout)

    async def _score(self, h: Any, status: str, error: str | None) -> list[RunScore]:
        try:
            async with self._gate:
                return await self.score_run(h, status, error)
        except Exception:
            log.exception("online.score_failed", run_id=h.run_id)
            return []

    async def score_run(self, h: Any, status: str, error: str | None) -> list[RunScore]:
        """Score the run with the online evaluators that sample it, then route it to the annotation queues whose
        rules it meets (a failed run, a low score)."""
        out = await self._evaluate(h, status, error)
        from agentcanvas.services import annotation

        async with self.db.sessionmaker() as s:
            scores = {r.evaluator: r.score for r in out}
            await annotation.route(s, h.workflow_id, h.run_id, h.thread_id, status=status, scores=scores)
        return out

    async def _evaluate(self, h: Any, status: str, error: str | None) -> list[RunScore]:
        from agentcanvas.services.experiments import scorers
        from agentcanvas.services.projects import PROJECT_OF

        async with self.db.sessionmaker() as s:
            input, labels = h.input, h.labels
            if input is None:  # a resume or hand-over continues the thread's last fresh run: score with its input
                q0 = (
                    select(Run.recording, Run.labels)
                    .where(Run.thread_id == h.thread_id, Run.id != h.run_id, Run.recording.is_not(None))
                    .order_by(Run.started_at.desc())
                    .limit(1)
                )
                first = (await s.execute(q0)).first()
                input = ((first[0] or {}).get("input") if first else None) or {}
                labels = labels or (first[1] if first else None) or {}
            q = (
                select(OnlineEvaluator, Evaluator)
                .join(Evaluator, Evaluator.id == OnlineEvaluator.evaluator_id)
                .where(OnlineEvaluator.deployment_id == h.deployment_id)
                .order_by(OnlineEvaluator.created_at, OnlineEvaluator.id)
            )
            chosen = [
                (o, e)
                for o, e in (await s.execute(q)).all()
                if matches(o.filter or {}, status, labels) and sampled(h.run_id, o.id, o.sampling)
            ]
            if not chosen:
                return []
            version = await s.get(WorkflowVersion, h.version_id) if h.version_id else None
            row = await s.get(WorkflowRow, h.workflow_id)
            if version is None or row is None:
                return []
            wf = self._workflows.get(version.id) or Workflow.model_validate(version.ir)
            if len(self._workflows) > 256:
                self._workflows.clear()
            self._workflows[version.id] = wf
            PROJECT_OF[wf.id] = row.project_id
            siblings = (await s.scalars(select(Evaluator).where(Evaluator.workflow_id == h.workflow_id))).all()
        obs = observation(h, status, error, input)
        out: list[RunScore] = []
        for _, e in chosen:
            usage: dict[str, dict[str, int]] = {}
            sc = await score(spec_of(e, siblings), obs, **scorers(self.runner, wf, usage))
            tokens, cost = judge_cost(usage)
            if missing := unpriced(usage):
                note = f"(judge cost not counted: no price for {', '.join(missing)})"
                sc = sc.model_copy(update={"comment": f"{sc.comment} {note}".strip()})
            out.append(
                RunScore(
                    id=new_id("rsc"),
                    run_id=h.run_id,
                    thread_id=h.thread_id,
                    workflow_id=h.workflow_id,
                    deployment_id=h.deployment_id,
                    evaluator=e.name,
                    source="online",
                    score=sc.score,
                    passed=sc.passed,
                    comment=sc.comment,
                    judge_tokens=tokens,
                    judge_cost_usd=cost,
                    created_at=datetime.now(UTC),
                )
            )
        async with self.db.sessionmaker() as s:
            s.add_all(out)
            await s.commit()
            now = asyncio.get_running_loop().time()
            for online, e in chosen:
                key = (online.deployment_id, e.name)
                if self.drift_every_s and now - self._checked.get(key, -self.drift_every_s) < self.drift_every_s:
                    continue  # checked a moment ago: the window has barely moved
                self._checked[key] = now
                await self.check_drift(s, online, e.name, h.workflow_id)
            await s.commit()
        return out

    async def check_drift(self, s: Any, online: OnlineEvaluator, evaluator: str, workflow_id: str) -> None:
        from agentcanvas import observability

        now = datetime.now(UTC)
        window_start = now - timedelta(minutes=online.window_minutes)
        base = (
            RunScore.deployment_id == online.deployment_id,
            RunScore.evaluator == evaluator,
            RunScore.source == "online",
        )
        total, n, mean = (
            await s.execute(
                select(func.count(), func.count(RunScore.score), func.avg(RunScore.score)).where(
                    *base, RunScore.created_at >= window_start
                )
            )
        ).one()
        if not n or n < online.min_samples:
            # too few runs to tell (traffic stopped): nothing is known to be broken. Runs the evaluator could not
            # score (a judge that keeps failing) are not good news: an open alert stays open
            if (total or 0) < online.min_samples:
                await resolve(s, online.deployment_id, evaluator)
            return
        mean = float(mean)
        observability.ONLINE_SCORE.labels(deployment=online.deployment_id, evaluator=evaluator).set(mean)
        bn, baseline = (
            await s.execute(
                select(func.count(), func.avg(RunScore.score)).where(
                    *base,
                    RunScore.score.is_not(None),
                    RunScore.created_at >= now - timedelta(days=BASELINE_DAYS),
                    RunScore.created_at < window_start,
                )
            )
        ).one()
        baseline = float(baseline) if bn and bn >= online.min_samples else None
        rules = {
            "below": (
                online.alert_below is not None and mean < online.alert_below,
                f"{evaluator}: mean {mean:.2f} over the last {online.window_minutes} min "
                f"is below {online.alert_below:g}"
                if online.alert_below is not None
                else "",
            ),
            "drop": (
                online.alert_drop is not None and baseline is not None and baseline - mean > online.alert_drop,
                f"{evaluator}: mean {mean:.2f} over the last {online.window_minutes} min is "
                f"{(baseline or 0) - mean:.2f} below its {BASELINE_DAYS}-day mean {baseline or 0:.2f}",
            ),
        }
        for kind, (broken, message) in rules.items():
            q = select(EvalAlert).where(
                EvalAlert.deployment_id == online.deployment_id, EvalAlert.evaluator == evaluator,
                EvalAlert.kind == kind, EvalAlert.resolved_at.is_(None),
            )  # fmt: skip
            current = await s.scalar(q.limit(1))
            if broken and current is None:
                alert = EvalAlert(
                    id=new_id("alr"),
                    workflow_id=workflow_id,
                    deployment_id=online.deployment_id,
                    evaluator=evaluator,
                    kind=kind,
                    message=message,
                    value=round(mean, 4),
                    baseline=None if baseline is None else round(baseline, 4),
                    created_at=now,
                )
                try:
                    async with s.begin_nested():  # another task or pod may open the same alert at once
                        s.add(alert)
                except IntegrityError:
                    continue
                observability.EVAL_ALERTS.labels(kind=kind).inc()
                log.warning("online.alert", deployment_id=online.deployment_id, evaluator=evaluator, kind=kind)
            elif not broken and current is not None:
                current.resolved_at = now
                log.info("online.alert_resolved", deployment_id=online.deployment_id, evaluator=evaluator, kind=kind)


async def buckets(s: Any, deployment_id: str, since: datetime, hours: int) -> list[dict[str, Any]]:
    """Online scores of a deployment grouped by evaluator and time bucket (an hour or a UTC day: `hours` 1 or 24),
    counted by the database rather than read row by row."""
    if s.bind.dialect.name == "postgresql":
        start: Any = func.date_trunc("hour" if hours == 1 else "day", func.timezone("UTC", RunScore.created_at))
    else:  # SQLite keeps UTC text
        start = func.strftime("%Y-%m-%d %H:00:00" if hours == 1 else "%Y-%m-%d 00:00:00", RunScore.created_at)
    start = start.label("start")
    q = (
        select(
            start,
            RunScore.evaluator,
            func.count(),
            func.count(RunScore.score),
            func.avg(RunScore.score),
            func.sum(case((RunScore.passed, 1), else_=0)),
            func.coalesce(func.sum(RunScore.judge_cost_usd), 0.0),
        )
        .where(RunScore.deployment_id == deployment_id, RunScore.source == "online", RunScore.created_at >= since)
        .group_by(start, RunScore.evaluator)
        .order_by(start, RunScore.evaluator)
    )
    out = []
    for when, evaluator, runs, scored, mean, passed, cost in (await s.execute(q)).all():
        at = _utc(when if isinstance(when, datetime) else datetime.fromisoformat(when))
        out.append({
            "start": at, "evaluator": evaluator, "runs": runs, "scored": scored,
            "mean": round(float(mean), 4) if mean is not None else None,
            "pass_rate": round(int(passed or 0) / runs, 4), "judge_cost_usd": round(float(cost or 0.0), 6),
        })  # fmt: skip
    return out
