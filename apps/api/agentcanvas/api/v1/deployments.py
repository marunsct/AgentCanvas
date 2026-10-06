"""Publish (M6-T4): deployments of a pinned version, their API keys, and the public run endpoints.

Public endpoints authenticate with `Authorization: Bearer <deployment key>` and always run the deployment's
version — editing the draft never changes what callers get.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note, payload_hash
from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.api.v1.runs import _context, _respond, _thread
from agentcanvas.api.v1.versions import workspace_commit
from agentcanvas.api.v1.workflows import service
from agentcanvas.db.models import Deployment, Project, WorkflowVersion
from agentcanvas.errors import InvalidInput
from agentcanvas.ir.contract import breaking_changes, contract
from agentcanvas.ir.models import Workflow
from agentcanvas.runtime.runner import RunHandle
from agentcanvas.runtime.types import ResumeSpec
from agentcanvas.services.deployments import Check, DeploymentService
from agentcanvas.services.projects import PROJECT_OF
from agentcanvas.services.redeploy import thread_version
from agentcanvas.services.testcases import TestCaseService
from agentcanvas.services.versions import VersionService
from agentcanvas.services.workflows import WorkflowService
from agentcanvas.validation.policies import CODES as POLICY_CODES

router = APIRouter(tags=["deployments"])
public_router = APIRouter(tags=["deployments"])  # deployment API keys, not user sign-in
WAIT_TIMEOUT_S = 300.0


class PublishIn(BaseModel):
    environment: str = Field("prod", pattern=r"^[a-z][a-z0-9-]{0,30}$")
    message: str = Field("", max_length=500)


class DeploymentOut(BaseModel):
    id: str
    environment: str
    version_id: str
    semver: str
    key_prefix: str | None
    active: bool
    created_at: datetime
    endpoint: str


class PublishOut(BaseModel):
    deployment: DeploymentOut
    api_key: str | None = Field(None, description="shown once, when the deployment is created")
    checklist: list[Check]


class KeyOut(BaseModel):
    api_key: str


def _dep(d: Deployment, v: WorkflowVersion) -> DeploymentOut:
    return DeploymentOut(
        id=d.id, environment=d.environment, version_id=v.id, semver=v.semver, key_prefix=d.key_prefix,
        active=d.active, created_at=d.created_at, endpoint=f"/v1/deployments/{d.id}/runs/wait",
    )  # fmt: skip


@router.post("/workflows/{wf_id}/publish", response_model=PublishOut)
async def publish(
    wf_id: str,
    body: PublishIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    svc: WorkflowService = Depends(service),
    user: CurrentUser = Depends(current_user),
) -> PublishOut:
    """Pre-publish checklist (validation, policies, secrets, test cases), then pin the draft as a version behind a
    key."""
    view = await svc.get(wf_id)
    wf, project_id = view.workflow, view.row.project_id
    # the organisation's policies as they apply to this environment (FR-X-19) are their own checklist item
    diagnostics = await svc._validate(wf, project_id, environment=body.environment)
    errors = [f"{d.code} {d.message}" for d in diagnostics if d.severity == "error" and d.code not in POLICY_CODES]
    broken = [f"{d.code} {d.message}" for d in diagnostics if d.severity == "error" and d.code in POLICY_CODES]
    projects = request.app.state.projects
    tests = TestCaseService(session)

    async def run_tests():
        cases = await tests.list(wf_id)
        return await tests.run(request.app.state.runner, wf, cases, lambda g: _context(wf, g, user), user_id=user.id)

    deps = DeploymentService(session)
    checks = await deps.checklist(wf, errors, set(projects.secrets.get(project_id, {})), run_tests, policies=broken)
    checks.append(await _contract_check(session, wf_id, body.environment, wf))
    failed = [c for c in checks if not c.ok]
    if failed:
        raise InvalidInput(
            "publish refused: " + "; ".join(f"{c.name}: {c.detail}" for c in failed),
            checklist=[c.model_dump() for c in checks],
        )
    commit = await workspace_commit(request, session, wf_id)
    version, _ = await VersionService(session).create(
        wf_id, message=body.message or f"published to {body.environment}", workspace_commit=commit,
        created_by=user.id, reuse_unchanged=True,
    )  # fmt: skip
    dep, key = await deps.publish(view.row, body.environment, version)
    return PublishOut(deployment=_dep(dep, version), api_key=key, checklist=checks)


async def _contract_check(session: AsyncSession, wf_id: str, environment: str, wf: Workflow) -> Check:
    """Informational (FR-X-14): breaking contract changes against what this environment serves now."""
    from sqlalchemy import select

    dep = await session.scalar(
        select(Deployment).where(Deployment.workflow_id == wf_id, Deployment.environment == environment)
    )
    live = await session.get(WorkflowVersion, dep.version_id) if dep and dep.active else None
    if live is None:
        return Check(name="contract", ok=True, detail="first publish of this environment")
    breaking = [
        c
        for c in breaking_changes(contract(Workflow.model_validate(live.ir)), contract(wf))
        if c.severity == "breaking"
    ]
    detail = f"{len(breaking)} breaking change(s) vs v{live.semver}" if breaking else f"compatible with v{live.semver}"
    return Check(name="contract", ok=True, detail=detail, items=[c.message for c in breaking])


@router.get("/workflows/{wf_id}/deployments", response_model=list[DeploymentOut])
async def list_deployments(wf_id: str, session: AsyncSession = Depends(get_session)) -> list[DeploymentOut]:
    return [_dep(d, v) for d, v in await DeploymentService(session).list(wf_id)]


@router.post("/deployments/{dep_id}/rotate-key", response_model=KeyOut)
async def rotate_key(dep_id: str, session: AsyncSession = Depends(get_session)) -> KeyOut:
    """A new key; the old one stops working immediately."""
    return KeyOut(api_key=await DeploymentService(session).rotate(dep_id))


@router.delete("/deployments/{dep_id}", status_code=204)
async def unpublish(dep_id: str, session: AsyncSession = Depends(get_session)) -> Response:
    await DeploymentService(session).unpublish(dep_id)
    return Response(status_code=204)


# ------------------------------------------------------------------------------------------ public API ----
class ApiRunIn(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    thread_id: str | None = Field(None, description="continue a conversation this deployment started")
    context: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = Field(
        None, description="your end user's id; context fields with source 'user' get api:<deployment>:<user_id>"
    )


class ApiResumeIn(BaseModel):
    value: Any = None
    interrupt_id: str | None = None


class ApiResult(BaseModel):
    run_id: str
    thread_id: str
    status: Literal["success", "interrupted", "error", "cancelled", "running"]
    output: dict[str, Any] | None = None
    interrupts: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


async def _caller(session: AsyncSession, dep_id: str, authorization: str | None) -> tuple[Deployment, Workflow]:
    dep, wf = await DeploymentService(session).authenticate(dep_id, authorization)
    from agentcanvas.db.models import WorkflowRow

    row = await session.get(WorkflowRow, dep.workflow_id)
    if row is not None:
        PROJECT_OF[wf.id] = row.project_id
    return dep, wf


async def _api_context(session: AsyncSession, dep: Deployment, wf: Workflow, body: ApiRunIn) -> dict[str, Any]:
    """Identity fields come from the key's deployment (tenant) and the caller's `user_id`, never from `context`.
    The caller's end users live under the deployment ("api:<deployment>:<user_id>"), so a key holder cannot
    pass an organisation member's id and reach that member's memories."""
    from agentcanvas.db.models import WorkflowRow

    row = await session.get(WorkflowRow, dep.workflow_id)
    project = await session.get(Project, row.project_id) if row else None
    end_user = f"api:{dep.id}:{body.user_id}" if body.user_id else f"api:{dep.id}"
    user = CurrentUser(id=end_user, org_id=project.org_id if project else "", role="api")
    return _context(wf, body.context, user)


async def _start(request: Request, session: AsyncSession, dep_id: str, body: ApiRunIn, auth: str | None) -> RunHandle:
    dep, wf = await _caller(session, dep_id, auth)
    version_id = dep.version_id
    if body.thread_id:
        t = await _thread(session, body.thread_id)
        if t.deployment_id != dep.id:
            raise InvalidInput(f"thread '{body.thread_id}' was not started by this deployment")
        wf, version_id = await _continue_on(session, dep, wf, body.thread_id)
    return await request.app.state.runner.start(
        wf, thread_id=body.thread_id, input=body.input, context=await _api_context(session, dep, wf, body),
        deployment_id=dep.id, version_id=version_id,
    )  # fmt: skip


async def _continue_on(session: AsyncSession, dep: Deployment, wf: Workflow, thread_id: str) -> tuple[Workflow, str]:
    """A conversation continues on the deployment's version only if that version can resume it (FR-X-10)."""
    current = await session.get(WorkflowVersion, dep.version_id)
    ver = await thread_version(session, thread_id, current) if current else None
    if ver is None or ver.id == dep.version_id:
        return wf, dep.version_id
    return Workflow.model_validate(ver.ir), ver.id


async def _result(h: RunHandle) -> ApiResult:
    try:
        await asyncio.wait_for(h.done.wait(), WAIT_TIMEOUT_S)
    except TimeoutError:
        return ApiResult(run_id=h.run_id, thread_id=h.thread_id, status="running")
    res = ApiResult(run_id=h.run_id, thread_id=h.thread_id, status="error")
    for ev in h.events:
        if ev["type"] == "RUN_FINISHED":
            res.status, res.output = ev.get("status") or "success", ev.get("output")
            res.interrupts = ev.get("interrupts") or []
        elif ev["type"] == "RUN_ERROR":
            # "redeploy": the server restarted mid-run; another instance continues the conversation (FR-X-10)
            code = ev.get("code")
            res.status = {"cancelled": "cancelled", "redeploy": "interrupted"}.get(code, "error")  # type: ignore[assignment]
            res.error = ev.get("message")
    return res


@public_router.post("/deployments/{dep_id}/runs/wait", response_model=ApiResult)
async def api_run_wait(
    dep_id: str,
    body: ApiRunIn,
    request: Request,
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> ApiResult:
    """Run the published version and return its final state (or its pending interrupts)."""
    h = await _start(request, session, dep_id, body, authorization)
    await session.close()  # no connection held while the run works
    return await _result(h)


@public_router.post("/deployments/{dep_id}/runs/stream")
async def api_run_stream(
    dep_id: str,
    body: ApiRunIn,
    request: Request,
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Run the published version, streaming canonical events (SSE)."""
    h = await _start(request, session, dep_id, body, authorization)
    return _respond(request.app.state.runner, h, True)


@public_router.post("/deployments/{dep_id}/threads/{thread_id}/resume", response_model=ApiResult)
async def api_resume(
    dep_id: str,
    thread_id: str,
    body: ApiResumeIn,
    request: Request,
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> ApiResult:
    """Answer a pending interrupt (approval, input) of a conversation this deployment started."""
    dep, wf = await _caller(session, dep_id, authorization)
    t = await _thread(session, thread_id)
    if t.deployment_id != dep.id:
        raise InvalidInput(f"thread '{thread_id}' was not started by this deployment")
    note(request, interrupt_id=body.interrupt_id, payload_sha256=payload_hash(body.value))
    wf, version_id = await _continue_on(session, dep, wf, thread_id)
    h = await request.app.state.runner.start(
        wf, thread_id=thread_id, resume=ResumeSpec(body.value, body.interrupt_id),
        deployment_id=dep.id, version_id=version_id,
    )  # fmt: skip
    await session.close()
    return await _result(h)
