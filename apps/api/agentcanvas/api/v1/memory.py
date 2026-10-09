"""Memory inspector (M4-T12, FR-MEM-04): browse, search and delete long-term memories; erase a namespace.

Runs keep each organisation's memories under its own root ("org:<id>", see the adapter's `org_prefix`). The
inspector works inside the caller's organisation only: namespaces are shown and taken without that root."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel

from agentcanvas.adapters.spi import MemoryAbility, require
from agentcanvas.api.audit import note
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


def _root(user: CurrentUser) -> tuple[str, ...]:
    return (f"org:{user.org_id}",)


def _parse(namespace: str, user: CurrentUser, *, allow_empty: bool = False) -> tuple[str, ...]:
    """The store namespace for `namespace`, inside the caller's organisation."""
    parts = tuple(p for p in namespace.strip("/").split("/") if p)
    if not parts and not allow_empty:
        raise InvalidInput("namespace is required, e.g. memories/user/<user id>")
    # only admins look into other users' memories
    if user.role != "admin" and user.id not in parts:
        raise Forbidden("you can only inspect your own memories")
    return _root(user) + parts


def _memory(r: Runner) -> MemoryAbility:
    return require(r.adapter, "memory_store", MemoryAbility)


@router.get("/namespaces", response_model=list[NamespaceOut])
async def namespaces(
    prefix: str = "", r: Runner = Depends(runner), user: CurrentUser = Depends(current_user)
) -> list[NamespaceOut]:
    if user.role == "admin":
        found = await _memory(r).memory_namespaces(_parse(prefix, user, allow_empty=True))
    else:  # everyone else sees only the namespaces that are theirs
        parts = tuple(p for p in prefix.strip("/").split("/") if p)
        found = await _memory(r).memory_namespaces(_root(user) + parts, limit=10_000)
        found = sorted(ns for ns in found if user.id in ns[len(_root(user)) :])[:200]
    own = [ns[len(_root(user)) :] for ns in sorted(found)]
    return [NamespaceOut(namespace="/".join(ns), parts=list(ns)) for ns in own]


@router.get("/items", response_model=list[MemoryItem])
async def items(
    namespace: str,
    q: str | None = None,
    limit: int = Query(50, ge=1, le=500),
    r: Runner = Depends(runner),
    user: CurrentUser = Depends(current_user),
) -> list[MemoryItem]:
    found = await _memory(r).memory_items(_parse(namespace, user), q or None, limit)
    return [MemoryItem(**{**i, "namespace": i["namespace"][len(_root(user)) :]}) for i in found]


@router.delete("/items", status_code=204)
async def delete_item(
    namespace: str,
    key: str,
    request: Request,
    r: Runner = Depends(runner),
    user: CurrentUser = Depends(current_user),
) -> Response:
    await _memory(r).memory_delete(_parse(namespace, user), key)
    note(request, target=f"namespace={namespace}", key=key)
    return Response(status_code=204)


@router.delete("/namespaces")
async def erase(
    namespace: str, request: Request, r: Runner = Depends(runner), user: CurrentUser = Depends(current_user)
) -> dict:
    """Delete every memory under a namespace prefix (e.g. all of one user's memories)."""
    deleted = await _memory(r).memory_erase(_parse(namespace, user))
    note(request, target=f"namespace={namespace}", deleted=deleted)
    return {"deleted": deleted}
