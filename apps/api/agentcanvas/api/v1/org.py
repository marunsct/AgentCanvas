"""The signed-in user's organisation: members and roles (M7-T3), the audit log (M7-T4). Changes and the log
need the admin role."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.repositories import AuditRepo, MemberRepo

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
    body: MemberIn, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> MemberOut:
    """Invite someone by email or change their role. Invitations take effect at their first verified sign-in."""
    u, role = await MemberRepo(session).set_role(user.org_id, body.email, body.role)
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
