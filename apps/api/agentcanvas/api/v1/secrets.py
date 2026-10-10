"""Project secrets API (M5-T6): names only ever leave the server."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.repositories import ProjectRepo
from agentcanvas.ir.models import SECRET_NAME
from agentcanvas.services.secrets import SecretService

router = APIRouter(prefix="/projects/{project_id}/secrets", tags=["secrets"])
NAME = Path(pattern=SECRET_NAME.pattern, description="a variable-safe name")


class SecretOut(BaseModel):
    name: str
    created_at: datetime


class SecretIn(BaseModel):
    value: str


def service(request: Request, session: AsyncSession = Depends(get_session)) -> SecretService:
    return SecretService(session, request.app.state.keyring)


@router.get("", response_model=list[SecretOut])
async def list_secrets(
    project_id: str, svc: SecretService = Depends(service), session: AsyncSession = Depends(get_session)
) -> list[SecretOut]:
    await ProjectRepo(session).require(project_id)
    return [SecretOut(name=s.name, created_at=s.created_at) for s in await svc.names(project_id)]


@router.put("/{name}", status_code=204)
async def put_secret(
    project_id: str,
    body: SecretIn,
    request: Request,
    name: str = NAME,
    svc: SecretService = Depends(service),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> Response:
    await ProjectRepo(session).require(project_id)
    await svc.put(project_id, name, body.value, user.id)
    await request.app.state.secrets.refresh(project_id)  # runs here see it right away, other instances soon
    return Response(status_code=204)


@router.delete("/{name}", status_code=204)
async def delete_secret(
    project_id: str, request: Request, name: str = NAME, svc: SecretService = Depends(service)
) -> Response:
    await svc.delete(project_id, name)
    await request.app.state.secrets.refresh(project_id)
    return Response(status_code=204)
