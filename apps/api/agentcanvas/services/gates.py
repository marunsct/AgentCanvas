"""Release gates (S12 FR-EVAL-05): publishing to an environment can require an evaluation to pass first.

A gate names a dataset and thresholds: a minimum mean score per evaluator (none named: every evaluator of the
workflow must reach its own pass threshold on average) and, optionally, the largest drop in pass rate allowed
against the version the environment serves now. At publish the exact workflow being published is evaluated on the
dataset's latest version. An experiment that already ran this exact workflow (same IR hash, dataset version and
evaluator configs) is reused; otherwise one runs now, with the live version as a second target when the
regression limit needs it. Admins can publish over a failed gate with a reason, which the audit log records.

A code evaluator's copy also pins the hash of the workspace code it runs (`code_hash`): a builder who rewrites that
code fails the gate instead of lowering its bar, until an admin saves the gate again.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import select

from agentcanvas.db.models import Evaluator, Experiment, ExperimentResult, ReleaseGate
from agentcanvas.errors import Forbidden
from agentcanvas.evaluation import (
    CODE_CHANGED,
    DEFAULT_THRESHOLD,
    ONLY_IN_EXPERIMENTS,
    Spec,
    code_hash,
    pinned_code,
    spec_of,
)
from agentcanvas.ir.canonical import ir_hash
from agentcanvas.ir.models import Workflow
from agentcanvas.services.datasets import DatasetService
from agentcanvas.services.deployments import Check
from agentcanvas.services.experiments import ExperimentService, TargetSummary, scorers, summary

GATE_TIMEOUT_S = 600.0


async def guard(
    s: Any, role: str, *, dataset_id: str | None = None, evaluator_id: str | None = None, delete: bool = False
) -> None:
    """Gates are admin-only, so a builder must not lower the bar through what they measure with (403).

    A gate keeps its own copy of its evaluators and pins its dataset version when an admin saves it (S13), so
    later edits cannot touch it; deleting its dataset would delete the gate, so that needs an admin. Gates saved
    before S13 have no copy and follow the live dataset and evaluators, which then need an admin to change."""
    if role == "admin":
        return
    q = select(ReleaseGate.environment).limit(1)
    if dataset_id is not None:
        q = q.where(ReleaseGate.dataset_id == dataset_id)
        if not delete:
            q = q.where(ReleaseGate.dataset_version.is_(None))
        what = f"dataset '{dataset_id}'"
    else:
        q = q.join(Evaluator, Evaluator.workflow_id == ReleaseGate.workflow_id).where(
            Evaluator.id == evaluator_id, ReleaseGate.evaluators.is_(None)
        )
        what = f"evaluator '{evaluator_id}'"
    if (environment := await s.scalar(q)) is not None:
        raise Forbidden(f"{what} is used by the '{environment}' release gate; only an admin can change it")


def _spec(e: Evaluator, siblings: list[Evaluator]) -> dict[str, Any]:
    return spec_of(e, siblings).model_dump()


def snapshot(evaluators: list[Evaluator], min_scores: dict[str, float], workspace: Path | None) -> list[dict[str, Any]]:
    """The evaluator specs a gate keeps: those it names, or every evaluator of the workflow. Code evaluators (also
    a composite's parts) pin the hash of the `workspace` code they run."""
    by_name = {e.name: e for e in evaluators}
    specs = [_spec(by_name[n], evaluators) for n in (list(min_scores) or list(by_name)) if n in by_name]
    for code in (c for x in specs for c in pinned_code(x)):
        code["config"]["pinned"] = code_hash(workspace, code["config"]["function"])
    return specs


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
        if gate.evaluators is not None:  # the copy taken when the gate was saved
            specs = list(gate.evaluators)
            names = [x["name"] for x in specs]
        else:  # a gate saved before S13: the workflow's evaluators as they are now
            evaluators = (await s.scalars(select(Evaluator).where(Evaluator.workflow_id == gate.workflow_id))).all()
            by_name = {e.name: e for e in evaluators}
            names = list(gate.min_scores) or list(by_name)
            if gone := [n for n in names if n not in by_name]:
                return Check(
                    name="evaluation", ok=False, detail=f"the gate names evaluators that no longer exist: {gone}"
                )
            specs = [_spec(by_name[n], list(evaluators)) for n in names]
        if not names:
            return Check(name="evaluation", ok=False, detail="the release gate needs at least one evaluator")
        fingerprint = scorers(job.runner, candidate)["fingerprint"]
        if changed := [
            x["name"]
            for x in specs
            for c in pinned_code(x)
            if c["config"].get("pinned") and fingerprint(c["config"]["function"]) != c["config"]["pinned"]
        ]:
            return Check(name="evaluation", ok=False, detail=f"evaluator(s) {sorted(set(changed))}: {CODE_CHANGED}")
        minimums = {
            n: gate.min_scores.get(
                n, x["threshold"] if x["threshold"] is not None else DEFAULT_THRESHOLD.get(x["kind"], 1.0)
            )
            for n, x in zip(names, specs, strict=True)
        }
        version = await DatasetService(s).version(gate.dataset_id, gate.dataset_version)
        number, examples = version.number, version.examples
        environment, max_regression = gate.environment, gate.max_regression
        regression = gate.max_regression is not None and live is not None
        # a pairwise judge compares the candidate with the live version in one experiment: nothing is reused
        pairwise = [x["name"] for x in specs if x["kind"] in ONLY_IN_EXPERIMENTS]
        paired = bool(pairwise) and live is not None
        found = None if paired else await _reuse(s, gate, number, specs, ir_hash(candidate), commit)
        before = (
            await _reuse(s, gate, number, specs, ir_hash(live[0]), commit)
            if regression and live and not paired
            else None
        )
        targets: list[dict[str, Any]] = []
        exp_id = ""
        if found is None:
            targets.append({"label": "candidate", "version": None, "version_id": None, "overrides": {},
                            "ir": candidate.model_dump(mode="json")})  # fmt: skip
        if (regression or paired) and live and before is None:
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
            await job.cancel(exp_id)  # the gate has failed: stop the experiment and its model spend with it
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
        if name in pairwise and not paired:
            notes.append(f"{name}: no live version to compare with")
            continue
        mean = next((e.mean for e in found.evaluators if e.evaluator == name), None)
        minimum = minimums[name]
        what = "win rate vs live" if name in pairwise else "mean"
        line = f"{name}: {what} {mean if mean is not None else '—'} (needs {minimum:g})"
        (failures if mean is None or mean < minimum else notes).append(line)
    if regression and before is not None and live is not None:
        now, was = found.pass_rate or 0.0, before.pass_rate or 0.0
        line = f"pass rate {now:.0%} vs {was:.0%} for v{live[2]} (may drop {max_regression:.0%})"
        (failures if was - now > (max_regression or 0) + 1e-9 else notes).append(line)
    detail = f"{found.completed} example(s) on dataset v{number}: " + (
        f"{len(failures)} threshold(s) missed" if failures else "every threshold met"
    )
    return Check(name="evaluation", ok=not failures, detail=detail, items=failures + notes)
