"""FastAPI dependencies: settings, database session, current user, preconditions."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from urllib.parse import quote

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db import rls
from agentcanvas.db.repositories import DEV_ORG, DEV_USER, ScopeRepo, UserRepo
from agentcanvas.db.session import Database
from agentcanvas.errors import Conflict, Forbidden, InvalidInput, NotFound, PreconditionRequired, Unauthorized
from agentcanvas.log import get_logger
from agentcanvas.security.auth import Authenticator
from agentcanvas.services import matura, networks, sso_groups, tokens
from agentcanvas.settings import Settings

log = get_logger(__name__)


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
    token_id: str | None = None  # signed in with a personal API token (G-13), not the identity provider


def _authenticator(request: Request) -> Authenticator:
    auth = getattr(request.app.state, "authenticator", None)
    if auth is None:
        auth = request.app.state.authenticator = Authenticator(request.app.state.settings)
    return auth


# declared as a security scheme so OpenAPI clients and Swagger UI know how to authenticate; absence is allowed here
# (dev mode needs no token) and turned into 401 problem+json below
bearer_scheme = HTTPBearer(
    auto_error=False, description="the identity provider's access token (OIDC mode), or a personal API token (act_…)"
)


async def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    org: str | None = Header(None, alias="X-Org-Id", description="organisation to act in (default: last used)"),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    """The signed-in user and the organisation this request acts in, from an address the organisation's IP
    allow-list admits (403 otherwise, `services/networks.py`)."""
    user = await _signed_in(request, credentials, org, session)
    await networks.check_org(request, session, user.org_id, "API token" if user.token_id else "sign-in")
    return user


async def _signed_in(
    request: Request, credentials: HTTPAuthorizationCredentials | None, org: str | None, session: AsyncSession
) -> CurrentUser:
    """The signed-in user and the organisation this request acts in.

    `auth_mode = dev` (local work, tests): the seeded development admin. `oidc`: the bearer token's
    subject, provisioned on first sign-in; 401 without a valid token, 403 without a membership. `matura`: the
    token's organisation (`tid`) and the role its permissions give (`services/matura.py`). In every mode, a personal
    API token (`act_…`, `services/tokens.py`) acts as its owner in its own organisation.
    """
    settings: Settings = request.app.state.settings
    if credentials is not None and tokens.is_personal(credentials.credentials):
        caller = await tokens.TokenService(session).authenticate(credentials.credentials)
        if org and org != caller.org_id:
            raise Forbidden(f"this API token acts in organisation '{caller.org_id}' only")
        if settings.auth_mode == "matura":
            await rls.scope(session, caller.org_id)
        return CurrentUser(
            id=caller.user_id, org_id=caller.org_id, role=caller.role, email=caller.email,
            memberships={caller.org_id: caller.role}, token_id=caller.token_id,
        )  # fmt: skip
    if settings.auth_mode == "dev":
        return CurrentUser(id=DEV_USER, org_id=DEV_ORG, role="admin", memberships={DEV_ORG: "admin"})
    if credentials is None or not credentials.credentials:
        raise Unauthorized("sign in: send Authorization: Bearer <token>")
    identity = await _authenticator(request).verify(credentials.credentials)
    if settings.auth_mode == "matura":  # the token names the organisation and the permissions (TS9a)
        uid, org_id, role, email, memberships = await matura.sign_in(
            session, identity.claims, org, matura.portal_base(settings)
        )
        await rls.scope(session, org_id)  # Postgres: this request's queries see only this organisation's rows
        return CurrentUser(id=uid, org_id=org_id, role=role, email=email, memberships=memberships)
    repo = UserRepo(session)
    # only a verified email is stored: invitations and the admin bootstrap match people by it
    email = identity.email if identity.email_verified else ""
    user, first = await repo.provision(identity.subject, email, identity.name, identity.email_verified)
    uid, user_email = user.id, user.email  # read before anything below commits or rolls back
    await sso_groups.apply(  # the provider's groups decide roles (G-13)
        session, uid, identity.claims, add_members=settings.sso_groups_add_members,
        sink=getattr(request.app.state, "audit_sink", None),
    )  # fmt: skip
    memberships = await repo.memberships(uid)
    # bootstrap happens once, at the first sign-in with a verified email (a first try without one, e.g. Entra ID
    # before AGENTCANVAS_OIDC_TRUST_EMAIL, must not lose it): removing a bootstrap admin later sticks
    if (
        first
        and not memberships
        and identity.email_verified
        and identity.email.lower() in {e.lower() for e in settings.bootstrap_admin_emails}
    ):
        memberships = await repo.bootstrap_admin(uid, DEV_ORG)
    if not memberships and (invited := await repo.invitations(uid)):
        # nowhere else to be: the first invitation needs no acceptance (it does when it would move them)
        await repo.answer(uid, invited[0][0].id, accept=True)
        memberships = await repo.memberships(uid)
    if not memberships:
        if not identity.email_verified:  # invitations and the admin bootstrap match people by a verified email
            log.warning("oidc.email_unverified", email_claim=settings.oidc_email_claim, has_email=bool(identity.email))
            raise Forbidden(
                "you are not a member of any organisation, and your sign-in carries no verified email to match an "
                f"invitation by ('{settings.oidc_email_claim}' claim"
                + (" without email_verified" if identity.email else " missing")
                + "): ask the operator to check the provider's token, or to set AGENTCANVAS_OIDC_TRUST_EMAIL=true "
                "if it never sends email_verified (Entra ID)"
            )
        raise Forbidden("you are not a member of any organisation; ask an admin to invite you")
    default = next(iter(memberships))  # the organisation used last
    org_id = org or default
    if org_id not in memberships:
        raise Forbidden(f"you are not a member of organisation '{org_id}'")
    if org_id != default:
        await repo.used(uid, org_id)  # choosing another organisation makes it the default
    return CurrentUser(id=uid, org_id=org_id, role=memberships[org_id], email=user_email, memberships=memberships)


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
    "kb_id": "knowledge_base",
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
    ("PUT", "/me/preferences"): "viewer",  # anyone keeps their own preferences
    ("POST", "/me/tokens"): "viewer",  # anyone makes API tokens for themselves, with at most their own role
    ("DELETE", "/me/tokens/{token_id}"): "viewer",  # and revokes them
    # anyone connects their own app connector (a reviewer resumes a run with their own connection); the endpoints
    # check ownership, keep the service identity for admins and refuse API tokens for the sign-in (G-7)
    ("POST", "/connections/authorize"): "viewer",
    ("POST", "/connections/callback"): "viewer",
    ("DELETE", "/connections/{connection_id}"): "viewer",
    ("POST", "/workflows/{wf_id}/copilot/messages"): "viewer",  # Ask mode for everyone; Build checks for builder
    ("POST", "/workflows/{wf_id}/copilot/stop"): "viewer",  # stop your own answer, in either mode
    ("DELETE", "/workflows/{wf_id}/copilot"): "viewer",  # anyone starts their own chat over
    ("POST", "/workflows/{wf_id}/copilot/threads/{conv_id}/open"): "viewer",  # their own conversations
    ("PATCH", "/workflows/{wf_id}/copilot/threads/{conv_id}"): "viewer",
    ("DELETE", "/workflows/{wf_id}/copilot/threads/{conv_id}"): "viewer",
    ("PUT", "/workflows/{wf_id}/gates/{environment}"): "admin",  # a builder must not lower the bar they publish over
    ("DELETE", "/workflows/{wf_id}/gates/{environment}"): "admin",
    ("PUT", "/projects/{project_id}/trace-export"): "admin",  # run inputs and outputs leave the server
    ("DELETE", "/projects/{project_id}/trace-export"): "admin",
    ("GET", "/deployments/{dep_id}/share"): "builder",  # the link is a capability: builders hand it out (S23)
    ("GET", "/deployments/{dep_id}/keys"): "builder",  # who holds which key, and when each was last used
    ("POST", "/workflows/{wf_id}/presence"): "viewer",  # anyone who has the editor open is shown as being here (G-4)
    ("DELETE", "/workflows/{wf_id}/presence/{session_id}"): "viewer",
    ("POST", "/knowledge/{kb_id}/search"): "viewer",
    ("PATCH", "/projects/{project_id}"): "admin",  # a project's name and existence are the organisation's
    ("DELETE", "/projects/{project_id}"): "admin",  # a read: what a Search knowledge block would find (S24)
    ("PUT", "/projects/{project_id}/workspace"): "admin",  # which Matura workspace a project lives in
}


def required_role(method: str, path: str) -> str:
    """The weakest role allowed `method` on the route template `path` (with or without the /v1 prefix)."""
    path = path.removeprefix("/v1")
    # the log, spending, everyone's tokens and the SCIM token: admins only
    if path.startswith(("/org/audit", "/org/spending", "/org/tokens", "/org/scim", "/server/")):
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


def refuse_token(user: CurrentUser, what: str) -> None:
    """`what` hands out access that would outlive the caller's API token being revoked (another token, the SCIM
    token, a seat, a role through groups): it needs a real sign-in."""
    if user.token_id:
        raise Forbidden(f"sign in to {what}: an API token cannot, so a leaked one cannot outlive its revocation")


def members_here(settings: Settings = Depends(get_settings_dep)) -> None:
    """With Matura, members, invitations and roles are managed there: the local endpoints refuse changes."""
    if settings.auth_mode == "matura":
        raise Conflict("members and roles are managed" + matura.portal_hint(matura.portal_base(settings)))


def if_match(if_match: str | None = Header(default=None, alias="If-Match")) -> int:
    """Parse `If-Match: "<version>"` (optimistic locking). Missing header -> 428."""
    if if_match is None:
        raise PreconditionRequired("send If-Match with the workflow version you edited (from ETag)")
    try:
        return int(if_match.strip().removeprefix("W/").strip('"'))
    except ValueError as exc:
        raise InvalidInput(f"If-Match must be a version number, got '{if_match}'") from exc


def attachment(filename: str) -> dict[str, str]:
    """The `Content-Disposition` of a download: an ASCII `filename` for every client and, when the name has other
    characters, the real one as `filename*` (RFC 6266/5987). A header is Latin-1 only: a raw "日本" would be a 500."""
    plain = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    plain = re.sub(r"\s+", " ", re.sub(r'["\\\x00-\x1f\x7f]', "_", plain)).strip() or "download"
    if plain == filename:
        return {"Content-Disposition": f'attachment; filename="{plain}"'}
    return {"Content-Disposition": f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(filename, safe='')}"}
