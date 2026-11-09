"""Server settings an organisation's admin may override (Settings > Organisation): local MCP servers, the rate limits
and two retentions. The server's environment variable stays the default, and where security or the operator's
capacity is at stake it is also the ceiling, applied every time a value is read (so an operator who tightens the
server later tightens every organisation too):

- `allow_local_mcp`: an organisation may switch local (stdio) MCP servers off, never on when the server forbids them;
- the four rate limits and the concurrent-run limit: an organisation may lower them, never raise them above the
  server's (when the server's is 0, unlimited, any limit is lower);
- `audit_retention_days`: an organisation may keep its audit log longer than the server does (0, forever, included),
  never shorter: the log is how the operator and the organisation answer for changes;
- `memory_retention_days`: any number of days (0, forever, included): deleting sooner is the privacy-friendly way.

Conversation retention (`Organization.retention_days`) was already an organisation setting and has no ceiling.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import Organization, Project
from agentcanvas.errors import InvalidInput
from agentcanvas.settings import Settings

RATE_LIMITS = (
    "rate_limit_per_deployment",
    "rate_limit_per_webhook",
    "rate_limit_per_project",
    "rate_limit_per_share_client",
)
CEILINGS = (*RATE_LIMITS, "max_concurrent_runs")  # the organisation's may be lower than the server's, not higher
RETENTIONS = ("audit_retention_days", "memory_retention_days")
FIELDS = ("allow_local_mcp", *CEILINGS, *RETENTIONS)


class OrgLimits(BaseModel):
    """What an organisation sets for itself; a field left out keeps its value, null returns it to the server's."""

    allow_local_mcp: bool | None = Field(None, description="false: no local MCP servers, even if the server allows")
    rate_limit_per_deployment: int | None = Field(
        None, ge=1, le=1_000_000, description="calls a minute, each deployment"
    )
    rate_limit_per_webhook: int | None = Field(None, ge=1, le=1_000_000, description="calls a minute per webhook")
    rate_limit_per_project: int | None = Field(None, ge=1, le=1_000_000, description="runs a minute per project")
    rate_limit_per_share_client: int | None = Field(
        None, ge=1, le=1_000_000, description="messages a minute per share-link visitor"
    )
    max_concurrent_runs: int | None = Field(
        None, ge=1, le=100_000, description="runs in flight at once; past it runs wait in a queue by priority"
    )
    audit_retention_days: int | None = Field(None, ge=0, le=3650, description="not below the server's; 0 = forever")
    memory_retention_days: int | None = Field(None, ge=0, le=3650, description="0 = forever")


def stored(org_settings: dict[str, Any] | None) -> dict[str, Any]:
    """The organisation's own values (only known fields with a value)."""
    raw = org_settings or {}
    return {k: raw[k] for k in FIELDS if raw.get(k) is not None}


def effective(server: Settings, org_settings: dict[str, Any] | None) -> dict[str, Any]:
    """What applies to the organisation: its own value within the server's ceilings, else the server's."""
    own = stored(org_settings)
    out: dict[str, Any] = {k: getattr(server, k) for k in FIELDS}
    if own.get("allow_local_mcp") is False:
        out["allow_local_mcp"] = False
    for key in CEILINGS:
        if key in own:
            ceiling = getattr(server, key)
            out[key] = own[key] if ceiling <= 0 else min(own[key], ceiling)
    if "audit_retention_days" in own:
        floor, days = server.audit_retention_days, own["audit_retention_days"]
        if floor > 0:  # the server deletes at some age: the organisation may keep longer (0 is forever)
            out["audit_retention_days"] = 0 if days == 0 else max(days, floor)
    if "memory_retention_days" in own:
        out["memory_retention_days"] = own["memory_retention_days"]
    return out


def check(server: Settings, changes: dict[str, Any]) -> None:
    """Refuse a new value the server's ceiling would not let apply (the save says why instead of ignoring it)."""
    if changes.get("allow_local_mcp") is True and not server.allow_local_mcp:
        raise InvalidInput(
            "local MCP servers are off on this server (AGENTCANVAS_ALLOW_LOCAL_MCP): an organisation cannot switch "
            "them on, only the operator can"
        )
    for key in CEILINGS:
        value, ceiling = changes.get(key), getattr(server, key)
        if value is not None and ceiling > 0 and value > ceiling:
            unit = " runs at once" if key == "max_concurrent_runs" else " a minute"
            raise InvalidInput(
                f"the server limits this to {ceiling}{unit} (AGENTCANVAS_{key.upper()}): lower it, not higher"
            )
    days = changes.get("audit_retention_days")
    floor = server.audit_retention_days
    if days is not None and days != 0 and floor <= 0:
        raise InvalidInput("the server keeps the audit log forever: an organisation cannot shorten that")
    if days is not None and days != 0 and days < floor:
        raise InvalidInput(f"the server keeps the audit log {floor} days: an organisation may keep it longer")


def apply_server(server: Settings, org_settings: dict[str, Any] | None) -> Settings:
    """A copy of the server's settings with the organisation's effective values in place of its own."""
    return server.model_copy(update=effective(server, org_settings))


async def of_org(session: AsyncSession, server: Settings, org_id: str) -> Settings:
    org = await session.get(Organization, org_id)
    return apply_server(server, org.settings if org else None)


async def of_project(session: AsyncSession, server: Settings, project_id: str | None) -> Settings:
    """The effective settings of the organisation a project belongs to (the server's when there is none)."""
    if not project_id:
        return server
    own = await session.scalar(
        select(Organization.settings).join(Project, Project.org_id == Organization.id).where(Project.id == project_id)
    )
    return apply_server(server, own)


class OrgSettingsCache:
    """The organisations' own values by project, for the paths every call takes (rate limits, the run gate): read
    from the database at most every `ttl` seconds per project. A save forgets them at once on its own replica; the
    others see it within `ttl`."""

    def __init__(self, db: Any, ttl: float = 15.0):
        self.db, self.ttl = db, ttl
        self._by_project: dict[str, tuple[float, dict[str, Any] | None]] = {}

    def forget(self) -> None:
        self._by_project.clear()

    async def for_project(self, server: Settings, project_id: str | None) -> Settings:
        if not project_id:
            return server
        now = time.monotonic()
        hit = self._by_project.get(project_id)
        if hit is None or hit[0] < now:
            async with self.db.sessionmaker() as s:
                own = await s.scalar(
                    select(Organization.settings)
                    .join(Project, Project.org_id == Organization.id)
                    .where(Project.id == project_id)
                )
            hit = self._by_project[project_id] = (now + self.ttl, own)
            if len(self._by_project) > 1000:  # a long-running server with many projects
                self._by_project = {k: v for k, v in self._by_project.items() if v[0] >= now}
        return apply_server(server, hit[1])
