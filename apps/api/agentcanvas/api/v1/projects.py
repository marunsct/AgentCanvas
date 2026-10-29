"""Projects of the signed-in user's organisation (M7-T2), optionally grouped by Matura workspace (TS26)."""

from __future__ import annotations

import asyncio
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session, get_settings_dep
from agentcanvas.db.models import Organization, Project
from agentcanvas.db.repositories import ProjectRepo
from agentcanvas.errors import Conflict, InvalidInput
from agentcanvas.registry import default_registry
from agentcanvas.services import matura, model_defaults
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
    """Move a project into a Matura workspace, or out of one with `workspace_id: null`. Admins only."""
    await _checked(session, settings, user, body.workspace_id)
    p = await ProjectRepo(session).require(project_id)
    p.workspace_id = body.workspace_id
    await session.commit()
    return _out(p)


class ProjectRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def rename_project(
    project_id: str,
    body: ProjectRenameIn,
    session: AsyncSession = Depends(get_session),
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
    return _out(p)


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
    p = await ProjectRepo(session).require(project_id)
    # one delete at a time per organisation (a row lock on Postgres) and the count after the write (on SQLite the
    # write lock makes a parallel delete wait, then see this one): two at once cannot remove the last two projects
    await session.execute(select(Organization.id).where(Organization.id == user.org_id).with_for_update())
    await session.delete(p)
    await session.flush()
    if not await session.scalar(select(func.count()).select_from(Project).where(Project.org_id == user.org_id)):
        await session.rollback()
        raise Conflict("this is the organisation's only project: create another one before deleting it")
    await session.commit()
    projects = getattr(request.app.state, "projects", None)
    if projects is not None:
        await projects.forget(project_id)
    # a workspace can hold thousands of files: deleted in a worker thread, not on the event loop
    await asyncio.to_thread(shutil.rmtree, Path(settings.workspaces_dir) / project_id, ignore_errors=True)
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
    p = await ProjectRepo(session).require(project_id)
    merged = {**model_defaults.stored(p.settings), **body.model_dump(include=body.model_fields_set)}
    for purpose, model in merged.items():
        if model and model != model_defaults.BUILTIN_EMBEDDING and not default_registry().usable_model(model):
            raise InvalidInput(f"{purpose}: '{model}' is not a catalog model or a known provider's model")
    p.settings = {**(p.settings or {}), model_defaults.KEY: {k: v for k, v in merged.items() if v}}
    await session.commit()
    return _defaults_out(p)
