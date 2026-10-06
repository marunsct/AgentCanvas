"""FastAPI dependencies: settings, database session, current user, preconditions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.repositories import DEV_ORG, DEV_USER, ScopeRepo, UserRepo
from agentcanvas.db.session import Database
from agentcanvas.errors import Forbidden, InvalidInput, NotFound, PreconditionRequired, Unauthorized
from agentcanvas.security.auth import Authenticator
from agentcanvas.settings import Settings


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.sessionmaker() as s:
        request.state.sessions = [*getattr(request.state, "sessions", []), s]
        yield s


async def release_sessions(request: Request) -> None:
    """Give the request's database connections back to the pool once the response starts: a stream can stay open
    for minutes and must not hold a connection meanwhile (M8-T5). A session used again simply reconnects."""
    for s in getattr(request.state, "sessions", []):
        await s.close()


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


# declared as a security scheme so OpenAPI clients and Swagger UI know how to authenticate; absence is allowed here
# (dev mode needs no token) and turned into 401 problem+json below
bearer_scheme = HTTPBearer(auto_error=False, description="the identity provider's access token (OIDC mode)")


async def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    org: str | None = Header(None, alias="X-Org-Id", description="organisation to act in (default: last used)"),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    """The signed-in user and the organisation this request acts in.

    `auth_mode = dev` (local work, tests): the seeded development admin. `oidc`: the bearer token's
    subject, provisioned on first sign-in; 401 without a valid token, 403 without a membership.
    """
    settings: Settings = request.app.state.settings
    if settings.auth_mode == "dev":
        return CurrentUser(id=DEV_USER, org_id=DEV_ORG, role="admin", memberships={DEV_ORG: "admin"})
    if credentials is None or not credentials.credentials:
        raise Unauthorized("sign in: send Authorization: Bearer <token>")
    identity = await _authenticator(request).verify(credentials.credentials)
    repo = UserRepo(session)
    # only a verified email is stored: invitations and the admin bootstrap match people by it
    email = identity.email if identity.email_verified else ""
    user, created = await repo.provision(identity.subject, email, identity.name, identity.email_verified)
    memberships = await repo.memberships(user.id)
    if (  # bootstrap happens once, at the first sign-in: removing a bootstrap admin later sticks
        created
        and not memberships
        and identity.email_verified
        and identity.email.lower() in {e.lower() for e in settings.bootstrap_admin_emails}
    ):
        memberships = await repo.bootstrap_admin(user.id, DEV_ORG)
    if not memberships and (invited := await repo.invitations(user.id)):
        # nowhere else to be: the first invitation needs no acceptance (it does when it would move them)
        await repo.answer(user.id, invited[0][0].id, accept=True)
        memberships = await repo.memberships(user.id)
    if not memberships:
        raise Forbidden("you are not a member of any organisation; ask an admin to invite you")
    default = next(iter(memberships))  # the organisation used last
    org_id = org or default
    if org_id not in memberships:
        raise Forbidden(f"you are not a member of organisation '{org_id}'")
    if org_id != default:
        await repo.used(user.id, org_id)  # choosing another organisation makes it the default
    return CurrentUser(id=user.id, org_id=org_id, role=memberships[org_id], email=user.email, memberships=memberships)


# path/query parameters that name a resource, and its kind (M7-T2)
SCOPED_PARAMS = {
    "project_id": "project",
    "wf_id": "workflow",
    "workflow_id": "workflow",
    "thread_id": "thread",
    "run_id": "run",
    "dep_id": "deployment",
    "trigger_id": "trigger",
    "interrupt_id": "interrupt",
    "ds_id": "dataset",
    "evaluator_id": "evaluator",
    "exp_id": "experiment",
    "oev_id": "online_evaluator",
    "aq_id": "annotation_queue",
    "item_id": "annotation_item",
}


async def org_scope(
    request: Request, user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> None:
    """Every resource a request names must belong to the caller's organisation.

    Another organisation's resource answers 404, exactly like a missing one, so ids cannot be probed.
    Runs on every signed-in route; list endpoints without an id filter by `user.org_id` themselves.
    """
    named = {**request.query_params, **request.path_params}
    repo = ScopeRepo(session)
    for param, kind in SCOPED_PARAMS.items():
        rid = named.get(param)
        if rid and await repo.org_of(kind, rid) != user.org_id:
            raise NotFound(f"{kind} '{rid}' not found")


# Roles (M7-T3), weakest first. viewer: read only. reviewer: read and answer inbox items. builder: build, run,
# publish. admin: everything, including members.
ROLES = ("viewer", "reviewer", "builder", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}
# requests that need something other than the default (reads: viewer; any other method: builder)
ROLE_EXCEPTIONS = {
    ("POST", "/workflows/{wf_id}/validate"): "viewer",  # read-only checks
    ("POST", "/expressions/check"): "viewer",
    ("POST", "/inbox/{interrupt_id}/decision"): "reviewer",
    ("POST", "/threads/{thread_id}/resume"): "reviewer",
    ("POST", "/annotation-queues/{aq_id}/items"): "reviewer",  # reviewers add runs by hand and review them (S13)
    ("POST", "/annotation-items/{item_id}/annotations"): "reviewer",
    ("POST", "/me/invitations/{org_id}/accept"): "viewer",  # anyone answers their own invitations
    ("DELETE", "/me/invitations/{org_id}"): "viewer",
    ("PUT", "/workflows/{wf_id}/gates/{environment}"): "admin",  # a builder must not lower the bar they publish over
    ("DELETE", "/workflows/{wf_id}/gates/{environment}"): "admin",
}


def required_role(method: str, path: str) -> str:
    """The weakest role allowed `method` on the route template `path` (with or without the /v1 prefix)."""
    path = path.removeprefix("/v1")
    if path.startswith("/org/audit"):
        return "admin"
    if path.startswith("/org/"):
        return "viewer" if method == "GET" else "admin"
    if (method, path) in ROLE_EXCEPTIONS:
        return ROLE_EXCEPTIONS[(method, path)]
    return "viewer" if method in ("GET", "HEAD", "OPTIONS") else "builder"


async def role_policy(request: Request, user: CurrentUser = Depends(current_user)) -> None:
    """Every signed-in route needs the role its method and path call for (403 otherwise)."""
    request.state.user = user  # the audit log's actor (M7-T4)
    route = request.scope.get("route")
    needed = required_role(request.method, getattr(route, "path", request.url.path))
    if RANK.get(user.role, -1) < RANK[needed]:
        raise Forbidden(f"your role '{user.role}' cannot do this; it needs '{needed}' or above")


def if_match(if_match: str | None = Header(default=None, alias="If-Match")) -> int:
    """Parse `If-Match: "<version>"` (optimistic locking). Missing header -> 428."""
    if if_match is None:
        raise PreconditionRequired("send If-Match with the workflow version you edited (from ETag)")
    try:
        return int(if_match.strip().removeprefix("W/").strip('"'))
    except ValueError as exc:
        raise InvalidInput(f"If-Match must be a version number, got '{if_match}'") from exc
