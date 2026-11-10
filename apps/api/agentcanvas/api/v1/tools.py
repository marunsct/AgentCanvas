"""Tool discovery: browse an MCP server's tools before wiring them (M3-T5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from agentcanvas.adapters.spi import McpAbility, require
from agentcanvas.api.deps import get_settings_dep
from agentcanvas.api.v1.runs import runner
from agentcanvas.errors import Forbidden, InvalidInput, UpstreamError
from agentcanvas.ir.models import Workflow
from agentcanvas.runtime.identity import CURRENT, RunIdentity
from agentcanvas.runtime.runner import Runner
from agentcanvas.services.oauth import ConnectionRequired
from agentcanvas.settings import Settings

router = APIRouter(tags=["tools"])


class McpInspectRequest(BaseModel):
    name: str = Field(description="Resource name (lets dev/test hosts substitute an in-process server)")
    config: dict[str, Any]


class McpToolInfo(BaseModel):
    name: str
    description: str = ""
    destructive: bool = False
    read_only: bool = False
    hash: str | None = Field(
        None, description="SHA-256 of the tool's description, input schema and annotations: publishing pins it"
    )


@router.post("/mcp/inspect", response_model=list[McpToolInfo])
async def inspect_mcp(
    body: McpInspectRequest,
    request: Request,
    project_id: str = Query(description="the project whose secrets the server's headers may reference"),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
) -> list[McpToolInfo]:
    return [McpToolInfo(**t) for t in await list_tools(request, settings, r, project_id, body.name, body.config)]


async def list_tools(
    request: Request, settings: Settings, r: Runner, project_id: str, name: str, cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    """The tools an MCP server offers now, as the person making the request may see them."""
    mcp = require(r.adapter, "mcp_inspect", McpAbility)
    me = request.state.user
    # a connector that signs in is browsed with your own connection (the service one: admins only)
    CURRENT.set(RunIdentity(me.org_id, me.id, service_ok=me.role == "admin"))
    url = str(cfg.get("url") or "")
    local = cfg.get("transport") == "stdio" or not url.startswith(("http://", "https://"))
    settings = await request.app.state.org_settings.for_project(settings, project_id)  # the organisation's own
    if local and not settings.allow_local_mcp:  # same condition as validator E042
        raise Forbidden("local (stdio) MCP servers are disabled; an admin must enable allow_local_mcp")
    try:
        # the project's own resources (its secrets fill `$secret` headers); the org guard checked project_id
        factory = getattr(request.app.state, "resources_factory", None)
        resources = factory("mcp-inspect", None) if factory else request.app.state.projects.resources(project_id)
        return await mcp.inspect_mcp(name, cfg, resources)
    except ConnectionRequired:
        raise
    except Exception as exc:  # network errors, protocol errors, auth failures
        if (found := _connection_problem(exc)) is not None:
            raise found from exc
        raise UpstreamError(f"could not list tools of '{name}': {type(exc).__name__}: {exc}") from exc


async def refresh_pins(request: Request, wf: Workflow, project_id: str) -> dict[str, list[dict[str, Any]]]:
    """Fingerprints for connectors browsed before "Browse tools" recorded them, so publishing can pin them (E-4):
    resource id -> its tools as browsed, each with the fingerprint the server gives it now. Only tools the builder
    saw are kept, and only while they read as they did then (description, destructive, read-only); one that changed
    since refuses the publish, so the builder reviews it first. Empty when nothing is stale."""
    settings, r = request.app.state.settings, request.app.state.runner
    out: dict[str, list[dict[str, Any]]] = {}
    for res in wf.resources:
        seen = [d for d in res.config.get("discovered") or [] if isinstance(d, dict) and d.get("name")]
        if res.type != "mcp.server" or all(isinstance(d.get("hash"), str) for d in seen):
            continue
        cfg = {k: v for k, v in res.config.items() if k != "discovered"}
        live = {t["name"]: t for t in await list_tools(request, settings, r, project_id, res.name, cfg)}
        kept, changed = [], []
        for d in seen:
            now = live.get(d["name"])
            if isinstance(d.get("hash"), str):  # browsed since fingerprints are recorded
                kept.append(d)
                continue
            if now is None:  # gone from the server
                continue
            then = (d.get("description") or "", bool(d.get("destructive")), bool(d.get("read_only")))
            if then != (now["description"], now["destructive"], now["read_only"]):
                changed.append(d["name"])
            kept.append({**d, "hash": now["hash"]})
        if changed:
            raise InvalidInput(
                f"publish refused: '{res.name}' changed since its tools were browsed ({', '.join(changed)}). Select "
                "it, click Browse tools to review its tools as they are now, then publish."
            )
        out[res.id] = kept
    return out


def _connection_problem(exc: BaseException) -> ConnectionRequired | None:
    """The sign-in problem inside an error: the MCP client wraps what its transport raises in exception groups."""
    if isinstance(exc, ConnectionRequired):
        return exc
    for inner in getattr(exc, "exceptions", ()):
        if (found := _connection_problem(inner)) is not None:
            return found
    return _connection_problem(exc.__cause__) if exc.__cause__ is not None else None
