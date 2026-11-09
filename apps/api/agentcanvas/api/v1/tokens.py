"""Personal API tokens (G-13): Settings > You > API tokens, and the organisation's list for admins.

A token is shown once, when it is made; afterwards only its first characters are. Making a token needs a real
sign-in: a token cannot make another one (that would let a leaked token outlive its revocation). For the same reason a
token cannot make the SCIM token, invite or remove members, or change the groups-to-roles table (`refuse_token`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note
from agentcanvas.api.deps import CurrentUser, current_user, get_session, refuse_token
from agentcanvas.db.models import PersonalToken
from agentcanvas.errors import InvalidInput
from agentcanvas.services import org_settings
from agentcanvas.services.tokens import TokenService

router = APIRouter(tags=["tokens"])
Role = Literal["admin", "builder", "reviewer", "viewer"]


class TokenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="what the token is for, e.g. 'CI pipeline'")
    role: Role | None = Field(None, description="the most it may do; default: your role now")
    expires_in_days: int | None = Field(90, ge=1, le=3650, description="null: never expires")


class TokenOut(BaseModel):
    id: str
    name: str
    prefix: str = Field(description="the token's first characters, to tell tokens apart")
    role: Role
    user_id: str
    email: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class TokenCreated(TokenOut):
    token: str = Field(description="the token itself: shown this once, store it now")


def _out(t: PersonalToken, email: str) -> TokenOut:
    return TokenOut(
        id=t.id, name=t.name, prefix=t.prefix, role=t.role, user_id=t.user_id, email=email,  # type: ignore[arg-type]
        created_at=t.created_at, expires_at=t.expires_at, last_used_at=t.last_used_at, revoked_at=t.revoked_at,
    )  # fmt: skip


@router.get("/me/tokens", response_model=list[TokenOut])
async def my_tokens(
    user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[TokenOut]:
    """Your API tokens in this organisation, newest first (revoked ones too, marked)."""
    return [_out(t, e) for t, e in await TokenService(session).list(user.org_id, user.id)]


@router.post("/me/tokens", response_model=TokenCreated, status_code=201)
async def create_token(
    body: TokenIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> TokenCreated:
    """Make an API token that acts as you in this organisation (`Authorization: Bearer act_…`)."""
    refuse_token(user, "make an API token")
    cap = (await org_settings.of_org(session, request.app.state.settings, user.org_id)).max_token_days
    if cap and (body.expires_in_days is None or body.expires_in_days > cap):
        raise InvalidInput(f"your organisation allows API tokens of at most {cap} days: choose a shorter lifetime")
    row, value = await TokenService(session).create(
        user.org_id, user.id, name=body.name.strip() or "API token", role=body.role or user.role,
        holder_role=user.role, expires_in_days=body.expires_in_days,
    )  # fmt: skip
    note(request, target=f"token_id={row.id}", name=row.name, role=row.role, expires_at=str(row.expires_at))
    return TokenCreated(**_out(row, user.email).model_dump(), token=value)


@router.delete("/me/tokens/{token_id}", status_code=204)
async def revoke_my_token(
    token_id: str, user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> Response:
    """Revoke one of your tokens: it stops working at once."""
    await TokenService(session).revoke(user.org_id, token_id, user.id)
    return Response(status_code=204)


@router.get("/org/tokens", response_model=list[TokenOut])
async def org_tokens(
    user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> list[TokenOut]:
    """Every member's API tokens in this organisation (admins)."""
    return [_out(t, e) for t, e in await TokenService(session).list(user.org_id)]


@router.delete("/org/tokens/{token_id}", status_code=204)
async def revoke_org_token(
    token_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Revoke any member's token (admins), e.g. one that leaked."""
    row = await TokenService(session).revoke(user.org_id, token_id)
    note(request, owner=row.user_id)
    return Response(status_code=204)
