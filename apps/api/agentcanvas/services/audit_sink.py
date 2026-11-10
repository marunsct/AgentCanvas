"""Forwarding security-relevant audit entries out of AgentCanvas (TS26, ASVS V16).

Every change is written to AgentCanvas's own audit log (`api/audit.py`, and SCIM and group-driven role changes from
their services). The actions below also leave the server, in every sign-in mode:

- always, as one structured log line each (`audit.event` on the `agentcanvas.audit` logger, INFO), for the log
  shipper that already collects the server's logs into a SIEM;
- with `AGENTCANVAS_AUDIT_SINK_URL` (and `AGENTCANVAS_AUDIT_SINK_SECRET`), POSTed there as a CloudEvent signed with
  `AgentCanvas-Signature` (`t=<unix time>,v1=<HMAC-SHA256 of "<t>.<body>">`, as Matura signs its webhooks);
- in Matura mode with `AGENTCANVAS_MATURA_AUDIT_URL`, POSTed to Matura (`Matura-Signature`, the webhook secret):
  the placeholder for Matura's coming audit ingest API.

Deliveries run in the background and are best effort: a failed one is logged, never retried, and never fails the
request (the local log keeps the entry).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx

from agentcanvas.log import get_logger
from agentcanvas.vendor.matura import sign

log = get_logger(__name__)
audit_log = get_logger("agentcanvas.audit")

# route names (the audit log's `action`) that change who can do what, what runs in production, or what was approved
SECURITY_ACTIONS = frozenset({
    "publish", "unpublish", "rotate_key",  # workflows published to an environment, their keys
    "put_secret", "delete_secret",  # project secrets
    "decide", "resume_thread", "api_resume",  # approvals of paused runs (inbox, canvas, public API)
    "set_member", "remove_member",  # members and roles (dev and oidc modes)
    "put_org_settings", "create_policy", "replace_policy", "delete_policy",  # organisation settings and policies
    "set_gate", "delete_gate",  # release gates
    "put_trace_export", "delete_trace_export",  # where traces (and the data in them) are sent
    "set_project_workspace",  # a project moved between Matura workspaces
    "create_token", "revoke_my_token", "revoke_org_token",  # personal API tokens (G-13)
    "create_scim_token", "delete_scim_token",  # who may provision members over SCIM (G-13)
    "scim_create_user", "scim_set_active", "scim_delete_user", "scim_role_sync",  # the identity provider over SCIM
    "scim_create_group", "scim_replace_group", "scim_patch_group", "scim_delete_group",
    "sso_role_sync", "sso_member_removed",  # roles from the sign-in token's groups
})  # fmt: skip


@dataclass(frozen=True)
class AuditEvent:
    org_id: str
    actor_id: str | None
    action: str
    target: str
    detail: dict[str, Any]
    ip: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


class AuditSink(Protocol):
    async def emit(self, event: AuditEvent) -> None:
        """Hand one entry over. Must return quickly and never raise."""
        ...


class LogSink:
    """One structured log line per entry: what a log shipper sends on to a SIEM."""

    async def emit(self, event: AuditEvent) -> None:
        audit_log.info(
            "audit.event", org_id=event.org_id, actor_id=event.actor_id, action=event.action, target=event.target,
            detail=event.detail, ip=event.ip, at=event.at.isoformat(),
        )  # fmt: skip


class WebhookSink:
    """POSTs entries to `url` as CloudEvents signed with `secret` in the `header` header."""

    header = "agentcanvas-signature"

    def __init__(self, url: str, secret: str, source: str = "agentcanvas", client: httpx.AsyncClient | None = None):
        self.url, self.secret, self.source = url, secret, source
        self.client = client or httpx.AsyncClient(timeout=5.0)
        self._pending: set[asyncio.Task[None]] = set()

    @staticmethod
    def cloudevent(event: AuditEvent, source: str) -> dict[str, Any]:
        data = asdict(event)
        data["at"] = event.at.isoformat()
        return {
            "specversion": "1.0", "id": f"evt_{uuid.uuid4().hex}", "source": source,
            "type": f"agentcanvas.audit.{event.action}", "time": data["at"], "tenantid": event.org_id,
            "datacontenttype": "application/json", "data": data,
        }  # fmt: skip

    async def emit(self, event: AuditEvent) -> None:
        task = asyncio.create_task(self._send(event))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _send(self, event: AuditEvent) -> None:
        body = json.dumps(self.cloudevent(event, self.source), default=str).encode()
        try:
            r = await self.client.post(
                self.url, content=body,
                headers={"content-type": "application/cloudevents+json", self.header: sign(self.secret, body)},
            )  # fmt: skip
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - best effort; the local audit log has the entry
            log.warning("audit.forward_failed", action=event.action, error=str(exc))

    async def drain(self) -> None:
        """Wait for deliveries in flight (shutdown, tests)."""
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    async def aclose(self) -> None:
        await self.drain()
        await self.client.aclose()


class MaturaWebhookSink(WebhookSink):
    """The placeholder until Matura has an audit ingest API: signed like Matura's own webhooks."""

    header = "matura-signature"


class Sinks:
    """The log line, then the configured endpoints."""

    def __init__(self, *sinks: AuditSink):
        self.sinks = sinks

    @property
    def webhook(self) -> WebhookSink | None:
        return next((s for s in self.sinks if isinstance(s, WebhookSink)), None)

    async def emit(self, event: AuditEvent) -> None:
        for sink in self.sinks:
            await sink.emit(event)

    async def aclose(self) -> None:
        for sink in self.sinks:
            if isinstance(sink, WebhookSink):
                await sink.aclose()


def sink_for(settings: Any) -> Sinks:
    sinks: list[AuditSink] = [LogSink()]
    if settings.audit_sink_url:
        if not settings.audit_sink_secret:
            raise ValueError("AGENTCANVAS_AUDIT_SINK_URL needs AGENTCANVAS_AUDIT_SINK_SECRET to sign the events")
        sinks.append(WebhookSink(settings.audit_sink_url, settings.audit_sink_secret))
    if settings.auth_mode == "matura" and settings.matura_audit_url and settings.matura_webhook_secret:
        sinks.append(MaturaWebhookSink(settings.matura_audit_url, settings.matura_webhook_secret))
    return Sinks(*sinks)
