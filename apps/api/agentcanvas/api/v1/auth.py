"""Sign-in configuration for the web app and the signed-in user (M7-T1)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from agentcanvas.api.deps import CurrentUser, current_user, get_settings_dep
from agentcanvas.settings import Settings

public_router = APIRouter(tags=["auth"])
router = APIRouter(tags=["auth"])


class AuthConfig(BaseModel):
    mode: Literal["dev", "oidc"]
    issuer: str | None = None
    client_id: str | None = None
    scopes: str | None = None
    audience: str | None = None


class MeOut(BaseModel):
    id: str
    email: str
    org_id: str
    role: str
    memberships: dict[str, str]


@public_router.get("/auth/config", response_model=AuthConfig)
def auth_config(settings: Settings = Depends(get_settings_dep)) -> AuthConfig:
    """How the web app signs in: `dev` (no sign-in) or `oidc` with the public client details."""
    if settings.auth_mode == "dev":
        return AuthConfig(mode="dev")
    return AuthConfig(
        mode="oidc", issuer=settings.oidc_issuer, client_id=settings.oidc_client_id,
        scopes=settings.oidc_scopes, audience=settings.oidc_audience,
    )  # fmt: skip


@router.get("/me", response_model=MeOut)
def me(user: CurrentUser = Depends(current_user)) -> MeOut:
    """The signed-in user, the organisation this request acts in and their role there."""
    return MeOut(id=user.id, email=user.email, org_id=user.org_id, role=user.role, memberships=user.memberships)
