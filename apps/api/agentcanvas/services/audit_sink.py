"""Forwarding audit entries out of AgentCanvas to a SIEM, reliably (ASVS V16.4.3).

Every change is written to AgentCanvas's own audit log through `AuditRepo.add`, the one write path (requests in
`api/audit.py`, SCIM and the sign-in token's groups from their services). Security-relevant entries (the actions
below), or every entry with `AGENTCANVAS_AUDIT_FORWARD_ALL`, also leave the server, in every sign-in mode:

- always, as one structured log line each (`audit.event` on the `agentcanvas.audit` logger, INFO), for the log
  shipper that already collects the server's logs into a SIEM;
- to each configured destination, through the outbox: `AuditRepo.add` writes one `audit_outbox` row per destination
  in the same transaction as the entry, so an entry that was recorded is never lost on the way out. The destinations:
  - `webhook` (`AGENTCANVAS_AUDIT_SINK_URL`, `AGENTCANVAS_AUDIT_SINK_SECRET`): a CloudEvent signed with
    `AgentCanvas-Signature` (`t=<unix time>,v1=<HMAC-SHA256 of "<t>.<body>">`, as Matura signs its webhooks);
  - `splunk` (`AGENTCANVAS_AUDIT_SPLUNK_HEC_URL`, `AGENTCANVAS_AUDIT_SPLUNK_HEC_TOKEN`): Splunk's HTTP Event Collector;
  - `matura` (Matura mode with `AGENTCANVAS_MATURA_AUDIT_URL`): the CloudEvent signed like Matura's webhooks
    (`Matura-Signature`), the placeholder for Matura's coming audit ingest API.

`AuditForwarder` runs on every API server: it claims due outbox rows (locked with SKIP LOCKED and leased, so two
servers never send the same row at once), sends them, deletes what was delivered, and retries a failure with a
growing delay. After the last attempt the row is kept as `dead` (counted, alerted on) for an operator to retry
(`python -m agentcanvas.audit retry`). A delivery can repeat (a server that stops between sending and recording it):
the event's id is the audit entry's, so a receiver can drop the copy.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx
from sqlalchemy import delete, func, select, update

from agentcanvas import observability
from agentcanvas.db.models import AuditLog, AuditOutbox
from agentcanvas.log import get_logger
from agentcanvas.vendor.matura import sign

log = get_logger(__name__)
audit_log = get_logger("agentcanvas.audit")

# route names (the audit log's `action`) that change who can do what, what runs in production, or what was approved.
# tests/test_audit_forwarding.py fails when a new write route is neither here nor listed there as routine.
SECURITY_ACTIONS = frozenset({
    "publish", "unpublish", "rotate_key",  # workflows published to an environment, their keys
    "create_deployment_key", "revoke_deployment_key",
    "rotate_callback_secret",  # the secret that signs a deployment's run callbacks
    "update_share", "regenerate_share",  # public share links of deployments
    "create_trigger", "enable_trigger",  # webhooks and schedules: a way in that runs workflows without a person
    "put_secret", "delete_secret",  # project secrets
    "put_client", "delete_client", "callback", "delete_connection",  # OAuth app credentials and connected accounts
    "put_sandbox",  # what the project's code may reach (network, limits)
    "decide", "resume_thread", "api_resume", "share_resume",  # approvals of paused runs (inbox, canvas, API, share)
    "edit_state",  # a conversation's state changed by hand, past what was approved
    "stop_all_runs",  # the organisation's kill switch
    "set_member", "remove_member", "accept_invitation",  # members and roles (dev and oidc modes)
    "put_org_settings", "create_policy", "replace_policy", "delete_policy",  # organisation settings and policies
    "put_notifications",  # where approval notifications (and what they say) go
    "delete_project", "delete_workflow", "erase",  # deletions that cannot be undone (memory erasure)
    "connect", "disconnect", "push", "push_to_github",  # workflow code sent to a git remote
    "set_gate", "delete_gate",  # release gates
    "put_trace_export", "delete_trace_export",  # where traces (and the data in them) are sent
    "set_project_workspace",  # a project moved between Matura workspaces
    "create_token", "revoke_my_token", "revoke_org_token",  # personal API tokens
    "create_scim_token", "delete_scim_token",  # who may provision members over SCIM
    "scim_create_user", "scim_set_active", "scim_delete_user", "scim_role_sync",  # the identity provider over SCIM
    "scim_create_group", "scim_replace_group", "scim_patch_group", "scim_delete_group",
    "sso_role_sync", "sso_member_removed",  # roles from the sign-in token's groups
    "put_spending_caps", "spend_cap_warning", "spend_cap_reached",  # spending caps, and spending reaching them
    "mcp_tool_drift",  # an app connector's server changed tools a deployment pinned (runs leave them out)
    "delete_organization", "cancel_organization_deletion", "purge_organization",  # offboarding (E-7)
    "erase_member", "erase_end_user", "api_erase_end_user",  # data-subject erasure (E-7)
    "export_member", "export_end_user", "api_export_end_user",  # and access (E-7)
    "create_org_export", "create_org_export_link", "download_org_export",  # the organisation's data leaving
    "audit_outbox_retried", "audit_outbox_discarded",  # undelivered entries sent again or dropped (command line)
})  # fmt: skip

FORWARDED, FORWARD_FAILED, FORWARD_DEAD, OUTBOX = (
    observability.AUDIT_FORWARDED, observability.AUDIT_FORWARD_FAILED, observability.AUDIT_FORWARD_DEAD,
    observability.AUDIT_OUTBOX,
)  # fmt: skip

BATCH = 20  # rows claimed at a time
LEASE_S = 300.0  # a claimed row is another server's again after this (the one that claimed it stopped)
BASE_DELAY_S, MAX_DELAY_S = 5.0, 3600.0  # the first retry's delay, doubling up to an hour
ROUND_BUDGET_S = 60.0  # the most one round spends delivering a backlog; the rest is the next round's
GAUGE_EVERY_S = 30.0  # how often the outbox is counted for the gauge


@dataclass(frozen=True)
class AuditEvent:
    org_id: str | None
    actor_id: str | None
    action: str
    target: str
    detail: dict[str, Any]
    ip: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    id: int | None = None  # the audit entry's id: the same on every delivery of it
    # its place in the organisation's hash chain: the SIEM's copy shows if the stored chain is ever rewritten
    prev_hash: str | None = None
    row_hash: str | None = None

    @classmethod
    def of(cls, row: AuditLog) -> AuditEvent:
        return cls(
            row.org_id, row.actor_id, row.action, row.target, dict(row.detail or {}), row.ip,
            row.created_at or datetime.now(UTC), row.id, row.prev_hash, row.row_hash,
        )  # fmt: skip

    def data(self) -> dict[str, Any]:
        out = asdict(self)
        out["at"] = self.at.isoformat()
        return out


def log_event(event: AuditEvent) -> None:
    """The structured log line: what a log shipper sends on to a SIEM."""
    audit_log.info(
        "audit.event", id=event.id, org_id=event.org_id, actor_id=event.actor_id, action=event.action,
        target=event.target, detail=event.detail, ip=event.ip, at=event.at.isoformat(),
    )  # fmt: skip


class Destination(Protocol):
    name: str

    async def send(self, event: AuditEvent) -> None:
        """Deliver one entry; raise when it was not accepted."""
        ...

    async def aclose(self) -> None: ...


class WebhookDestination:
    """POSTs entries to `url` as CloudEvents signed with `secret` in the `header` header."""

    name = "webhook"
    header = "agentcanvas-signature"

    def __init__(self, url: str, secret: str, source: str = "agentcanvas", client: httpx.AsyncClient | None = None):
        self.url, self.secret, self.source = url, secret, source
        self.client = client or httpx.AsyncClient(timeout=5.0)

    @staticmethod
    def cloudevent(event: AuditEvent, source: str) -> dict[str, Any]:
        data = event.data()
        return {
            "specversion": "1.0", "id": f"audit-{event.id}", "source": source,
            "type": f"agentcanvas.audit.{event.action}", "time": data["at"], "tenantid": event.org_id,
            "datacontenttype": "application/json", "data": data,
        }  # fmt: skip

    async def send(self, event: AuditEvent) -> None:
        body = json.dumps(self.cloudevent(event, self.source), default=str).encode()
        r = await self.client.post(
            self.url, content=body,
            headers={"content-type": "application/cloudevents+json", self.header: sign(self.secret, body)},
        )  # fmt: skip
        r.raise_for_status()

    async def aclose(self) -> None:
        await self.client.aclose()


class MaturaDestination(WebhookDestination):
    """The placeholder until Matura has an audit ingest API: signed like Matura's own webhooks."""

    name = "matura"
    header = "matura-signature"


class SplunkHecDestination:
    """Splunk's HTTP Event Collector: one event per entry, `sourcetype` agentcanvas:audit."""

    name = "splunk"

    def __init__(self, url: str, token: str, source: str = "agentcanvas", client: httpx.AsyncClient | None = None):
        self.url, self.token, self.source = url, token, source
        self.client = client or httpx.AsyncClient(timeout=5.0)

    def body(self, event: AuditEvent) -> dict[str, Any]:
        return {
            "time": round(event.at.timestamp(), 3), "source": self.source, "sourcetype": "agentcanvas:audit",
            "event": event.data(),
        }  # fmt: skip

    async def send(self, event: AuditEvent) -> None:
        r = await self.client.post(self.url, json=self.body(event), headers={"authorization": f"Splunk {self.token}"})
        r.raise_for_status()

    async def aclose(self) -> None:
        await self.client.aclose()


def destinations_for(settings: Any) -> list[Destination]:
    """The destinations the settings configure; a half-configured one stops the start with what is missing."""
    out: list[Destination] = []
    if settings.audit_sink_url:
        if not settings.audit_sink_secret:
            raise ValueError("AGENTCANVAS_AUDIT_SINK_URL needs AGENTCANVAS_AUDIT_SINK_SECRET to sign the events")
        out.append(WebhookDestination(settings.audit_sink_url, settings.audit_sink_secret))
    if settings.audit_splunk_hec_url:
        if not settings.audit_splunk_hec_token:
            raise ValueError("AGENTCANVAS_AUDIT_SPLUNK_HEC_URL needs AGENTCANVAS_AUDIT_SPLUNK_HEC_TOKEN")
        if settings.env in ("staging", "prod") and not settings.audit_splunk_hec_url.startswith("https://"):
            raise ValueError("AGENTCANVAS_AUDIT_SPLUNK_HEC_URL must be https:// here: the token would travel in clear")
        out.append(SplunkHecDestination(settings.audit_splunk_hec_url, settings.audit_splunk_hec_token))
    if settings.auth_mode == "matura" and settings.matura_audit_url and settings.matura_webhook_secret:
        out.append(MaturaDestination(settings.matura_audit_url, settings.matura_webhook_secret))
    return out


@dataclass(frozen=True)
class Forwarding:
    """What `AuditRepo.add` forwards: the names of the configured destinations, and whether every entry goes."""

    destinations: tuple[str, ...] = ()
    forward_all: bool = False

    def forwards(self, action: str) -> bool:
        return self.forward_all or action in SECURITY_ACTIONS


_forwarding = Forwarding()


def install(forwarding: Forwarding) -> None:
    """Set at start-up from the settings (the API, and the command line tools)."""
    global _forwarding  # noqa: PLW0603 - one setting per process, chosen by the server
    _forwarding = forwarding


def forwarding() -> Forwarding:
    return _forwarding


def retry_delay(attempts: int) -> float:
    """Seconds before the next try after `attempts` failed ones: 5 s doubling to an hour, give or take a fifth so
    servers that failed together do not retry together."""
    return min(BASE_DELAY_S * 2 ** max(attempts - 1, 0), MAX_DELAY_S) * random.uniform(0.8, 1.2)


def _reason(exc: Exception) -> str:
    """What went wrong, short and without the request (its URL may carry a token)."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    return f"{type(exc).__name__}: {exc}"[:300]


@dataclass
class _Claimed:
    outbox_id: int
    destination: str
    attempts: int
    event: AuditEvent


class AuditForwarder:
    """Delivers the outbox to the destinations (every API server runs one; they share the rows)."""

    def __init__(
        self, db: Any, destinations: Sequence[Destination], *, max_attempts: int = 24, interval_s: float = 2.0
    ):
        self.db, self.max_attempts, self.interval_s = db, max_attempts, interval_s
        self.destinations = {d.name: d for d in destinations}
        self._task: asyncio.Task | None = None
        self._counted_at = 0.0

    def start(self) -> None:
        observability.job_started("audit_forward")
        self._task = asyncio.create_task(self._loop(), name="audit_forward")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def aclose(self) -> None:
        await self.stop()
        for d in self.destinations.values():
            await d.aclose()

    async def _loop(self) -> None:
        while True:
            try:
                with observability.job_round("audit_forward"):
                    await self.drain()
            except Exception:
                log.exception("audit.forward_round_failed")
            await asyncio.sleep(self.interval_s)

    async def drain(self, now: datetime | None = None) -> int:
        """Deliver what is due, a batch at a time, within the round's time budget; the number delivered."""
        start, delivered = time.monotonic(), 0
        while time.monotonic() - start < ROUND_BUDGET_S:
            claimed, ok = await self.tick(now)
            delivered += ok
            if claimed < BATCH:
                break
        if time.monotonic() - self._counted_at > GAUGE_EVERY_S:
            await self.count()
        return delivered

    async def tick(self, now: datetime | None = None) -> tuple[int, int]:
        """One batch: claim due rows, send them, record the outcomes. (rows claimed, rows delivered)."""
        now = now or datetime.now(UTC)
        claimed = await self._claim(now)
        if not claimed:
            return 0, 0
        by_destination: dict[str, list[_Claimed]] = defaultdict(list)
        for c in claimed:
            by_destination[c.destination].append(c)
        outcomes: dict[int, str | None] = {}  # outbox id -> None (delivered) or why it failed
        await asyncio.gather(*(self._send(self.destinations[n], rows, outcomes) for n, rows in by_destination.items()))
        await self._settle(now, claimed, outcomes)
        return len(claimed), sum(1 for c in claimed if c.outbox_id in outcomes and outcomes[c.outbox_id] is None)

    async def _claim(self, now: datetime) -> list[_Claimed]:
        """Due rows of this server's destinations, leased so no other server sends them meanwhile. On Postgres the
        rows are locked while they are leased, and rows another server is leasing are skipped, not waited for."""
        if not self.destinations:
            return []
        async with self.db.sessionmaker() as s:
            ids = list(
                (
                    await s.scalars(
                        select(AuditOutbox.id)
                        .where(AuditOutbox.state == "pending", AuditOutbox.next_attempt_at <= now)
                        .where(AuditOutbox.destination.in_(list(self.destinations)))
                        .order_by(AuditOutbox.next_attempt_at, AuditOutbox.id)
                        .limit(BATCH)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            if not ids:
                await s.rollback()
                return []
            # checked again as it is leased: SQLite has no row locks, so another server may have leased it since
            leased = await s.scalars(
                update(AuditOutbox)
                .where(AuditOutbox.id.in_(ids), AuditOutbox.state == "pending", AuditOutbox.next_attempt_at <= now)
                .values(attempts=AuditOutbox.attempts + 1, next_attempt_at=now + timedelta(seconds=LEASE_S))
                .returning(AuditOutbox.id)
            )
            ids = list(leased.all())
            rows = (
                await s.execute(
                    select(AuditOutbox.id, AuditOutbox.destination, AuditOutbox.attempts, AuditLog)
                    .join(AuditLog, AuditLog.id == AuditOutbox.audit_id)
                    .where(AuditOutbox.id.in_(ids))
                    .order_by(AuditOutbox.id)
                )
            ).all()
            await s.commit()
        return [_Claimed(oid, dest, attempts, AuditEvent.of(row)) for oid, dest, attempts, row in rows]

    async def _send(self, dest: Destination, rows: list[_Claimed], outcomes: dict[int, str | None]) -> None:
        """In order; after a failure the rest wait for the retry too (the destination is likely down)."""
        for c in rows:
            try:
                await dest.send(c.event)
            except Exception as exc:  # noqa: BLE001 - recorded on the row and retried
                outcomes[c.outbox_id] = _reason(exc)
                return
            outcomes[c.outbox_id] = None

    async def _settle(self, now: datetime, claimed: list[_Claimed], outcomes: dict[int, str | None]) -> None:
        delivered = [c.outbox_id for c in claimed if c.outbox_id in outcomes and outcomes[c.outbox_id] is None]
        async with self.db.sessionmaker() as s:
            if delivered:
                await s.execute(delete(AuditOutbox).where(AuditOutbox.id.in_(delivered)))
            retry_at: dict[str, datetime] = {}
            for c in claimed:  # the failures first: the rows not tried wait as long as the one that failed
                why = outcomes.get(c.outbox_id)
                if c.outbox_id not in outcomes or why is None:
                    continue
                values: dict[str, Any] = {"last_error": why}
                if c.attempts >= self.max_attempts:
                    values["state"] = "dead"
                    FORWARD_DEAD.labels(destination=c.destination).inc()
                    log.error(
                        "audit.forward_dead", destination=c.destination, audit_id=c.event.id, action=c.event.action,
                        attempts=c.attempts, error=why,
                        hint="kept in the outbox: `python -m agentcanvas.audit retry` sends it again",
                    )  # fmt: skip
                else:
                    values["next_attempt_at"] = now + timedelta(seconds=retry_delay(c.attempts))
                    log.warning(
                        "audit.forward_failed", destination=c.destination, audit_id=c.event.id,
                        action=c.event.action, attempts=c.attempts, error=why,
                    )  # fmt: skip
                retry_at[c.destination] = values.get("next_attempt_at", now + timedelta(seconds=BASE_DELAY_S))
                FORWARD_FAILED.labels(destination=c.destination).inc()
                await s.execute(update(AuditOutbox).where(AuditOutbox.id == c.outbox_id).values(**values))
            untried = [c for c in claimed if c.outbox_id not in outcomes]
            for c in untried:  # not an attempt: give it back
                await s.execute(
                    update(AuditOutbox)
                    .where(AuditOutbox.id == c.outbox_id)
                    .values(attempts=AuditOutbox.attempts - 1, next_attempt_at=retry_at.get(c.destination, now))
                )
            await s.commit()
        for c in claimed:
            if c.outbox_id in delivered:
                FORWARDED.labels(destination=c.destination).inc()

    async def count(self) -> dict[tuple[str, str], int]:
        """The outbox by destination and state, into the gauge (every server reports the same numbers)."""
        async with self.db.sessionmaker() as s:
            rows = (
                await s.execute(
                    select(AuditOutbox.destination, AuditOutbox.state, func.count()).group_by(
                        AuditOutbox.destination, AuditOutbox.state
                    )
                )
            ).all()
        counts = {(name, state): 0 for name in self.destinations for state in ("pending", "dead")}
        counts.update({(d, st): n for d, st, n in rows})
        for (d, st), n in counts.items():
            OUTBOX.labels(destination=d, state=st).set(n)
        self._counted_at = time.monotonic()
        return counts
