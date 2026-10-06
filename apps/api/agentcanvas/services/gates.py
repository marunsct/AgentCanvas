"""Release gates (S12 FR-EVAL-05): publishing to an environment can require an evaluation to pass first.

A gate names a dataset and thresholds: a minimum mean score per evaluator (none named: every evaluator of the
workflow must reach its own pass threshold on average) and, optionally, the largest drop in pass rate allowed
against the version the environment serves now. At publish the exact workflow being published is evaluated on the
dataset's latest version. An experiment that already ran this exact workflow (same IR hash, dataset version and
evaluator configs) is reused; otherwise one runs now, with the live version as a second target when the
regression limit needs it. Admins can publish over a failed gate with a reason, which the audit log records.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from agentcanvas.db.models import Evaluator, Experiment, ExperimentResult, ReleaseGate
from agentcanvas.errors import Forbidden
from agentcanvas.evaluation import DEFAULT_THRESHOLD, Spec
from agentcanvas.ir.canonical import ir_hash
from agentcanvas.ir.models import Workflow
from agentcanvas.services.datasets import DatasetService
from agentcanvas.services.deployments import Check
from agentcanvas.services.experiments import ExperimentService, TargetSummary, summary

GATE_TIMEOUT_S = 600.0


async def guard(s: Any, role: str, *, dataset_id: str | None = None, evaluator_id: str | None = None) -> None:
    """Gates are admin-only, so whatever they measure with is too: a builder must not lower the bar by deleting or
    editing a gating dataset or an evaluator of a gated workflow (403)."""
    if role == "admin":
        return
    q = select(ReleaseGate.environment).limit(1)
    if dataset_id is not None:
        q = q.where(ReleaseGate.dataset_id == dataset_id)
        what = f"dataset '{dataset_id}'"
    else:
        q = q.join(Evaluator, Evaluator.workflow_id == ReleaseGate.workflow_id).where(Evaluator.id == evaluator_id)
        what = f"evaluator '{evaluator_id}'"
    if (environment := await s.scalar(q)) is not None:
        raise Forbidden(f"{what} is used by the '{environment}' release gate; only an admin can change it")


def _spec(e: Evaluator) -> dict[str, Any]:
    return Spec(name=e.name, kind=e.kind, config=e.config, threshold=e.threshold).model_dump()  # type: ignore[arg-type]


async def _reuse(
    s: Any, gate: ReleaseGate, version: int, specs: list[dict], wanted: str, commit: str | None
) -> TargetSummary | None:
    """The newest finished experiment target that evaluated exactly `wanted` (an IR hash) with the workspace at
    `commit`, the same way."""
    if commit is None:  # without a workspace head nothing proves the code was the same
        return None
    q = (
        select(Experiment)
        .where(
            Experiment.workflow_id == gate.workflow_id, Experiment.dataset_id == gate.dataset_id,
            Experiment.dataset_version == version, Experiment.status == "done", Experiment.split.is_(None),
            Experiment.workspace_commit == commit,
        )
        .order_by(Experiment.id.desc())
        .limit(20)
    )  # fmt: skip
    for exp in (await s.scalars(q)).all():
        if any(spec not in exp.evaluators for spec in specs):
            continue
        for i, t in enumerate(exp.targets):
            if ir_hash(t["ir"]) == wanted:
                rows = (await s.scalars(select(ExperimentResult).where(ExperimentResult.experiment_id == exp.id))).all()
                return summary(exp, list(rows))[i]
    return None


async def evaluate(
    db: Any,
    job: Any,
    gate: ReleaseGate,
    candidate: Workflow,
    live: tuple[Workflow, str, str] | None,
    user_id: str | None,
    commit: str | None = None,
) -> Check:
    """The `evaluation` checklist item for publishing `candidate` (`live`: the served workflow, version id, semver;
    `commit`: the project's workspace head being published)."""
    async with db.sessionmaker() as s:
        evaluators = (await s.scalars(select(Evaluator).where(Evaluator.workflow_id == gate.workflow_id))).all()
        by_name = {e.name: e for e in evaluators}
        names = list(gate.min_scores) or list(by_name)
        if not names:
            return Check(name="evaluation", ok=False, detail="the release gate needs at least one evaluator")
        if gone := [n for n in names if n not in by_name]:
            return Check(name="evaluation", ok=False, detail=f"the gate names evaluators that no longer exist: {gone}")
        specs = [_spec(by_name[n]) for n in names]
        minimums = {
            n: gate.min_scores.get(
                n, x["threshold"] if x["threshold"] is not None else DEFAULT_THRESHOLD.get(x["kind"], 1.0)
            )
            for n, x in zip(names, specs, strict=True)
        }
        version = await DatasetService(s).version(gate.dataset_id)
        number, examples = version.number, version.examples
        environment, max_regression = gate.environment, gate.max_regression
        regression = gate.max_regression is not None and live is not None
        found = await _reuse(s, gate, number, specs, ir_hash(candidate), commit)
        before = await _reuse(s, gate, number, specs, ir_hash(live[0]), commit) if regression and live else None
        targets: list[dict[str, Any]] = []
        exp_id = ""
        if found is None:
            targets.append({"label": "candidate", "version": None, "version_id": None, "overrides": {},
                            "ir": candidate.model_dump(mode="json")})  # fmt: skip
        if regression and live and before is None:
            targets.append({"label": f"v{live[2]} (live)", "version": live[2], "version_id": live[1], "overrides": {},
                            "ir": live[0].model_dump(mode="json")})  # fmt: skip
        if targets:
            exp = await ExperimentService(s).create(
                gate.workflow_id, name=f"release gate: {environment}", dataset_id=gate.dataset_id,
                dataset_version=number, split=None, examples=examples, targets=targets,
                evaluators=[Spec(**x) for x in specs], concurrency=4, created_by=user_id, workspace_commit=commit,
            )  # fmt: skip
            exp_id = exp.id
    if targets:
        try:
            await asyncio.wait_for(asyncio.shield(job.launch(exp_id)), GATE_TIMEOUT_S)
        except TimeoutError:
            return Check(name="evaluation", ok=False, detail=f"the evaluation did not finish in {GATE_TIMEOUT_S:g} s")
        async with db.sessionmaker() as s:
            exp = await s.get(Experiment, exp_id)
            assert exp is not None
            if exp.status != "done":
                return Check(name="evaluation", ok=False, detail=f"the evaluation ended {exp.status}: {exp.error}")
            rows = (await s.scalars(select(ExperimentResult).where(ExperimentResult.experiment_id == exp_id))).all()
            fresh = summary(exp, list(rows))
        found = found or fresh[0]
        if regression and before is None:
            before = fresh[-1]
    assert found is not None
    failures, notes = [], []
    if found.completed < found.examples:
        failures.append(f"only {found.completed} of {found.examples} example(s) finished")
    for name in names:
        mean = next((e.mean for e in found.evaluators if e.evaluator == name), None)
        minimum = minimums[name]
        line = f"{name}: mean {mean if mean is not None else '—'} (needs {minimum:g})"
        (failures if mean is None or mean < minimum else notes).append(line)
    if regression and before is not None and live is not None:
        now, was = found.pass_rate or 0.0, before.pass_rate or 0.0
        line = f"pass rate {now:.0%} vs {was:.0%} for v{live[2]} (may drop {max_regression:.0%})"
        (failures if was - now > (max_regression or 0) + 1e-9 else notes).append(line)
    detail = f"{found.completed} example(s) on dataset v{number}: " + (
        f"{len(failures)} threshold(s) missed" if failures else "every threshold met"
    )
    return Check(name="evaluation", ok=not failures, detail=detail, items=failures + notes)
