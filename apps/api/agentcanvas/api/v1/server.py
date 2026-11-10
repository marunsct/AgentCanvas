"""The server's own settings as Settings > Server shows them (admins, read-only): what this server does and which
environment variable changes it. Values that are secrets, or addresses that carry credentials, are never shown.
Also the release it runs (Settings > About) and its OpenAPI document, for every signed-in user."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agentcanvas import version
from agentcanvas.adapters import installed_adapters

router = APIRouter(tags=["server"])


class ServerSetting(BaseModel):
    group: str
    label: str
    value: str
    env: str = Field(description="the environment variable an operator sets it with")


class ReleaseOut(BaseModel):
    version: str = Field(description="the release, e.g. 0.33.0")
    commit: str | None = Field(description="the git commit the server's image was built from, when it was given")


@router.get("/version", response_model=ReleaseOut)
async def server_version() -> ReleaseOut:
    """The release and commit this server runs (Settings > About), for any signed-in user: what support asks for."""
    return ReleaseOut(version=version.VERSION, commit=version.COMMIT)


@router.get("/openapi.json", include_in_schema=False)
async def api_description(request: Request) -> JSONResponse:
    """This server's OpenAPI document, for signed-in callers (people and API tokens): in production `/openapi.json`
    and `/docs` are off for anonymous callers, since they map the attack surface (S15), but integrators and client
    generators still need the reference of the release they talk to."""
    return JSONResponse(request.app.openapi())


def _days(n: int) -> str:
    return "kept forever" if n == 0 else f"{n} days"


def _mb(n: float) -> str:
    return "no limit" if n == 0 else f"{n:g} MB"


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
            ("allowed, in the sandbox" if s.sandbox_mcp else "allowed") if s.allow_local_mcp else "not allowed",
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
        (
            "Limits",
            "Runs at once per organisation (unless it sets a lower limit)",
            str(s.max_concurrent_runs) if s.max_concurrent_runs else "no limit",
            "AGENTCANVAS_MAX_CONCURRENT_RUNS",
        ),
        (
            "Limits",
            "Longest wait for a run slot",
            f"{s.max_queue_wait_s:g} seconds" if s.max_queue_wait_s else "no limit",
            "AGENTCANVAS_MAX_QUEUE_WAIT_S",
        ),
        (
            "Limits",
            "Spending cap per organisation a day (unless it sets a lower one)",
            f"{s.max_cost_usd_day:g} USD" if s.max_cost_usd_day else "no cap",
            "AGENTCANVAS_MAX_COST_USD_DAY",
        ),
        (
            "Limits",
            "Spending cap per organisation a month (unless it sets a lower one)",
            f"{s.max_cost_usd_month:g} USD" if s.max_cost_usd_month else "no cap",
            "AGENTCANVAS_MAX_COST_USD_MONTH",
        ),
        ("Limits", "Steps per run", str(s.recursion_limit), "AGENTCANVAS_RECURSION_LIMIT"),
        ("Limits", "Largest attached file", _mb(s.max_upload_mb), "AGENTCANVAS_MAX_UPLOAD_MB"),
        ("Limits", "Attached files kept per project", _mb(s.max_project_files_mb), "AGENTCANVAS_MAX_PROJECT_FILES_MB"),
        (
            "Retention",
            "Conversations (unless the organisation sets its own)",
            _days(s.retention_days),
            "AGENTCANVAS_RETENTION_DAYS",
        ),
        ("Retention", "Audit log", _days(s.audit_retention_days), "AGENTCANVAS_AUDIT_RETENTION_DAYS"),
        ("Retention", "Long-term memories", _days(s.memory_retention_days), "AGENTCANVAS_MEMORY_RETENTION_DAYS"),
        ("Retention", "Undecided approvals", _days(s.approval_expiry_days), "AGENTCANVAS_APPROVAL_EXPIRY_DAYS"),
        ("Observability", "Prometheus metrics", "on" if s.metrics_enabled else "off", "AGENTCANVAS_METRICS_ENABLED"),
        (
            "Notifications",
            "Mail server for approval e-mails",
            f"{s.smtp_host}:{s.smtp_port} ({s.smtp_security})" if s.smtp_host and s.smtp_from else "none",
            "AGENTCANVAS_SMTP_HOST",
        ),
        (
            "Notifications",
            "Webhooks may reach private addresses",
            "yes" if s.notify_allow_private else "no",
            "AGENTCANVAS_NOTIFY_ALLOW_PRIVATE",
        ),
    ]
    return [ServerSetting(group=g, label=label, value=v, env=e) for g, label, v, e in rows]
