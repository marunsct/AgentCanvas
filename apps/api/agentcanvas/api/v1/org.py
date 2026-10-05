"""Members of the signed-in user's organisation and their roles (M7-T3). Changes need the admin role."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.repositories import MemberRepo

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
