"""Events as code (FR-X-22): send an event to the project's handlers, dry-run it, read the event log."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.models import EventLog
from agentcanvas.db.repositories import ProjectRepo

router = APIRouter(prefix="/projects/{project_id}/events", tags=["events"])


class EventIn(BaseModel):
    type: str = Field(pattern=r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$", examples=["crm.lead.created"])
    data: dict[str, Any] = Field(default_factory=dict)


class EventLogOut(BaseModel):
    id: str
    type: str
    source: str
    status: str
    data: dict[str, Any]
    results: list[dict[str, Any]]
    created_at: datetime


async def _deliver(
    project_id: str, body: EventIn, request: Request, session: AsyncSession, user: CurrentUser, dry: bool
):
    await ProjectRepo(session).require(project_id)
    result = await request.app.state.events.dispatch(project_id, body.type, body.data, dry_run=dry, user_id=user.id)
    return asdict(result)


@router.post("")
async def send_event(
    project_id: str,
    body: EventIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> dict[str, Any]:
    """Deliver an event: matching handlers run and the actions they record are performed."""
    return await _deliver(project_id, body, request, session, user, dry=False)


@router.post("/dry-run")
async def dry_run(
    project_id: str,
    body: EventIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> dict[str, Any]:
    """Replay an event against the handlers and show what they *would* do — nothing is started or logged."""
    return await _deliver(project_id, body, request, session, user, dry=True)


@router.get("", response_model=list[EventLogOut])
async def event_log(
    project_id: str, limit: int = Query(50, ge=1, le=500), session: AsyncSession = Depends(get_session)
) -> list[EventLogOut]:
    q = (
        select(EventLog)
        .where(EventLog.project_id == project_id)
        .order_by(EventLog.created_at.desc(), EventLog.id.desc())
    )
    rows = (await session.scalars(q.limit(limit))).all()
    return [
        EventLogOut(
            id=r.id,
            type=r.type,
            source=r.source,
            status=r.status,
            data=r.data,
            results=r.results,
            created_at=r.created_at,
        )
        for r in rows
    ]
