"""Forwarding security-relevant audit entries out of AgentCanvas (TS26).

Every change is written to AgentCanvas's own audit log (`api/audit.py`). The actions below are also handed to an
`AuditSink`, so an organisation's security trail can live next to its sign-ins in Matura. Matura has no audit
ingest API yet (it is building the tenant audit view), so the default sink does nothing. `MaturaWebhookSink` is the
placeholder for that API: with `AGENTCANVAS_MATURA_AUDIT_URL` set it POSTs each entry as a CloudEvent signed like
Matura's own webhooks (`Matura-Signature`, HMAC-SHA256 with `AGENTCANVAS_MATURA_WEBHOOK_SECRET`), in the background
and best effort: a failed delivery is logged, never retried, and never fails the request (the local log keeps it).
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


class NoopSink:
    async def emit(self, event: AuditEvent) -> None:
        return None


class MaturaWebhookSink:
    """POSTs entries to `url` as signed CloudEvents (placeholder until Matura has an audit ingest API)."""

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
                headers={"content-type": "application/cloudevents+json", "matura-signature": sign(self.secret, body)},
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


def sink_for(settings: Any) -> AuditSink:
    if settings.auth_mode == "matura" and settings.matura_audit_url and settings.matura_webhook_secret:
        return MaturaWebhookSink(settings.matura_audit_url, settings.matura_webhook_secret)
    return NoopSink()
