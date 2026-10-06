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

from sqlalchemy import func, select

from agentcanvas.db.models import EvalAlert, Evaluator, OnlineEvaluator, RunScore, WorkflowRow, WorkflowVersion
from agentcanvas.evaluation import Observation, Spec, score, trajectory_of
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.log import get_logger
from agentcanvas.runtime.runner import price

log = get_logger(__name__)

BASELINE_DAYS = 7
CONCURRENCY = 4  # scoring tasks at once per instance: judges must not crowd out runs


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


def observation(h: Any, status: str, error: str | None) -> Observation:
    output: dict[str, Any] = {}
    for ev in h.events:
        if ev["type"] == "RUN_FINISHED":
            output = ev.get("output") or {}
    return Observation(
        input=h.input or {}, output=output, trajectory=trajectory_of(h.events), status=status, error=error
    )


def judge_cost(usage: dict[str, dict[str, int]]) -> tuple[int, float]:
    tokens, cost = 0, 0.0
    for model, u in usage.items():
        t_in, t_out = u.get("input_tokens", 0), u.get("output_tokens", 0)
        tokens += t_in + t_out
        cost += price(model, t_in, t_out) or 0.0
    return tokens, round(cost, 6)


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=UTC)  # SQLite returns naive times


class OnlineScorer:
    """Scores deployment runs in the background (a runner finish hook) and keeps drift alerts current."""

    def __init__(self, db: Any, runner: Any, interval_s: float = 60.0):
        self.db, self.runner, self.interval_s = db, runner, interval_s
        self.tasks: set[asyncio.Task] = set()
        self._gate = asyncio.Semaphore(CONCURRENCY)
        self._loop_task: asyncio.Task | None = None
        self._reported: set[tuple[str, str, str]] = set()

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

    async def on_run_finished(self, h: Any, status: str, error: str | None, _interrupts: list[dict[str, Any]]) -> None:
        """Runner finish hook: schedule the scoring and return at once."""
        if h.deployment_id is None or h.origin == "test":
            return
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
            q = (
                select(OnlineEvaluator, Evaluator)
                .join(Evaluator, Evaluator.id == OnlineEvaluator.evaluator_id)
                .where(OnlineEvaluator.deployment_id == h.deployment_id)
                .order_by(OnlineEvaluator.created_at, OnlineEvaluator.id)
            )
            chosen = [
                (o, e)
                for o, e in (await s.execute(q)).all()
                if matches(o.filter or {}, status, h.labels) and sampled(h.run_id, o.id, o.sampling)
            ]
            if not chosen:
                return []
            version = await s.get(WorkflowVersion, h.version_id) if h.version_id else None
            row = await s.get(WorkflowRow, h.workflow_id)
            if version is None or row is None:
                return []
            wf = Workflow.model_validate(version.ir)
            PROJECT_OF[wf.id] = row.project_id
        obs = observation(h, status, error)
        out: list[RunScore] = []
        for _, e in chosen:
            usage: dict[str, dict[str, int]] = {}
            spec = Spec(name=e.name, kind=e.kind, config=e.config, threshold=e.threshold)  # type: ignore[arg-type]
            sc = await score(spec, obs, **scorers(self.runner, wf, usage))
            tokens, cost = judge_cost(usage)
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
            for online, e in chosen:
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
            RunScore.score.is_not(None),
        )
        n, mean = (
            await s.execute(
                select(func.count(), func.avg(RunScore.score)).where(*base, RunScore.created_at >= window_start)
            )
        ).one()
        if not n or n < online.min_samples:
            return
        mean = float(mean)
        observability.ONLINE_SCORE.labels(deployment=online.deployment_id, evaluator=evaluator).set(mean)
        bn, baseline = (
            await s.execute(
                select(func.count(), func.avg(RunScore.score)).where(
                    *base,
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
                s.add(
                    EvalAlert(
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
                )
                observability.EVAL_ALERTS.labels(kind=kind).inc()
                log.warning("online.alert", deployment_id=online.deployment_id, evaluator=evaluator, kind=kind)
            elif not broken and current is not None:
                current.resolved_at = now
                log.info("online.alert_resolved", deployment_id=online.deployment_id, evaluator=evaluator, kind=kind)


def buckets(rows: list[tuple[datetime, str, float | None, bool, float]], hours: int) -> list[dict[str, Any]]:
    """Scores grouped by evaluator and time bucket (`hours` long, aligned to UTC midnight)."""
    out: dict[tuple[datetime, str], dict[str, Any]] = {}
    for created, evaluator, value, passed, cost in rows:
        t = _utc(created)
        day = t.replace(hour=0, minute=0, second=0, microsecond=0)
        start = day + timedelta(hours=(t.hour // hours) * hours)
        b = out.setdefault((start, evaluator), {"start": start, "evaluator": evaluator, "runs": 0, "values": [],
                                                "passed": 0, "judge_cost_usd": 0.0})  # fmt: skip
        b["runs"] += 1
        b["passed"] += bool(passed)
        b["judge_cost_usd"] += cost or 0.0
        if value is not None:
            b["values"].append(value)
    result = []
    for b in sorted(out.values(), key=lambda b: (b["start"], b["evaluator"])):
        values = b.pop("values")
        b["scored"] = len(values)
        b["mean"] = round(sum(values) / len(values), 4) if values else None
        b["pass_rate"] = round(b.pop("passed") / b["runs"], 4)
        b["judge_cost_usd"] = round(b["judge_cost_usd"], 6)
        result.append(b)
    return result
