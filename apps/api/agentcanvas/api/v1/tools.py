"""Tool discovery: browse an MCP server's tools before wiring them (M3-T5)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from agentcanvas.adapters.spi import McpAbility, require
from agentcanvas.api.deps import get_settings_dep
from agentcanvas.api.v1.runs import runner
from agentcanvas.errors import Forbidden, UpstreamError
from agentcanvas.runtime.runner import Runner
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


@router.post("/mcp/inspect", response_model=list[McpToolInfo])
async def inspect_mcp(
    body: McpInspectRequest,
    request: Request,
    project_id: str = Query(description="the project whose secrets the server's headers may reference"),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
) -> list[McpToolInfo]:
    mcp = require(r.adapter, "mcp_inspect", McpAbility)
    cfg = body.config
    url = str(cfg.get("url") or "")
    local = cfg.get("transport") == "stdio" or not url.startswith(("http://", "https://"))
    if local and not settings.allow_local_mcp:  # same condition as validator E042
        raise Forbidden("local (stdio) MCP servers are disabled; an admin must enable allow_local_mcp")
    try:
        # the project's own resources (its secrets fill `$secret` headers); the org guard checked project_id
        factory = getattr(request.app.state, "resources_factory", None)
        resources = factory("mcp-inspect", None) if factory else request.app.state.projects.resources(project_id)
        tools = await mcp.inspect_mcp(body.name, cfg, resources)
    except Exception as exc:  # network errors, protocol errors, auth failures
        raise UpstreamError(f"could not list tools of '{body.name}': {type(exc).__name__}: {exc}") from exc
    return [McpToolInfo(**t) for t in tools]
