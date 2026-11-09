"""SCIM 2.0 provisioning (G-13; RFC 7643 schemas, RFC 7644 protocol): the part identity providers (Okta, Entra ID,
OneLogin, Keycloak plugins) use to keep an organisation's people in step with their directory.

| SCIM resource | in AgentCanvas |
|---|---|
| User `id` | the person's id; they must be a member of the organisation the token belongs to |
| User `userName`, `emails` | the email (the key people are matched by: invitations, sign-in) |
| User `active` | `false` suspends the membership (no access, nothing deleted); `true` brings it back |
| User `externalId` | the provider's own id, kept on the membership and echoed back |
| DELETE User | removes the membership (the person's account stays: they may belong elsewhere) |
| Group `displayName`, `members` | a group of the organisation's members; the group's name is looked up in the |
| | organisation's groups table (Settings > Organisation, `organization.sso_groups.roles`) for its role |

A member's role is the strongest role of the SCIM groups they are in, `viewer` when none gives one. It is
recomputed for the people a request touches, so a person nobody pushes (the first admin) keeps the role they have. A
change that would leave the organisation without an admin who can sign in is refused for a user and skipped for a
group-driven role. Everything here acts on one organisation, the one the bearer token belongs to.
"""

from __future__ import annotations

import hmac
import re
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import AppUser, AuditLog, Membership, Organization, ScimGroup, ScimGroupMember
from agentcanvas.db.repositories import AuditRepo, MemberRepo, pending_subject
from agentcanvas.errors import Conflict, DomainError
from agentcanvas.ids import new_id
from agentcanvas.log import get_logger
from agentcanvas.services.tokens import RANK, hash_token

log = get_logger(__name__)
USER, GROUP = "urn:ietf:params:scim:schemas:core:2.0:User", "urn:ietf:params:scim:schemas:core:2.0:Group"
LIST = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
PREFIX = "scim_"
DEFAULT_ROLE = "viewer"
MAX_COUNT = 200
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+$")


class ScimError(Exception):
    """An error in RFC 7644 section 3.12's shape."""

    def __init__(self, status: int, detail: str, scim_type: str | None = None):
        super().__init__(detail)
        self.status, self.detail, self.scim_type = status, detail, scim_type

    def body(self) -> dict[str, Any]:
        out: dict[str, Any] = {"schemas": [ERROR], "status": str(self.status), "detail": self.detail}
        if self.scim_type:
            out["scimType"] = self.scim_type
        return out


def from_domain(exc: DomainError) -> ScimError:
    kind = "uniqueness" if isinstance(exc, Conflict) else None
    return ScimError(exc.status, exc.detail, kind)


def new_token() -> str:
    return PREFIX + secrets.token_urlsafe(32)


async def organisation_of(session: AsyncSession, token: str) -> Organization:
    """The organisation a SCIM bearer token belongs to (401 for anything else)."""
    org = None
    if token.startswith(PREFIX):
        org = await session.scalar(select(Organization).where(Organization.scim_token_hash == hash_token(token)))
    if org is None or not hmac.compare_digest(org.scim_token_hash or "", hash_token(token)):
        raise ScimError(401, "a valid SCIM token is required (Authorization: Bearer scim_...)")
    if org.suspended_at is not None or org.deleted_at is not None:  # E-7: being deleted
        raise ScimError(403, "this organisation is being deleted")
    return org


def _truthy(value: Any) -> bool:
    """`active` as providers send it: a boolean, or Entra ID's "True" and "False" strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    raise ScimError(400, "active must be true or false", "invalidValue")


def _iso(when: datetime | None) -> str | None:
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if when else None


def _attr(path: str) -> str:
    """A patch or filter attribute without its schema URN (`urn:...:User:active` -> `active`), lower-case."""
    return (path.rsplit(":", 1)[-1] if path.lower().startswith("urn:") else path).strip().lower()


FILTER = re.compile(r'^\s*([A-Za-z][\w.]*)\s+eq\s+"((?:[^"\\]|\\.)*)"\s*$', re.IGNORECASE)


def parse_filter(expr: str | None, allowed: set[str]) -> tuple[str, str] | None:
    """`attr eq "value"`, the one expression providers use to find a resource; anything else is 400 invalidFilter."""
    if not expr:
        return None
    m = FILTER.match(expr)
    attr = _attr(m.group(1)) if m else ""
    if m is None or attr not in allowed:
        names = ", ".join(sorted(allowed))
        raise ScimError(400, f'only `attribute eq "value"` filters on {names} are supported', "invalidFilter")
    return attr, m.group(2).replace('\\"', '"').replace("\\\\", "\\")


def paging(start: Any, count: Any) -> tuple[int, int]:
    try:
        start_index, n = max(int(start or 1), 1), min(max(int(count if count is not None else 100), 0), MAX_COUNT)
    except (TypeError, ValueError):
        raise ScimError(400, "startIndex and count must be numbers", "invalidValue") from None
    return start_index, n


def listing(resources: list[dict[str, Any]], total: int, start: int) -> dict[str, Any]:
    return {"schemas": [LIST], "totalResults": total, "startIndex": start, "itemsPerPage": len(resources),
            "Resources": resources}  # fmt: skip


def operations(body: dict[str, Any]) -> list[dict[str, Any]]:
    if PATCH not in (body.get("schemas") or []) or not isinstance(body.get("Operations"), list):
        raise ScimError(400, "a PATCH needs the PatchOp schema and an Operations list", "invalidSyntax")
    ops = []
    for op in body["Operations"]:
        name = str(op.get("op", "")).lower() if isinstance(op, dict) else ""
        if name not in ("add", "remove", "replace"):
            raise ScimError(400, "an operation's op is add, remove or replace", "invalidSyntax")
        ops.append({"op": name, "path": op.get("path"), "value": op.get("value")})
    return ops


class ScimService:
    def __init__(self, s: AsyncSession, org_id: str):
        self.s, self.org_id = s, org_id
        self.members = MemberRepo(s)

    # --- helpers ------------------------------------------------------------------------------------------------

    async def _roles_table(self) -> dict[str, str]:
        org = await self.s.get(Organization, self.org_id)
        raw = org.sso_groups if org is not None else None
        return dict((raw or {}).get("roles") or {})

    async def _member(self, user_id: str) -> tuple[AppUser, Membership]:
        row = (
            await self.s.execute(
                select(AppUser, Membership)
                .join(Membership, Membership.user_id == AppUser.id)
                .where(Membership.org_id == self.org_id, AppUser.id == user_id)
            )
        ).first()
        if row is None:
            raise ScimError(404, f"User {user_id} not found")
        return row[0], row[1]

    async def _shared(self, u: AppUser) -> bool:
        """Whether this person also belongs to (or is invited by) another organisation. A person who has not signed in
        yet is one row whichever organisation invited them, so this organisation's directory must not rename or
        re-address that row: the other organisation's invitation would follow the new address."""
        other = await self.s.scalar(
            select(func.count())
            .select_from(Membership)
            .where(Membership.user_id == u.id, Membership.org_id != self.org_id)
        )
        return bool(other)

    async def _groups_of(self, user_ids: list[str]) -> dict[str, list[ScimGroup]]:
        if not user_ids:
            return {}
        rows = await self.s.execute(
            select(ScimGroupMember.user_id, ScimGroup)
            .join(ScimGroup, ScimGroup.id == ScimGroupMember.group_id)
            .where(ScimGroup.org_id == self.org_id, ScimGroupMember.user_id.in_(user_ids))
            .order_by(ScimGroup.display_name)
        )
        out: dict[str, list[ScimGroup]] = {}
        for uid, g in rows.all():
            out.setdefault(uid, []).append(g)
        return out

    def _user(self, u: AppUser, m: Membership, groups: list[ScimGroup]) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schemas": [USER], "id": u.id, "userName": u.email, "active": not m.suspended,
            "emails": [{"value": u.email, "primary": True, "type": "work"}],
            "groups": [{"value": g.id, "display": g.display_name, "type": "direct"} for g in groups],
            "meta": {"resourceType": "User", "created": _iso(u.created_at)},
        }  # fmt: skip
        if m.scim_external_id:
            out["externalId"] = m.scim_external_id
        if u.display_name:
            out["displayName"] = u.display_name
            out["name"] = {"formatted": u.display_name}
        return out

    async def _group(self, g: ScimGroup, members: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schemas": [GROUP], "id": g.id, "displayName": g.display_name,
            "meta": {"resourceType": "Group", "created": _iso(g.created_at), "lastModified": _iso(g.updated_at)},
        }  # fmt: skip
        if g.external_id:
            out["externalId"] = g.external_id
        if members:
            rows = await self.s.execute(
                select(AppUser.id, AppUser.email)
                .join(ScimGroupMember, ScimGroupMember.user_id == AppUser.id)
                .where(ScimGroupMember.group_id == g.id)
                .order_by(AppUser.email)
            )
            out["members"] = [{"value": uid, "display": email, "type": "User"} for uid, email in rows.all()]
        return out

    async def _audit(self, action: str, target: str, **detail: Any) -> None:
        row = AuditLog(org_id=self.org_id, actor_id=None, action=action, target=target,
                       detail={"caller": "scim", **detail})  # fmt: skip
        await AuditRepo(self.s).add(row)  # the entry also leaves the server (services/audit_sink.py)

    async def _sync_roles(self, user_ids: set[str]) -> None:
        """Give each person the strongest role of their SCIM groups (`viewer` without one)."""
        table = await self._roles_table()
        groups = await self._groups_of(sorted(user_ids))
        for uid in sorted(user_ids):
            m = await self.s.get(Membership, (self.org_id, uid))
            if m is None:
                continue
            given = [table[g.display_name] for g in groups.get(uid, []) if g.display_name in table]
            desired = max(given, key=RANK.__getitem__) if given else DEFAULT_ROLE
            if m.role == desired:
                continue
            before = m.role
            try:
                await self.members.change_role(self.org_id, uid, desired)
            except Conflict:  # the organisation's last admin: the directory cannot lock it out of itself
                log.warning("scim.role_kept_last_admin", org_id=self.org_id, user_id=uid)
                await self.s.rollback()
                continue
            await self._audit("scim_role_sync", f"user_id={uid}", **{"from": before, "to": desired})

    # --- users --------------------------------------------------------------------------------------------------

    async def list_users(self, flt: str | None, start: Any, count: Any) -> dict[str, Any]:
        match = parse_filter(flt, {"username", "externalid", "id", "emails.value"})
        q = (
            select(AppUser, Membership)
            .join(Membership, Membership.user_id == AppUser.id)
            .where(Membership.org_id == self.org_id)
        )
        if match:
            attr, value = match
            if attr in ("username", "emails.value"):
                q = q.where(func.lower(AppUser.email) == value.lower())
            elif attr == "externalid":
                q = q.where(Membership.scim_external_id == value)
            else:
                q = q.where(AppUser.id == value)
        total = await self.s.scalar(select(func.count()).select_from(q.subquery())) or 0
        start_index, n = paging(start, count)
        rows = (await self.s.execute(q.order_by(AppUser.email).offset(start_index - 1).limit(n))).all()
        groups = await self._groups_of([u.id for u, _ in rows])
        return listing([self._user(u, m, groups.get(u.id, [])) for u, m in rows], total, start_index)

    async def get_user(self, user_id: str) -> dict[str, Any]:
        u, m = await self._member(user_id)
        return self._user(u, m, (await self._groups_of([u.id])).get(u.id, []))

    @staticmethod
    def _email(body: dict[str, Any]) -> str:
        candidates = [body.get("userName")]
        emails = body.get("emails")
        if isinstance(emails, list):
            plain = [e for e in emails if isinstance(e, dict)]
            candidates += [e.get("value") for e in sorted(plain, key=lambda e: not e.get("primary"))]
        for c in candidates:
            if isinstance(c, str) and EMAIL.match(c.strip()):
                return c.strip().lower()
        raise ScimError(400, "userName must be the person's email address", "invalidValue")

    @staticmethod
    def _name(body: dict[str, Any]) -> str | None:
        raw = body.get("name")
        name: dict[str, Any] = raw if isinstance(raw, dict) else {}
        shown = (
            body.get("displayName")
            or name.get("formatted")
            or " ".join(p for p in (name.get("givenName"), name.get("familyName")) if p)
        )
        return str(shown).strip()[:200] or None if shown else None

    async def create_user(self, body: dict[str, Any]) -> dict[str, Any]:
        email = self._email(body)
        taken = await self.s.scalar(
            select(Membership.user_id)
            .join(AppUser, AppUser.id == Membership.user_id)
            .where(Membership.org_id == self.org_id, func.lower(AppUser.email) == email)
        )
        if taken is not None:
            raise ScimError(409, f"{email} is already a member of this organisation", "uniqueness")
        try:
            user, _role = await self.members.set_role(self.org_id, email, DEFAULT_ROLE)
        except DomainError as exc:
            raise from_domain(exc) from None
        m = await self.s.get(Membership, (self.org_id, user.id))
        assert m is not None
        if user.oidc_subject.startswith("pending:") and (name := self._name(body)) and not await self._shared(user):
            user.display_name = name  # until their first sign-in brings their own
        m.scim_external_id = str(body["externalId"])[:200] if body.get("externalId") else None
        m.suspended = "active" in body and not _truthy(body["active"])
        await self.s.commit()
        await self._audit("scim_create_user", f"user_id={user.id}", email=email, active=not m.suspended)
        return await self.get_user(user.id)

    async def _apply_user_attrs(self, u: AppUser, m: Membership, attrs: dict[str, Any], *, replace: bool) -> None:
        if "active" in attrs or replace:
            suspended = not _truthy(attrs.get("active", True))
            if suspended != m.suspended:
                try:
                    await self.members.set_suspended(self.org_id, u.id, suspended)
                except DomainError as exc:
                    raise from_domain(exc) from None
                await self._audit("scim_set_active", f"user_id={u.id}", active=not suspended)
        if "externalid" in attrs or replace:
            ext = attrs.get("externalid")
            m.scim_external_id = str(ext)[:200] if ext else None
        if u.oidc_subject.startswith("pending:"):  # not signed in yet: the directory's name and address stand
            shared = await self._shared(u)
            email = attrs.get("username")
            moved = isinstance(email, str) and EMAIL.match(email.strip()) and email.strip().lower() != u.email
            if moved and shared:  # the row is another organisation's invitation too: never re-address it
                raise ScimError(
                    409,
                    f"{u.email} is also invited by another organisation, so its address cannot change here; "
                    "delete this user and create one with the new address",
                    "mutability",
                )
            if not shared and (
                (name := self._name({"displayName": attrs.get("displayname"), "name": attrs.get("name")})) or replace
            ):
                u.display_name = name
            if moved and isinstance(email, str):
                u.email, u.oidc_subject = email.strip().lower(), pending_subject(email)
        await self.s.commit()

    async def replace_user(self, user_id: str, body: dict[str, Any]) -> dict[str, Any]:
        u, m = await self._member(user_id)
        await self._apply_user_attrs(u, m, {k.lower(): v for k, v in body.items()}, replace=True)
        return await self.get_user(user_id)

    async def patch_user(self, user_id: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
        u, m = await self._member(user_id)
        for op in ops:
            path, value = op["path"], op["value"]
            if path:
                if op["op"] == "remove":
                    value = {_attr(path): None if _attr(path) != "active" else True}
                else:
                    value = {_attr(path): value}
            elif isinstance(value, dict):
                value = {_attr(k): v for k, v in value.items()}
            else:
                raise ScimError(400, "an operation without a path needs an object value", "invalidSyntax")
            await self._apply_user_attrs(u, m, value, replace=False)
        return await self.get_user(user_id)

    async def delete_user(self, user_id: str) -> None:
        u, _m = await self._member(user_id)
        email, mine = u.email, select(ScimGroup.id).where(ScimGroup.org_id == self.org_id)
        try:
            await self.members.remove(self.org_id, user_id)
        except DomainError as exc:
            raise from_domain(exc) from None
        await self.s.execute(
            delete(ScimGroupMember).where(ScimGroupMember.user_id == user_id, ScimGroupMember.group_id.in_(mine))
        )
        await self.s.commit()
        await self._audit("scim_delete_user", f"user_id={user_id}", email=email)

    # --- groups -------------------------------------------------------------------------------------------------

    async def list_groups(self, flt: str | None, start: Any, count: Any, with_members: bool = True) -> dict[str, Any]:
        match = parse_filter(flt, {"displayname", "externalid", "id"})
        q = select(ScimGroup).where(ScimGroup.org_id == self.org_id)
        if match:
            attr, value = match
            col = {"displayname": ScimGroup.display_name, "externalid": ScimGroup.external_id, "id": ScimGroup.id}[attr]
            q = q.where(col == value)
        total = await self.s.scalar(select(func.count()).select_from(q.subquery())) or 0
        start_index, n = paging(start, count)
        rows = (await self.s.scalars(q.order_by(ScimGroup.display_name).offset(start_index - 1).limit(n))).all()
        return listing([await self._group(g, with_members) for g in rows], total, start_index)

    async def _require_group(self, group_id: str) -> ScimGroup:
        g = await self.s.get(ScimGroup, group_id)
        if g is None or g.org_id != self.org_id:
            raise ScimError(404, f"Group {group_id} not found")
        return g

    async def get_group(self, group_id: str) -> dict[str, Any]:
        return await self._group(await self._require_group(group_id))

    async def _member_ids(self, group_id: str) -> set[str]:
        return set(await self.s.scalars(select(ScimGroupMember.user_id).where(ScimGroupMember.group_id == group_id)))

    async def _check_members(self, ids: set[str]) -> None:
        if not ids:
            return
        known = set(
            await self.s.scalars(
                select(Membership.user_id).where(Membership.org_id == self.org_id, Membership.user_id.in_(ids))
            )
        )
        if missing := sorted(ids - known):
            raise ScimError(400, f"{missing[0]} is not a user of this organisation", "invalidValue")

    @staticmethod
    def _ids(value: Any) -> set[str]:
        if value is None:
            return set()
        if not isinstance(value, list) or not all(isinstance(v, dict) and v.get("value") for v in value):
            raise ScimError(400, 'members is a list of {"value": <user id>}', "invalidValue")
        return {str(v["value"]) for v in value}

    async def _set_members(self, g: ScimGroup, wanted: set[str]) -> set[str]:
        """Make `wanted` the group's members; the people whose membership changed."""
        await self._check_members(wanted)
        current = await self._member_ids(g.id)
        for uid in wanted - current:
            self.s.add(ScimGroupMember(group_id=g.id, user_id=uid))
        if gone := current - wanted:
            await self.s.execute(
                delete(ScimGroupMember).where(ScimGroupMember.group_id == g.id, ScimGroupMember.user_id.in_(gone))
            )
        await self.s.commit()
        return (wanted - current) | gone

    async def _name_free(self, name: str, except_id: str | None = None) -> str:
        name = name.strip()
        if not name or len(name) > 200:
            raise ScimError(400, "displayName is 1 to 200 characters", "invalidValue")
        other = await self.s.scalar(
            select(ScimGroup.id).where(ScimGroup.org_id == self.org_id, ScimGroup.display_name == name)
        )
        if other is not None and other != except_id:
            raise ScimError(409, f"a group named '{name}' exists", "uniqueness")
        return name

    async def create_group(self, body: dict[str, Any]) -> dict[str, Any]:
        name = await self._name_free(str(body.get("displayName") or ""))
        ids = self._ids(body.get("members"))
        await self._check_members(ids)
        g = ScimGroup(id=new_id("sgr"), org_id=self.org_id, display_name=name,
                      external_id=str(body["externalId"])[:200] if body.get("externalId") else None)  # fmt: skip
        self.s.add(g)
        await self.s.commit()
        gid = g.id  # read now: skipping a refused role change rolls back, which expires `g`
        touched = await self._set_members(g, ids)
        await self._sync_roles(touched)
        await self._audit("scim_create_group", f"group_id={gid}", name=name, members=len(ids))
        return await self.get_group(gid)

    async def replace_group(self, group_id: str, body: dict[str, Any]) -> dict[str, Any]:
        g = await self._require_group(group_id)
        renamed = await self._name_free(str(body.get("displayName") or g.display_name), g.id)
        everyone = await self._member_ids(g.id) if renamed != g.display_name else set()
        g.display_name = renamed
        g.external_id = str(body["externalId"])[:200] if body.get("externalId") else None
        touched = await self._set_members(g, self._ids(body.get("members")))
        await self._sync_roles(touched | everyone)
        await self._audit("scim_replace_group", f"group_id={group_id}", name=renamed)
        return await self.get_group(group_id)

    async def patch_group(self, group_id: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
        g = await self._require_group(group_id)
        touched: set[str] = set()
        for op in ops:
            path, value, kind = op["path"], op["value"], op["op"]
            attr = _attr(path) if path else ""
            if m := re.match(r'^members\[\s*value\s+eq\s+"([^"]+)"\s*\]$', (path or "").strip(), re.IGNORECASE):
                if kind != "remove":
                    raise ScimError(400, "a members filter works with remove", "invalidPath")
                touched |= await self._set_members(g, (await self._member_ids(g.id)) - {m.group(1)})
            elif attr == "members":
                current = await self._member_ids(g.id)
                if kind == "add":
                    wanted = current | self._ids(value)
                elif kind == "remove":
                    wanted = current - self._ids(value) if value else set()
                else:
                    wanted = self._ids(value)
                touched |= await self._set_members(g, wanted)
            elif attr in ("displayname", "externalid") or (not attr and isinstance(value, dict)):
                attrs = value if isinstance(value, dict) else {attr: value}
                attrs = {_attr(k): v for k, v in attrs.items()}
                if "displayname" in attrs:
                    new = await self._name_free(str(attrs["displayname"] or ""), g.id)
                    if new != g.display_name:
                        touched |= await self._member_ids(g.id)
                        g.display_name = new
                if "externalid" in attrs:
                    g.external_id = str(attrs["externalid"])[:200] if attrs["externalid"] else None
                if "members" in attrs:
                    touched |= await self._set_members(g, self._ids(attrs["members"]))
                await self.s.commit()
            else:
                raise ScimError(400, f"cannot patch '{path}'", "invalidPath")
        name = g.display_name
        await self._sync_roles(touched)
        await self._audit("scim_patch_group", f"group_id={group_id}", name=name)
        return await self.get_group(group_id)

    async def delete_group(self, group_id: str) -> None:
        g = await self._require_group(group_id)
        former = await self._member_ids(g.id)
        name = g.display_name
        await self.s.execute(delete(ScimGroupMember).where(ScimGroupMember.group_id == g.id))
        await self.s.delete(g)
        await self.s.commit()
        await self._sync_roles(former)
        await self._audit("scim_delete_group", f"group_id={group_id}", name=name)
