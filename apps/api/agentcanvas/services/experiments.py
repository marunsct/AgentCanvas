"""Experiments (S12 FR-EVAL-04): run a workflow over a dataset version and score every result.

An experiment has one or more **targets**: the draft or a published version, each optionally with **overrides**
(config values replaced on named resources or steps, e.g. another model or prompt). Targets, the dataset version
and the evaluators are fixed when the experiment starts, so the numbers always describe what actually ran.

Each example of each target is an ordinary run with `origin="test"` (no event handlers, no recordings), started
through the normal runner with bounded concurrency; its result keeps the thread, output, tool calls, scores,
tokens, cost and latency. `summary()` aggregates per target.

Experiments run in the background on the instance that started them (`ExperimentJob`). That instance refreshes a
heartbeat; when it stops (a redeploy drains it, or it dies) another instance takes the experiment over and runs the
examples that have no result yet, so an experiment survives a redeploy.
"""

from __future__ import annotations

import asyncio
import copy
import math
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import Experiment, ExperimentResult, WorkflowRow
from agentcanvas.db.repositories import ScopeRepo
from agentcanvas.errors import Conflict, InvalidInput, NotFound, Unavailable
from agentcanvas.evaluation import (
    ONLY_IN_EXPERIMENTS,
    Observation,
    Score,
    Spec,
    code_hash,
    pairwise,
    score,
    trajectory_of,
)
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.log import get_logger
from agentcanvas.services import simulation
from agentcanvas.services.simulation import Simulation

log = get_logger(__name__)

EXAMPLE_TIMEOUT_S = 300.0
MAX_CONCURRENCY = 16
MAX_RUNS = 5000  # targets x examples per experiment


class TargetIn(BaseModel):
    label: str | None = Field(default=None, max_length=100)
    version: str | None = Field(default=None, description="a published version (semver or id); None: the draft")
    overrides: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="resource or step (id or name) -> config values to replace"
    )
    framework: str | None = Field(
        default=None, max_length=64, description="the framework this target runs on (S25); None: the workflow's own"
    )


def apply_overrides(wf: Workflow, overrides: dict[str, dict[str, Any]]) -> Workflow:
    """A copy of `wf` with config values replaced on the named resources and steps (422 for unknown names)."""
    if not overrides:
        return wf
    ir = copy.deepcopy(wf.model_dump(mode="json"))
    items = {
        **{i["name"]: i for i in ir["resources"] + ir["nodes"]},
        **{i["id"]: i for i in ir["resources"] + ir["nodes"]},
    }
    for key, values in overrides.items():
        if key not in items:
            raise InvalidInput(f"override: '{key}' is not a resource or step of this workflow")
        items[key]["config"] = {**items[key].get("config", {}), **values}
    return Workflow.model_validate(ir)


def context_for(wf: Workflow, given: dict[str, Any], user_id: str | None, org_id: str | None) -> dict[str, Any]:
    """The run context of an example: identity fields come from whoever started the experiment, never the data."""
    ctx: dict[str, Any] = {}
    for f in wf.context.fields:
        if f.source == "user":
            ctx[f.name] = user_id
        elif f.source == "tenant":
            ctx[f.name] = org_id
        elif f.name in given:
            ctx[f.name] = given[f.name]
        elif f.required:
            raise InvalidInput(f"context field '{f.name}' is required")
    return ctx


def scorers(runner: Any, wf: Workflow, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    """The model service's call (for judges) and the workflow adapter's workspace functions (for code evaluators,
    with the hash of the code they run), for `wf`'s project. `usage`, when given, collects the judge's tokens per
    model: {model: {input_tokens, output_tokens}}."""
    adapter, resources, models = runner.adapter, runner.resources("", wf), runner.models

    async def complete(system: str, prompt: str, model: str) -> str:
        if usage is None:
            return await models.complete(resources, model, system, prompt)
        return await models.complete(resources, model, system, prompt, usage=usage.setdefault(model, {}))

    return {
        "complete": complete,
        "script": lambda path: adapter.script(resources, path),
        "fingerprint": lambda path: code_hash(getattr(resources, "workspace", None), path),
    }


def judged(sc: Score, spent: dict[str, dict[str, int]]) -> dict[str, Any]:
    """A score as stored on an experiment result, with what its AI judge cost (never added to the run's cost) and the
    judge models without a price, whose calls that cost leaves out."""
    from agentcanvas.services.online import judge_cost, unpriced

    out = sc.model_dump()
    tokens, cost = judge_cost(spent)
    if tokens:
        out.update(judge_tokens=tokens, judge_cost_usd=cost)
    if missing := unpriced(spent):
        out["judge_unpriced"] = missing
    return out


def _percentile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


class EvaluatorSummary(BaseModel):
    evaluator: str
    mean: float | None
    pass_rate: float | None
    scored: int
    ci: list[float] | None = Field(
        default=None, description="pairwise: 95% confidence interval of the win rate (the mean; ties count half)"
    )


def confidence(values: list[float]) -> list[float] | None:
    """95% interval of a mean (normal approximation), clipped to 0-1."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    half = 1.96 * sd / math.sqrt(n)
    return [round(max(0.0, mean - half), 4), round(min(1.0, mean + half), 4)]


class TargetSummary(BaseModel):
    target: int
    label: str
    examples: int
    completed: int
    errors: int = Field(description="runs that did not end in success")
    pass_rate: float | None = Field(description="share of completed examples that passed every evaluator")
    evaluators: list[EvaluatorSummary]
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    tokens: int
    cost_usd: float
    judge_cost_usd: float = Field(0.0, description="what the AI judges cost scoring this target (not in cost_usd)")
    judge_unpriced: list[str] = Field(
        default_factory=list, description="judge models without a price: their calls are not in judge_cost_usd"
    )


def summary(exp: Experiment, results: list[ExperimentResult]) -> list[TargetSummary]:
    out = []
    for i, target in enumerate(exp.targets):
        mine = [r for r in results if r.target == i]
        done = [r for r in mine if r.status == "done"]
        evaluators = []
        for spec in exp.evaluators:
            scores = [s for r in done for s in r.scores or [] if s["evaluator"] == spec["name"]]
            values = [s["score"] for s in scores if s["score"] is not None]
            evaluators.append(
                EvaluatorSummary(
                    evaluator=spec["name"],
                    mean=round(sum(values) / len(values), 4) if values else None,
                    pass_rate=round(sum(s["passed"] for s in scores) / len(scores), 4) if scores else None,
                    scored=len(values),
                    ci=confidence(values) if spec["kind"] == "pairwise" else None,
                )
            )
        # a pairwise verdict compares two targets: it says nothing about whether this target's answer passes
        graded = {e["name"] for e in exp.evaluators if e["kind"] not in ONLY_IN_EXPERIMENTS}
        passed = [all(s["passed"] for s in r.scores or [] if s["evaluator"] in graded) for r in done]
        latencies = [r.latency_ms for r in done if r.latency_ms is not None]
        out.append(
            TargetSummary(
                target=i,
                label=target["label"],
                examples=len(mine),
                completed=len(done),
                errors=sum(r.run_status != "success" for r in done),
                pass_rate=round(sum(passed) / len(passed), 4) if passed and graded else None,
                evaluators=evaluators,
                latency_p50_ms=_percentile(latencies, 0.5),
                latency_p95_ms=_percentile(latencies, 0.95),
                tokens=sum(r.tokens_in + r.tokens_out for r in done),
                cost_usd=round(sum(r.cost_usd for r in done), 6),
                judge_cost_usd=round(sum(float(x.get("judge_cost_usd") or 0) for r in mine for x in r.scores or []), 6),
                judge_unpriced=sorted({m for r in mine for x in r.scores or [] for m in x.get("judge_unpriced") or []}),
            )
        )
    return out


class ExperimentService:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def create(
        self,
        wf_id: str,
        *,
        name: str,
        dataset_id: str,
        dataset_version: int,
        split: str | None,
        examples: list[dict[str, Any]],
        targets: list[dict[str, Any]],
        evaluators: list[Spec],
        concurrency: int,
        created_by: str | None,
        workspace_commit: str | None = None,
        simulation: dict[str, Any] | None = None,
    ) -> Experiment:
        """`targets` are resolved: {label, version, version_id, overrides, ir}."""
        if split is not None:
            examples = [e for e in examples if e.get("split", "") == split]
        if not examples:
            raise InvalidInput("the dataset version has no examples" + (f" in split '{split}'" if split else ""))
        if len(examples) * len(targets) > MAX_RUNS:
            raise InvalidInput(f"an experiment runs at most {MAX_RUNS} examples in all targets")
        exp = Experiment(
            id=new_id("exp"),
            workflow_id=wf_id,
            name=name,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
            split=split,
            targets=targets,
            evaluators=[e.model_dump() for e in evaluators],
            concurrency=concurrency,
            workspace_commit=workspace_commit,
            simulation=simulation,
            status="running",
            heartbeat_at=datetime.now(UTC),
            created_by=created_by,
        )
        self.s.add(exp)
        await self.s.flush()
        for t in range(len(targets)):
            for pos, ex in enumerate(examples):
                self.s.add(
                    ExperimentResult(
                        id=new_id("exr"), experiment_id=exp.id, target=t, example_id=ex["id"], position=pos
                    )
                )
        await self.s.commit()
        return exp

    async def get(self, exp_id: str) -> Experiment:
        exp = await self.s.get(Experiment, exp_id)
        if exp is None:
            raise NotFound(f"experiment '{exp_id}' not found")
        return exp

    async def list(self, wf_id: str) -> list[Experiment]:
        q = select(Experiment).where(Experiment.workflow_id == wf_id).order_by(Experiment.id.desc())
        return list((await self.s.scalars(q)).all())

    async def results(self, exp_id: str) -> list[ExperimentResult]:
        q = (
            select(ExperimentResult)
            .where(ExperimentResult.experiment_id == exp_id)
            .order_by(ExperimentResult.position, ExperimentResult.target)
        )
        return list((await self.s.scalars(q)).all())

    async def delete(self, exp_id: str) -> None:
        exp = await self.get(exp_id)
        if exp.status == "running":
            raise Conflict("cancel the experiment before deleting it")
        await self.s.delete(exp)
        await self.s.commit()


class ExperimentJob:
    """Runs experiments in the background and takes over those whose instance stopped (every instance runs it)."""

    def __init__(self, db: Any, runner: Any, interval_s: float = 10.0, orphan_after_s: float = 60.0):
        self.db, self.runner, self.interval_s, self.orphan_after_s = db, runner, interval_s, orphan_after_s
        self.owner = f"ins_{uuid.uuid4().hex[:16]}"  # this instance's name on the experiments it works on (S13)
        self.tasks: dict[str, asyncio.Task] = {}
        self._loop_task: asyncio.Task | None = None

    # ------------------------------------------------------------------------------------------- lifecycle ----
    def start(self) -> None:
        self._loop_task = asyncio.create_task(self._loop(), name="experiments")

    async def stop(self) -> None:
        """Shutting down: stop working and leave the experiments for another instance (FR-X-10)."""
        if self._loop_task:
            self._loop_task.cancel()
            await asyncio.gather(self._loop_task, return_exceptions=True)
        ids = list(self.tasks)
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        if ids:
            async with self.db.sessionmaker() as s:  # stale at once: the next instance takes them over
                await s.execute(
                    update(Experiment)
                    .where(Experiment.id.in_(ids), Experiment.status == "running", Experiment.owner == self.owner)
                    .values(heartbeat_at=datetime(2000, 1, 1, tzinfo=UTC))
                )
                await s.commit()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("experiments.tick_failed")
            await asyncio.sleep(self.interval_s)

    async def tick(self, now: datetime | None = None) -> list[str]:
        """Refresh the heartbeat of the experiments running here and take over stale ones; returns those taken.

        Each experiment has one owner. One that is no longer this instance's (taken over while this instance
        stalled) or no longer running (cancelled from another instance) stops here."""
        now = now or datetime.now(UTC)
        mine = [i for i, t in self.tasks.items() if not t.done()]
        cutoff = now - timedelta(seconds=self.orphan_after_s)
        taken = []
        async with self.db.sessionmaker() as s:
            if mine:
                await s.execute(
                    update(Experiment)
                    .where(Experiment.id.in_(mine), Experiment.owner == self.owner)
                    .values(heartbeat_at=now)
                )
                q = select(Experiment.id).where(
                    Experiment.id.in_(mine), or_(Experiment.status != "running", Experiment.owner != self.owner)
                )
                for (gone,) in (await s.execute(q)).all():
                    log.info("experiment.stopped_here", experiment_id=gone)
                    self.tasks[gone].cancel()
            stale = (
                Experiment.status == "running",
                func.coalesce(Experiment.heartbeat_at, Experiment.created_at) < cutoff,
            )
            for (exp_id,) in (await s.execute(select(Experiment.id).where(*stale).limit(20))).all():
                if exp_id in self.tasks:
                    continue
                claimed = await s.execute(  # exactly one instance wins the conditional update
                    update(Experiment).where(Experiment.id == exp_id, *stale).values(heartbeat_at=now, owner=self.owner)
                )
                if claimed.rowcount == 1:  # type: ignore[attr-defined]
                    taken.append(exp_id)
            await s.commit()
        for exp_id in taken:
            log.info("experiment.taken_over", experiment_id=exp_id)
            self.launch(exp_id)
        return taken

    def launch(self, exp_id: str) -> asyncio.Task:
        task = asyncio.create_task(self._run(exp_id), name=f"experiment:{exp_id}")
        self.tasks[exp_id] = task

        def forget(t: asyncio.Task) -> None:
            if self.tasks.get(exp_id) is t:
                del self.tasks[exp_id]

        task.add_done_callback(forget)
        return task

    async def cancel(self, exp_id: str) -> bool:
        """Stop an experiment: no more examples start, running ones are cancelled. Works from any instance."""
        async with self.db.sessionmaker() as s:
            res = await s.execute(
                update(Experiment)
                .where(Experiment.id == exp_id, Experiment.status == "running")
                .values(status="cancelled", ended_at=datetime.now(UTC))
            )
            await s.commit()
        task = self.tasks.get(exp_id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return res.rowcount == 1  # type: ignore[attr-defined]

    # ------------------------------------------------------------------------------------------- execution ----
    async def _run(self, exp_id: str) -> None:
        from agentcanvas.services.projects import PROJECT_OF

        async with self.db.sessionmaker() as s:
            mine = await s.execute(  # a new experiment becomes this instance's; a taken-over one already is
                update(Experiment)
                .where(
                    Experiment.id == exp_id, Experiment.status == "running",
                    or_(Experiment.owner.is_(None), Experiment.owner == self.owner),
                )
                .values(owner=self.owner, heartbeat_at=datetime.now(UTC))
            )  # fmt: skip
            await s.commit()
            if mine.rowcount != 1:  # type: ignore[attr-defined]
                return
            exp = await s.get(Experiment, exp_id)
            if exp is None:
                return
            row = await s.get(WorkflowRow, exp.workflow_id)
            # rows left running by a stopped instance start over
            await s.execute(
                update(ExperimentResult)
                .where(ExperimentResult.experiment_id == exp_id, ExperimentResult.status == "running")
                .values(status="pending")
            )
            await s.commit()
            pending = (
                await s.scalars(
                    select(ExperimentResult)
                    .where(ExperimentResult.experiment_id == exp_id, ExperimentResult.status == "pending")
                    .order_by(ExperimentResult.position, ExperimentResult.target)
                )
            ).all()
            jobs = [(r.id, r.target, r.example_id) for r in pending]
            from agentcanvas.services.datasets import DatasetService

            version = await DatasetService(s).version(exp.dataset_id, exp.dataset_version)
            examples = {e["id"]: e for e in version.examples}
            targets, specs, created_by = exp.targets, [Spec(**e) for e in exp.evaluators], exp.created_by
            org_id = await ScopeRepo(s).org_of("workflow", exp.workflow_id)
            concurrency, sim = exp.concurrency, exp.simulation
        if row is not None:
            PROJECT_OF[exp.workflow_id] = row.project_id
        try:  # a target that no longer parses ends the experiment, else every instance would retry it forever
            workflows = [Workflow.model_validate(t["ir"]) for t in targets]
        except (ValueError, KeyError, TypeError) as exc:  # pydantic.ValidationError is a ValueError
            log.warning("experiment.bad_target", experiment_id=exp_id)
            await self._finish(exp_id, "error", f"a target's workflow is not valid: {exc}"[:500])
            return
        gate = asyncio.Semaphore(concurrency)

        async def one(result_id: str, target: int, example_id: str) -> None:
            async with gate:
                example = examples.get(example_id)
                if example is None:  # cannot happen with a pinned version; kept safe
                    await self._save(result_id, status="done", run_status="error", error="example not found")
                    return
                who = (created_by, org_id)
                await self._example(result_id, workflows[target], targets[target], example, specs, who, sim)

        tasks = [asyncio.ensure_future(one(*j)) for j in jobs]
        try:
            await asyncio.gather(*tasks)
        except Exception as exc:
            for t in tasks:  # nothing may keep running for an experiment that has ended
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            log.exception("experiment.failed", experiment_id=exp_id)
            await self._finish(exp_id, "error", f"{type(exc).__name__}: {exc}"[:500])
            return
        if self.runner.draining:  # examples were refused while shutting down: leave them to the next instance
            return
        if len(targets) > 1 and any(x.kind == "pairwise" for x in specs):
            try:
                await self._pairwise(exp_id, workflows[0], [x for x in specs if x.kind == "pairwise"], examples, gate)
            except Exception as exc:
                log.exception("experiment.pairwise_failed", experiment_id=exp_id)
                await self._finish(exp_id, "error", f"pairwise judging: {type(exc).__name__}: {exc}"[:500])
                return
        await self._finish(exp_id, "done")

    async def _pairwise(
        self,
        exp_id: str,
        wf: Workflow,
        specs: list[Spec],
        examples: dict[str, dict[str, Any]],
        gate: asyncio.Semaphore,
    ) -> None:
        """Every target against the first, per example and pairwise judge: the target's result gets its score
        against the first target, and the first target's result the opposite score (one per comparison)."""
        async with self.db.sessionmaker() as s:
            rows = (await s.scalars(select(ExperimentResult).where(ExperimentResult.experiment_id == exp_id))).all()
        by_example: dict[str, dict[int, ExperimentResult]] = {}
        for r in rows:
            by_example.setdefault(r.example_id, {})[r.target] = r
        added: dict[str, list[dict[str, Any]]] = {r.id: [] for r in rows}

        def observed(r: ExperimentResult) -> Observation:
            status = r.run_status if r.status == "done" else "error"
            return Observation(output=r.output or {}, status=status or "error", error=r.error)

        async def judge(spec: Spec, ex_id: str, base: ExperimentResult, other: ExperimentResult) -> None:
            async with gate:
                example = examples.get(ex_id) or {}
                spent: dict[str, dict[str, int]] = {}  # two judge calls (both orders): counted on the target's result
                complete = scorers(self.runner, wf, spent)["complete"]
                sc = await pairwise(spec, example.get("input") or {}, observed(base), observed(other), complete)
            added[other.id].append(judged(sc, spent))
            mirrored = None if sc.score is None else 1 - sc.score
            added[base.id].append(
                Score(evaluator=spec.name, score=mirrored, passed=mirrored is not None and mirrored >= 0.5,
                      comment=sc.comment).model_dump()
            )  # fmt: skip

        await asyncio.gather(
            *(
                judge(spec, ex_id, by_target[0], r)
                for spec in specs
                for ex_id, by_target in by_example.items()
                if 0 in by_target
                for t, r in sorted(by_target.items())
                if t > 0
            )
        )
        names = {x.name for x in specs}
        async with self.db.sessionmaker() as s:
            for r in rows:
                if added[r.id]:  # judged again after a takeover: the new verdicts replace any earlier ones
                    kept = [x for x in r.scores or [] if x["evaluator"] not in names]
                    await s.execute(
                        update(ExperimentResult).where(ExperimentResult.id == r.id).values(scores=kept + added[r.id])
                    )
            await s.commit()

    async def _finish(self, exp_id: str, status: str, error: str | None = None) -> None:
        async with self.db.sessionmaker() as s:
            await s.execute(
                update(Experiment)
                .where(Experiment.id == exp_id, Experiment.status == "running")
                .values(status=status, error=error, ended_at=datetime.now(UTC))
            )
            await s.commit()

    async def _save(self, result_id: str, **values: Any) -> None:
        async with self.db.sessionmaker() as s:
            await s.execute(update(ExperimentResult).where(ExperimentResult.id == result_id).values(**values))
            await s.commit()

    async def _example(
        self,
        result_id: str,
        wf: Workflow,
        target: dict[str, Any],
        example: dict[str, Any],
        specs: list[Spec],
        who: tuple[str | None, str | None],
        simulation: dict[str, Any] | None = None,
    ) -> None:
        if simulation is not None:
            await self._conversation(result_id, wf, target, example, specs, who, Simulation(**simulation))
            return
        user_id, org_id = who
        started = time.perf_counter()
        try:
            context = context_for(wf, example.get("context") or {}, user_id, org_id)
            handle = await self.runner.start(
                wf,
                input=example.get("input") or {},
                context=context,
                user_id=user_id,
                origin="test",
                version_id=target.get("version_id"),
            )
        except Unavailable:  # draining: another instance runs this example
            return
        except Exception as exc:  # noqa: BLE001 - reported on the result
            await self._not_started(result_id, specs, exc)
            return
        await self._save(result_id, status="running", run_id=handle.run_id, thread_id=handle.thread_id)
        run_status, error, output = await self._wait(handle)
        usage = (handle.usage["input_tokens"], handle.usage["output_tokens"], float(handle.usage["cost_usd"] or 0))
        await self._scored(
            result_id, wf, example, specs, run_status=run_status, error=error, output=output,
            trajectory=trajectory_of(handle.events), usage=usage, started=started,
        )  # fmt: skip

    async def _not_started(self, result_id: str, specs: list[Spec], exc: Exception) -> None:
        # a run that never started fails every evaluator: leaving it unscored would let averages (and release
        # gates) skip exactly the examples the workflow cannot handle
        failed = [
            Score(evaluator=x.name, score=0.0, passed=False, comment="the run did not start")
            for x in specs
            if x.kind not in ONLY_IN_EXPERIMENTS
        ]
        await self._save(
            result_id, status="done", run_status="error", error=f"could not start: {exc}"[:500],
            scores=[f.model_dump() for f in failed],
        )  # fmt: skip

    async def _wait(self, handle: Any) -> tuple[str, str | None, dict[str, Any]]:
        """A started run's status, error and final output (the run is cancelled when it takes too long)."""
        try:
            await asyncio.wait_for(handle.done.wait(), EXAMPLE_TIMEOUT_S)
        except TimeoutError:
            await self.runner.cancel(handle.run_id)
            return "timeout", f"no result after {EXAMPLE_TIMEOUT_S:g} s", {}
        except asyncio.CancelledError:
            await self.runner.cancel(handle.run_id)
            raise
        run_status, error, output = "error", None, {}
        for ev in handle.events:
            if ev["type"] == "RUN_FINISHED":
                run_status, output = ev.get("status") or "success", ev.get("output") or {}
            elif ev["type"] == "RUN_ERROR":
                run_status, error = "error", ev.get("message")
        return run_status, error, output

    async def _scored(
        self,
        result_id: str,
        wf: Workflow,
        example: dict[str, Any],
        specs: list[Spec],
        *,
        run_status: str,
        error: str | None,
        output: dict[str, Any],
        trajectory: list[str],
        usage: tuple[int, int, float],
        started: float,
    ) -> None:
        latency = round((time.perf_counter() - started) * 1000)
        obs = Observation(
            input=example.get("input") or {},
            output=output,
            reference=example.get("reference") or {},
            trajectory=trajectory,
            expected_trajectory=example.get("trajectory"),
            status=run_status,
            error=error,
        )
        scores: list[dict[str, Any]] = []
        for spec in specs:
            if spec.kind not in ONLY_IN_EXPERIMENTS:
                spent: dict[str, dict[str, int]] = {}
                scores.append(judged(await score(spec, obs, **scorers(self.runner, wf, spent)), spent))
        await self._save(
            result_id,
            status="done",
            run_status=run_status,
            error=error,
            output=output,
            trajectory=trajectory,
            scores=scores,
            tokens_in=usage[0],
            tokens_out=usage[1],
            cost_usd=usage[2],
            latency_ms=latency,
        )

    async def _conversation(
        self,
        result_id: str,
        wf: Workflow,
        target: dict[str, Any],
        example: dict[str, Any],
        specs: list[Spec],
        who: tuple[str | None, str | None],
        sim: Simulation,
    ) -> None:
        """FR-EVAL-07: a simulated user talks with the workflow on one thread; the conversation is one result."""
        user_id, org_id = who
        started = time.perf_counter()
        complete = scorers(self.runner, wf)["complete"]
        goal = str((example.get("metadata") or {}).get("goal") or sim.goal)
        first_input = {k: v for k, v in (example.get("input") or {}).items() if k != "messages"}
        transcript: list[dict[str, str]] = []
        trajectory: list[str] = []
        tokens_in = tokens_out = 0
        cost = 0.0
        run_status, error, output, ended = "success", None, {}, "max_turns"
        try:
            context = context_for(wf, example.get("context") or {}, user_id, org_id)
            message = simulation.opener(example) or await simulation.next_message(sim, goal, [], complete)
        except Exception as exc:  # noqa: BLE001 - reported on the result
            await self._not_started(result_id, specs, exc)
            return
        if message is None:
            await self._not_started(result_id, specs, RuntimeError("the simulated user did not say anything"))
            return
        thread_id: str | None = None
        for turn in range(sim.max_turns):
            transcript.append({"role": "user", "content": message})
            try:
                handle = await self.runner.start(
                    wf,
                    thread_id=thread_id,
                    input={**(first_input if turn == 0 else {}), "messages": [{"role": "user", "content": message}]},
                    context=context,
                    user_id=user_id,
                    origin="test",
                    version_id=target.get("version_id"),
                )
            except Unavailable:
                return
            except Exception as exc:  # noqa: BLE001
                if turn == 0:
                    await self._not_started(result_id, specs, exc)
                    return
                run_status, error, ended = "error", f"turn {turn + 1} could not start: {exc}"[:500], "error"
                break
            thread_id = handle.thread_id
            await self._save(result_id, status="running", run_id=handle.run_id, thread_id=handle.thread_id)
            run_status, error, output = await self._wait(handle)
            tokens_in += handle.usage["input_tokens"]
            tokens_out += handle.usage["output_tokens"]
            cost += float(handle.usage["cost_usd"] or 0)
            trajectory += trajectory_of(handle.events)
            transcript.append({"role": "assistant", "content": simulation.reply_of(output)})
            if run_status != "success":
                ended = "error"
                break
            if turn == sim.max_turns - 1:
                break
            try:
                following = await simulation.next_message(sim, goal, transcript, complete)
            except Exception as exc:  # noqa: BLE001 - the workflow did nothing wrong; the conversation is cut short
                error, ended = f"the simulated user failed: {type(exc).__name__}: {exc}"[:500], "simulator_error"
                break
            if following is None:
                ended = "goal"
                break
            message = following
        final = {k: v for k, v in output.items() if k != "messages"}
        final.update(transcript=transcript, turns=sum(t["role"] == "user" for t in transcript), ended=ended)
        await self._scored(
            result_id, wf, example, specs, run_status=run_status, error=error, output=final,
            trajectory=trajectory, usage=(tokens_in, tokens_out, round(cost, 6)), started=started,
        )  # fmt: skip
