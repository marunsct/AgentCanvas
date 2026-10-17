"""AgentCanvas on Matura (TS9a): sign-in, organisations, members and roles come from Matura.

With `AGENTCANVAS_AUTH_MODE=matura` the access token is a Matura token: `tid` is the organisation (Matura tenant) the
request acts in and `perms` what the user may do there. Roles are defined and assigned in Matura (its ADR 0008); this
app only knows the four permissions its role checks map to (see `matura/manifest.json` at the repository root):

| permission       | AgentCanvas role it gives | what it allows                                   |
|------------------|---------------------------|--------------------------------------------------|
| `canvas.read`    | viewer                    | read everything in the organisation              |
| `inbox.review`   | reviewer                  | answer inbox items, annotate runs                |
| `workflow.build` | builder                   | build, run and publish                           |
| `org.admin`      | admin                     | organisation settings, gates, audit log          |

The strongest permission decides the role, so `role_policy` keeps working unchanged.

Organisations, users and memberships stay in AgentCanvas's own tables, keyed by Matura's ids (organisation id =
tenant id, user id = Matura user id). They are written just in time from the token, and kept current by Matura's
signed webhooks (`POST /v1/matura/events`) and its change feed (`MaturaFeed`), which first fill the `matura_*`
contract tables and then mirror them here.
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from agentcanvas.db.models import AppUser, Membership, Organization
from agentcanvas.errors import Forbidden, InvalidInput, Unavailable
from agentcanvas.log import get_logger
from agentcanvas.vendor.matura import Contract, MaturaAPI, Principal, Sync

log = get_logger(__name__)

# weakest first, as in api/deps.ROLES
PERMISSIONS = {"viewer": "canvas.read", "reviewer": "inbox.review", "builder": "workflow.build", "admin": "org.admin"}
OWNER_ROLE = "admin"  # the manifest's owner_role: Matura gives it to the organisation's owners and admins


def role_for(permissions: tuple[str, ...] | list[str] | set[str]) -> str | None:
    """The strongest AgentCanvas role whose permissions the token carries in full; None when not even viewer's.

    The roles are a ladder (each includes the ones below), so a role counts only with every permission up to it: a
    role edited in Matura to hold `org.admin` without `workflow.build` must not build workflows here.
    """
    if "*" in permissions:
        return "admin"
    best = None
    for role, perm in PERMISSIONS.items():
        if perm not in permissions:
            break
        best = role
    return best


def subject(user_id: str) -> str:
    """How a Matura user's subject is stored (`oidc:` and `pending:` are the other modes' prefixes)."""
    return f"matura:{user_id}"


def portal_hint(portal: str | None) -> str:
    return f" in Matura ({portal})" if portal else " in Matura"


# --- the contract tables and the mirror ------------------------------------------------------------------------------

_CONTRACTS: dict[str, Contract] = {}


def contract_for(dialect: str) -> Contract:
    """The contract for this database, with the hooks that mirror it into AgentCanvas's tables."""
    if dialect not in _CONTRACTS:
        c = Contract(dialect)
        c.on("tenant", _on_tenant)
        c.on("user", _on_user)
        c.on("membership", _on_membership)
        c.on("grant", _on_grant)
        c.on("app_role", _on_app_role)
        c.on("tenant_app", _on_tenant_app)
        _CONTRACTS[dialect] = c
    return _CONTRACTS[dialect]


def _id(conn: AsyncConnection, v: Any) -> Any:
    return contract_for(conn.dialect.name)._d.id(v)


def _perms(v: Any) -> list[str]:
    return list(v) if isinstance(v, list | tuple) else json.loads(v or "[]")


async def role_permissions(conn: AsyncConnection) -> dict[str, list[str]]:
    """The app's roles as Matura last sent them (for tokens with `perms_ref`)."""
    rows = await conn.execute(text("SELECT name, permissions FROM matura_app_role WHERE status = 'active'"))
    return {name: _perms(perms) for name, perms in rows.all()}


async def recompute(conn: AsyncConnection, tenant_id: str, user_id: str) -> str | None:
    """Mirror one person's membership from the contract tables: their role from their active grants (tenant-wide or
    in the default workspace, as in their token) plus the owner role for owners and admins. No AgentCanvas role, or
    no active membership, removes them from the organisation here. Returns the role."""
    tid, uid = _id(conn, tenant_id), _id(conn, user_id)
    row = (
        await conn.execute(
            text("""SELECT m.tenant_role, m.status, u.status FROM matura_membership m
                JOIN matura_user u ON u.id = m.user_id WHERE m.tenant_id = :t AND m.user_id = :u"""),
            {"t": tid, "u": uid},
        )
    ).first()
    role: str | None = None
    if row is not None and row[1] == "active" and row[2] == "active":
        roles = set(
            (
                await conn.execute(
                    text("""SELECT role FROM matura_role_grant WHERE tenant_id = :t AND subject_id = :u
                        AND subject_type = 'user' AND status = 'active' AND (workspace_id IS NULL OR workspace_id IN
                        (SELECT id FROM matura_workspace WHERE tenant_id = :t AND is_default))"""),
                    {"t": tid, "u": uid},
                )
            ).scalars()
        )
        if row[0] in ("owner", "admin"):
            roles.add(OWNER_ROLE)
        defined = await role_permissions(conn)
        role = role_for({p for r in roles for p in defined.get(r, ())})
    org_id, member_id = str(tenant_id), str(user_id)
    if role is None:
        await conn.execute(delete(Membership).where(Membership.org_id == org_id, Membership.user_id == member_id))
        return None
    if (await conn.execute(select(Organization.id).where(Organization.id == org_id))).first() is None:
        name = (await conn.execute(text("SELECT name FROM matura_tenant WHERE id = :t"), {"t": tid})).scalar()
        await conn.execute(insert(Organization).values(id=org_id, name=name or org_id))
    if (await conn.execute(select(AppUser.id).where(AppUser.id == member_id))).first() is None:
        u = (await conn.execute(text("SELECT email, display_name FROM matura_user WHERE id = :u"), {"u": uid})).first()
        await conn.execute(
            insert(AppUser).values(
                id=member_id, oidc_subject=subject(member_id), email=(u[0] if u else None) or "",
                display_name=u[1] if u else None,
            )
        )  # fmt: skip
    current = (
        await conn.execute(select(Membership.role).where(Membership.org_id == org_id, Membership.user_id == member_id))
    ).first()
    if current is None:
        await conn.execute(insert(Membership).values(org_id=org_id, user_id=member_id, role=role, accepted=True))
    elif current[0] != role:
        await conn.execute(
            update(Membership)
            .where(Membership.org_id == org_id, Membership.user_id == member_id)
            .values(role=role, accepted=True)
        )
    return role


async def _members(conn: AsyncConnection, tenant_id: Any = None, user_id: Any = None) -> list[tuple[str, str]]:
    q, params = "SELECT tenant_id, user_id FROM matura_membership WHERE 1 = 1", {}
    if tenant_id is not None:
        q, params["t"] = q + " AND tenant_id = :t", _id(conn, tenant_id)
    if user_id is not None:
        q, params["u"] = q + " AND user_id = :u", _id(conn, user_id)
    return [(str(t), str(u)) for t, u in (await conn.execute(text(q), params)).all()]


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    data = event.get("data") or {}
    return data.get(data.get("object") or "") or {}


async def _on_tenant(conn: AsyncConnection, event: dict[str, Any]) -> None:
    p = _payload(event)
    tid = _id(conn, p["id"])
    name = (await conn.execute(text("SELECT name FROM matura_tenant WHERE id = :t"), {"t": tid})).scalar()
    if name:  # the stored one: an older event must not rename it back
        await conn.execute(update(Organization).where(Organization.id == str(p["id"])).values(name=name))


async def _on_user(conn: AsyncConnection, event: dict[str, Any]) -> None:
    p = _payload(event)
    u = (
        await conn.execute(
            text("SELECT email, display_name, status FROM matura_user WHERE id = :u"), {"u": _id(conn, p["id"])}
        )
    ).first()
    if u is None:
        return
    await conn.execute(
        update(AppUser).where(AppUser.id == str(p["id"])).values(email=u[0] or "", display_name=u[1])
    )  # a deleted user keeps the row, with name and email cleared, so history still points somewhere
    for t, uid in await _members(conn, user_id=p["id"]):
        await recompute(conn, t, uid)


async def _on_membership(conn: AsyncConnection, event: dict[str, Any]) -> None:
    p = _payload(event)
    await recompute(conn, p["tenant_id"], p["user_id"])


async def _on_grant(conn: AsyncConnection, event: dict[str, Any]) -> None:
    p = _payload(event)
    if p.get("subject_type") == "user":
        await recompute(conn, p["tenant_id"], p["subject_id"])


async def _on_app_role(conn: AsyncConnection, event: dict[str, Any]) -> None:
    """A role's permissions changed: everyone's role may have."""
    for t, u in await _members(conn):
        await recompute(conn, t, u)


async def _on_tenant_app(conn: AsyncConnection, event: dict[str, Any]) -> None:
    """The organisation turned AgentCanvas off (nobody keeps access; its data stays) or on again."""
    tenant_id = _payload(event).get("tenant_id")
    if not tenant_id:
        return
    if event.get("type", "").endswith(".disabled"):
        await conn.execute(delete(Membership).where(Membership.org_id == str(tenant_id)))
    else:
        for t, u in await _members(conn, tenant_id=tenant_id):
            await recompute(conn, t, u)


# --- workspaces and the portal (TS26) --------------------------------------------------------------------------------


async def workspaces(session: AsyncSession, tenant_id: str) -> list[tuple[str, str, bool]]:
    """The organisation's active Matura workspaces as (id, name, is_default), default first, then by name."""
    conn = await session.connection()
    try:
        tid = _id(conn, tenant_id)
    except ValueError:  # not a Matura id (an organisation from before Matura): no workspaces
        return []
    rows = await conn.execute(
        text("""SELECT id, name, is_default FROM matura_workspace WHERE tenant_id = :t AND status = 'active'
            ORDER BY is_default DESC, name, id"""),
        {"t": tid},
    )
    return [(str(i), n, bool(d)) for i, n, d in rows.all()]


async def check_workspace(session: AsyncSession, tenant_id: str, workspace_id: str) -> None:
    """422 unless `workspace_id` is an active workspace of the organisation (as Matura last sent it)."""
    if workspace_id not in {w for w, _, _ in await workspaces(session, tenant_id)}:
        raise InvalidInput(f"'{workspace_id}' is not an active workspace of this organisation in Matura")


def portal_base(settings: Any) -> str | None:
    """Matura's customer portal: `AGENTCANVAS_MATURA_PORTAL_URL`, else `<AGENTCANVAS_MATURA_PUBLIC_URL>/portal/`."""
    if settings.matura_portal_url:
        return settings.matura_portal_url.rstrip("/") + "/"
    if settings.matura_public_url:
        return settings.matura_public_url.rstrip("/") + "/portal/"
    return None


async def portal_link(session: AsyncSession, settings: Any, tenant_id: str) -> str | None:
    """The portal page of one organisation (`<portal>/<tenant slug>/`), where its admins manage members, roles, SSO
    and security settings. The slug is Matura's, from the contract tables; the id when it is not known yet."""
    base = portal_base(settings)
    if base is None:
        return None
    conn = await session.connection()
    try:
        tid = _id(conn, tenant_id)
    except ValueError:  # not a Matura id (an organisation from before Matura)
        tid = None
    q = text("SELECT slug FROM matura_tenant WHERE id = :t")
    slug = (await conn.execute(q, {"t": tid})).scalar() if tid is not None else None
    return f"{base}{slug or tenant_id}/"


# --- requests --------------------------------------------------------------------------------------------------------

# who was provisioned with what: most requests then write nothing (contract §5)
_SEEN: OrderedDict[tuple[Any, ...], None] = OrderedDict()
_SEEN_MAX = 10_000


async def sign_in(
    session: AsyncSession, claims: dict[str, Any], org: str | None, portal: str | None
) -> tuple[str, str, str, str, dict[str, str]]:
    """The caller of a request with a verified Matura token: (user id, organisation id, role, email, memberships).

    403 without an organisation in the token, for another organisation than `X-Org-Id` names, or without any
    AgentCanvas permission there.
    """
    p = Principal.from_claims(claims)
    tid = p.tenant_id
    if not tid:
        raise Forbidden("choose or create an organisation" + portal_hint(portal) + ", then sign in again")
    if p.placement_mode not in (None, "shared"):  # the manifest declares tenancy.modes = ["shared"]
        raise Unavailable(
            f"Matura placed this organisation's data in '{p.placement_mode}' mode; AgentCanvas keeps every "
            "organisation in one shared database (row-level security) and supports only 'shared'"
        )
    if org and org != tid:
        raise Forbidden(f"this sign-in is for organisation '{tid}'; sign in again choosing '{org}' to act there")
    if p.perms_ref:
        p = p.expand(await role_permissions(await session.connection()))
    role = role_for(p.permissions)
    if role is None:
        raise Forbidden(
            "your roles in this organisation give no access to AgentCanvas; ask its admin for one" + portal_hint(portal)
        )
    await provision(session, p, role)
    memberships = await _memberships(session, p.user_id)
    memberships[tid] = role  # the token is newer than any mirror
    return p.user_id, tid, role, p.email, memberships


async def provision(session: AsyncSession, p: Principal, role: str) -> None:
    """Write the contract rows the token can (only when missing) and the organisation, user and membership here."""
    key = (p.user_id, p.tenant_id, p.tenant_slug, role, p.email, p.name, tuple(sorted(p.versions.items())))
    if key in _SEEN:
        _SEEN.move_to_end(key)
        return
    for attempt in (1, 2):  # two first requests in parallel: the loser finds the rows and updates them
        try:
            await _provision(session, p, role)
            await session.commit()
            break
        except IntegrityError:
            await session.rollback()
            if attempt == 2:
                raise
    _SEEN[key] = None
    if len(_SEEN) > _SEEN_MAX:
        _SEEN.popitem(last=False)


async def _provision(session: AsyncSession, p: Principal, role: str) -> None:
    assert p.tenant_id
    conn = await session.connection()
    await contract_for(conn.dialect.name).apply_principal(conn, p)
    if await session.get(Organization, p.tenant_id) is None:
        session.add(Organization(id=p.tenant_id, name=p.tenant_slug or p.tenant_id))
    user = await session.get(AppUser, p.user_id)
    if user is None:
        session.add(AppUser(id=p.user_id, oidc_subject=subject(p.user_id), email=p.email, display_name=p.name))
    elif (p.email and user.email != p.email) or (p.name and user.display_name != p.name):
        user.email, user.display_name = p.email or user.email, p.name or user.display_name
    await session.flush()
    m = await session.get(Membership, (p.tenant_id, p.user_id))
    if m is None:
        session.add(
            Membership(org_id=p.tenant_id, user_id=p.user_id, role=role, accepted=True, last_used_at=datetime.now(UTC))
        )
    elif m.role != role or not m.accepted:
        m.role, m.accepted = role, True


async def _memberships(session: AsyncSession, user_id: str) -> dict[str, str]:
    q = (
        select(Membership.org_id, Membership.role)
        .where(Membership.user_id == user_id, Membership.accepted.is_(True))
        .order_by(Membership.last_used_at.desc().nulls_last(), Membership.org_id)
    )
    return {o: r for o, r in (await session.execute(q)).all()}


def forget() -> None:
    """Drop the provisioning cache (tests)."""
    _SEEN.clear()


# --- webhooks and the change feed ------------------------------------------------------------------------------------


async def apply(db: Any, event: dict[str, Any]) -> bool:
    """Apply one event from Matura in one transaction; False when it was applied before."""
    async with db.engine.begin() as conn:
        return await contract_for(conn.dialect.name).apply(conn, event)


class MaturaFeed:
    """Reads the change feed at start-up and every `interval_s`: webhooks bring changes as they happen, the feed
    whatever webhooks missed (the receiver was down, a delivery was dropped). Every replica may run it: applying is
    idempotent, so overlapping replicas only repeat work."""

    def __init__(self, db: Any, settings: Any, api: MaturaAPI | None = None):
        self.db = db
        self.interval_s = settings.matura_sync_interval_s
        self.api = api or MaturaAPI(
            settings.matura_url, settings.matura_app,
            settings.oidc_client_id or settings.oidc_audience or settings.matura_app,
            settings.matura_client_secret or "",
        )  # fmt: skip
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="matura-feed")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self.api.aclose()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - keep reading on the next round; webhooks still arrive
                log.warning("matura.feed_failed", error=str(exc))
            await asyncio.sleep(self.interval_s)

    async def tick(self) -> int:
        contract = contract_for(self.db.engine.dialect.name)
        applied = await Sync(self.api, contract, self.db.engine.begin).catch_up()
        if applied:
            log.info("matura.feed_applied", events=applied)
        return applied
