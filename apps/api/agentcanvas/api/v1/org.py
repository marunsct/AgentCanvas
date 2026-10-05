"""The signed-in user's organisation: members and roles (M7-T3), the audit log (M7-T4), workflow policies
(FR-X-19). Changes and the log need the admin role."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note
from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.models import Organization
from agentcanvas.db.repositories import AuditRepo, MemberRepo, PolicyRepo
from agentcanvas.errors import InvalidInput, NotFound
from agentcanvas.expr import ExpressionError
from agentcanvas.validation.policies import check_policy

router = APIRouter(prefix="/org", tags=["organisation"])
Role = Literal["admin", "builder", "reviewer", "viewer"]


class MemberOut(BaseModel):
    user_id: str
    email: str
    display_name: str | None
    role: Role
    invited: bool = Field(description="has not signed in yet")


class MemberIn(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$", max_length=320)
    role: Role


def _out(u, role: str) -> MemberOut:
    return MemberOut(
        user_id=u.id, email=u.email, display_name=u.display_name, role=role,  # type: ignore[arg-type]
        invited=u.oidc_subject.startswith("pending:"),
    )  # fmt: skip


@router.get("/members", response_model=list[MemberOut])
async def list_members(
    session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> list[MemberOut]:
    return [_out(u, role) for u, role in await MemberRepo(session).list(user.org_id)]


@router.put("/members", response_model=MemberOut)
async def set_member(
    body: MemberIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> MemberOut:
    """Invite someone by email or change their role. Invitations take effect at their first verified sign-in."""
    u, role = await MemberRepo(session).set_role(user.org_id, body.email, body.role)
    note(request, target=f"user_id={u.id}", email=u.email, role=role)
    return _out(u, role)


@router.delete("/members/{user_id}", status_code=204)
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
    before: int | None = Query(None, description="page: entries older than this id"),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> list[AuditOut]:
    """The organisation's audit log, newest first."""
    rows = await AuditRepo(session).list(
        user.org_id, action=action, actor_id=actor_id, target=target, before=before, limit=limit
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
    retention_days: int | None = Field(
        None, ge=0, le=3650,
        description="days a conversation is kept after its last activity; null = the server default, 0 = forever",
    )  # fmt: skip


class OrgSettingsOut(OrgSettings):
    effective_retention_days: int = Field(description="what applies now (0 = conversations are kept forever)")


async def _settings_out(session: AsyncSession, request: Request, org_id: str) -> OrgSettingsOut:
    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound(f"organisation '{org_id}' not found")
    default = request.app.state.settings.retention_days
    days = org.retention_days
    return OrgSettingsOut(retention_days=days, effective_retention_days=default if days is None else days)


@router.get("/settings", response_model=OrgSettingsOut)
async def get_org_settings(
    request: Request, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> OrgSettingsOut:
    """Organisation settings: how long conversations are kept (M8-T4)."""
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
    org.retention_days = body.retention_days
    await session.commit()
    note(request, target=f"org_id={user.org_id}", retention_days=body.retention_days)
    return await _settings_out(session, request, user.org_id)


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
