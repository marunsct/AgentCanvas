"""Access per project (E-10): who sees a project, and with what role.

A project's members (`project_member`) are people or SCIM groups, each with a role there: viewer, reviewer, builder
or admin. What they give depends on the organisation's setting `private_projects` (Settings > Organisation >
Security, off by default):

- **off (shared projects)**, as before: every member of the organisation sees every project with their organisation
  role. A project binding can only raise it there (an organisation viewer who builds one team's workflows).
- **on (private projects)**: organisation admins see every project as admin; anyone else sees only the projects
  they are bound to, directly or through a SCIM group, with the highest of those bindings as their role there. A
  project they are not bound to answers 404, like one of another organisation.

A binding may be above the person's organisation role: their organisation role governs the organisation's own pages
(members, settings, audit log, spending), the project role governs the project and everything in it. An API token
never does more than the role it was made with, in any project. Project admins manage their project's members and
spending caps; renaming or deleting a project, moving it between workspaces and sending its traces elsewhere stay the
organisation admins'.

The check is made once per request, in `api/deps.org_scope`, for every id a request names, so every route that takes
a project, workflow, deployment, conversation, run, dataset, file or secret is covered; lists without an id filter by
`visible_projects`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import Membership, Organization, Project, ProjectMember, ScimGroup, ScimGroupMember

KEY = "private_projects"  # in `organization.settings`
ROLES = ("viewer", "reviewer", "builder", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}


def private(org_settings: dict[str, Any] | None) -> bool:
    return bool((org_settings or {}).get(KEY))


def stronger(a: str | None, b: str | None) -> str | None:
    if a is None or b is None:
        return a or b
    return a if RANK[a] >= RANK[b] else b


def weaker(a: str, b: str) -> str:
    return a if RANK[a] <= RANK[b] else b


@dataclass
class Access:
    """One person's access to the projects of one organisation."""

    member_role: str  # their organisation role (not lowered by an API token)
    private: bool
    bound: dict[str, str] = field(default_factory=dict)  # project id -> their highest binding there
    token_role: str | None = None  # an API token's own role: the most it may do anywhere

    @property
    def sees_all(self) -> bool:
        return not self.private or self.member_role == "admin"

    def role_in(self, project_id: str) -> str | None:
        """Their role in the project, None when they may not see it."""
        own = self.bound.get(project_id)
        if self.member_role == "admin":
            role: str | None = "admin"
        elif self.private:
            role = own
        else:
            role = stronger(self.member_role, own)
        if role is None:
            return None
        return weaker(role, self.token_role) if self.token_role else role

    def project_filter(self) -> ColumnElement[bool]:
        """A WHERE clause on `Project.id` keeping the projects they see."""
        if self.sees_all:
            return true()
        return Project.id.in_(list(self.bound))


async def bindings(session: AsyncSession, org_id: str, user_id: str) -> dict[str, str]:
    """Project id -> the highest role a person's bindings give them there (directly or through a SCIM group)."""
    groups = (
        select(ScimGroupMember.group_id)
        .join(ScimGroup, ScimGroup.id == ScimGroupMember.group_id)
        .where(ScimGroupMember.user_id == user_id, ScimGroup.org_id == org_id)
    )
    q = (
        select(ProjectMember.project_id, ProjectMember.role)
        .join(Project, Project.id == ProjectMember.project_id)
        .where(Project.org_id == org_id, or_(ProjectMember.user_id == user_id, ProjectMember.group_id.in_(groups)))
    )
    out: dict[str, str] = {}
    for project_id, role in (await session.execute(q)).all():
        out[project_id] = stronger(out.get(project_id), role) or role
    return out


async def is_private(session: AsyncSession, org_id: str) -> bool:
    return private(await session.scalar(select(Organization.settings).where(Organization.id == org_id)))


async def load(
    session: AsyncSession, org_id: str, user_id: str, member_role: str, token_role: str | None = None
) -> Access:
    """A person's access; an organisation admin's needs no lookup of their bindings."""
    if member_role == "admin":
        return Access(member_role, await is_private(session, org_id), token_role=token_role)
    return Access(member_role, await is_private(session, org_id), await bindings(session, org_id, user_id), token_role)


async def member_roles(session: AsyncSession, org_id: str, project_id: str) -> dict[str, str]:
    """User id -> role in the project, for every active member of the organisation who sees it (who may be told
    about, and decide, what waits there)."""
    found = await session.execute(
        select(Membership.user_id, Membership.role).where(
            Membership.org_id == org_id, Membership.accepted.is_(True), Membership.suspended.is_(False)
        )
    )
    org_roles = dict(found.tuples().all())
    shared = not await is_private(session, org_id)
    bound: dict[str, str] = {}
    direct = await session.execute(
        select(ProjectMember.user_id, ProjectMember.role).where(
            ProjectMember.project_id == project_id, ProjectMember.user_id.is_not(None)
        )
    )
    via_groups = await session.execute(
        select(ScimGroupMember.user_id, ProjectMember.role)
        .join(ProjectMember, ProjectMember.group_id == ScimGroupMember.group_id)
        .where(ProjectMember.project_id == project_id)
    )
    for uid, role in [*direct.tuples().all(), *via_groups.tuples().all()]:
        if uid is not None:
            bound[uid] = stronger(bound.get(uid), role) or role
    out: dict[str, str] = {}
    for uid, org_role in org_roles.items():
        role = Access(org_role, not shared, {project_id: bound[uid]} if uid in bound else {}).role_in(project_id)
        if role is not None:
            out[uid] = role
    return out


def lowest(roles: Iterable[str]) -> str:
    return min(roles, key=RANK.__getitem__)
