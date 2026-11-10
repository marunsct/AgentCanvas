"""Publish (M6-T4): deployments of a pinned version, their API keys, and the public run endpoints.

Public endpoints authenticate with `Authorization: Bearer <deployment key>` and always run the deployment's
version — editing the draft never changes what callers get.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Header, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note, payload_hash
from agentcanvas.api.deps import CurrentUser, current_user, get_session, get_settings_dep
from agentcanvas.api.urls import api_url
from agentcanvas.api.v1.files import FileOut, file_out, file_service, read_upload
from agentcanvas.api.v1.gates import gate_for
from agentcanvas.api.v1.gitsync import GitSyncOut, sync_after_version
from agentcanvas.api.v1.org import ErasedEndUserOut, end_user_ref
from agentcanvas.api.v1.runs import SSE_HEADERS, _context, _sse, stop_run
from agentcanvas.api.v1.tools import refresh_pins
from agentcanvas.api.v1.versions import workspace_commit
from agentcanvas.api.v1.workflows import service
from agentcanvas.db.models import (
    Deployment,
    DeploymentKey,
    Organization,
    Project,
    Run,
    RunCallback,
    RunScore,
    Thread,
    WorkflowVersion,
)
from agentcanvas.errors import Conflict, Forbidden, InvalidInput, NotFound, Unauthorized
from agentcanvas.ids import new_id
from agentcanvas.ir.commands import UpdateResource
from agentcanvas.ir.contract import breaking_changes, contract
from agentcanvas.ir.models import Workflow
from agentcanvas.runtime import nesting
from agentcanvas.runtime.runner import RunHandle
from agentcanvas.runtime.types import ResumeSpec
from agentcanvas.services import annotation, approvers, callbacks, deployment_oauth, gates, networks
from agentcanvas.services.data_subjects import end_user_label
from agentcanvas.services.deployments import KEY_PREFIX, Check, DeploymentService
from agentcanvas.services.disclosure import AI_HEADER, GeneratedBy, marked
from agentcanvas.services.projects import PROJECT_OF
from agentcanvas.services.redeploy import thread_version
from agentcanvas.services.run_results import ResultRepo, RunResult, from_handle
from agentcanvas.services.testcases import TestCaseService
from agentcanvas.services.versions import VersionService
from agentcanvas.services.workflows import WorkflowService
from agentcanvas.settings import Settings
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
    tool_drift: dict[str, Any] | None = Field(
        None,
        description="app connector -> the tools its runs leave out because the server changed them since publishing "
        "(added, changed, outcome); cleared when the workflow is published again",
    )
    oauth: bool = Field(False, description="people may also sign in to its MCP and A2A endpoints with OAuth")


class PublishOut(BaseModel):
    deployment: DeploymentOut
    api_key: str | None = Field(
        None, description="shown once: when the deployment is created, or published again after an unpublish"
    )
    checklist: list[Check]
    git: GitSyncOut | None = Field(
        default=None, description="the pull request opened or updated for this version (G-14)"
    )


class DeploymentKeyOut(BaseModel):
    id: str
    name: str
    prefix: str = Field(description="the key's first characters, to tell it from the others")
    created_by: str | None = Field(description="who made it (none: the key the deployment had before E-8)")
    created_at: datetime
    expires_at: datetime | None = Field(description="none: never expires")
    last_used_at: datetime | None = Field(description="the last call made with it, to the minute")
    revoked_at: datetime | None
    active: bool = Field(description="works now: neither revoked nor expired")


class KeyIn(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    expires_in_days: int | None = Field(None, ge=1, le=3650, description="null: never expires")


class KeyCreated(DeploymentKeyOut):
    api_key: str = Field(description="the key itself: shown this once")


class RotateIn(BaseModel):
    overlap_hours: int = Field(
        0, ge=0, le=720, description="how long the old keys keep working, so callers can move over (0: they stop now)"
    )


class KeyOut(BaseModel):
    api_key: str = Field(description="the new key: shown this once")
    key: DeploymentKeyOut | None = Field(None, description="the new key's details")


def _dep(d: Deployment, v: WorkflowVersion, key_prefix: str | None) -> DeploymentOut:
    return DeploymentOut(
        id=d.id, environment=d.environment, version_id=v.id, semver=v.semver, key_prefix=key_prefix,
        active=d.active, created_at=d.created_at, endpoint=f"/v1/deployments/{d.id}/runs/wait",
        tool_drift=d.tool_drift, oauth=d.oauth is not None,
    )  # fmt: skip


def _key(k: DeploymentKey) -> DeploymentKeyOut:
    now = datetime.now(UTC)
    return DeploymentKeyOut(
        id=k.id, name=k.name, prefix=k.prefix, created_by=k.created_by, created_at=k.created_at,
        expires_at=k.expires_at, last_used_at=k.last_used_at, revoked_at=k.revoked_at,
        active=k.revoked_at is None and (k.expires_at is None or k.expires_at > now),
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
    project_id = view.row.project_id
    # connectors browsed before tool fingerprints were recorded get them now, so the version pins its tools (E-4)
    if refreshed := await refresh_pins(request, view.workflow, project_id):
        fill = [UpdateResource(id=rid, patch={"config": {"discovered": tools}}) for rid, tools in refreshed.items()]
        view, _ = await svc.apply_commands(wf_id, list(fill), view.row.version)
        note(request, pinned_tools_of=sorted(r.name for r in view.workflow.resources if r.id in refreshed))
    wf = view.workflow
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
        # someone waits to publish: the checklist's runs do not wait behind every person's run in the run queue
        return await tests.run(
            request.app.state.runner, wf, cases, lambda g: _context(wf, g, user), user_id=user.id, priority="normal"
        )

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
    dep, key = await deps.publish(view.row, body.environment, version, created_by=user.id)
    prefix = (await deps.newest_prefixes([dep.id])).get(dep.id)
    out = PublishOut(deployment=_dep(dep, version, prefix), api_key=key, checklist=checks)
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
    svc = DeploymentService(session)
    found = await svc.list(wf_id)
    prefixes = await svc.newest_prefixes([d.id for d, _ in found])
    return [_dep(d, v, prefixes.get(d.id)) for d, v in found]


@router.get("/deployments/{dep_id}/keys", response_model=list[DeploymentKeyOut])
async def list_deployment_keys(dep_id: str, session: AsyncSession = Depends(get_session)) -> list[DeploymentKeyOut]:
    """The deployment's API keys, newest first, with revoked and expired ones (builders)."""
    svc = DeploymentService(session)
    await svc.require(dep_id)
    return [_key(k) for k in await svc.keys(dep_id)]


@router.post("/deployments/{dep_id}/keys", response_model=KeyCreated, status_code=201)
async def create_deployment_key(
    dep_id: str,
    body: KeyIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> KeyCreated:
    """Another key for the deployment, next to the ones that work already (shown once). To replace a key without
    downtime: make a new one, move the callers to it, then revoke the old one."""
    row, value = await DeploymentService(session).add_key(dep_id, body.name, user.id, body.expires_in_days)
    note(request, key=row.id, prefix=row.prefix, name=row.name, expires_at=row.expires_at)
    return KeyCreated(**_key(row).model_dump(), api_key=value)


@router.delete("/deployments/{dep_id}/keys/{key_id}", status_code=204)
async def revoke_deployment_key(
    dep_id: str, key_id: str, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """Revoke a key: calls with it are refused at once, on every server."""
    row = await DeploymentService(session).revoke_key(dep_id, key_id)
    note(request, key=row.id, prefix=row.prefix, name=row.name)
    return Response(status_code=204)


@router.post("/deployments/{dep_id}/rotate-key", response_model=KeyOut)
async def rotate_key(
    dep_id: str,
    request: Request,
    body: RotateIn | None = None,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> KeyOut:
    """A new key; the deployment's other keys stop working at once, or after `overlap_hours`."""
    overlap = body.overlap_hours if body else 0
    row, value = await DeploymentService(session).rotate(dep_id, user.id, overlap)
    note(request, key=row.id, prefix=row.prefix, overlap_hours=overlap)
    return KeyOut(api_key=value, key=_key(row))


class CallbackSecretOut(BaseModel):
    callback_secret: str = Field(description="shown once: the receiver checks each callback's signature with it")


@router.post("/deployments/{dep_id}/callback-secret", response_model=CallbackSecretOut)
async def rotate_callback_secret(
    dep_id: str, request: Request, session: AsyncSession = Depends(get_session)
) -> CallbackSecretOut:
    """Create the secret that signs this deployment's run callbacks, or replace it (the old one stops signing at
    once, also for callbacks still waiting to be delivered). Runs can ask for a callback once it exists."""
    dep = await DeploymentService(session).require(dep_id)
    return CallbackSecretOut(callback_secret=await callbacks.set_secret(session, request.app.state.keyring, dep))


SCOPE = Annotated[str, StringConstraints(pattern=r"^[\x21\x23-\x5b\x5d-\x7e]{1,200}$")]  # RFC 6749 scope-token


class OAuthIn(BaseModel):
    enabled: bool = Field(description="people may sign in to the MCP and A2A endpoints; the keys keep working")
    issuer: str | None = Field(
        None, max_length=2000, description="the authorization server (its `iss`); none: the one people sign in with"
    )
    audience: str | None = Field(
        None, max_length=2000,
        description="also accepted as the token's audience besides the endpoint's own address (an identity provider "
        "that cannot issue tokens for an address, e.g. api://agentcanvas)",
    )  # fmt: skip
    scopes: list[SCOPE] = Field(
        default_factory=lambda: [deployment_oauth.DEFAULT_SCOPE], max_length=20,
        description="the scopes a token must have, every one (empty: none)",
    )  # fmt: skip


class OAuthOut(BaseModel):
    enabled: bool
    issuer: str | None = Field(description="the issuer set for this deployment (none: the server's)")
    authorization_server: str | None = Field(description="the issuer tokens must come from")
    audience: str | None
    scopes: list[str]
    mcp_resource: str = Field(description="the MCP endpoint's resource identifier: its tokens' audience")
    a2a_resource: str = Field(description="the A2A endpoint's resource identifier: its tokens' audience")
    mcp_metadata: str = Field(description="the MCP endpoint's protected resource metadata (RFC 9728)")
    a2a_metadata: str = Field(description="the A2A endpoint's protected resource metadata")


def oauth_resource(request: Request, route: str, dep_id: str) -> str:
    """The resource identifier of a deployment's MCP or A2A endpoint: its public address. Tokens are bound to it, so
    in production it must not come from the request's Host header (a token issued for another server, replayed with
    that server's name as Host, would match): AGENTCANVAS_PUBLIC_URL is required there."""
    s: Settings = request.app.state.settings
    if not s.public_url and (s.env in ("staging", "prod") or s.edition == "hosted"):
        raise Conflict("OAuth sign-in needs AGENTCANVAS_PUBLIC_URL: access tokens are bound to the public address")
    return api_url(request, route, dep_id=dep_id)


def _oauth_out(request: Request, dep: Deployment) -> OAuthOut:
    cfg = deployment_oauth.config(dep.oauth)
    mcp = api_url(request, "mcp_endpoint", dep_id=dep.id)
    a2a = api_url(request, "a2a_endpoint", dep_id=dep.id)
    settings = request.app.state.settings
    issuer = deployment_oauth.issuer_of(cfg, settings) if cfg else settings.oidc_issuer
    return OAuthOut(
        enabled=cfg is not None, issuer=cfg.issuer if cfg else None, authorization_server=issuer,
        audience=cfg.audience if cfg else None, scopes=cfg.scopes if cfg else [deployment_oauth.DEFAULT_SCOPE],
        mcp_resource=mcp, a2a_resource=a2a,
        mcp_metadata=deployment_oauth.metadata_url(mcp), a2a_metadata=deployment_oauth.metadata_url(a2a),
    )  # fmt: skip


def access_tokens(request: Request) -> deployment_oauth.AccessTokens:
    tokens = getattr(request.app.state, "access_tokens", None)
    if tokens is None:
        tokens = request.app.state.access_tokens = deployment_oauth.AccessTokens(request.app.state.settings)
    return tokens


@router.get("/deployments/{dep_id}/oauth", response_model=OAuthOut)
async def get_deployment_oauth(dep_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> OAuthOut:
    """Whether people may sign in to the deployment's MCP and A2A endpoints with OAuth, and the addresses a client
    and the identity provider need."""
    return _oauth_out(request, await DeploymentService(session).require(dep_id))


@router.put("/deployments/{dep_id}/oauth", response_model=OAuthOut)
async def put_deployment_oauth(
    dep_id: str,
    body: OAuthIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> OAuthOut:
    """Let people sign in to the deployment's MCP and A2A endpoints with an access token from the identity provider
    (OAuth 2.1, the MCP authorization spec), or stop it. The keys keep working either way. Turning it on checks that
    the issuer's signing keys can be loaded (502 otherwise)."""
    svc = DeploymentService(session)
    await svc.require(dep_id)
    if not body.enabled:
        dep = await svc.set_oauth(dep_id, None)
        note(request, enabled=False)
        return _oauth_out(request, dep)
    issuer = deployment_oauth.check_issuer_url(body.issuer) if body.issuer and body.issuer.strip() else None
    effective = issuer or settings.oidc_issuer
    if not effective:
        raise InvalidInput("name the issuer: this server has no sign-in issuer (AGENTCANVAS_OIDC_ISSUER) to use")
    if "offline_access" in body.scopes:  # a client asks for refresh tokens itself; it is not the resource's to require
        raise InvalidInput("offline_access is not a scope a deployment can require")
    oauth_resource(request, "mcp_endpoint", dep_id)  # refused in production without a public address
    await access_tokens(request).check(effective)
    audience = body.audience.strip() if body.audience and body.audience.strip() else None
    cfg = deployment_oauth.OAuthConfig(issuer=issuer, audience=audience, scopes=list(dict.fromkeys(body.scopes)))
    dep = await svc.set_oauth(dep_id, cfg.model_dump())
    note(request, enabled=True, issuer=effective, audience=audience, scopes=cfg.scopes)
    return _oauth_out(request, dep)


@router.delete("/deployments/{dep_id}", status_code=204)
async def unpublish(dep_id: str, session: AsyncSession = Depends(get_session)) -> Response:
    await DeploymentService(session).unpublish(dep_id)
    return Response(status_code=204)


# ------------------------------------------------------------------------------------------ public API ----
CALLBACK_HELP = (
    "an https address sent the run's result (a signed POST) once it ends; needs the deployment's callback secret"
)


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
    callback_url: str | None = Field(default=None, max_length=2000, description=CALLBACK_HELP)


class ApiResumeIn(BaseModel):
    value: Any = None
    interrupt_id: str | None = None
    callback_url: str | None = Field(default=None, max_length=2000, description=CALLBACK_HELP)


class ApiResult(BaseModel):
    run_id: str
    thread_id: str
    status: Literal["success", "interrupted", "error", "cancelled", "running"]
    output: dict[str, Any] | None = None
    interrupts: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    generated_by: GeneratedBy = Field(
        default_factory=GeneratedBy, description="marks the output as AI-generated (AI Act Art. 50)"
    )


async def _caller(
    request: Request, session: AsyncSession, dep_id: str, authorization: str | None, oauth: str | None = None
) -> tuple[Deployment, Workflow]:
    """The key's deployment and workflow, after counting the request against the deployment's and the project's
    rate limits (429 when over). The organisation's IP allow-list comes first, so a caller outside it cannot even
    learn whether a key is valid. `oauth`: the route (MCP or A2A) whose callers may also sign in with an access
    token when the deployment allows it (E-11)."""
    await networks.check_deployment(request, session, dep_id, "deployment key")
    svc = DeploymentService(session)
    dep = await session.get(Deployment, dep_id) if oauth else None
    cfg = deployment_oauth.config(dep.oauth) if dep is not None and dep.active else None
    if oauth and dep is not None and cfg is not None:
        dep, wf = await _signed_in(request, svc, dep, cfg, oauth, authorization)
    else:
        dep, wf = await svc.authenticate(dep_id, authorization)
    await count_call(request, session, dep, wf)
    return dep, wf


async def _signed_in(
    request: Request,
    svc: DeploymentService,
    dep: Deployment,
    cfg: deployment_oauth.OAuthConfig,
    route: str,
    authorization: str | None,
) -> tuple[Deployment, Workflow]:
    """A call of an endpoint that takes OAuth: a deployment key as always, else an access token for this endpoint.
    Every refusal names where to sign in (`WWW-Authenticate`, RFC 9728), so a client can find it on its own."""
    scheme, _, token = (authorization or "").partition(" ")
    token = token.strip() if scheme.lower() == "bearer" else ""
    try:
        resource = oauth_resource(request, route, dep.id)
    except Conflict as exc:  # the public address was unset since: keys still work, access tokens cannot be checked
        if token.startswith(KEY_PREFIX):
            return await svc.authenticate(dep.id, authorization)
        raise Unauthorized(exc.detail) from None
    meta = deployment_oauth.metadata_url(resource)
    ask = deployment_oauth.challenge(meta, cfg.scopes)
    if not token:
        raise Unauthorized("sign in: send Authorization: Bearer <access token or deployment key>", challenge=ask)
    if token.startswith(KEY_PREFIX):
        try:
            return await svc.authenticate(dep.id, authorization)
        except Unauthorized as exc:
            raise Unauthorized(exc.detail, challenge=ask) from None
    issuer = deployment_oauth.issuer_of(cfg, request.app.state.settings)
    if issuer is None:
        raise Unauthorized("this deployment's sign-in has no issuer: set one in its OAuth settings", challenge=ask)
    audiences = [resource, *([cfg.audience] if cfg.audience else [])]
    try:
        who = await access_tokens(request).verify(token, issuer=issuer, audiences=audiences, scopes=cfg.scopes)
    except deployment_oauth.InvalidToken as exc:
        bad = deployment_oauth.challenge(meta, cfg.scopes, "invalid_token", exc.detail)
        raise Unauthorized(exc.detail, challenge=bad) from None
    except deployment_oauth.InsufficientScope as exc:
        bad = deployment_oauth.challenge(meta, cfg.scopes, "insufficient_scope", exc.detail)
        raise Forbidden(exc.detail, challenge=bad) from None
    wf = await svc.pinned(dep)
    request.state.signed_in = who
    note(request, caller="oauth", end_user=who.user_id)
    return dep, wf


def signed_in(request: Request) -> deployment_oauth.SignedIn | None:
    """The person who signed in with OAuth to call the deployment (E-11); None for a key holder."""
    return getattr(request.state, "signed_in", None)


def bound_end_user(request: Request, dep: Deployment) -> str | None:
    """The end user a signed-in caller is: they may continue only their own conversations. None for a key holder,
    who acts for the deployment's owner and may continue any of its conversations."""
    who = signed_in(request)
    return end_user_label(dep.id, who.user_id) if who else None


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
    end_user = end_user_label(dep.id, body.user_id) if body.user_id else f"api:{dep.id}"
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
    if (who := signed_in(request)) is not None:  # a signed-in person is the run's end user, whatever the body says
        body = body.model_copy(update={"user_id": who.user_id})
    version_id = dep.version_id
    callback = await _callback_url(request, session, dep, body.callback_url)
    if body.thread_id:
        await own_thread(session, dep, body.thread_id, bound_end_user(request, dep))
        wf, version_id = await _continue_on(session, dep, wf, body.thread_id)
    h = await request.app.state.runner.start(
        wf, thread_id=body.thread_id, input=body.input, context=await _api_context(session, dep, wf, body),
        deployment_id=dep.id, version_id=version_id, labels={**body.metadata, **(labels or {})},
        caller_run_id=nesting.caller(request.headers.get(nesting.HEADER)),
        end_user=end_user_label(dep.id, body.user_id) if body.user_id else None,
    )  # fmt: skip
    await _add_callback(session, dep, h, callback)
    return h


async def _callback_url(request: Request, session: AsyncSession, dep: Deployment, url: str | None) -> str | None:
    """The run's callback address, checked before the run starts (422 when it may not be used)."""
    if url is None:
        return None
    await callbacks.require_secret(session, dep)
    return await callbacks.check_url(url, request.app.state.settings.edition == "hosted")


async def _add_callback(session: AsyncSession, dep: Deployment, h: RunHandle, url: str | None) -> None:
    if url is not None:
        session.add(RunCallback(run_id=h.run_id, deployment_id=dep.id, url=url))
        await session.commit()


async def own_thread(session: AsyncSession, dep: Deployment, thread_id: str, end_user: str | None = None) -> Thread:
    """A conversation this deployment started. Any other (unknown, another deployment's, another organisation's)
    is the same 404, so a key holder cannot learn which thread ids exist elsewhere (S-6). `end_user`: a signed-in
    caller's, who may continue only the conversations they started (another person's is the same 404)."""
    t = await session.scalar(select(Thread).where(Thread.id == thread_id, Thread.deployment_id == dep.id))
    if t is None or (end_user is not None and t.end_user != end_user):
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
    return api_result(from_handle(h))


def api_result(res: RunResult) -> ApiResult:
    return ApiResult(**asdict(res))


@public_router.post("/deployments/{dep_id}/runs/wait", response_model=ApiResult)
async def api_run_wait(
    dep_id: str,
    body: ApiRunIn,
    request: Request,
    response: Response,
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> ApiResult:
    """Run the published version and return its final state (or its pending interrupts). After 5 minutes it answers
    `status: running`; the result is then read with `GET .../runs/{run_id}`."""
    h = await _start(request, session, dep_id, body, authorization)
    response.headers.update(AI_HEADER)
    await session.close()  # no connection held while the run works
    return await _result(h)


async def key_caller(
    dep_id: str,
    request: Request,
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> tuple[Deployment, Workflow]:
    """The deployment and workflow of the request's key, as a dependency: the key is checked before the body is read."""
    return await _caller(request, session, dep_id, authorization)


@public_router.post("/deployments/{dep_id}/files", response_model=FileOut, status_code=201)
async def api_upload(
    request: Request,
    file: Annotated[UploadFile, File(description="an image, PDF, .docx or text file")],
    caller: tuple[Deployment, Workflow] = Depends(key_caller),
    session: AsyncSession = Depends(get_session),
) -> FileOut:
    """Upload a file for the workflow's file input, with the deployment's key (N-4): the answer is the reference to
    put in the run's input, instead of sending the bytes inline as base64. Its media types and the size limit are the
    ones of the file input; only a workflow that has one takes files."""
    from agentcanvas.db.models import WorkflowRow
    from agentcanvas.services.share import attach_shape

    dep, wf = caller
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
    """Run the published version, streaming canonical events (SSE); RUN_FINISHED carries `generated_by`."""
    h = await _start(request, session, dep_id, body, authorization)
    return StreamingResponse(
        _sse(marked(request.app.state.runner.subscribe(h.run_id))),
        media_type="text/event-stream",
        headers={**SSE_HEADERS, "x-run-id": h.run_id, "x-thread-id": h.thread_id, **AI_HEADER},
    )


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


@public_router.post("/deployments/{dep_id}/runs", response_model=ApiResult, status_code=202)
async def api_run_start(
    dep_id: str,
    body: ApiRunIn,
    request: Request,
    response: Response,
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> ApiResult:
    """Start a run of the published version and answer at once (202, `status: running`): read its result later with
    `GET .../runs/{run_id}` (its address is in `Location`), from any server."""
    h = await _start(request, session, dep_id, body, authorization)
    response.headers["Location"] = api_url(request, "api_run_get", dep_id=dep_id, run_id=h.run_id)
    return ApiResult(run_id=h.run_id, thread_id=h.thread_id, status="running")


@public_router.get("/deployments/{dep_id}/runs/{run_id}", response_model=ApiResult, name="api_run_get")
async def api_run_get(
    run_id: str,
    request: Request,
    dep: Deployment = Depends(_key_holder),
    session: AsyncSession = Depends(get_session),
) -> ApiResult:
    """A run of this deployment: `running` while it works (or waits for a run slot), then its output, the pauses it
    waits for, or its error. Any server answers; a run another deployment made is not found."""
    found = await ResultRepo(session, request.app.state.runner).of(run_id, dep.id)
    if found is None:
        raise NotFound(f"run '{run_id}' not found")
    return api_result(found)


class CancelOut(BaseModel):
    cancelled: bool = Field(description="the run was stopped, or will be")
    pending: bool = Field(description="it runs on another server, which stops it within a few seconds")


@public_router.post("/deployments/{dep_id}/runs/{run_id}/cancel", response_model=CancelOut, status_code=202)
async def api_run_cancel(
    run_id: str,
    request: Request,
    dep: Deployment = Depends(_key_holder),
    session: AsyncSession = Depends(get_session),
) -> CancelOut:
    """Stop a run of this deployment, wherever it runs. A run that already ended answers `cancelled: false`."""
    found = await ResultRepo(session, request.app.state.runner).of(run_id, dep.id)
    if found is None:
        raise NotFound(f"run '{run_id}' not found")
    # a run a restart stopped continues as another run: that one is stopped
    return CancelOut(**await stop_run(session, request.app.state.runner, found.run_id))


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


class ApiEndUserIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=500, description="the `user_id` you sent with their runs")


@public_router.post("/deployments/{dep_id}/end-users/erase", response_model=ErasedEndUserOut)
async def api_erase_end_user(
    body: ApiEndUserIn,
    request: Request,
    dep: Deployment = Depends(_key_holder),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Erase one of your end users (GDPR Art. 17), by the `user_id` you sent with their runs: their conversations on
    this deployment (with feedback), long-term memories and the files their runs used. Answers the counts; an
    end user with nothing left answers zeros."""
    from agentcanvas.db.repositories import ScopeRepo
    from agentcanvas.services.data_subjects import erase_end_user

    org_id = await ScopeRepo(session).org_of("deployment", dep.id) or ""
    counts = await erase_end_user(session, request.app.state.runner, org_id, body.user_id, dep.id)
    note(request, target=end_user_ref(body.user_id), **counts)
    return counts


@public_router.post("/deployments/{dep_id}/end-users/export")
async def api_export_end_user(
    body: ApiEndUserIn,
    request: Request,
    dep: Deployment = Depends(_key_holder),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """One of your end users' data as JSON (GDPR Art. 15 and 20), by the `user_id` you sent with their runs: their
    conversations on this deployment with each run's input, answer and steps, long-term memories and the files their
    runs used (names and sizes)."""
    from agentcanvas.api.v1.org import download
    from agentcanvas.db.repositories import ScopeRepo
    from agentcanvas.services.data_subjects import export_end_user

    org_id = await ScopeRepo(session).org_of("deployment", dep.id) or ""
    data = await export_end_user(session, request.app.state.runner, org_id, body.user_id, dep.id)
    note(request, target=end_user_ref(body.user_id), conversations=len(data["conversations"]))
    return download(data, "agentcanvas-end-user.json")


@public_router.post("/deployments/{dep_id}/threads/{thread_id}/resume", response_model=ApiResult)
async def api_resume(
    dep_id: str,
    thread_id: str,
    body: ApiResumeIn,
    request: Request,
    response: Response,
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_session),
) -> ApiResult:
    """Answer a pending interrupt (approval, input) of a conversation this deployment started."""
    dep, wf = await _caller(request, session, dep_id, authorization)
    response.headers.update(AI_HEADER)
    await own_thread(session, dep, thread_id)
    note(request, interrupt_id=body.interrupt_id, payload_sha256=payload_hash(body.value))
    await approvers.check_pending(session, thread_id, body.interrupt_id, approvers.CALLER)
    callback = await _callback_url(request, session, dep, body.callback_url)
    wf, version_id = await _continue_on(session, dep, wf, thread_id)
    h = await request.app.state.runner.start(
        wf, thread_id=thread_id, resume=ResumeSpec(body.value, body.interrupt_id),
        deployment_id=dep.id, version_id=version_id, caller_run_id=nesting.caller(request.headers.get(nesting.HEADER)),
    )  # fmt: skip
    await _add_callback(session, dep, h, callback)
    await session.close()
    return await _result(h)
