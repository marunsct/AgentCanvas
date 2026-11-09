"""The signed-in user's organisation: members and roles (M7-T3), the audit log (M7-T4), workflow policies
(FR-X-19). Changes and the log need the admin role."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import null, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note, record_read
from agentcanvas.api.deps import CurrentUser, attachment, current_user, get_session, members_here, refuse_token
from agentcanvas.db.models import Organization, Project, SpendCapAlert
from agentcanvas.db.repositories import AuditRepo, MemberRepo, PolicyRepo
from agentcanvas.errors import Conflict, InvalidInput, NotFound
from agentcanvas.expr import ExpressionError
from agentcanvas.services import matura, mfa, networks, offboarding, org_settings, scim, tokens
from agentcanvas.services.org_settings import OrgLimits
from agentcanvas.services.spend_caps import periods, project_caps, spent_by_project
from agentcanvas.services.spending import spending
from agentcanvas.services.sso_groups import GroupMapping
from agentcanvas.validation.policies import check_policy

router = APIRouter(prefix="/org", tags=["organisation"])
Role = Literal["admin", "builder", "reviewer", "viewer"]


class MemberOut(BaseModel):
    user_id: str
    email: str
    display_name: str | None
    role: Role
    invited: bool = Field(description="has not signed in yet")
    accepted: bool = Field(True, description="false: belongs to another organisation and has not accepted yet")
    deactivated: bool = Field(False, description="suspended by the identity provider over SCIM (G-13)")


class MemberIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$", max_length=320)
    role: Role


def _out(u, role: str, accepted: bool = True, deactivated: bool = False) -> MemberOut:
    """Profile details (the name from their identity provider) only once they joined (S11)."""
    return MemberOut(
        user_id=u.id, email=u.email, display_name=u.display_name if accepted else None,
        role=role, invited=u.oidc_subject.startswith("pending:"), accepted=accepted, deactivated=deactivated,  # type: ignore[arg-type]
    )  # fmt: skip


@router.get("/members", response_model=list[MemberOut])
async def list_members(
    session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> list[MemberOut]:
    return [_out(u, m.role, m.accepted, m.suspended) for u, m in await MemberRepo(session).list(user.org_id)]


@router.put("/members", response_model=MemberOut, dependencies=[Depends(members_here)])
async def set_member(
    body: MemberIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> MemberOut:
    """Invite someone by email or change their role. Invitations take effect at their first verified sign-in."""
    refuse_token(user, "invite people or change roles")
    repo = MemberRepo(session)
    u, role = await repo.set_role(user.org_id, body.email, body.role)
    note(request, target=f"user_id={u.id}", email=u.email, role=role)
    accepted = next(m.accepted for v, m in await repo.list(user.org_id) if v.id == u.id)
    return _out(u, role, accepted)


@router.delete("/members/{user_id}", status_code=204, dependencies=[Depends(members_here)])
async def remove_member(
    user_id: str, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> Response:
    refuse_token(user, "remove members")
    await MemberRepo(session).remove(user.org_id, user_id)
    return Response(status_code=204)


class EraseMemberIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] = Field(
        description="why (the request you are answering), for the audit log"
    )
    conversations: bool = Field(
        False, description="also delete the conversations they started and the files they attached"
    )


class ErasedMemberOut(BaseModel):
    tokens: int = Field(description="API tokens revoked")
    connections: int = Field(description="app-connector sign-ins removed")
    memories: int = Field(description="long-term memories deleted (namespaces that carry their user id)")
    copilot_conversations: int
    conversations: int = Field(0, description="conversations deleted (with `conversations: true`)")
    files: int = Field(0, description="files they attached, deleted (with `conversations: true`)")
    account_erased: bool = Field(
        description="their email and name were removed; false when they still belong to another organisation"
    )


@router.post("/members/{user_id}/erase", response_model=ErasedMemberOut, dependencies=[Depends(members_here)])
async def erase_member(
    user_id: str,
    body: EraseMemberIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> dict[str, Any]:
    """Erase a member (GDPR Art. 17): remove them, revoke their API tokens and app-connector sign-ins, delete their
    long-term memories and Copilot chats, and, when asked, the conversations they started. Their account's email and
    name are removed when they belong to no other organisation; audit entries keep their id. Also for someone already
    removed who belongs nowhere else. Admins, with a real sign-in."""
    from agentcanvas.services import data_subjects

    refuse_token(user, "erase people")
    data_subjects.refuse_self(user.id, user_id)
    counts = await data_subjects.erase_member(
        session, request.app.state.runner, user.org_id, user_id, conversations=body.conversations
    )
    note(request, target=f"user_id={user_id}", reason=body.reason, **counts)
    return counts


class EndUserIn(BaseModel):
    user_id: str = Field(min_length=1, max_length=500, description="the `user_id` the deployment API was sent")
    deployment_id: str | None = Field(None, description="only this deployment's end user; null: every deployment")
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] = Field(
        description="why (the request you are answering), for the audit log"
    )


class ErasedEndUserOut(BaseModel):
    conversations: int
    memories: int = Field(description="long-term memories deleted")
    files: int = Field(description="files given to the deployment that their runs used")


def end_user_ref(user_id: str) -> str:
    """How the audit log names an end user: a hash, so the log does not keep the id an erasure removed."""
    return "end_user_sha256=" + hashlib.sha256(user_id.encode()).hexdigest()


@router.post("/end-users/erase", response_model=ErasedEndUserOut)
async def erase_end_user(
    body: EndUserIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> dict[str, int]:
    """Erase an end user of the organisation's deployments (GDPR Art. 17), by the `user_id` the deployment API was
    sent: their conversations (with checkpoints, feedback and search index), long-term memories and the files their
    runs used. Of one deployment, or of every one. Admins; a deployment's key can do the same for its own end users
    (`POST /v1/deployments/{id}/end-users/erase`)."""
    from agentcanvas.services import data_subjects

    counts = await data_subjects.erase_end_user(
        session, request.app.state.runner, user.org_id, body.user_id, body.deployment_id
    )
    note(request, target=end_user_ref(body.user_id), deployment_id=body.deployment_id, reason=body.reason, **counts)
    return counts


def download(data: dict[str, Any], filename: str) -> JSONResponse:
    return JSONResponse(jsonable_encoder(data), headers=attachment(filename))


@router.get("/members/{user_id}/export")
async def export_member(
    user_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> JSONResponse:
    """A member's data in this organisation as JSON (GDPR Art. 15 and 20): their account, membership, API tokens
    (never the secret), app-connector sign-ins, Copilot chats, the conversations they started with each run's input,
    answer and steps, their decisions, long-term memories, files and audit entries. Admins; audited. Also for
    someone already removed who belongs nowhere else."""
    from agentcanvas.services import data_subjects

    data = await data_subjects.export_member(
        session, request.app.state.runner, user.org_id, user_id, every_organisation=False
    )
    await record_read(request, "export_member", f"user_id={user_id}")
    return download(data, f"agentcanvas-{user_id}.json")


@router.post("/end-users/export")
async def export_end_user(
    body: EndUserIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> JSONResponse:
    """An end user's data as JSON (GDPR Art. 15 and 20), by the `user_id` the deployment API was sent: their
    conversations with each run's input, answer and steps, long-term memories and the files their runs used (names
    and sizes). Admins; audited (the log names them by a hash)."""
    from agentcanvas.services import data_subjects

    data = await data_subjects.export_end_user(
        session, request.app.state.runner, user.org_id, body.user_id, body.deployment_id
    )
    note(request, target=end_user_ref(body.user_id), deployment_id=body.deployment_id, reason=body.reason,
         conversations=len(data["conversations"]))  # fmt: skip
    return download(data, "agentcanvas-end-user.json")


class ExportOut(BaseModel):
    id: str
    status: Literal["queued", "building", "ready", "downloaded", "expired", "failed"]
    requested_by: str | None
    created_at: datetime
    finished_at: datetime | None
    expires_at: datetime | None = Field(description="when the archive is deleted (downloaded or not)")
    size: int | None = Field(description="bytes of the zip")
    sha256: str | None = Field(description="of the zip, to check the download")
    error: str | None
    downloaded_at: datetime | None


class ExportLinkOut(BaseModel):
    path: str = Field(description="GET it once, without signing in; behind the web server it is under /api")
    expires_at: datetime


def _export(e) -> ExportOut:
    return ExportOut(
        id=e.id, status=e.status, requested_by=e.requested_by, created_at=e.created_at, finished_at=e.finished_at,
        expires_at=e.expires_at, size=e.size, sha256=e.sha256, error=e.error, downloaded_at=e.downloaded_at,
    )  # fmt: skip


@router.post("/export", response_model=ExportOut, status_code=202)
async def create_org_export(
    request: Request, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> ExportOut:
    """Export everything the organisation holds (admins, with a real sign-in): a zip of NDJSON files (projects,
    workflows and versions, deployments without keys, conversations with their runs and events, memories, files,
    datasets, evaluations, the audit log...), the attached files and the workspaces. A background job builds it;
    poll `GET /v1/org/export/{id}` until it is `ready`, then make a download link. Also during a deletion's grace
    period."""
    from agentcanvas.services import org_export

    refuse_token(user, "export the organisation")
    row = await org_export.request_export(session, user.org_id, user.id)
    request.app.state.offboarding.wake()  # this server starts on it at once
    note(request, target=f"export_id={row.id}")
    return _export(row)


@router.get("/export", response_model=list[ExportOut])
async def list_org_exports(
    session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> list[ExportOut]:
    """The organisation's exports, newest first (the last 20)."""
    from agentcanvas.db.models import OrgExport

    q = select(OrgExport).where(OrgExport.org_id == user.org_id).order_by(OrgExport.created_at.desc()).limit(20)
    return [_export(e) for e in (await session.scalars(q)).all()]


@router.get("/export/{export_id}", response_model=ExportOut)
async def get_org_export(
    export_id: str, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> ExportOut:
    from agentcanvas.services import org_export

    return _export(await org_export.get_export(session, user.org_id, export_id))


@router.post("/export/{export_id}/link", response_model=ExportLinkOut)
async def create_org_export_link(
    export_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> ExportLinkOut:
    """A link that downloads the built export once, within 15 minutes, without signing in (so a browser or `curl`
    can fetch it). A new link replaces the one before. After the download the archive is deleted."""
    from agentcanvas.services import org_export

    refuse_token(user, "download the organisation's data")
    token, expires = await org_export.make_link(session, user.org_id, export_id)
    note(request, target=f"export_id={export_id}", link_expires_at=expires.isoformat())
    return ExportLinkOut(path=f"/v1/exports/{token}", expires_at=expires)


exports_router = APIRouter(tags=["organisation"])  # the download link: the token is the capability


@exports_router.get("/exports/{token}", responses={200: {"content": {"application/zip": {}}}})
async def download_org_export(token: str, request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    """Download an organisation export through its link, once."""
    from fastapi.responses import StreamingResponse

    from agentcanvas.db.models import AuditLog
    from agentcanvas.db.repositories import AuditRepo
    from agentcanvas.services import org_export
    from agentcanvas.services.audit_sink import AuditEvent

    row = await org_export.use_link(session, token)
    entry = AuditLog(
        org_id=row.org_id, actor_id=None, action="download_org_export", target=f"export_id={row.id}",
        detail={"caller": "export link", "size": row.size}, ip=request.client.host if request.client else None,
    )  # fmt: skip
    await AuditRepo(session).add(entry)
    if (sink := getattr(request.app.state, "audit_sink", None)) is not None:
        await sink.emit(AuditEvent(row.org_id, None, entry.action, entry.target, entry.detail, entry.ip))
    await session.close()
    headers = attachment(f"agentcanvas-{row.org_id}-{row.created_at:%Y%m%d}.zip")
    if row.size is not None:
        headers["Content-Length"] = str(row.size)
    return StreamingResponse(
        org_export.stream_parts(request.app.state.db, row.id), media_type="application/zip", headers=headers
    )


class StopRunsIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] = Field(
        description="why, for the audit log"
    )
    deployment_id: str | None = Field(None, description="only the runs of this deployment")
    workflow_id: str | None = Field(None, description="only the runs of this workflow (drafts and deployments)")


class StopRunsOut(BaseModel):
    stopping: list[str] = Field(description="runs in flight: each stops within one heartbeat (10 s) on its server")
    cancelled: list[str] = Field(description="runs that were waiting for another server to continue them")


@router.post("/runs/stop", response_model=StopRunsOut)
async def stop_all_runs(
    body: StopRunsIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> StopRunsOut:
    """The kill switch (admins): stop every run in flight in the organisation, or of one deployment or workflow.
    Audited with its reason. It does not refuse new runs; stop a deployment on its page for that."""
    from agentcanvas.services.kill_switch import stop_runs

    done = await stop_runs(session, request.app.state.runner, user.org_id, deployment_id=body.deployment_id,
                           workflow_id=body.workflow_id)  # fmt: skip
    scope = f"deployment_id={body.deployment_id}" if body.deployment_id else (
        f"wf_id={body.workflow_id}" if body.workflow_id else f"org_id={user.org_id}")  # fmt: skip
    note(request, target=scope, reason=body.reason, stopping=len(done.running), cancelled=len(done.handed_over))
    return StopRunsOut(stopping=done.running, cancelled=done.handed_over)


class DeleteOrgIn(BaseModel):
    confirm: str = Field(description="the organisation's name, typed exactly as it is shown")
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)] = Field(
        description="why, for the audit log"
    )


class DeletionOut(BaseModel):
    scheduled: bool = Field(description="true while the organisation waits to be deleted")
    suspended_at: datetime | None = Field(description="when it was suspended: nobody can use it since")
    delete_after: datetime | None = Field(description="when it is deleted for good; until then an admin can cancel")
    requested_by: str | None = Field(description="the admin who asked for it")


def _deletion(org: Organization) -> DeletionOut:
    return DeletionOut(
        scheduled=org.suspended_at is not None, suspended_at=org.suspended_at, delete_after=org.delete_after,
        requested_by=org.deletion_requested_by,
    )  # fmt: skip


@router.get("/deletion", response_model=DeletionOut)
async def organization_deletion(
    session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> DeletionOut:
    """Whether the organisation is waiting to be deleted, and when that happens (admins)."""
    org = await session.get(Organization, user.org_id)
    if org is None:
        raise NotFound(f"organisation '{user.org_id}' not found")
    return _deletion(org)


@router.delete("/deletion", response_model=DeletionOut)
async def cancel_organization_deletion(
    request: Request, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> DeletionOut:
    """Take the deletion back during its grace period: the organisation opens again as it was. Runs the deletion
    stopped stay stopped."""
    refuse_token(user, "take back the organisation's deletion")
    await offboarding.cancel_deletion(session, user.org_id)
    note(request, target=f"org_id={user.org_id}")
    org = await session.get(Organization, user.org_id, populate_existing=True)
    assert org is not None
    return _deletion(org)


org_router = APIRouter(tags=["organisation"])  # the organisation itself, at /org


@org_router.delete("/org", response_model=DeletionOut, status_code=202)
async def delete_organization(
    body: DeleteOrgIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> DeletionOut:
    """Delete the organisation with everything in it (admins, with a real sign-in). It is suspended at once: every
    sign-in, API token, deployment key, share link and run is refused, and the runs in flight stop. After the grace
    period (`AGENTCANVAS_ORG_DELETION_GRACE_DAYS`, 7 days by default) a background job deletes its data for good;
    until then an admin can take it back (`DELETE /v1/org/deletion`) and export the data."""
    refuse_token(user, "delete the organisation")
    settings = request.app.state.settings
    if settings.auth_mode == "matura":
        raise Conflict("organisations are removed" + matura.portal_hint(matura.portal_base(settings)))
    org = await session.get(Organization, user.org_id)
    if org is None:
        raise NotFound(f"organisation '{user.org_id}' not found")
    if body.confirm != org.name:
        raise InvalidInput(f"type the organisation's name exactly to confirm: '{org.name}'")
    org = await offboarding.request_deletion(session, user.org_id, user.id, settings.org_deletion_grace_days)
    out = _deletion(org)
    from agentcanvas.services.kill_switch import stop_runs

    done = await stop_runs(session, request.app.state.runner, user.org_id)
    when = org.delete_after.isoformat() if org.delete_after else None
    note(request, target=f"org_id={user.org_id}", reason=body.reason, delete_after=when, stopping=len(done.running),
         cancelled=len(done.handed_over))  # fmt: skip
    return out


class AuditOut(BaseModel):
    id: int
    created_at: datetime
    actor_id: str | None
    actor_email: str | None
    action: str
    target: str
    detail: dict[str, Any]
    ip: str | None


@router.get("/audit", response_model=list[AuditOut])
async def audit_log(
    action: str | None = Query(None, description="e.g. publish, decide, put_secret"),
    actor_id: str | None = None,
    target: str | None = Query(None, description="matches part of the target, e.g. a workflow id"),
    before: int | None = Query(None, ge=0, le=2**63 - 1, description="page: entries older than this id"),
    since: datetime | None = Query(None, description="entries at or after this time"),
    until: datetime | None = Query(None, description="entries before this time"),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> list[AuditOut]:
    """The organisation's audit log, newest first."""
    rows = await AuditRepo(session).list(
        user.org_id,
        action=action,
        actor_id=actor_id,
        target=target,
        before=before,
        limit=limit,
        since=since,
        until=until,
    )
    return [
        AuditOut(
            id=r.id,
            created_at=r.created_at,
            actor_id=r.actor_id,
            actor_email=email,
            action=r.action,
            target=r.target,
            detail=r.detail,
            ip=r.ip,
        )
        for r, email in rows
    ]


class PolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field("", max_length=1000, description="shown with every violation")
    scope: Literal["node", "resource", "workflow"] = Field(
        "node", description="what each check is about: every step, every resource, or the workflow once"
    )
    match: str = Field("true", max_length=2000, description="CEL: which elements the policy is about")
    require: str = Field(min_length=1, max_length=2000, description="CEL: what must hold for each of them")
    severity: Literal["error", "warning"] = "error"
    environments: list[str] = Field(
        default_factory=list, description="enforce only when publishing to these (empty: always, also in the editor)"
    )
    enabled: bool = True


class PolicyOut(PolicyIn):
    id: str
    created_at: datetime


def _policy(p) -> PolicyOut:
    return PolicyOut(
        id=p.id, name=p.name, description=p.description, scope=p.scope, match=p.match, require=p.require,
        severity=p.severity, environments=p.environments, enabled=p.enabled, created_at=p.created_at,
    )  # fmt: skip


def _checked(body: PolicyIn) -> dict[str, Any]:
    try:
        check_policy(body.match, body.require)
    except ExpressionError as exc:
        raise InvalidInput(str(exc)) from None
    return body.model_dump()


class OrgSettings(BaseModel):
    """Fields left out keep their value; `retention_days: null` returns to the server default."""

    name: str | None = Field(None, min_length=1, max_length=100, description="the organisation's name")
    retention_days: int | None = Field(
        None, ge=0, le=3650,
        description="days a conversation is kept after its last activity; null = the server default, 0 = forever",
    )  # fmt: skip
    environments: list[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,30}$")]] | None = Field(
        None, max_length=20, description="where workflows are published; null: any name (dev, staging, prod offered)"
    )
    limits: OrgLimits | None = Field(
        None, description="local MCP, rate limits, audit and memory retention: what is sent changes, null clears"
    )
    sso_groups: GroupMapping | None = Field(
        None, description="OIDC groups claim -> roles, applied at sign-in; null: roles are set by hand"
    )
    security: OrgSecurity | None = Field(None, description="the IP allow-list and required multi-factor sign-in")


DEFAULT_ENVIRONMENTS = ["dev", "staging", "prod"]


class OrgSecurity(BaseModel):
    """Who may act in the organisation, and from where; a field left out keeps its value."""

    allowed_networks: (
        list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]] | None
    ) = Field(
        None, max_length=networks.MAX_NETWORKS,
        description="the only networks (CIDR or addresses) requests may come from, for every sign-in, token, "
        "deployment key, share link and SCIM; [] or null: any address. Your own address must be on it",
    )  # fmt: skip
    require_mfa: bool | None = Field(
        None, description="members must sign in with multi-factor authentication; turning it on needs a sign-in "
        "that is multi-factor itself",
    )  # fmt: skip


class OrgSecurityOut(BaseModel):
    allowed_networks: list[str] = Field(description="[]: requests may come from any address")
    your_address: str | None = Field(description="the address this request came from, as the allow-list sees it")
    networks_ignored: bool = Field(description="the operator switched every allow-list off (an emergency)")
    require_mfa: bool = Field(description="members must sign in with multi-factor authentication")
    mfa_available: bool = Field(description="people sign in through an identity provider, so MFA can be required")
    your_sign_in_is_mfa: bool = Field(description="this request's sign-in counts as multi-factor on this server")


class OrgLimitsOut(BaseModel):
    own: OrgLimits = Field(description="what this organisation set (null: the server's value applies)")
    server: dict[str, int | float | bool] = Field(
        description="the server's values: the default and, mostly, the ceiling"
    )
    effective: dict[str, int | float | bool] = Field(description="what applies now")


class OrgSettingsOut(BaseModel):
    name: str
    name_editable: bool = Field(description="false in Matura mode, where organisations are named in Matura")
    retention_days: int | None
    effective_retention_days: int = Field(description="what applies now (0 = conversations are kept forever)")
    audit_retention_days: int = Field(description="the server's: audit-log rows older than this go (0 = kept)")
    memory_retention_days: int = Field(description="the server's: memories not updated for this long go (0 = kept)")
    environments: list[str] | None = Field(description="the organisation's list, or null when none is set")
    offered_environments: list[str] = Field(description="what the web app offers when publishing")
    limits: OrgLimitsOut = Field(description="the settings an organisation may override, and what applies")
    sso_groups: GroupMapping | None = Field(description="the groups claim -> roles table, or null")
    sso_groups_editable: bool = Field(description="false in Matura mode, where roles come from Matura")
    security: OrgSecurityOut


async def _settings_out(session: AsyncSession, request: Request, user: CurrentUser) -> OrgSettingsOut:
    org_id = user.org_id
    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound(f"organisation '{org_id}' not found")
    server = request.app.state.settings
    days = org.retention_days
    return OrgSettingsOut(
        name=org.name, name_editable=server.auth_mode != "matura", retention_days=days,
        effective_retention_days=server.retention_days if days is None else days,
        audit_retention_days=server.audit_retention_days, memory_retention_days=server.memory_retention_days,
        environments=org.environments, offered_environments=org.environments or DEFAULT_ENVIRONMENTS,
        limits=OrgLimitsOut(
            own=OrgLimits.model_validate(org_settings.stored(org.settings)),
            server={k: getattr(server, k) for k in org_settings.FIELDS},
            effective=org_settings.effective(server, org.settings),
        ),
        sso_groups=GroupMapping.model_validate(org.sso_groups) if org.sso_groups else None,
        sso_groups_editable=server.auth_mode == "oidc",
        security=OrgSecurityOut(
            allowed_networks=networks.of(org.settings), your_address=str(ip) if (ip := networks.client_ip(request))
            else None, networks_ignored=server.ignore_org_networks, require_mfa=mfa.org_requires(org.settings),
            mfa_available=server.auth_mode != "dev",
            your_sign_in_is_mfa=user.claims is not None and mfa.is_mfa(server, user.claims),
        ),
    )  # fmt: skip


@router.get("/settings", response_model=OrgSettingsOut)
async def get_org_settings(
    request: Request, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> OrgSettingsOut:
    """Organisation settings: its name, how long conversations are kept (M8-T4), and the server's other retentions."""
    return await _settings_out(session, request, user)


@router.put("/settings", response_model=OrgSettingsOut)
async def put_org_settings(
    body: OrgSettings,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> OrgSettingsOut:
    if "sso_groups" in body.model_fields_set:
        refuse_token(user, "change which groups give which roles")
    # read afresh and held until the commit: `settings` is merged, so a concurrent change must not be lost
    org = await session.get(Organization, user.org_id, with_for_update=True, populate_existing=True)
    if org is None:
        raise NotFound(f"organisation '{user.org_id}' not found")
    if "name" in body.model_fields_set:
        if request.app.state.settings.auth_mode == "matura":
            raise Conflict(
                "organisations are named in Matura" + matura.portal_hint(matura.portal_base(request.app.state.settings))
            )
        if not (body.name or "").strip():
            raise InvalidInput("an organisation needs a name")
        org.name = (body.name or "").strip()
    if "retention_days" in body.model_fields_set:
        org.retention_days = body.retention_days
    if "environments" in body.model_fields_set:
        names = list(dict.fromkeys(body.environments or []))
        org.environments = names or None
    if body.limits is not None:
        changes = body.limits.model_dump(include=body.limits.model_fields_set)
        org_settings.check(request.app.state.settings, changes)
        kept = {k: v for k, v in (org.settings or {}).items() if k not in org_settings.FIELDS}  # the security ones
        merged = {**kept, **org_settings.stored(org.settings), **changes}
        org.settings = {k: v for k, v in merged.items() if v is not None} or None
        request.app.state.org_settings.forget()  # this replica reads the new values at once
        request.app.state.spend_caps.forget()
    if body.security is not None and "allowed_networks" in body.security.model_fields_set:
        allowed = networks.parse(body.security.allowed_networks or [])
        here = networks.client_ip(request)
        if allowed and not networks.allows(allowed, here):  # the admin saving it would be refused at once
            raise InvalidInput(
                f"your own address ({here or 'unknown'}) is not on the list: saving it would lock you out. Add your "
                "network, or save it from a network on the list"
            )
        rest = {k: v for k, v in (org.settings or {}).items() if k != networks.KEY}
        org.settings = {**rest, networks.KEY: allowed} if allowed else rest or None
    if body.security is not None and "require_mfa" in body.security.model_fields_set:
        on = bool(body.security.require_mfa)
        if on:
            refuse_token(user, "require multi-factor sign-in")
            if request.app.state.settings.auth_mode == "dev":
                raise Conflict("multi-factor sign-in is required at an identity provider: this server has none")
            if user.claims is None or not mfa.is_mfa(request.app.state.settings, user.claims):
                raise InvalidInput(
                    "sign in with a second factor first: your sign-in does not count as multi-factor here, and "
                    "requiring it would lock you out (the operator sets what counts: AGENTCANVAS_OIDC_MFA_AMR, _ACR)"
                )
        elif mfa.org_requires(org.settings):
            refuse_token(user, "stop requiring multi-factor sign-in")
        rest = {k: v for k, v in (org.settings or {}).items() if k != mfa.KEY}
        org.settings = {**rest, mfa.KEY: True} if on else rest or None
    if "sso_groups" in body.model_fields_set:
        if request.app.state.settings.auth_mode != "oidc":
            raise Conflict("groups map to roles only when people sign in with OpenID Connect (AUTH_MODE=oidc)")
        org.sso_groups = body.sso_groups.model_dump() if body.sso_groups else null()
    await session.commit()
    await session.refresh(org)  # an attribute set to SQL NULL is reloaded
    note(request, target=f"org_id={user.org_id}", **body.model_dump(exclude_unset=True))
    return await _settings_out(session, request, user)


class ScimStatus(BaseModel):
    available: bool = Field(description="true when people sign in with OpenID Connect (not dev or Matura mode)")
    enabled: bool
    prefix: str | None = Field(description="the token's first characters, to tell it from another")
    created_at: datetime | None
    base_path: str = Field(default="/scim/v2", description="behind the web container: https://<host>/api/scim/v2")


class ScimCreated(ScimStatus):
    token: str = Field(description="the SCIM bearer token: shown this once, give it to the identity provider")


def _scim_status(org: Organization, settings) -> ScimStatus:
    return ScimStatus(
        available=settings.auth_mode == "oidc", enabled=bool(org.scim_token_hash), prefix=org.scim_token_prefix,
        created_at=org.scim_token_created_at,
    )  # fmt: skip


@router.get("/scim", response_model=ScimStatus)
async def scim_status(
    request: Request, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> ScimStatus:
    """Whether the identity provider may provision this organisation's members over SCIM 2.0 (admins)."""
    org = await session.get(Organization, user.org_id)
    if org is None:
        raise NotFound(f"organisation '{user.org_id}' not found")
    return _scim_status(org, request.app.state.settings)


@router.post("/scim/token", response_model=ScimCreated, status_code=201)
async def create_scim_token(
    request: Request, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> ScimCreated:
    """Make (or replace) the organisation's SCIM token. The old one stops working at once. Needs a real sign-in: the
    SCIM token belongs to the organisation and would outlive a revoked API token."""
    refuse_token(user, "make the SCIM token")
    settings = request.app.state.settings
    if settings.auth_mode == "matura":
        raise Conflict("members and roles are managed" + matura.portal_hint(matura.portal_base(settings)))
    if settings.auth_mode != "oidc":
        raise Conflict("SCIM provisions people who sign in with OpenID Connect (AUTH_MODE=oidc)")
    org = await session.get(Organization, user.org_id)
    if org is None:
        raise NotFound(f"organisation '{user.org_id}' not found")
    value = scim.new_token()
    org.scim_token_hash, org.scim_token_prefix = tokens.hash_token(value), value[:11]
    org.scim_token_created_at = datetime.now(UTC)
    await session.commit()
    note(request, target=f"org_id={user.org_id}", prefix=org.scim_token_prefix)
    return ScimCreated(**_scim_status(org, settings).model_dump(), token=value)  # base_path: its default


@router.delete("/scim/token", status_code=204)
async def delete_scim_token(
    request: Request, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> Response:
    """Switch SCIM off: the token stops working."""
    org = await session.get(Organization, user.org_id)
    if org is None:
        raise NotFound(f"organisation '{user.org_id}' not found")
    org.scim_token_hash = org.scim_token_prefix = org.scim_token_created_at = None
    await session.commit()
    note(request, target=f"org_id={user.org_id}")
    return Response(status_code=204)


@router.get("/policies", response_model=list[PolicyOut])
async def list_policies(
    session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> list[PolicyOut]:
    """The organisation's workflow policies, checked by the validator and when publishing."""
    return [_policy(p) for p in await PolicyRepo(session).list(user.org_id)]


@router.post("/policies", response_model=PolicyOut, status_code=201)
async def create_policy(
    body: PolicyIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> PolicyOut:
    p = await PolicyRepo(session).save(user.org_id, _checked(body), created_by=user.id)
    note(request, target=f"policy_id={p.id}", name=p.name)
    return _policy(p)


@router.put("/policies/{policy_id}", response_model=PolicyOut)
async def replace_policy(
    policy_id: str,
    body: PolicyIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> PolicyOut:
    p = await PolicyRepo(session).save(user.org_id, _checked(body), policy_id=policy_id)
    note(request, name=p.name, enabled=p.enabled)
    return _policy(p)


@router.delete("/policies/{policy_id}", status_code=204)
async def delete_policy(
    policy_id: str, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> Response:
    await PolicyRepo(session).delete(user.org_id, policy_id)
    return Response(status_code=204)


class SpendTotal(BaseModel):
    runs: int
    tokens_in: int
    tokens_out: int
    cost_usd: float
    cache_hits: int = Field(description="model answers taken from the answer cache")
    fallbacks: int = Field(description="model calls a backup model answered")


class SpendEntry(SpendTotal):
    id: str | None = Field(description="null: runs of no person")
    name: str


class SpendProject(SpendEntry):
    pass


class SpendWorkflow(SpendEntry):
    project_id: str


class SpendPerson(SpendEntry):
    email: str | None


class SpendDeployment(SpendEntry):
    workflow_id: str | None = Field(description="the deployed workflow; null: runs not made through a deployment")


class SpendOutsideRuns(BaseModel):
    kind: Literal["copilot", "judge"] = Field(description="the Copilot's answers, or AI judges scoring runs")
    calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float


class SpendingOut(BaseModel):
    since: datetime
    until: datetime
    total: SpendTotal = Field(description="runs only: model calls outside runs are in `outside_runs`")
    by_project: list[SpendProject]
    by_workflow: list[SpendWorkflow]
    by_person: list[SpendPerson]
    by_deployment: list[SpendDeployment]
    outside_runs: list[SpendOutsideRuns] = Field(
        description="model calls outside runs (the Copilot, AI judges), which the spending caps count too"
    )


@router.get("/spending", response_model=SpendingOut)
async def org_spending(
    since: datetime | None = Query(None, description="runs started at or after this time (default: 30 days ago)"),
    until: datetime | None = Query(None, description="runs started before this time (default: now)"),
    limit: int = Query(100, ge=1, le=500, description="entries in each list, biggest spenders first"),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> dict[str, Any]:
    """What the organisation's runs spent in a period (UTC; a time without an offset is UTC): in total and per
    project, workflow, person (who started the conversation) and deployment, with tokens and cache hits (G-8)."""
    until = _utc(until) if until else datetime.now(UTC)
    since = _utc(since) if since else until - timedelta(days=30)
    if since >= until:
        raise InvalidInput("'since' must be before 'until'")
    return await spending(session, user.org_id, since, until, limit=limit)


class CapUse(BaseModel):
    period: Literal["day", "month"] = Field(description="a UTC day or a calendar month (UTC)")
    period_start: str = Field(description='"2026-11-10" or "2026-11"')
    limit_usd: float | None = Field(description="the cap that applies; null: none")
    spent_usd: float = Field(description="what was spent in the period so far")


class ScopeCaps(BaseModel):
    id: str | None = Field(description="the project's id; null: the organisation")
    name: str
    own: dict[str, float | None] = Field(description="the caps this organisation or project set itself")
    day: CapUse
    month: CapUse


class SpendCapAlertOut(BaseModel):
    project_id: str | None = Field(description="null: the organisation's cap")
    period: str
    period_start: str
    share: float = Field(description="of the cap; 1: the cap was reached")
    spent_usd: float
    limit_usd: float
    message: str
    created_at: datetime


class SpendingCapsOut(BaseModel):
    warn_percent: int = Field(description="the share of a cap, in percent, at which an alert is raised")
    server: dict[str, float] = Field(description="the server's caps for every organisation (0: none)")
    organisation: ScopeCaps
    projects: list[ScopeCaps]
    alerts: list[SpendCapAlertOut] = Field(description="the latest alerts, newest first")


@router.get("/spending/caps", response_model=SpendingCapsOut)
async def spending_caps(
    request: Request, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> SpendingCapsOut:
    """The organisation's and its projects' spending caps, and what each spent today and this month (UTC): runs of
    every kind, what conversations deleted since had spent, the Copilot and AI judges."""
    org = await session.get(Organization, user.org_id)
    if org is None:
        raise NotFound(f"organisation '{user.org_id}' not found")
    server = request.app.state.settings
    eff = org_settings.effective(server, org.settings)
    own = org_settings.stored(org.settings)
    labels = {p: label for p, (label, _) in periods().items()}
    spent = await spent_by_project(session, user.org_id)

    def use(period: str, limit: float | None, value: float) -> CapUse:
        return CapUse(period=period, period_start=labels[period], limit_usd=limit or None, spent_usd=round(value, 6))  # type: ignore[arg-type]

    def scope(pid: str | None, name: str, caps: dict[str, float | None], day: float, month: float) -> ScopeCaps:
        return ScopeCaps(
            id=pid, name=name, own=caps,
            day=use("day", caps.get("max_cost_usd_day") if pid else eff["max_cost_usd_day"], day),
            month=use("month", caps.get("max_cost_usd_month") if pid else eff["max_cost_usd_month"], month),
        )  # fmt: skip

    projects = []
    for p in await session.scalars(select(Project).where(Project.org_id == user.org_id).order_by(Project.name)):
        d, m = spent.get(p.id, (0.0, 0.0))
        projects.append(scope(p.id, p.name, dict(project_caps(p.settings)), d, m))
    org_caps = {k: own.get(k) for k in ("max_cost_usd_day", "max_cost_usd_month")}
    total_day, total_month = sum(d for d, _ in spent.values()), sum(m for _, m in spent.values())
    alerts = await session.scalars(
        select(SpendCapAlert).where(SpendCapAlert.org_id == user.org_id)
        .order_by(SpendCapAlert.created_at.desc(), SpendCapAlert.id.desc()).limit(20)
    )  # fmt: skip
    return SpendingCapsOut(
        warn_percent=eff["cost_warn_percent"],
        server={"max_cost_usd_day": server.max_cost_usd_day, "max_cost_usd_month": server.max_cost_usd_month},
        organisation=scope(None, org.name, org_caps, total_day, total_month),
        projects=projects,
        alerts=[SpendCapAlertOut.model_validate(a, from_attributes=True) for a in alerts],
    )


def _utc(when: datetime) -> datetime:
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)
