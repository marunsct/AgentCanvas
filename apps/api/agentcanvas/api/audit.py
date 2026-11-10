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
    "api_run_wait", "api_run_stream", "api_run_start", "webhook", "check_expression", "validate_workflow",
    "inspect_mcp", "dry_run", "set_layout", "share_run_stream",
}  # fmt: skip
# public run calls that are recorded only when they carry a decision (an AG-UI resume): the route notes it
NOTED_ONLY = {"api_ag_ui"}
# path parameters, most specific first: the first one present is the row's target
TARGETS = ("interrupt_id", "run_id", "thread_id", "trigger_id", "dep_id", "ex_id", "ds_id", "evaluator_id", "exp_id",
           "oev_id", "item_id", "aq_id", "case_id", "policy_id", "token_id", "name", "environment", "user_id", "wf_id",
           "project_id")  # fmt: skip


# the credentials whose first characters are shown in the app to tell them apart (and are safe to log)
CREDENTIALS = {"act_": "personal token", "scim_": "SCIM token", "ac_": "deployment key", "sh_": "share link token"}


def credential_hint(request: Request) -> str:
    """What credential a request carried, by kind and the first characters the app shows: never the whole value."""
    scheme, _, value = (request.headers.get("authorization") or "").partition(" ")
    value = value.strip()
    if not value:
        return "none"
    if scheme.lower() != "bearer":
        return f"{scheme.lower()[:10]} credentials"
    kind = next((k for p, k in CREDENTIALS.items() if value.startswith(p)), None)
    if kind is not None:
        return f"{kind} {value[:10]}"
    return "sign-in token" if value.count(".") == 2 else "unknown bearer token"


def log_refusal(request: Request, response: Response) -> None:
    """A refused sign-in (401) or action (403) is a warning with what was tried (ASVS V16): enough to tell a token
    spray or a role probe from noise, with the request id already bound to the line."""
    if response.status_code not in (401, 403):
        return
    user = getattr(request.state, "user", None)
    log.warning(
        "auth.refused" if response.status_code == 401 else "auth.forbidden",
        method=request.method, path=request.url.path, route=getattr(request.scope.get("route"), "name", None),
        credential=credential_hint(request), ip=request.client.host if request.client else None,
        org_id=user.org_id if user is not None else request.headers.get("x-org-id"),
        user_id=user.id if user is not None else None,
    )  # fmt: skip


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
    if name in NOTED_ONLY and not getattr(request.state, "audit", None):
        return
    if not request.url.path.startswith("/v1/"):
        return
    params = {k: str(v) for k, v in request.path_params.items()}
    user = getattr(request.state, "user", None)
    try:  # the change is already committed: a failing lookup must not turn it into a 500 the client retries
        org_id = user.org_id if user is not None else await _org_of_public_call(request, params)
    except Exception:
        log.exception("audit.org_lookup_failed", action=name)
        return
    if org_id is None:
        return
    extra = dict(getattr(request.state, "audit", {}))
    # a route that changes something not named in its URL (a member by email, a memory key) names it itself
    target = extra.pop("target", None) or next((f"{k}={params[k]}" for k in TARGETS if k in params), request.url.path)
    detail: dict[str, Any] = {"method": request.method, "path": request.url.path, "status": response.status_code}
    if params:
        detail["params"] = params
    if user is None:
        who = getattr(request.state, "signed_in", None)  # a person signed in to a deployment with OAuth (E-11)
        detail.update({"caller": "oauth", "end_user": who.user_id} if who else {"caller": "deployment key"})
    elif user.token_id:
        detail["caller"] = f"personal token {user.token_id}"
    detail.update(extra)
    row = AuditLog(
        org_id=org_id,
        actor_id=user.id if user is not None and user.role != "api" else None,
        action=name,
        target=target,
        detail=detail,
        ip=request.client.host if request.client else None,  # the caller behind proxies with uvicorn --proxy-headers
    )
    try:  # forwarded entries also leave the server (services/audit_sink.py)
        async with request.app.state.db.sessionmaker() as s:
            await AuditRepo(s).add(row)
    except Exception:
        log.exception("audit.write_failed", action=name, target=target)


async def record_read(request: Request, action: str, target: str, **detail: Any) -> None:
    """Record a read that is security-relevant, which `record` leaves out (reads are not changes): an export of a
    person's data (E-7). Written before the data is sent, so a failure to record refuses the export."""
    user = request.state.user
    row = AuditLog(
        org_id=user.org_id, actor_id=user.id, action=action, target=target,
        detail={"method": request.method, "path": request.url.path, **detail,
                **({"caller": f"personal token {user.token_id}"} if user.token_id else {})},
        ip=request.client.host if request.client else None,
    )  # fmt: skip
    async with request.app.state.db.sessionmaker() as s:
        await AuditRepo(s).add(row)
