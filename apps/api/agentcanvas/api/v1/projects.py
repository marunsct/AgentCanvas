"""Projects (single organisation until M7)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.repositories import ProjectRepo

router = APIRouter(tags=["projects"])


class ProjectOut(BaseModel):
    id: str
    name: str
    created_at: datetime


@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(
    session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> list[ProjectOut]:
    rows = await ProjectRepo(session).list(user.org_id)
    return [ProjectOut(id=p.id, name=p.name, created_at=p.created_at) for p in rows]
