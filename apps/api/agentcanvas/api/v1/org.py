"""The signed-in user's organisation: members and roles (M7-T3), the audit log (M7-T4), workflow policies
(FR-X-19). Changes and the log need the admin role."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import null
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note
from agentcanvas.api.deps import CurrentUser, current_user, get_session, members_here
from agentcanvas.db.models import Organization
from agentcanvas.db.repositories import AuditRepo, MemberRepo, PolicyRepo
from agentcanvas.errors import Conflict, InvalidInput, NotFound
from agentcanvas.expr import ExpressionError
from agentcanvas.services import matura, org_settings, scim, tokens
from agentcanvas.services.org_settings import OrgLimits
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
    repo = MemberRepo(session)
    u, role = await repo.set_role(user.org_id, body.email, body.role)
    note(request, target=f"user_id={u.id}", email=u.email, role=role)
    accepted = next(m.accepted for v, m in await repo.list(user.org_id) if v.id == u.id)
    return _out(u, role, accepted)


@router.delete("/members/{user_id}", status_code=204, dependencies=[Depends(members_here)])
async def remove_member(
    user_id: str, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> Response:
    await MemberRepo(session).remove(user.org_id, user_id)
    return Response(status_code=204)


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


DEFAULT_ENVIRONMENTS = ["dev", "staging", "prod"]


class OrgLimitsOut(BaseModel):
    own: OrgLimits = Field(description="what this organisation set (null: the server's value applies)")
    server: dict[str, int | bool] = Field(description="the server's values: the default and, mostly, the ceiling")
    effective: dict[str, int | bool] = Field(description="what applies now")


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


async def _settings_out(session: AsyncSession, request: Request, org_id: str) -> OrgSettingsOut:
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
    )  # fmt: skip


@router.get("/settings", response_model=OrgSettingsOut)
async def get_org_settings(
    request: Request, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> OrgSettingsOut:
    """Organisation settings: its name, how long conversations are kept (M8-T4), and the server's other retentions."""
    return await _settings_out(session, request, user.org_id)


@router.put("/settings", response_model=OrgSettingsOut)
async def put_org_settings(
    body: OrgSettings,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> OrgSettingsOut:
    org = await session.get(Organization, user.org_id)
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
        merged = {**org_settings.stored(org.settings), **changes}
        org.settings = {k: v for k, v in merged.items() if v is not None} or None
        request.app.state.org_settings.forget()  # this replica reads the new values at once
    if "sso_groups" in body.model_fields_set:
        if request.app.state.settings.auth_mode != "oidc":
            raise Conflict("groups map to roles only when people sign in with OpenID Connect (AUTH_MODE=oidc)")
        org.sso_groups = body.sso_groups.model_dump() if body.sso_groups else null()
    await session.commit()
    await session.refresh(org)  # an attribute set to SQL NULL is reloaded
    note(request, target=f"org_id={user.org_id}", **body.model_dump(exclude_unset=True))
    return await _settings_out(session, request, user.org_id)


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
    """Make (or replace) the organisation's SCIM token. The old one stops working at once."""
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


class SpendingOut(BaseModel):
    since: datetime
    until: datetime
    total: SpendTotal
    by_project: list[SpendProject]
    by_workflow: list[SpendWorkflow]
    by_person: list[SpendPerson]


@router.get("/spending", response_model=SpendingOut)
async def org_spending(
    since: datetime | None = Query(None, description="runs started at or after this time (default: 30 days ago)"),
    until: datetime | None = Query(None, description="runs started before this time (default: now)"),
    limit: int = Query(100, ge=1, le=500, description="entries in each list, biggest spenders first"),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> dict[str, Any]:
    """What the organisation's runs spent in a period (UTC; a time without an offset is UTC): in total and per
    project, workflow and person (who started the conversation), with tokens and cache hits (G-8)."""
    until = _utc(until) if until else datetime.now(UTC)
    since = _utc(since) if since else until - timedelta(days=30)
    if since >= until:
        raise InvalidInput("'since' must be before 'until'")
    return await spending(session, user.org_id, since, until, limit=limit)


def _utc(when: datetime) -> datetime:
    return when.replace(tzinfo=UTC) if when.tzinfo is None else when.astimezone(UTC)
