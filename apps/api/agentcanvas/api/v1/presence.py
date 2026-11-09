"""/v1 presence (G-4): who else has a workflow open in the editor, and which block each one is selecting."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.models import WorkflowRow
from agentcanvas.errors import NotFound
from agentcanvas.observability import counted_stream
from agentcanvas.services.presence import Beat, Peer, PresenceHub

router = APIRouter(tags=["presence"])
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class Here(BaseModel):
    peers: list[Peer]


def hub(request: Request) -> PresenceHub:
    return request.app.state.presence


async def _workflow(session: AsyncSession, wf_id: str) -> None:
    if await session.get(WorkflowRow, wf_id) is None:
        raise NotFound(f"workflow '{wf_id}' not found")


@router.post("/workflows/{wf_id}/presence", response_model=Here)
async def heartbeat(
    wf_id: str,
    body: Beat,
    session: AsyncSession = Depends(get_session),
    presence: PresenceHub = Depends(hub),
    user: CurrentUser = Depends(current_user),
) -> Here:
    """This editor tab is here (every few seconds, and when its selection or version changes). Answers with
    everyone who is. Any role can be present: viewers watch too."""
    await _workflow(session, wf_id)
    return Here(peers=await presence.beat(wf_id, user.id, body))


@router.get("/workflows/{wf_id}/presence", response_model=Here)
async def here(wf_id: str, session: AsyncSession = Depends(get_session), presence: PresenceHub = Depends(hub)) -> Here:
    await _workflow(session, wf_id)
    return Here(peers=await presence.peers(wf_id))


@router.get("/workflows/{wf_id}/presence/stream")
async def stream(
    wf_id: str, session: AsyncSession = Depends(get_session), presence: PresenceHub = Depends(hub)
) -> StreamingResponse:
    """Server-Sent Events: `{"peers": [...]}` now and whenever someone arrives, leaves or changes their selection."""
    await _workflow(session, wf_id)
    await session.close()  # the stream holds no database connection of the request's
    return StreamingResponse(
        counted_stream(presence.stream(wf_id)), media_type="text/event-stream", headers=SSE_HEADERS
    )


@router.delete("/workflows/{wf_id}/presence/{session_id}", status_code=204)
async def leave(
    wf_id: str, session_id: str, presence: PresenceHub = Depends(hub), user: CurrentUser = Depends(current_user)
) -> Response:
    """This tab closed."""
    await presence.leave(wf_id, user.id, session_id)
    return Response(status_code=204)
