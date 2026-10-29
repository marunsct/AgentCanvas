"""Roles from the identity provider's groups (G-13): at sign-in, the groups claim of a verified token decides the
person's role in each organisation that maps groups to roles.

An organisation's setting (`organization.sso_groups`) names the claim (a dotted path reaches nested claims, such as
Keycloak's `realm_access.roles`), a table group -> role, and what a token with none of the mapped groups means:
`keep` the role the person has (the default: the table only grants), or `remove` them from the organisation.
Where a table is set, the provider is the source of truth: the role it gives replaces one set by hand, at every
sign-in. A change that would leave an organisation without a signed-in admin is skipped, not applied.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import AuditLog, Membership, Organization
from agentcanvas.db.repositories import AuditRepo, MemberRepo
from agentcanvas.errors import Conflict
from agentcanvas.log import get_logger

log = get_logger(__name__)
RANK = {"viewer": 0, "reviewer": 1, "builder": 2, "admin": 3}
Role = Literal["admin", "builder", "reviewer", "viewer"]
MAX_GROUPS = 100


class GroupMapping(BaseModel):
    """An organisation's groups claim and its table of group -> role."""

    claim: str = Field(
        "groups", pattern=r"^[A-Za-z0-9_:/.\-]{1,100}$", description="the token's claim; a.b reaches a nested one"
    )
    roles: dict[str, Role] = Field(default_factory=dict, description="group name -> the role it gives")
    unmatched: Literal["keep", "remove"] = Field(
        "keep", description="a token with none of these groups: keep the person's role, or remove them"
    )

    @field_validator("roles")
    @classmethod
    def _groups(cls, roles: dict[str, Role]) -> dict[str, Role]:
        if len(roles) > MAX_GROUPS:
            raise ValueError(f"at most {MAX_GROUPS} groups")
        if any(not g.strip() or len(g) > 200 for g in roles):
            raise ValueError("a group name is 1 to 200 characters")
        return roles


def groups_in(claims: dict[str, Any], claim: str) -> set[str]:
    """The groups a token carries under `claim`: a list of strings, or one string (split at commas and spaces)."""
    value: Any = claims.get(claim)  # a claim name may itself contain dots (a URL-style namespace): try it whole first
    if value is None:
        value = claims
        for part in claim.split("."):
            value = value.get(part) if isinstance(value, dict) else None
    if isinstance(value, str):
        return {g for g in value.replace(",", " ").split() if g}
    if isinstance(value, list):
        return {g for g in value if isinstance(g, str) and g}
    return set()


def role_for(mapping: GroupMapping, groups: set[str]) -> str | None:
    """The strongest role any of the groups gives; None when none is in the table."""
    given = [mapping.roles[g] for g in groups if g in mapping.roles]
    return max(given, key=RANK.__getitem__) if given else None


async def apply(session: AsyncSession, user_id: str, claims: dict[str, Any]) -> bool:
    """Bring the person's roles in line with the organisations' group tables. True when something changed."""
    q = select(Organization.id, Organization.sso_groups).where(Organization.sso_groups.is_not(None))
    rows = (await session.execute(q)).all()
    changed = False
    for org_id, raw in rows:
        if not raw:  # a JSON null left by a clear
            continue
        mapping = GroupMapping.model_validate(raw)
        groups = groups_in(claims, mapping.claim)
        role = role_for(mapping, groups)
        repo = MemberRepo(session)
        try:
            if role is not None:
                before = await repo.assign(org_id, user_id, role)
                if before == role:
                    continue
                action, detail = "sso_role_sync", {"from": before, "to": role}
            else:
                current = await session.get(Membership, (org_id, user_id))
                if mapping.unmatched != "remove" or current is None:
                    continue
                had = current.role
                await repo.remove(org_id, user_id)
                action, detail = "sso_member_removed", {"from": had}
        except Conflict:  # the last signed-in admin: the provider cannot lock the organisation out of itself
            log.warning("sso.groups_kept_last_admin", org_id=org_id, user_id=user_id)
            await session.rollback()
            continue
        changed = True
        await AuditRepo(session).add(
            AuditLog(org_id=org_id, actor_id=user_id, action=action, target=f"user_id={user_id}",
                     detail={**detail, "groups": sorted(groups & set(mapping.roles))})
        )  # fmt: skip
    return changed
