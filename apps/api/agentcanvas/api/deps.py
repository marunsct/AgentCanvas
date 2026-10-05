"""FastAPI dependencies: settings, database session, current user, preconditions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.repositories import DEV_ORG, DEV_USER, UserRepo
from agentcanvas.db.session import Database
from agentcanvas.errors import Forbidden, InvalidInput, PreconditionRequired
from agentcanvas.security.auth import Authenticator, bearer
from agentcanvas.settings import Settings


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.sessionmaker() as s:
        yield s


@dataclass
class CurrentUser:
    id: str
    org_id: str
    role: str
    email: str = ""
    memberships: dict[str, str] = field(default_factory=dict)  # org id -> role


def _authenticator(request: Request) -> Authenticator:
    auth = getattr(request.app.state, "authenticator", None)
    if auth is None:
        auth = request.app.state.authenticator = Authenticator(request.app.state.settings)
    return auth


async def current_user(
    request: Request,
    authorization: str | None = Header(None),
    org: str | None = Header(None, alias="X-Org-Id", description="organisation to act in (default: your first)"),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    """The signed-in user and the organisation this request acts in.

    `auth_mode = dev` (local work, tests): the seeded development admin. `oidc`: the bearer token's
    subject, provisioned on first sign-in; 401 without a valid token, 403 without a membership.
    """
    settings: Settings = request.app.state.settings
    if settings.auth_mode == "dev":
        return CurrentUser(id=DEV_USER, org_id=DEV_ORG, role="admin", memberships={DEV_ORG: "admin"})
    identity = await _authenticator(request).verify(bearer(authorization))
    repo = UserRepo(session)
    user = await repo.provision(identity.subject, identity.email, identity.name)
    memberships = await repo.memberships(user.id)
    if (
        not memberships
        and identity.email_verified
        and identity.email.lower() in {e.lower() for e in settings.bootstrap_admin_emails}
    ):
        memberships = await repo.bootstrap_admin(user.id, DEV_ORG)
    if not memberships:
        raise Forbidden("you are not a member of any organisation; ask an admin to invite you")
    org_id = org or sorted(memberships)[0]
    if org_id not in memberships:
        raise Forbidden(f"you are not a member of organisation '{org_id}'")
    return CurrentUser(id=user.id, org_id=org_id, role=memberships[org_id], email=user.email, memberships=memberships)


def if_match(if_match: str | None = Header(default=None, alias="If-Match")) -> int:
    """Parse `If-Match: "<version>"` (optimistic locking). Missing header -> 428."""
    if if_match is None:
        raise PreconditionRequired("send If-Match with the workflow version you edited (from ETag)")
    try:
        return int(if_match.strip().removeprefix("W/").strip('"'))
    except ValueError as exc:
        raise InvalidInput(f"If-Match must be a version number, got '{if_match}'") from exc
