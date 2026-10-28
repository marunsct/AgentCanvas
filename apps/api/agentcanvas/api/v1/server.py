"""The server's own settings as Settings > Server shows them (admins, read-only): what this server does and which
environment variable changes it. Values that are secrets, or addresses that carry credentials, are never shown."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from agentcanvas.adapters import installed_adapters

router = APIRouter(tags=["server"])


class ServerSetting(BaseModel):
    group: str
    label: str
    value: str
    env: str = Field(description="the environment variable an operator sets it with")


def _days(n: int) -> str:
    return "kept forever" if n == 0 else f"{n} days"


@router.get("/server/settings", response_model=list[ServerSetting])
async def server_settings(request: Request) -> list[ServerSetting]:
    """The effective server settings an admin may want to know (never a secret's value)."""
    s = request.app.state.settings
    database = "PostgreSQL" if s.database_url.startswith("postgres") else "SQLite (single server, local use)"
    egress = (
        "through the egress proxy" if s.script_execution == "remote" and s.egress_proxy
        else "through an egress container the server starts" if s.script_execution == "docker"
        else "LangSmith's proxy" if s.script_execution == "langsmith"
        else "not possible in this mode (no list is accepted in staging or production)"
    )  # fmt: skip
    rows = [
        ("Server", "Environment", s.env, "AGENTCANVAS_ENV"),
        ("Server", "Edition", s.edition, "AGENTCANVAS_EDITION"),
        ("Server", "Database", database, "DATABASE_URL"),
        (
            "Sign-in",
            "Sign-in",
            {"dev": "none (development)", "oidc": "OpenID Connect", "matura": "Matura"}[s.auth_mode],
            "AGENTCANVAS_AUTH_MODE",
        ),
        ("Sign-in", "Identity provider", s.oidc_issuer or "none", "AGENTCANVAS_OIDC_ISSUER"),
        ("Workspace code", "Where it runs", s.script_execution, "AGENTCANVAS_SCRIPT_EXECUTION"),
        ("Workspace code", "Network allow-lists", egress, "AGENTCANVAS_EGRESS_PROXY"),
        (
            "Workspace code",
            "Local MCP servers",
            "allowed" if s.allow_local_mcp else "not allowed",
            "AGENTCANVAS_ALLOW_LOCAL_MCP",
        ),
        (
            "Frameworks",
            "Offered",
            ", ".join(
                a.manifest.label if getattr(a, "manifest", None) else a.id for a in installed_adapters().values()
            ),
            "AGENTCANVAS_ADAPTERS",
        ),
        (
            "Limits",
            "Calls per deployment",
            f"{s.rate_limit_per_deployment} a minute",
            "AGENTCANVAS_RATE_LIMIT_PER_DEPLOYMENT",
        ),
        ("Limits", "Calls per webhook", f"{s.rate_limit_per_webhook} a minute", "AGENTCANVAS_RATE_LIMIT_PER_WEBHOOK"),
        ("Limits", "Runs per project", f"{s.rate_limit_per_project} a minute", "AGENTCANVAS_RATE_LIMIT_PER_PROJECT"),
        (
            "Limits",
            "Messages per share-link visitor",
            f"{s.rate_limit_per_share_client} a minute",
            "AGENTCANVAS_RATE_LIMIT_PER_SHARE_CLIENT",
        ),
        ("Limits", "Steps per run", str(s.recursion_limit), "AGENTCANVAS_RECURSION_LIMIT"),
        (
            "Retention",
            "Conversations (unless the organisation sets its own)",
            _days(s.retention_days),
            "AGENTCANVAS_RETENTION_DAYS",
        ),
        ("Retention", "Audit log", _days(s.audit_retention_days), "AGENTCANVAS_AUDIT_RETENTION_DAYS"),
        ("Retention", "Long-term memories", _days(s.memory_retention_days), "AGENTCANVAS_MEMORY_RETENTION_DAYS"),
        ("Observability", "Prometheus metrics", "on" if s.metrics_enabled else "off", "AGENTCANVAS_METRICS_ENABLED"),
    ]
    return [ServerSetting(group=g, label=label, value=v, env=e) for g, label, v, e in rows]
