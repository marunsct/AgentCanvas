"""Audit log (M7-T4): who changed what, when and from where.

Every successful request that changes something is recorded: edits, publishes, approvals, secret writes,
member and trigger changes. Reads are not. Routes add specifics through `request.state.audit` (for
example the hash of an approval's payload). High-volume public run calls are skipped; the decisions
callers make on those runs (resume) are kept.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import Request
from starlette.responses import Response

from agentcanvas.db.models import AuditLog
from agentcanvas.db.repositories import AuditRepo, ScopeRepo
from agentcanvas.log import get_logger

log = get_logger(__name__)
READS = {"GET", "HEAD", "OPTIONS"}
# public run calls (volume), checks that change nothing, and canvas positions (cosmetic)
SKIPPED = {
    "api_run_wait", "api_run_stream", "webhook", "check_expression", "validate_workflow", "inspect_mcp", "dry_run",
    "set_layout",
}  # fmt: skip
# path parameters, most specific first: the first one present is the row's target
TARGETS = ("interrupt_id", "run_id", "thread_id", "trigger_id", "dep_id", "case_id", "policy_id", "name", "user_id",
           "wf_id", "project_id")  # fmt: skip


def payload_hash(value: Any) -> str:
    """A stable fingerprint of a decision's payload: the log proves what was approved without storing it."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def note(request: Request, **detail: Any) -> None:
    """Add details to the audit row of this request."""
    request.state.audit = {**getattr(request.state, "audit", {}), **detail}


async def _org_of_public_call(request: Request, params: dict[str, str]) -> str | None:
    """Callers of the public endpoints have no user: the deployment they name gives the organisation."""
    if "dep_id" not in params:
        return None
    async with request.app.state.db.sessionmaker() as s:
        return await ScopeRepo(s).org_of("deployment", params["dep_id"])


async def record(request: Request, response: Response) -> None:
    """Write the audit row of a finished request, if it changed something."""
    route = request.scope.get("route")
    name = getattr(route, "name", None)
    if request.method in READS or name is None or name in SKIPPED or not 200 <= response.status_code < 300:
        return
    if not request.url.path.startswith("/v1/"):
        return
    params = {k: str(v) for k, v in request.path_params.items()}
    user = getattr(request.state, "user", None)
    org_id = user.org_id if user is not None else await _org_of_public_call(request, params)
    if org_id is None:
        return
    extra = dict(getattr(request.state, "audit", {}))
    # a route that changes something not named in its URL (a member by email, a memory key) names it itself
    target = extra.pop("target", None) or next((f"{k}={params[k]}" for k in TARGETS if k in params), request.url.path)
    detail: dict[str, Any] = {"method": request.method, "path": request.url.path, "status": response.status_code}
    if params:
        detail["params"] = params
    if user is None:
        detail["caller"] = "deployment key"
    detail.update(extra)
    row = AuditLog(
        org_id=org_id,
        actor_id=user.id if user is not None and user.role != "api" else None,
        action=name,
        target=target,
        detail=detail,
        ip=request.client.host if request.client else None,
    )
    try:
        async with request.app.state.db.sessionmaker() as s:
            await AuditRepo(s).add(row)
    except Exception:
        log.exception("audit.write_failed", action=name, target=target)
