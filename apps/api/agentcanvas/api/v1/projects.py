"""Projects of the signed-in user's organisation (M7-T2), optionally grouped by Matura workspace (TS26)."""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note
from agentcanvas.api.deps import (
    CurrentUser,
    current_user,
    get_session,
    get_settings_dep,
    members_here,
    project_access,
    refuse_token,
)
from agentcanvas.api.v1.org import CapUse
from agentcanvas.db.models import AppUser, Membership, Organization, Project, ScimGroup, WorkflowRow
from agentcanvas.db.repositories import ProjectMemberRepo, ProjectRepo
from agentcanvas.db.retry import retrying
from agentcanvas.errors import Conflict, InvalidInput
from agentcanvas.registry import default_registry
from agentcanvas.services import matura, model_defaults, org_settings, spend_caps
from agentcanvas.services.spend_caps import ProjectCaps
from agentcanvas.settings import Settings

router = APIRouter(tags=["projects"])
Role = Literal["admin", "builder", "reviewer", "viewer"]


class ProjectOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    workspace_id: str | None = None  # the Matura workspace it belongs to (Matura mode); None = none
    role: Role | None = Field(None, description="your role in this project")


class WorkspaceOut(BaseModel):
    id: str
    name: str
    is_default: bool


def _out(p: Project, role: str | None = None) -> ProjectOut:
    return ProjectOut(id=p.id, name=p.name, created_at=p.created_at, workspace_id=p.workspace_id, role=role)  # type: ignore[arg-type]


async def _checked(session: AsyncSession, settings: Settings, user: CurrentUser, workspace_id: str | None) -> None:
    if workspace_id is None:
        return
    if settings.auth_mode != "matura":
        raise InvalidInput("workspaces come from Matura: set AGENTCANVAS_AUTH_MODE=matura to use them")
    await matura.check_workspace(session, user.org_id, workspace_id)


@router.get("/workspaces", response_model=list[WorkspaceOut])
async def list_workspaces(
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings_dep),
) -> list[WorkspaceOut]:
    """The organisation's Matura workspaces, the default one first (Matura mode; empty otherwise). Workspaces are
    made and named in Matura; projects can be placed in one to group them."""
    if settings.auth_mode != "matura":
        return []
    return [WorkspaceOut(id=i, name=n, is_default=d) for i, n, d in await matura.workspaces(session, user.org_id)]


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(
    request: Request,
    workspace_id: str | None = Query(None, description="only the projects in this Matura workspace"),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> list[ProjectOut]:
    """The projects you see (all of the organisation's, unless its projects are private), with your role in each."""
    access = await project_access(request, session, user)
    rows = await ProjectRepo(session).list(user.org_id, workspace_id)
    return [_out(p, role) for p in rows if (role := access.role_in(p.id)) is not None]


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    workspace_id: str | None = None


@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings_dep),
) -> ProjectOut:
    """A new project in the caller's organisation (409 when the name is taken there), optionally in one of its
    Matura workspaces (422 when that is not an active workspace of the organisation). With the organisation's projects
    private, whoever creates one becomes its admin (else they could not see it)."""
    await _checked(session, settings, user, body.workspace_id)
    p = await ProjectRepo(session).create(user.org_id, body.name.strip(), body.workspace_id)
    access = await project_access(request, session, user)
    if access.private and user.member_role != "admin":
        await ProjectMemberRepo(session).bind(p.id, "admin", user_id=user.id, by=user.id)
        access.bound[p.id] = "admin"
    return _out(p, access.role_in(p.id))


class ProjectWorkspaceIn(BaseModel):
    workspace_id: str | None = None  # None takes the project out of its workspace


@router.put("/projects/{project_id}/workspace", response_model=ProjectOut)
async def set_project_workspace(
    project_id: str,
    body: ProjectWorkspaceIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings_dep),
) -> ProjectOut:
    """Move a project into a Matura workspace, or out of one with `workspace_id: null`. Admins only."""
    await _checked(session, settings, user, body.workspace_id)
    p = await ProjectRepo(session).require(project_id)
    p.workspace_id = body.workspace_id
    await session.commit()
    return _out(p, user.role)


class ProjectRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def rename_project(
    project_id: str,
    body: ProjectRenameIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> ProjectOut:
    """Rename a project (409 when another project of the organisation has the name)."""
    p = await ProjectRepo(session).require(project_id)
    p.name = body.name.strip()
    if not p.name:
        raise InvalidInput("a project needs a name")
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise Conflict(f"a project named '{body.name.strip()}' already exists") from None
    return _out(p, user.role)


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    """Delete a project with everything in it: workflows and their versions, deployments (their endpoints stop
    answering), runs, datasets, knowledge, secrets and the workspace's code. An organisation keeps at least one
    project (409 for the last one). Admins only."""
    await ProjectRepo(session).require(project_id)
    if await session.scalar(select(func.count()).select_from(Project).where(Project.org_id == user.org_id)) == 1:
        raise Conflict("this is the organisation's only project: create another one before deleting it")
    # its conversations' saved state first: their rows are the only index of it, and go with the project
    await request.app.state.runner.forget_conversations(session, WorkflowRow.project_id == project_id)

    async def rows() -> None:
        # one delete at a time per organisation (a row lock on Postgres) and the count after the write (on SQLite the
        # write lock makes a parallel delete wait, then see this one): two at once cannot remove the last two projects
        await session.execute(select(Organization.id).where(Organization.id == user.org_id).with_for_update())
        await session.execute(delete(Project).where(Project.id == project_id))
        if not await session.scalar(select(func.count()).select_from(Project).where(Project.org_id == user.org_id)):
            await session.rollback()
            raise Conflict("this is the organisation's only project: create another one before deleting it")
        await session.commit()

    # runs that just ended write under it (their finish hooks, the search index): a deadlock's victim runs again
    await retrying(session, rows, what="project delete")
    projects = getattr(request.app.state, "projects", None)
    if projects is not None:
        await projects.forget(project_id)
    # a workspace can hold thousands of files: deleted in a worker thread, not on the event loop
    await asyncio.to_thread(shutil.rmtree, Path(settings.workspaces_dir) / project_id, ignore_errors=True)
    return Response(status_code=204)


# ------------------------------------------------------------------ members (Settings > Members) ----
class ProjectMemberIn(BaseModel):
    """One person (by the email they are a member of the organisation with) or one SCIM group (by its name)."""

    email: str | None = Field(None, pattern=r"^[^@\s]+@[^@\s]+$", max_length=320)
    group: str | None = Field(None, min_length=1, max_length=200, description="a SCIM group's display name")
    role: Role

    @model_validator(mode="after")
    def _one(self) -> Self:
        if (self.email is None) == (self.group is None):
            raise ValueError("name one person (email) or one group, not both")
        return self


class ProjectMemberOut(BaseModel):
    id: str
    kind: Literal["user", "group"]
    user_id: str | None = None
    email: str | None = None
    display_name: str | None = None
    group_id: str | None = None
    group: str | None = Field(None, description="the SCIM group's display name")
    role: Role
    created_at: datetime


def _member(b, u: AppUser | None, g: ScimGroup | None) -> ProjectMemberOut:
    return ProjectMemberOut(
        id=b.id, kind="user" if b.user_id else "group", user_id=b.user_id, email=u.email if u else None,
        display_name=u.display_name if u else None, group_id=b.group_id, group=g.display_name if g else None,
        role=b.role, created_at=b.created_at,
    )  # fmt: skip


@router.get("/projects/{project_id}/members", response_model=list[ProjectMemberOut])
async def list_project_members(project_id: str, session: AsyncSession = Depends(get_session)) -> list[ProjectMemberOut]:
    """The people and SCIM groups bound to the project, with their role there. Organisation admins are admins of every
    project without being listed."""
    return [_member(b, u, g) for b, u, g in await ProjectMemberRepo(session).list(project_id)]


@router.put("/projects/{project_id}/members", response_model=ProjectMemberOut, dependencies=[Depends(members_here)])
async def put_project_member(
    project_id: str,
    body: ProjectMemberIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> ProjectMemberOut:
    """Add a member of the organisation, or one of its SCIM groups, to the project with a role, or change that role
    (project admins and organisation admins). The role may be above their organisation role: it applies here only."""
    refuse_token(user, "change who works in a project")
    project = await ProjectRepo(session).require(project_id)
    repo = ProjectMemberRepo(session)
    if body.email is not None:
        email = body.email.strip().lower()
        found = await session.scalar(
            select(AppUser)
            .join(Membership, Membership.user_id == AppUser.id)
            .where(Membership.org_id == project.org_id, func.lower(AppUser.email) == email)
        )
        if found is None:
            raise InvalidInput(f"{email} is not a member of the organisation: invite them in Settings > Members first")
        row, before = await repo.bind(project_id, body.role, user_id=found.id, by=user.id)
        target = f"user_id={found.id}"
        u, g = found, None
    else:
        name = (body.group or "").strip()
        groups = await session.scalars(select(ScimGroup).where(ScimGroup.org_id == project.org_id))
        g = next((x for x in groups if x.display_name.casefold() == name.casefold()), None)
        if g is None:
            raise InvalidInput(f"the identity provider has not provisioned a group named '{name}' over SCIM")
        row, before = await repo.bind(project_id, body.role, group_id=g.id, by=user.id)
        target, u = f"group_id={g.id}", None
    note(request, member=target, role=body.role, before=before)
    return _member(row, u, g)


@router.delete("/projects/{project_id}/members/{member_id}", status_code=204, dependencies=[Depends(members_here)])
async def delete_project_member(
    project_id: str,
    member_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> Response:
    """Take a person or group out of the project. With the organisation's projects private, they no longer see it
    (unless another binding, or being an organisation admin, still lets them)."""
    refuse_token(user, "change who works in a project")
    row = await ProjectMemberRepo(session).remove(project_id, member_id)
    note(request, member=f"user_id={row.user_id}" if row.user_id else f"group_id={row.group_id}", role=row.role)
    return Response(status_code=204)


# ------------------------------------------------------------------ model defaults (Settings > Models) ----
class ModelDefaultsIn(BaseModel):
    """Leave a purpose out to keep its default; null clears it (the project's model, or built-in embeddings)."""

    judge: str | None = Field(None, pattern=r"^[a-z0-9_\-]+:.+$", max_length=200)
    simulated_user: str | None = Field(None, pattern=r"^[a-z0-9_\-]+:.+$", max_length=200)
    embedding: str | None = Field(None, pattern=r"^([a-z0-9_\-]+:.+|builtin)$", max_length=200)


class ModelDefaultsOut(BaseModel):
    defaults: dict[str, str] = Field(description="the purposes with a default of their own")
    resolved: list[model_defaults.Resolved] = Field(description="every purpose: its model and where it comes from")


def _defaults_out(p: Project) -> ModelDefaultsOut:
    return ModelDefaultsOut(
        defaults=model_defaults.stored(p.settings),
        resolved=[model_defaults.resolve(p.settings, purpose) for purpose in model_defaults.PURPOSES],
    )


@router.get("/projects/{project_id}/model-defaults", response_model=ModelDefaultsOut)
async def get_model_defaults(project_id: str, session: AsyncSession = Depends(get_session)) -> ModelDefaultsOut:
    """The project's models by purpose: chat, judges, simulated users, embeddings, and why each is what it is."""
    return _defaults_out(await ProjectRepo(session).require(project_id))


@router.put("/projects/{project_id}/model-defaults", response_model=ModelDefaultsOut)
async def put_model_defaults(
    project_id: str, body: ModelDefaultsIn, session: AsyncSession = Depends(get_session)
) -> ModelDefaultsOut:
    p = await ProjectRepo(session).require(project_id, lock=True)
    merged = {**model_defaults.stored(p.settings), **body.model_dump(include=body.model_fields_set)}
    for purpose, model in merged.items():
        if model and model != model_defaults.BUILTIN_EMBEDDING and not default_registry().usable_model(model):
            raise InvalidInput(f"{purpose}: '{model}' is not a catalog model or a known provider's model")
    p.settings = {**(p.settings or {}), model_defaults.KEY: {k: v for k, v in merged.items() if v}}
    await session.commit()
    return _defaults_out(p)


# ------------------------------------------------------------- spending caps (Settings > Spending) ----
class ProjectCapsOut(BaseModel):
    own: ProjectCaps = Field(description="the project's own caps (null: none)")
    organisation: dict[str, float | None] = Field(description="the organisation's caps, which bind the project too")
    day: CapUse
    month: CapUse


@router.get("/projects/{project_id}/spending-caps", response_model=ProjectCapsOut)
async def get_spending_caps(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> ProjectCapsOut:
    """The project's spending caps, the organisation's that bind it too, and what the project spent today and this
    month (UTC), as the caps count it (project admins and organisation admins)."""
    p = await ProjectRepo(session).require(project_id)
    org = await session.get(Organization, p.org_id)
    eff = org_settings.effective(settings, org.settings if org else None)
    own = spend_caps.project_caps(p.settings)
    day, month = (await spend_caps.spent_by_project(session, p.org_id)).get(p.id, (0.0, 0.0))
    labels = {name: label for name, (label, _) in spend_caps.periods().items()}
    return ProjectCapsOut(
        own=ProjectCaps.model_validate(own),
        organisation={k: eff[k] or None for k in spend_caps.CAP_FIELDS},
        day=CapUse(period="day", period_start=labels["day"], limit_usd=own.get("max_cost_usd_day"),
                   spent_usd=round(day, 6)),
        month=CapUse(period="month", period_start=labels["month"], limit_usd=own.get("max_cost_usd_month"),
                     spent_usd=round(month, 6)),
    )  # fmt: skip


@router.put("/projects/{project_id}/spending-caps", response_model=ProjectCaps)
async def put_spending_caps(
    project_id: str,
    body: ProjectCaps,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> ProjectCaps:
    """Cap what the project spends a UTC day and a calendar month, in USD (project admins and organisation admins). A
    field left out keeps its value, null removes the cap. Never above the organisation's cap, which binds the project
    as well; at a cap, the project's new runs are refused (429) and its runs in flight stop."""
    p = await ProjectRepo(session).require(project_id, lock=True)
    changes = body.model_dump(include=body.model_fields_set)
    org = await session.get(Organization, p.org_id)
    spend_caps.check_project(changes, org_settings.effective(settings, org.settings if org else None))
    merged = {**spend_caps.project_caps(p.settings), **changes}
    caps = {k: v for k, v in merged.items() if v is not None}
    p.settings = {**{k: v for k, v in (p.settings or {}).items() if k != spend_caps.PROJECT_KEY},
                  **({spend_caps.PROJECT_KEY: caps} if caps else {})}  # fmt: skip
    await session.commit()
    request.app.state.spend_caps.forget()  # this server applies the new caps at once, the others within seconds
    note(request, **changes)
    return ProjectCaps.model_validate(caps)
