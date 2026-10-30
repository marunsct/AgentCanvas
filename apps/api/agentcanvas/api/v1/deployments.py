"""Publish (M6-T4): deployments of a pinned version, their API keys, and the public run endpoints.

Public endpoints authenticate with `Authorization: Bearer <deployment key>` and always run the deployment's
version — editing the draft never changes what callers get.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Header, Request, Response, UploadFile
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note, payload_hash
from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.api.v1.files import FileOut, file_out, file_service, read_upload
from agentcanvas.api.v1.gates import gate_for
from agentcanvas.api.v1.gitsync import GitSyncOut, sync_after_version
from agentcanvas.api.v1.runs import _context, _respond
from agentcanvas.api.v1.versions import workspace_commit
from agentcanvas.api.v1.workflows import service
from agentcanvas.db.models import Deployment, Organization, Project, Run, RunScore, Thread, WorkflowVersion
from agentcanvas.errors import Conflict, Forbidden, InvalidInput, NotFound
from agentcanvas.ids import new_id
from agentcanvas.ir.contract import breaking_changes, contract
from agentcanvas.ir.models import Workflow
from agentcanvas.runtime.runner import RunHandle
from agentcanvas.runtime.types import ResumeSpec
from agentcanvas.services import annotation, gates
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
    override_reason: str | None = Field(
        None, max_length=500, description="admins: publish although the release gate fails (audited)"
    )


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
    api_key: str | None = Field(
        None, description="shown once: when the deployment is created, or published again after an unpublish"
    )
    checklist: list[Check]
    git: GitSyncOut | None = Field(
        default=None, description="the pull request opened or updated for this version (G-14)"
    )


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
    org = await session.get(Organization, user.org_id)
    if org is not None and org.environments and body.environment not in org.environments:
        raise InvalidInput(
            f"'{body.environment}' is not one of this organisation's environments ({', '.join(org.environments)}); "
            "an admin adds it in Settings > Organisation"
        )
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
    gate = await gate_for(session, wf_id, body.environment)
    commit = await workspace_commit(request, session, wf_id)
    if gate is not None and all(c.ok for c in checks):  # an evaluation is costly: only when nothing else failed
        live, evaluated = await _live(session, wf_id, body.environment), view.row.ir_hash
        await session.commit()  # an evaluation can take minutes: hold no connection meanwhile
        evaluation = await gates.evaluate(
            request.app.state.db, request.app.state.experiments, gate, wf, live, user.id, commit,
        )  # fmt: skip
        # what gets pinned must be exactly what was evaluated: the draft or workspace may have moved meanwhile
        await session.refresh(view.row)
        if view.row.ir_hash != evaluated or await workspace_commit(request, session, wf_id) != commit:
            raise Conflict("the workflow changed while it was being evaluated; publish again")
        if not evaluation.ok and body.override_reason:
            if user.role != "admin":
                raise Forbidden("only an admin can publish over a failed release gate")
            evaluation.ok = True
            evaluation.detail = f"overridden by {user.id}: {body.override_reason} ({evaluation.detail})"
            note(request, gate_override=body.override_reason, gate_failures=evaluation.items)
        checks.append(evaluation)
    failed = [c for c in checks if not c.ok]
    if failed:
        raise InvalidInput(
            "publish refused: " + "; ".join(f"{c.name}: {c.detail}" for c in failed),
            checklist=[c.model_dump() for c in checks],
        )
    version, _ = await VersionService(session).create(
        wf_id, message=body.message or f"published to {body.environment}", workspace_commit=commit,
        created_by=user.id, reuse_unchanged=True,
    )  # fmt: skip
    dep, key = await deps.publish(view.row, body.environment, version)
    out = PublishOut(deployment=_dep(dep, version), api_key=key, checklist=checks)
    out.git = await sync_after_version(request, session, wf_id, version, "publish")  # G-14: never fails the publish
    return out


async def _live(session: AsyncSession, wf_id: str, environment: str) -> tuple[Workflow, str, str] | None:
    """The workflow, version id and semver `environment` serves now (None before its first publish)."""
    from sqlalchemy import select

    dep = await session.scalar(
        select(Deployment).where(Deployment.workflow_id == wf_id, Deployment.environment == environment)
    )
    live = await session.get(WorkflowVersion, dep.version_id) if dep and dep.active else None
    return (Workflow.model_validate(live.ir), live.id, live.semver) if live else None


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
    metadata: dict[str, Annotated[str, StringConstraints(max_length=200)]] = Field(
        default_factory=dict, max_length=20, description="labels for this run; online evaluators can filter on them"
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


async def _caller(
    request: Request, session: AsyncSession, dep_id: str, authorization: str | None
) -> tuple[Deployment, Workflow]:
    """The key's deployment and workflow, after counting the request against the deployment's and the project's
    rate limits (429 when over)."""
    dep, wf = await DeploymentService(session).authenticate(dep_id, authorization)
    await count_call(request, session, dep, wf)
    return dep, wf


async def count_call(
    request: Request, session: AsyncSession, dep: Deployment, wf: Workflow, *extra: tuple[str, int]
) -> None:
    """Count a call of the deployment (by key, MCP, A2A or share link) against its and its project's rate limits,
    and any `extra` (key, limit) buckets, e.g. a share link's per-client one (429 when over)."""
    from agentcanvas.db.models import WorkflowRow

    row = await session.get(WorkflowRow, dep.workflow_id)
    if row is not None:
        PROJECT_OF[wf.id] = row.project_id
    settings = await request.app.state.org_settings.for_project(
        request.app.state.settings, row.project_id if row else None
    )
    per_key = settings.rate_limit_by_environment.get(dep.environment, settings.rate_limit_per_deployment)
    await request.app.state.ratelimit.hit(
        (f"deployment:{dep.id}", per_key),
        (f"project:{row.project_id if row else dep.workflow_id}", settings.rate_limit_per_project),
        *extra,
    )


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


async def _start(
    request: Request,
    session: AsyncSession,
    dep_id: str,
    body: ApiRunIn,
    auth: str | None,
    caller: tuple[Deployment, Workflow] | None = None,
    labels: dict[str, str] | None = None,
) -> RunHandle:
    """Start a run of the deployment. `caller`: the deployment and workflow its key already unlocked (and counted
    against the rate limits) in this request."""
    dep, wf = caller or await _caller(request, session, dep_id, auth)
    version_id = dep.version_id
    if body.thread_id:
        await own_thread(session, dep, body.thread_id)
        wf, version_id = await _continue_on(session, dep, wf, body.thread_id)
    return await request.app.state.runner.start(
        wf, thread_id=body.thread_id, input=body.input, context=await _api_context(session, dep, wf, body),
        deployment_id=dep.id, version_id=version_id, labels={**body.metadata, **(labels or {})},
    )  # fmt: skip


async def own_thread(session: AsyncSession, dep: Deployment, thread_id: str) -> Thread:
    """A conversation this deployment started. Any other (unknown, another deployment's, another organisation's)
    is the same 404, so a key holder cannot learn which thread ids exist elsewhere (S-6)."""
    t = await session.scalar(select(Thread).where(Thread.id == thread_id, Thread.deployment_id == dep.id))
    if t is None:
        raise NotFound(f"thread '{thread_id}' not found for this deployment")
    return t


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


@public_router.post("/deployments/{dep_id}/files", response_model=FileOut, status_code=201)
async def api_upload(
    dep_id: str,
    request: Request,
    file: Annotated[UploadFile, File(description="an image, PDF, .docx or text file")],
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> FileOut:
    """Upload a file for the workflow's file input, with the deployment's key (N-4): the answer is the reference to
    put in the run's input, instead of sending the bytes inline as base64. Its media types and the size limit are the
    ones of the file input; only a workflow that has one takes files."""
    from agentcanvas.db.models import WorkflowRow
    from agentcanvas.services.share import attach_shape

    dep, wf = await _caller(request, session, dep_id, authorization)
    shape = attach_shape(wf)
    if shape is None:
        raise InvalidInput("this deployment's workflow has no file input")
    row = await session.get(WorkflowRow, dep.workflow_id)
    if row is None:
        raise NotFound("this deployment's workflow no longer exists")
    files = file_service(request, session)
    stored = await files.add(
        row.project_id, file.filename, await read_upload(file, files), source="api", deployment_id=dep.id,
        accept=shape.accept,
    )  # fmt: skip
    note(request, file=stored.id, media_type=stored.media_type, size=stored.size)
    return file_out(stored)


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


class FeedbackIn(BaseModel):
    rating: Literal["up", "down"]
    comment: str = Field(default="", max_length=2000)


async def _key_holder(
    dep_id: str,
    request: Request,
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> Deployment:
    """The deployment whose key the caller holds; a dependency, so a request without a key is refused (401) before
    its body is read."""
    return (await _caller(request, session, dep_id, authorization))[0]


@public_router.post("/deployments/{dep_id}/runs/{run_id}/feedback", status_code=204)
async def api_feedback(
    run_id: str,
    body: FeedbackIn,
    dep: Deployment = Depends(_key_holder),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Your end user's thumbs up or down on a run's answer (a later rating replaces the earlier one). A thumbs-down
    puts the run in the annotation queues that ask for them."""
    q = select(Run.thread_id).join(Thread, Thread.id == Run.thread_id)
    thread_id = await session.scalar(q.where(Run.id == run_id, Thread.deployment_id == dep.id))
    if thread_id is None:
        raise NotFound(f"run '{run_id}' not found")
    await session.execute(delete(RunScore).where(RunScore.run_id == run_id, RunScore.source == "feedback"))
    down = body.rating == "down"
    session.add(
        RunScore(
            id=new_id("rsc"),
            run_id=run_id,
            thread_id=thread_id,
            workflow_id=dep.workflow_id,
            deployment_id=dep.id,
            evaluator="user feedback",
            source="feedback",
            score=0.0 if down else 1.0,
            passed=not down,
            comment=body.comment,
            created_by="api",
        )
    )
    await session.commit()
    if down:
        await annotation.route(session, dep.workflow_id, run_id, thread_id, thumbs_down=True)
    return Response(status_code=204)


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
    dep, wf = await _caller(request, session, dep_id, authorization)
    await own_thread(session, dep, thread_id)
    note(request, interrupt_id=body.interrupt_id, payload_sha256=payload_hash(body.value))
    wf, version_id = await _continue_on(session, dep, wf, thread_id)
    h = await request.app.state.runner.start(
        wf, thread_id=thread_id, resume=ResumeSpec(body.value, body.interrupt_id),
        deployment_id=dep.id, version_id=version_id,
    )  # fmt: skip
    await session.close()
    return await _result(h)
