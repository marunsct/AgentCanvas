"""Workflow versions and saved test cases (M6-T1, M6-T9): snapshot, list, diff, restore; save and run test cases."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.adapters import get_adapter
from agentcanvas.api.deps import CurrentUser, current_user, get_session, if_match
from agentcanvas.api.v1.runs import _context
from agentcanvas.api.v1.workflows import WorkflowOut, _out, service
from agentcanvas.db.models import Run, TestCase, Thread, WorkflowVersion
from agentcanvas.db.repositories import WorkflowRepo
from agentcanvas.errors import InvalidInput, NotFound, WorkflowHasErrors
from agentcanvas.ir.contract import Contract, ContractChange, breaking_changes, contract
from agentcanvas.ir.diff import WorkflowDiff
from agentcanvas.ir.models import Workflow
from agentcanvas.runtime.types import REPLAYABLE
from agentcanvas.services.projects import PROJECT_OF
from agentcanvas.services.testcases import CaseResult, Expect, TestCaseService
from agentcanvas.services.versions import Bump, VersionService
from agentcanvas.services.workflows import WorkflowService
from agentcanvas.validation.policies import CODES as POLICY_CODES

router = APIRouter(tags=["versions"])


# ------------------------------------------------------------------------------------------------ versions ----
class VersionIn(BaseModel):
    message: str = Field("", max_length=500)
    bump: Bump = "minor"


class VersionOut(BaseModel):
    id: str
    semver: str
    message: str
    ir_hash: str
    workspace_commit: str | None
    created_by: str | None
    created_at: datetime


class VersionDetail(VersionOut):
    ir: dict[str, Any]
    layout: dict[str, Any]


class DiffOut(WorkflowDiff):
    a: str
    b: str
    summary: str


def _version(v: WorkflowVersion) -> VersionOut:
    return VersionOut.model_validate(v, from_attributes=True)


async def workspace_commit(request: Request, session: AsyncSession, wf_id: str) -> str | None:
    row = await WorkflowRepo(session).require(wf_id)
    return request.app.state.projects.workspace(row.project_id).head()


@router.post("/workflows/{wf_id}/versions", response_model=VersionOut, status_code=201)
async def create_version(
    wf_id: str,
    body: VersionIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> VersionOut:
    """Snapshot the draft IR, layout and workspace commit as the next semver (409 when nothing changed)."""
    commit = await workspace_commit(request, session, wf_id)
    v, _ = await VersionService(session).create(
        wf_id, message=body.message, bump=body.bump, workspace_commit=commit, created_by=user.id
    )
    return _version(v)


@router.get("/workflows/{wf_id}/versions", response_model=list[VersionOut])
async def list_versions(wf_id: str, session: AsyncSession = Depends(get_session)) -> list[VersionOut]:
    return [_version(v) for v in await VersionService(session).list(wf_id)]


@router.get("/workflows/{wf_id}/versions/{ref}", response_model=VersionDetail)
async def get_version(wf_id: str, ref: str, session: AsyncSession = Depends(get_session)) -> VersionDetail:
    return VersionDetail.model_validate(await VersionService(session).get(wf_id, ref), from_attributes=True)


@router.get("/workflows/{wf_id}/versions/{a}/diff/{b}", response_model=DiffOut)
async def diff_versions(wf_id: str, a: str, b: str, session: AsyncSession = Depends(get_session)) -> DiffOut:
    """What changed from `a` to `b` (version id, semver or `draft`): elements added/removed/changed, per field."""
    diff = await VersionService(session).diff(wf_id, a, b)
    return DiffOut(**diff.model_dump(), a=a, b=b, summary=diff.describe())


@router.post("/workflows/{wf_id}/versions/{ref}/restore", response_model=WorkflowOut)
async def restore_version(
    wf_id: str,
    ref: str,
    response: Response,
    version: int = Depends(if_match),
    session: AsyncSession = Depends(get_session),
    svc: WorkflowService = Depends(service),
) -> WorkflowOut:
    """Replace the draft by a version (an ordinary edit: the version itself never changes)."""
    v = await VersionService(session).get(wf_id, ref)
    await svc.replace(wf_id, {**v.ir, "id": wf_id}, version)
    await svc.repo.save_layout(wf_id, v.layout or {})
    return _out(await svc.get(wf_id), response)


ExportTarget = Literal["local", "server", "kubernetes", "cloudrun", "aws", "azure"]


async def _export_source(
    session: AsyncSession, svc: WorkflowService, wf_id: str, version: str | None
) -> tuple[Any, Workflow, str]:
    view = await svc.get(wf_id)
    if version is None:
        return view, view.workflow, "0.0.0"
    v = await VersionService(session).get(wf_id, version)
    return view, Workflow.model_validate(v.ir), v.semver


class ExportTargetOut(BaseModel):
    id: str
    label: str
    summary: str


class ExportPlanOut(BaseModel):
    targets: list[ExportTargetOut]
    prerequisites: dict[str, Any] = Field(description="what the chosen target needs: tools, keys, hosts, database")


@router.get("/workflows/{wf_id}/export/plan", response_model=ExportPlanOut)
async def export_plan(
    wf_id: str,
    target: ExportTarget = "local",
    version: str | None = None,
    session: AsyncSession = Depends(get_session),
    svc: WorkflowService = Depends(service),
) -> ExportPlanOut:
    """What deploying the export to `target` will need, before downloading it (S16)."""
    from agentcanvas.adapters.langchain import deploykit

    _, wf, semver = await _export_source(session, svc, wf_id, version)
    facts = deploykit.facts(wf, "workflow", semver)
    return ExportPlanOut(
        targets=[ExportTargetOut(id=t.id, label=t.label, summary=t.summary) for t in deploykit.TARGETS.values()],
        prerequisites=deploykit.prerequisites(facts, deploykit.TARGETS[target]),
    )


@router.get("/workflows/{wf_id}/export.zip", response_class=Response)
async def export_zip(
    wf_id: str,
    request: Request,
    version: str | None = None,
    target: ExportTarget = "local",
    session: AsyncSession = Depends(get_session),
    svc: WorkflowService = Depends(service),
) -> Response:
    """The workflow (draft, or `?version=`) as a deployable Python project with the deploy kit for `target`
    (M6-T2/T3, S16)."""
    view, wf, semver = await _export_source(session, svc, wf_id, version)
    cases = [
        {"name": c.name, "input": c.input, "context": c.context, "mocks": c.mocks, "pins": c.pins, "expect": c.expect}
        for c in await TestCaseService(session).list(wf_id)
    ]
    name, data = get_adapter(wf.framework).package(
        wf,
        svc.registry(view.row.project_id),
        workspace=request.app.state.projects.workspace(view.row.project_id).root,
        test_cases=cases,
        version=semver,
        target=target,
    )
    return Response(
        data, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


# ---------------------------------------------------------------------------------------------- test cases ----
class TestCaseIn(BaseModel):
    __test__ = False
    name: str = Field(min_length=1, max_length=200)
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] | None = None
    mocks: dict[str, list[Any]] = Field(default_factory=dict, description="model resource name -> replies")
    pins: dict[str, dict[str, Any]] = Field(default_factory=dict, description="node id -> pinned output")
    expect: Expect = Field(default_factory=Expect)


class TestCaseOut(TestCaseIn):
    id: str
    last_result: CaseResult | None = None
    created_at: datetime


class TestRunIn(BaseModel):
    __test__ = False
    ids: list[str] | None = Field(default=None, description="run only these cases (default: all)")
    version: str | None = Field(default=None, description="run against a version instead of the draft")


class TestRunOut(BaseModel):
    __test__ = False
    passed: int
    failed: int
    results: list[CaseResult]


def _case(c: TestCase) -> TestCaseOut:
    return TestCaseOut.model_validate(c, from_attributes=True)


@router.get("/workflows/{wf_id}/test-cases", response_model=list[TestCaseOut])
async def list_test_cases(wf_id: str, session: AsyncSession = Depends(get_session)) -> list[TestCaseOut]:
    return [_case(c) for c in await TestCaseService(session).list(wf_id)]


@router.post("/workflows/{wf_id}/test-cases", response_model=TestCaseOut, status_code=201)
async def add_test_case(
    wf_id: str,
    body: TestCaseIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> TestCaseOut:
    """Save an input and what the run must produce (typically from a finished run: "Save as test case")."""
    row = await TestCaseService(session).add(
        wf_id,
        name=body.name,
        input=body.input,
        expect=body.expect,
        context=body.context,
        mocks=body.mocks,
        pins=body.pins,
        created_by=user.id,
    )
    return _case(row)


@router.delete("/workflows/{wf_id}/test-cases/{case_id}", status_code=204)
async def delete_test_case(wf_id: str, case_id: str, session: AsyncSession = Depends(get_session)) -> Response:
    await TestCaseService(session).delete(wf_id, case_id)
    return Response(status_code=204)


@router.post("/workflows/{wf_id}/test-cases/run", response_model=TestRunOut)
async def run_test_cases(
    wf_id: str,
    body: TestRunIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    svc: WorkflowService = Depends(service),
    user: CurrentUser = Depends(current_user),
) -> TestRunOut:
    """Run the saved cases against the draft (or a version) and report pass/fail per case."""
    view = await svc.get(wf_id)  # also registers the workflow's project for workspace code
    wf = view.workflow if body.version is None else await VersionService(session).workflow_at(wf_id, body.version)
    PROJECT_OF[wf.id] = view.row.project_id
    diagnostics = await svc._validate(wf, view.row.project_id)
    # organisation policies are enforced at publish, not when trying the workflow out
    errors = [d.model_dump() for d in diagnostics if d.severity == "error" and d.code not in POLICY_CODES]
    if errors:
        raise WorkflowHasErrors(f"workflow has {len(errors)} error(s); fix them before running tests", errors=errors)
    tests = TestCaseService(session)
    cases = await tests.list(wf_id)
    if body.ids is not None:
        cases = [c for c in cases if c.id in set(body.ids)]
    results = await tests.run(
        request.app.state.runner,
        wf,
        cases,
        lambda given: _context(wf, given, user),
        user_id=user.id,
        record=body.version is None,
    )
    passed = sum(r.passed for r in results)
    return TestRunOut(passed=passed, failed=len(results) - passed, results=results)


class FromRunIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.post("/runs/{run_id}/test-case", response_model=TestCaseOut, status_code=201)
async def test_case_from_run(
    run_id: str,
    body: FromRunIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> TestCaseOut:
    """Record/replay (FR-X-13): a run (e.g. from production) becomes a regression test replaying its model replies."""
    run = await session.get(Run, run_id)
    if run is None:
        raise NotFound(f"run '{run_id}' not found")
    rec = run.recording
    if not rec or rec.get("status") not in REPLAYABLE:
        raise InvalidInput("this run has no recording (only fresh runs that succeeded or paused are recorded)")
    thread = await session.get(Thread, run.thread_id)
    assert thread is not None
    row = await WorkflowRepo(session).require(thread.workflow_id)
    chats = {c.name for c in Workflow.model_validate(row.draft_ir).state.channels if c.reducer == "add_messages"}
    expect = Expect(
        status=rec["status"],
        channels={k: v for k, v in (rec.get("output") or {}).items() if k not in chats},
        route=rec.get("route"),
    )
    case = await TestCaseService(session).add(
        row.id, name=body.name, input=rec["input"], expect=expect, mocks=rec.get("mocks") or {},
        context=run.context, created_by=user.id,
    )  # fmt: skip
    return _case(case)


# -------------------------------------------------------------------------------------- contract (FR-X-14) ----
class Surface(BaseModel):
    kind: str  # http | sse | webhook | cron | export
    detail: str


class ContractOut(Contract):
    surfaces: list[Surface] = Field(default_factory=list)


@router.get("/workflows/{wf_id}/contract", response_model=ContractOut)
async def get_contract(
    wf_id: str, version: str | None = None, session: AsyncSession = Depends(get_session)
) -> ContractOut:
    """What callers depend on: input/output fields, required context, pause points, and where it is exposed."""
    from agentcanvas.db.models import Deployment, Trigger

    wf = await VersionService(session).workflow_at(wf_id, version or "draft")
    out = ContractOut(**contract(wf).model_dump())
    deps = (await session.scalars(select(Deployment).where(Deployment.workflow_id == wf_id, Deployment.active))).all()
    for d in deps:
        v = await session.get(WorkflowVersion, d.version_id)
        out.surfaces.append(Surface(kind="http", detail=f"{d.environment} v{v.semver if v else '?'}: "
                                    f"POST /v1/deployments/{d.id}/runs/wait|stream (Bearer key)"))  # fmt: skip
    for t in (await session.scalars(select(Trigger).where(Trigger.workflow_id == wf_id, Trigger.enabled))).all():
        detail = f"POST /v1/hooks/{t.id} (HMAC signed)" if t.kind == "webhook" else f"cron {t.config.get('schedule')}"
        out.surfaces.append(Surface(kind=t.kind, detail=detail))
    out.surfaces.append(Surface(kind="export", detail=f"GET /v1/workflows/{wf_id}/export.zip (Python project)"))
    return out


@router.get("/workflows/{wf_id}/versions/{a}/contract-diff/{b}", response_model=list[ContractChange])
async def contract_diff(
    wf_id: str, a: str, b: str, session: AsyncSession = Depends(get_session)
) -> list[ContractChange]:
    """Contract changes from `a` to `b` (version or `draft`); `breaking` ones affect existing callers."""
    versions = VersionService(session)
    return breaking_changes(
        contract(await versions.workflow_at(wf_id, a)), contract(await versions.workflow_at(wf_id, b))
    )
