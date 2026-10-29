"""Saved test cases (FR-EVAL-01, M6-T9): replay an input against a workflow and check what it produced.

Assertions: the final status, exact values of selected output channels, and the route (the set of steps that
ran — changing a router condition changes it). Cases may carry pinned outputs and mock model replies so they run
deterministically without model calls. Test runs are ordinary runs with `origin="test"`: they never trigger the
project's event handlers.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import TestCase
from agentcanvas.db.repositories import WorkflowRepo
from agentcanvas.errors import NotFound
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow

CASE_TIMEOUT_S = 120.0
CONCURRENCY = 4


class Expect(BaseModel):
    status: Literal["success", "interrupted", "error"] = "success"
    channels: dict[str, Any] = Field(default_factory=dict, description="exact final values of these channels")
    route: list[str] | None = Field(default=None, description="node ids that must run (and no others)")


class CaseResult(BaseModel):
    id: str
    name: str
    passed: bool
    failures: list[str] = Field(default_factory=list)
    run_id: str | None = None
    thread_id: str | None = None
    duration_ms: int = 0
    status: str | None = None  # actual final status
    route: list[str] = Field(default_factory=list)  # actual steps, in order


def check(expect: Expect, status: str, error: str | None, output: dict[str, Any], route: list[str]) -> list[str]:
    failures = []
    if status != expect.status:
        failures.append(f"status: expected {expect.status}, got {status}" + (f" ({error})" if error else ""))
    for name, want in expect.channels.items():
        if name not in output:
            failures.append(f"channel '{name}': expected {want!r}, but the run did not produce it")
        elif output[name] != want:
            failures.append(f"channel '{name}': expected {want!r}, got {output[name]!r}")
    if expect.route is not None:
        missing = [n for n in expect.route if n not in route]
        extra = [n for n in route if n not in expect.route]
        if missing or extra:
            parts = ([f"did not run {', '.join(missing)}"] if missing else []) + (
                [f"also ran {', '.join(extra)}"] if extra else []
            )
            failures.append("route: " + "; ".join(parts))
    return failures


class TestCaseService:
    __test__ = False  # not a pytest class

    def __init__(self, session: AsyncSession):
        self.s = session
        self.workflows = WorkflowRepo(session)

    async def list(self, wf_id: str) -> list[TestCase]:
        await self.workflows.require(wf_id)
        q = select(TestCase).where(TestCase.workflow_id == wf_id).order_by(TestCase.id)
        return list((await self.s.scalars(q)).all())

    async def add(
        self,
        wf_id: str,
        *,
        name: str,
        input: dict[str, Any],
        expect: Expect,
        context: dict[str, Any] | None = None,
        mocks: dict[str, Any] | None = None,
        pins: dict[str, Any] | None = None,
        created_by: str | None = None,
    ) -> TestCase:
        await self.workflows.require(wf_id)
        row = TestCase(
            id=new_id("tc"),
            workflow_id=wf_id,
            name=name,
            input=input,
            context=context,
            mocks=mocks or {},
            pins=pins or {},
            expect=expect.model_dump(mode="json"),
            created_by=created_by,
        )
        self.s.add(row)
        await self.s.commit()
        await self.s.refresh(row)
        return row

    async def delete(self, wf_id: str, case_id: str) -> None:
        row = await self.s.get(TestCase, case_id)
        if row is None or row.workflow_id != wf_id:
            raise NotFound(f"test case '{case_id}' not found")
        await self.s.delete(row)
        await self.s.commit()

    async def run(
        self,
        runner: Any,
        wf: Workflow,
        cases: list[TestCase],
        context_for: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        user_id: str | None = None,
        record: bool = True,
    ) -> list[CaseResult]:
        """Run the cases against `wf` (the draft or a version); stores each result on its case when `record`.

        `context_for(case.context)` builds the run context (identity fields come from the caller, not the case)."""
        gate = asyncio.Semaphore(CONCURRENCY)

        async def one(case: TestCase) -> CaseResult:
            try:
                context = context_for(case.context or {})
            except Exception as exc:  # noqa: BLE001 - e.g. a required context field the case lacks
                return CaseResult(id=case.id, name=case.name, passed=False, failures=[f"context: {exc}"])
            async with gate:
                return await run_case(runner, wf, case, context, user_id)

        await self.s.commit()  # a case can run for minutes: hold no connection meanwhile (the next query reopens one)
        results = list(await asyncio.gather(*(one(c) for c in cases)))
        if record:
            for case, res in zip(cases, results, strict=True):
                case.last_result = res.model_dump(mode="json")
            await self.s.commit()
        return results


async def run_case(
    runner: Any, wf: Workflow, case: TestCase, context: dict[str, Any], user_id: str | None
) -> CaseResult:
    res = CaseResult(id=case.id, name=case.name, passed=False)
    started = time.perf_counter()
    debug = bool(case.pins or case.mocks)
    try:
        handle = await runner.start(
            wf,
            thread_id=new_id("th"),
            input=case.input,
            context=context,
            mode="debug" if debug else "normal",
            pins=case.pins or None,
            mocks=case.mocks or None,
            user_id=user_id,
            origin="test",
        )
    except Exception as exc:  # noqa: BLE001 - reported on the case
        res.failures = [f"could not start: {exc}"]
        return res
    res.run_id, res.thread_id = handle.run_id, handle.thread_id
    try:
        await asyncio.wait_for(handle.done.wait(), CASE_TIMEOUT_S)
    except TimeoutError:
        await runner.cancel(handle.run_id)
        res.failures = [f"timed out after {CASE_TIMEOUT_S:g} s"]
        return res
    res.duration_ms = round((time.perf_counter() - started) * 1000)
    status, error, output = "error", None, {}
    for ev in handle.events:
        if ev["type"] == "STEP_STARTED" and ev.get("canvas_node_id") and ev["canvas_node_id"] not in res.route:
            res.route.append(ev["canvas_node_id"])
        elif ev["type"] == "RUN_FINISHED":
            status, output = ev.get("status") or "success", ev.get("output") or {}
        elif ev["type"] == "RUN_ERROR":
            status, error = "error", ev.get("message")
    res.status = status
    res.failures = check(Expect.model_validate(case.expect), status, error, output, res.route)
    res.passed = not res.failures
    return res
