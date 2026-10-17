"""Projects of the signed-in user's organisation (M7-T2), optionally grouped by Matura workspace (TS26)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session, get_settings_dep
from agentcanvas.db.models import Project
from agentcanvas.db.repositories import ProjectRepo
from agentcanvas.errors import InvalidInput
from agentcanvas.services import matura
from agentcanvas.settings import Settings

router = APIRouter(tags=["projects"])


class ProjectOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    workspace_id: str | None = None  # the Matura workspace it belongs to (Matura mode); None = none


class WorkspaceOut(BaseModel):
    id: str
    name: str
    is_default: bool


def _out(p: Project) -> ProjectOut:
    return ProjectOut(id=p.id, name=p.name, created_at=p.created_at, workspace_id=p.workspace_id)


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
    workspace_id: str | None = Query(None, description="only the projects in this Matura workspace"),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> list[ProjectOut]:
    rows = await ProjectRepo(session).list(user.org_id, workspace_id)
    return [_out(p) for p in rows]


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    workspace_id: str | None = None


@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(
    body: ProjectIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
    settings: Settings = Depends(get_settings_dep),
) -> ProjectOut:
    """A new project in the caller's organisation (409 when the name is taken there), optionally in one of its
    Matura workspaces (422 when that is not an active workspace of the organisation)."""
    await _checked(session, settings, user, body.workspace_id)
    return _out(await ProjectRepo(session).create(user.org_id, body.name.strip(), body.workspace_id))


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
    """Move a project into a Matura workspace, or out of one with `workspace_id: null`."""
    await _checked(session, settings, user, body.workspace_id)
    p = await ProjectRepo(session).require(project_id)
    p.workspace_id = body.workspace_id
    await session.commit()
    return _out(p)
