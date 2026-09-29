"""Memory inspector (M4-T12, FR-MEM-04): browse, search and delete long-term memories; erase a namespace."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.api.v1.runs import runner
from agentcanvas.errors import Forbidden, InvalidInput
from agentcanvas.runtime.runner import Runner

router = APIRouter(prefix="/memory", tags=["memory"])


class NamespaceOut(BaseModel):
    namespace: str  # "memories/user/usr_x"
    parts: list[str]


class MemoryItem(BaseModel):
    namespace: list[str]
    key: str
    value: dict[str, Any]
    created_at: str | None = None
    updated_at: str | None = None


def _parse(namespace: str, user: CurrentUser, *, allow_empty: bool = False) -> tuple[str, ...]:
    parts = tuple(p for p in namespace.strip("/").split("/") if p)
    if not parts and not allow_empty:
        raise InvalidInput("namespace is required, e.g. memories/user/<user id>")
    # until roles land (Sprint 10) only admins may look into other users' memories
    if user.role != "admin" and user.id not in parts:
        raise Forbidden("you can only inspect your own memories")
    return parts


@router.get("/namespaces", response_model=list[NamespaceOut])
async def namespaces(
    prefix: str = "", r: Runner = Depends(runner), user: CurrentUser = Depends(current_user)
) -> list[NamespaceOut]:
    found = await r.adapter.memory_namespaces(_parse(prefix, user, allow_empty=True))
    return [NamespaceOut(namespace="/".join(ns), parts=list(ns)) for ns in sorted(found)]


@router.get("/items", response_model=list[MemoryItem])
async def items(
    namespace: str,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    r: Runner = Depends(runner),
    user: CurrentUser = Depends(current_user),
) -> list[MemoryItem]:
    return [MemoryItem(**i) for i in await r.adapter.memory_items(_parse(namespace, user), q or None, limit)]


@router.delete("/items", status_code=204)
async def delete_item(
    namespace: str, key: str, r: Runner = Depends(runner), user: CurrentUser = Depends(current_user)
) -> Response:
    await r.adapter.memory_delete(_parse(namespace, user), key)
    return Response(status_code=204)


@router.delete("/namespaces")
async def erase(namespace: str, r: Runner = Depends(runner), user: CurrentUser = Depends(current_user)) -> dict:
    """Delete every memory under a namespace prefix (e.g. all of one user's memories)."""
    return {"deleted": await r.adapter.memory_erase(_parse(namespace, user))}
