"""What the Copilot can see and do with runs (S15): recent runs, what happened in one, test runs and reproductions.

Test runs and reproductions run the Copilot's *working copy* (the saved draft plus its pending proposal) as dry runs:
risky tools are stubbed, so testing a fix never sends an email or writes to an outside system. A reproduction
replays the original run's recorded model replies and tool outputs when it has them, so it shows whether the change
fixes that very run.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from agentcanvas.adapters.spi import ThreadStateAbility, require
from agentcanvas.db.models import Run, Thread, WorkflowVersion
from agentcanvas.errors import InvalidInput
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.services.explain import explain
from agentcanvas.services.run_search import RunFilters, when
from agentcanvas.services.secrets import hide_secrets
from agentcanvas.services.testcases import Expect, check
from agentcanvas.validation.help import run_error_help

RUN_TIMEOUT_S = 120.0
MAX_VALUE = 1500


def _short(value: Any) -> Any:
    text = json.dumps(value, default=str)
    return value if len(text) <= MAX_VALUE else text[: MAX_VALUE - 1] + "…"


STATUS_WORDS = {
    "failed": "error", "failure": "error", "errored": "error", "succeeded": "success", "ok": "success",
    "waiting": "interrupted", "paused": "interrupted", "stopped": "cancelled", "canceled": "cancelled",
}  # fmt: skip


@dataclass
class RunFinder:
    """`search_runs`: the project's runs (every workflow of it) by status, time, cost, workflow, label and topic. The
    model only turns the person's words into filters; the runs come from SQL and the run-search index."""

    service: Any  # services.run_search.RunSearchService
    project_id: str
    found: list[dict[str, Any]] = field(default_factory=list)  # the runs this turn's searches returned, first first
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)

    def filters(self, args: dict[str, Any], now: datetime) -> RunFilters:
        given = {k: v for k, v in args.items() if k in RunFilters.model_fields and v not in (None, "", [])}
        if isinstance(given.get("status"), str):
            word = given["status"].strip().lower()
            given["status"] = STATUS_WORDS.get(word, word)
        for key in ("since", "until"):
            if key in given:
                given[key] = when(str(given[key]), now)
        return RunFilters.model_validate(given)

    async def find(self, args: dict[str, Any]) -> dict[str, Any]:
        now = self.clock()
        try:
            f = self.filters(args, now)
            result = await self.service.find(self.project_id, f)
        except ValidationError as exc:
            errors = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])
            return {"ok": False, "error": f"filters are not valid: {errors}"}
        except InvalidInput as exc:
            return {"ok": False, "error": str(exc)}
        runs = [r.model_dump(mode="json", exclude_none=True) for r in result.runs]
        seen = {r["run_id"] for r in self.found}
        self.found.extend(r for r in runs if r["run_id"] not in seen)
        out: dict[str, Any] = {
            "ok": True, "now": now.isoformat(timespec="seconds"),
            "filters": f.model_dump(mode="json", exclude_none=True, exclude={"limit"}),
            "matched": result.matched, "cost_usd": result.cost_usd, "runs": runs,
        }  # fmt: skip
        if result.stale:
            out["note"] = f"{result.stale} runs are not in the search index yet, so `text` could not rank them"
        return out


@dataclass
class RunAccess:
    runner: Any
    sessionmaker: Any
    workflow: Workflow  # the saved draft (explanations of past runs use it)
    context_for: Callable[[Workflow, dict[str, Any]], dict[str, Any]]
    user_id: str | None = None

    def _secrets(self, thread_id: str) -> dict[str, str]:
        res = self.runner.resources(thread_id, self.workflow)
        return getattr(res, "_secrets", None) or {}

    async def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        async with self.sessionmaker() as s:
            q = (
                select(Run)
                .join(Thread, Thread.id == Run.thread_id)
                .where(Thread.workflow_id == self.workflow.id)
                .order_by(Run.started_at.desc())
                .limit(min(max(limit, 1), 25))
            )
            runs = list((await s.scalars(q)).all())
        return [
            {"run_id": r.id, "thread_id": r.thread_id, "status": r.status, "error": (r.error or "")[:300] or None,
             "started_at": r.started_at.isoformat() if r.started_at else None}
            for r in runs
        ]  # fmt: skip

    async def _run(self, run_id: str) -> Run | None:
        async with self.sessionmaker() as s:
            q = (
                select(Run)
                .join(Thread, Thread.id == Run.thread_id)
                .where(Run.id == run_id, Thread.workflow_id == self.workflow.id)
            )
            return await s.scalar(q)

    async def _ran(self, run: Run) -> Workflow:
        """What the run ran: a deployment's pinned version, else the draft."""
        if run.workflow_version_id:
            async with self.sessionmaker() as s:
                ver = await s.get(WorkflowVersion, run.workflow_version_id)
            if ver is not None:
                return Workflow.model_validate(ver.ir)
        return self.workflow

    async def describe(self, run_id: str) -> dict[str, Any]:
        """A past run of this workflow: status, error with plain help, input, and what each step did."""
        run = await self._run(run_id)
        if run is None:
            return {"ok": False, "error": f"no run '{run_id}' of this workflow; use list_runs"}
        rec = run.recording or {}
        steps: list[dict[str, Any]] = []
        try:
            wf = await self._ran(run)
            res = self.runner.resources(run.thread_id, wf)
            travel = require(self.runner.adapter_for(wf), "time_travel", ThreadStateAbility)
            history = await travel.history(wf, run.thread_id, res, 60, include_values=True)
            steps = [{"step": s["step"], "text": s["text"]} for s in explain(wf, history)]
        except Exception as exc:  # noqa: BLE001 - e.g. checkpoints removed by retention; the rest still helps
            steps = [{"step": 0, "text": f"step history unavailable: {type(exc).__name__}"}]
        out = {
            "ok": True, "run_id": run.id, "thread_id": run.thread_id, "status": run.status, "error": run.error,
            "help": run_error_help(run.error), "input": _short(rec.get("input")), "route": rec.get("route"),
            "output": _short(rec.get("output")), "steps": steps[-30:], "replayable": bool(rec.get("mocks")),
        }  # fmt: skip
        return hide_secrets(out, self._secrets(run.thread_id))

    async def test(
        self,
        wf: Workflow,
        input: dict[str, Any],
        *,
        mocks: dict[str, list[Any]] | None = None,
        expect: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run `wf` once as a dry run and report status, output, the steps that ran and any expectation failures."""
        try:
            ctx = self.context_for(wf, context or {})
        except Exception as exc:  # noqa: BLE001 - e.g. a required context field
            return {"ok": False, "error": f"context: {exc}"}
        thread_id = new_id("th")
        try:
            handle = await self.runner.start(
                wf, thread_id=thread_id, input=input, context=ctx, mode="debug" if mocks else "normal",
                mocks=mocks or None, dry_run=True, user_id=self.user_id, origin="test", priority="normal",
            )  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - e.g. the workflow has errors
            return {"ok": False, "error": f"could not start: {exc}"}
        # a person waits for it: it queues like their own runs, and the time limit counts from when it took its slot
        late = await handle.done_within(RUN_TIMEOUT_S, queue_s=RUN_TIMEOUT_S)
        if late is not None:
            await self.runner.cancel(handle.run_id)
            if late == "queue":
                return {"ok": False, "error": f"the test run waited {RUN_TIMEOUT_S:g} s for a free run slot (the "
                        "organisation's runs-at-once limit) and was stopped; try again later"}  # fmt: skip
            return {"ok": False, "error": f"the test run took longer than {RUN_TIMEOUT_S:g} s and was stopped"}
        status, error, output, route = "error", None, {}, []
        for ev in handle.events:
            if ev["type"] == "STEP_STARTED" and ev.get("canvas_node_id") and ev["canvas_node_id"] not in route:
                route.append(ev["canvas_node_id"])
            elif ev["type"] == "RUN_FINISHED":
                status, output = ev.get("status") or "success", ev.get("output") or {}
            elif ev["type"] == "RUN_ERROR":
                status, error = "error", ev.get("message")
        out: dict[str, Any] = {
            "ok": True, "run_id": handle.run_id, "thread_id": thread_id, "status": status, "error": error,
            "help": run_error_help(error), "output": _short(output), "route": route,
        }  # fmt: skip
        if expect:
            failures = check(Expect.model_validate(expect), status, error, output, route)
            out["passed"], out["failures"] = not failures, failures
        return hide_secrets(out, self._secrets(thread_id))

    async def recorded_replies(self, run_id: str) -> dict[str, Any] | None:
        """The model replies and tool outputs a past run recorded, by name ({} when it kept none); None when there
        is no such run."""
        run = await self._run(run_id)
        if run is None:
            return None
        mocks = (run.recording or {}).get("mocks")
        return mocks if isinstance(mocks, dict) else {}

    async def reproduce(self, wf: Workflow, run_id: str) -> dict[str, Any]:
        """Run `wf` on a past run's input, replaying its recorded replies and tool outputs when it has them."""
        run = await self._run(run_id)
        if run is None:
            return {"ok": False, "error": f"no run '{run_id}' of this workflow; use list_runs"}
        rec = run.recording or {}
        if not isinstance(rec.get("input"), dict):
            return {
                "ok": False,
                "error": "this run kept no input (only fresh runs are recorded), so it cannot be rerun",
            }
        result = await self.test(wf, rec["input"], mocks=rec.get("mocks") or None, context=run.context)
        result["original"] = {"status": run.status, "error": run.error}
        result["replayed"] = bool(rec.get("mocks"))
        return result
