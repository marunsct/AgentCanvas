"""Approval notifications (E-6): tell the people who may decide an Inbox item that it waits, and when it expires.

An organisation's admin chooses the channels (Settings > Organisation > Notifications, `OrgNotifier`): e-mail through
the server's SMTP server (`AGENTCANVAS_SMTP_*`), a Slack incoming webhook, and a webhook of their own whose requests
are signed like webhook triggers (`X-AgentCanvas-Signature: sha256=<HMAC of "<timestamp>." + body>`). The Slack
address and the signing secret are encrypted with the organisation's data key.

Every API server runs the job. A tick first announces new Inbox items: it claims each one with a conditional update
(`interrupt_task.noticed`, set only where it is still NULL), so one server wins whatever the number of servers, and in
the same transaction writes one outbox row (`ApprovalNotice`) per channel. Then it sends the outbox rows that are due,
each claimed the same way for a lease, and retries a failure with backoff until it gives up after `MAX_ATTEMPTS`; a
server that dies mid-send leaves the row to be sent again after the lease. Outcomes are counted in
`agentcanvas_approval_notifications_total{channel, outcome}`.

A message names the workflow, the project, the step, who may decide and when the item expires, with a link to the
Inbox; never what the run asks (it can hold personal data). Draft runs a person starts in the editor are not
announced unless their step names who decides them: that person is watching the run. Test and evaluation runs never.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import smtplib
import socket
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlsplit

from prometheus_client import Counter
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas import observability
from agentcanvas.db.models import (
    ApprovalNotice,
    AppUser,
    AuditLog,
    InterruptTask,
    Membership,
    OrgNotifier,
    Project,
    Run,
    ScimGroup,
    ScimGroupMember,
    Thread,
    WorkflowRow,
)
from agentcanvas.errors import InvalidInput
from agentcanvas.ids import new_id
from agentcanvas.log import get_logger
from agentcanvas.security.addresses import is_metadata, is_public
from agentcanvas.security.kms import Keyring
from agentcanvas.services.approvers import ROLES, Approvers
from agentcanvas.services.triggers import sign
from agentcanvas.settings import Settings

log = get_logger(__name__)

NOTICES = Counter(
    "agentcanvas_approval_notifications_total",
    "Approval notifications by channel and outcome (sent, retry, failed, dropped: decided before it was sent)",
    ["channel", "outcome"],
)
ESCALATED = Counter(
    "agentcanvas_approvals_escalated_total",
    "Inbox items handed to their step's escalation role: nobody decided in time",
)
CHANNELS = ("email", "slack", "webhook")
MAX_ATTEMPTS = 6
BACKOFF_S = (30, 120, 600, 1800, 7200)  # after the 1st, 2nd... failed attempt
LEASE_S = 120  # a claimed notice not finished by then (its server died) is sent again
BATCH = 100
SENDS_AT_ONCE = 8
MAX_RECIPIENTS = 100
MAX_EMAIL_TO = 20
TIMEOUT_S = 10.0
SLACK_HOSTS = ("hooks.slack.com", "hooks.slack-gov.com")
SECRET_MIN = 24


class DeliveryError(Exception):
    """A channel refused or could not take a notification; the text is safe to store (no addresses with tokens)."""


# ------------------------------------------------------------------------------------------- configuration ---
@dataclass
class Channels:
    """An organisation's notification settings, decrypted."""

    email: bool = False
    email_to: list[str] = field(default_factory=list)
    slack_url: str | None = None
    webhook_url: str | None = None
    webhook_secret: str | None = None

    def enabled(self, smtp: bool) -> list[str]:
        on = {
            "email": self.email and smtp, "slack": bool(self.slack_url),
            "webhook": bool(self.webhook_url and self.webhook_secret),
        }  # fmt: skip
        return [c for c in CHANNELS if on[c]]


def channels_on(row: OrgNotifier, smtp: bool) -> list[str]:
    """The channels an organisation has on, from its columns alone (nothing to decrypt)."""
    on = {"email": row.email and smtp, "slack": row.slack_url is not None,
          "webhook": bool(row.webhook_url) and row.webhook_secret is not None}  # fmt: skip
    return [c for c in CHANNELS if on[c]]


def smtp_ready(settings: Settings) -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


async def load(session: AsyncSession, keyring: Keyring, org_id: str) -> Channels | None:
    row = await session.get(OrgNotifier, org_id)
    if row is None:
        return None
    f = await keyring.fernet(session, org_id) if row.slack_url or row.webhook_secret else None
    return Channels(
        email=row.email, email_to=list(row.email_to or []), webhook_url=row.webhook_url,
        slack_url=f.decrypt(row.slack_url).decode() if f and row.slack_url else None,
        webhook_secret=f.decrypt(row.webhook_secret).decode() if f and row.webhook_secret else None,
    )  # fmt: skip


def check_slack_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or parts.hostname not in SLACK_HOSTS or not parts.path.startswith("/services/"):
        raise InvalidInput("a Slack incoming webhook address looks like https://hooks.slack.com/services/…")
    return url.strip()


def check_webhook_url(url: str, allow_private: bool) -> str:
    """The address's form, and its host when it is written as an IP address; a name is checked when it is called
    (each call resolves it again and connects to the address it checked)."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise InvalidInput("the webhook needs an http(s) address")
    if parts.scheme == "http" and not allow_private:
        raise InvalidInput("the webhook needs an https address")
    if parts.username or parts.password:
        raise InvalidInput("put no user name or password in the webhook's address: it is signed instead")
    if _is_ip(parts.hostname):
        try:
            _allowed(parts.hostname, parts.hostname, allow_private)
        except DeliveryError as exc:
            raise InvalidInput(str(exc)) from None
    return url.strip()


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(32)


# ------------------------------------------------------------------------------------------------ messages ---
@dataclass
class Notice:
    """What a notification says about one Inbox item."""

    interrupt_id: str
    event: str  # waiting | escalated
    kind: str  # approval | tool_approval | input
    org_id: str
    project_id: str
    project_name: str
    workflow_id: str
    workflow_name: str
    step: str
    thread_id: str
    run_id: str
    deciders: str
    created_at: datetime
    expires_at: datetime | None
    link: str
    escalates_at: datetime | None = None  # when nobody deciding sends it to `escalate_to`
    escalate_to: str | None = None

    @property
    def subject(self) -> str:
        what = "Question" if self.kind == "input" else "Approval"
        lead = "escalated" if self.event == "escalated" else "waiting"
        return _one_line(f"{what} {lead}: {self.workflow_name}, {self.step}")

    @property
    def text(self) -> str:
        what = "an answer" if self.kind == "input" else "a decision"
        lines = [
            f'{self.workflow_name} (project {self.project_name}) is waiting for {what} at the step "{self.step}".',
            *(["Nobody decided it in time, so it was escalated."] if self.event == "escalated" else []),
            f"Who may decide: {self.deciders}.",
            f"If nobody decides it by {self.escalates_at:%Y-%m-%d %H:%M} UTC, it goes to the {self.escalate_to} role."
            if self.escalates_at
            else f"It expires on {self.expires_at:%Y-%m-%d %H:%M} UTC unless someone decides it."
            if self.expires_at
            else "It waits until someone decides it.",
            f"Open it in the Inbox: {self.link}",
        ]
        return "\n".join(lines) + "\n"

    def payload(self, notice_id: str) -> dict[str, Any]:
        return {
            "type": f"approval.{self.event}", "id": notice_id, "interrupt_id": self.interrupt_id, "kind": self.kind,
            "organisation_id": self.org_id, "project": {"id": self.project_id, "name": self.project_name},
            "workflow": {"id": self.workflow_id, "name": self.workflow_name}, "step": self.step,
            "thread_id": self.thread_id, "run_id": self.run_id, "deciders": self.deciders,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "escalates_at": self.escalates_at.isoformat() if self.escalates_at else None,
            "escalate_to": self.escalate_to if self.escalates_at else None, "url": self.link,
        }  # fmt: skip


def _one_line(text: str) -> str:
    return " ".join(text.split())[:250]


def _slack_escape(text: str) -> str:
    """Names someone typed cannot mention @channel or make links in Slack."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def slack_body(n: Notice) -> dict[str, Any]:
    lines = [_slack_escape(line) for line in n.text.strip().splitlines()[:-1]]
    return {"text": _slack_escape(n.subject), "blocks": [
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*{_slack_escape(n.subject)}*\n" + "\n".join(lines)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"<{n.link}|Open it in the Inbox>"}},
    ]}  # fmt: skip


# ------------------------------------------------------------------------------------------------ delivery ---
def _is_ip(host: str) -> bool:
    try:
        socket.inet_pton(socket.AF_INET6 if ":" in host else socket.AF_INET, host.strip("[]"))
    except OSError:
        return False
    return True


def _allowed(host: str, address: str, allow_private: bool) -> None:
    if is_metadata(address):
        raise DeliveryError(f"'{host}' resolves to a link-local or metadata address; refused")
    if not allow_private and not is_public(address):
        raise DeliveryError(f"'{host}' resolves to a private or internal address; refused")


async def _address(host: str, port: int, allow_private: bool) -> str:
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise DeliveryError(f"cannot resolve '{host}'") from None
    answers = [str(i[4][0]) for i in infos]
    for a in answers:
        _allowed(host, a, allow_private)
    if not answers:
        raise DeliveryError(f"'{host}' does not resolve")
    return answers[0]


async def post(url: str, body: bytes, headers: dict[str, str], allow_private: bool, transport: Any = None) -> None:
    """POST to an address checked the moment it is called: the name is resolved here, every answer must be allowed,
    and the request goes to the address that was checked (TLS still checks the name). Redirects are not followed."""
    import httpx

    parts = urlsplit(url)
    host = parts.hostname or ""
    target, extensions = url, {}
    if transport is None:  # tests answer from a mock transport
        port = parts.port or (443 if parts.scheme == "https" else 80)
        ip = await _address(host, port, allow_private)
        headers = {**headers, "Host": parts.netloc.rsplit("@", 1)[-1]}
        extensions["sni_hostname"] = host
        target = parts._replace(netloc=f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}").geturl()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=transport, follow_redirects=False) as client:
            r = await client.post(target, content=body, headers=headers, extensions=extensions or None)
    except httpx.HTTPError as exc:
        raise DeliveryError(f"{host}: {type(exc).__name__}") from None
    if not 200 <= r.status_code < 300:
        raise DeliveryError(f"{host} answered HTTP {r.status_code}")


def send_email(settings: Settings, to: list[str], subject: str, text: str) -> None:
    """One message to every recipient, each in Bcc (they do not see one another), through the server's SMTP server.
    Blocking: run it in a thread."""
    if not (settings.smtp_host and settings.smtp_from):
        raise DeliveryError("the server has no mail server (AGENTCANVAS_SMTP_HOST and AGENTCANVAS_SMTP_FROM)")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, "undisclosed-recipients:;", _one_line(subject)
    msg.set_content(text)
    context = ssl.create_default_context()
    try:
        if settings.smtp_security == "tls":
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=TIMEOUT_S, context=context
            )
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=TIMEOUT_S)
        with server:
            if settings.smtp_security == "starttls":
                server.starttls(context=context)
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password or "")
            server.send_message(msg, to_addrs=to)
    except (OSError, smtplib.SMTPException) as exc:
        raise DeliveryError(f"the mail server: {type(exc).__name__}: {str(exc)[:200]}") from None


Mailer = Callable[[Settings, list[str], str, str], None]


# ---------------------------------------------------------------------------------------------- escalation ---
async def escalate(s: AsyncSession, items: list[tuple[str, str, str]], now: datetime, smtp: bool) -> int:
    """Hand each Inbox item (id, role, why) to its step's escalation role: members with that role may decide it too
    and its expiry counts from now. Claimed with a conditional update, so one server escalates it once, whatever the
    number of servers and jobs (the notifier at the step's own time, retention at the expiry); the item is audited
    and its "escalated" notifications queued in the same transaction, which the caller commits. Returns how many."""
    n = 0
    for interrupt_id, role, why in items:
        res = await s.execute(
            update(InterruptTask)
            .where(
                InterruptTask.id == interrupt_id,
                InterruptTask.status == "pending",
                InterruptTask.escalated_at.is_(None),
            )
            .values(assignee_role=role, escalated_at=now, escalate_at=None)
        )
        if res.rowcount != 1:  # type: ignore[attr-defined]
            continue  # decided meanwhile, or another server escalated it
        q = (
            select(Project.org_id)
            .join(WorkflowRow, WorkflowRow.project_id == Project.id)
            .join(Thread, Thread.workflow_id == WorkflowRow.id)
            .join(InterruptTask, InterruptTask.thread_id == Thread.id)
            .where(InterruptTask.id == interrupt_id)
        )
        org_id = await s.scalar(q)
        if org_id is None:
            continue
        s.add(AuditLog(org_id=org_id, action="approval_escalated", target=f"interrupt_id={interrupt_id}",
                       detail={"role": role, "why": why}))  # fmt: skip
        row = await s.get(OrgNotifier, org_id)
        NotifierJob._queue(s, org_id, interrupt_id, "escalated", channels_on(row, smtp) if row else [], now)
        log.info("notifier.escalated", interrupt_id=interrupt_id, role=role, why=why)
        n += 1
    ESCALATED.inc(n)
    return n


# -------------------------------------------------------------------------------------------------- people ---
async def deciders_of(
    session: AsyncSession, org_id: str, policy: Approvers, assignee_role: str | None, started_by: str | None
) -> list[str]:
    """The user ids of the organisation's active members who may decide an item: those it names (with the role it
    was escalated to), else every member with the reviewer role or above."""
    active = (Membership.org_id == org_id, Membership.accepted.is_(True), Membership.suspended.is_(False))
    roles = [*policy.roles, *([assignee_role] if assignee_role else [])] if policy.named else list(ROLES)
    ids = set((await session.scalars(select(Membership.user_id).where(*active, Membership.role.in_(roles)))).all())
    if policy.named and policy.people:
        wanted = {p.strip().lower() for p in policy.people}
        q = select(AppUser.id).join(Membership, Membership.user_id == AppUser.id)
        ids |= set((await session.scalars(q.where(*active, func.lower(AppUser.email).in_(wanted)))).all())
    if policy.named and policy.groups:
        wanted = {g.casefold() for g in policy.groups}
        q = (
            select(ScimGroupMember.user_id, ScimGroup.display_name)
            .join(ScimGroup, ScimGroup.id == ScimGroupMember.group_id)
            .join(Membership, Membership.user_id == ScimGroupMember.user_id)
            .where(ScimGroup.org_id == org_id, *active)
        )
        ids |= {uid for uid, name in (await session.execute(q)).all() if name.casefold() in wanted}
    if policy.not_initiator and started_by:
        ids.discard(started_by)
    return sorted(ids)


# ----------------------------------------------------------------------------------------------------- job ---
class NotifierJob:
    def __init__(self, db: Any, keyring: Keyring, settings: Settings):
        self.db, self.keyring, self.settings = db, keyring, settings
        self.transport: Any = None  # tests: an httpx mock transport
        self.mailer: Mailer = send_email
        self._task: asyncio.Task | None = None

    @property
    def base_url(self) -> str:
        return (self.settings.public_url or next(iter(self.settings.cors_origins), "")).rstrip("/")

    def start(self) -> None:
        observability.job_started("notifier")
        self._task = asyncio.create_task(self._loop(), name="notifier")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                with observability.job_round("notifier"):
                    await self.tick(datetime.now(UTC))
            except Exception:
                log.exception("notifier.tick_failed")
            await asyncio.sleep(self.settings.notify_interval_s)

    async def tick(self, now: datetime) -> int:
        """Escalate what waited past its step's own time, announce new Inbox items and send what is due; returns
        the notifications sent."""
        await self.escalate_due(now)
        await self.announce(now)
        return await self.deliver(now)

    async def escalate_due(self, now: datetime) -> int:
        """Escalate the items whose step's "escalate after" ran out (an escalation at the approval expiry is the
        retention job's). Returns how many."""
        async with self.db.sessionmaker() as s:
            q = (
                select(InterruptTask.id, InterruptTask.approvers)
                .where(
                    InterruptTask.status == "pending",
                    InterruptTask.escalate_at <= now,
                    InterruptTask.escalated_at.is_(None),
                )
                .order_by(InterruptTask.escalate_at, InterruptTask.id)
                .limit(BATCH)
            )
            items = []
            for interrupt_id, stored in (await s.execute(q)).all():
                policy = Approvers.of(stored)
                if policy.escalate_to:
                    why = f"nobody decided within {policy.escalate_after_hours} hours"
                    items.append((interrupt_id, policy.escalate_to, why))
            n = await escalate(s, items, now, smtp_ready(self.settings))
            await s.commit()
        return n

    # ------------------------------------------------------------------------------------------ announce ---
    async def announce(self, now: datetime) -> int:
        """Claim each Inbox item not announced yet and queue one notification per channel of its organisation.
        Returns the notifications queued."""
        smtp = smtp_ready(self.settings)
        async with self.db.sessionmaker() as s:
            q = (
                select(
                    InterruptTask.id,
                    InterruptTask.approvers,
                    Thread.deployment_id,
                    Run.origin,
                    Project.org_id,
                )
                .join(Thread, Thread.id == InterruptTask.thread_id)
                .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
                .join(Project, Project.id == WorkflowRow.project_id)
                .outerjoin(Run, Run.id == InterruptTask.run_id)
                .where(InterruptTask.status == "pending", InterruptTask.noticed.is_(None))
                .order_by(InterruptTask.created_at, InterruptTask.id)  # every server locks rows in the same order
                .limit(BATCH)
            )
            rows = (await s.execute(q)).all()
            found = await s.scalars(select(OrgNotifier).where(OrgNotifier.org_id.in_({r.org_id for r in rows})))
            configs = {row.org_id: channels_on(row, smtp) for row in found.all()}
            queued = 0
            for r in rows:
                channels = configs.get(r.org_id, [])
                quiet = r.origin in ("test", "eval") or (
                    r.deployment_id is None and r.origin in ("user", None) and not Approvers.of(r.approvers).routed
                )
                state = "quiet" if quiet else "waiting" if channels else "off"
                claimed = await s.execute(
                    update(InterruptTask)
                    .where(InterruptTask.id == r.id, InterruptTask.noticed.is_(None))
                    .values(noticed=state)
                )
                if claimed.rowcount != 1 or state != "waiting":  # type: ignore[attr-defined]
                    continue  # another server announced it
                queued += self._queue(s, r.org_id, r.id, "waiting", channels, now)
            await s.commit()
        return queued

    @staticmethod
    def _queue(s: AsyncSession, org_id: str, interrupt_id: str, event: str, channels: list[str], now: datetime) -> int:
        for channel in channels:
            s.add(
                ApprovalNotice(
                    id=new_id("ntc"), org_id=org_id, interrupt_id=interrupt_id, event=event, channel=channel,
                    next_at=now,
                )
            )  # fmt: skip
        return len(channels)

    # ------------------------------------------------------------------------------------------- deliver ---
    async def deliver(self, now: datetime) -> int:
        """Send the notifications that are due, each claimed for a lease first; returns how many were sent."""
        async with self.db.sessionmaker() as s:
            due = (ApprovalNotice.status == "pending", ApprovalNotice.next_at <= now)
            q = select(ApprovalNotice.id).where(*due).order_by(ApprovalNotice.next_at, ApprovalNotice.id).limit(BATCH)
            ids = list(await s.scalars(q))
            mine: list[str] = []
            for notice_id in ids:
                res = await s.execute(
                    update(ApprovalNotice)
                    .where(ApprovalNotice.id == notice_id, *due)
                    .values(next_at=now + timedelta(seconds=LEASE_S), attempts=ApprovalNotice.attempts + 1)
                )
                if res.rowcount == 1:  # type: ignore[attr-defined]
                    mine.append(notice_id)
            await s.commit()
        gate = asyncio.Semaphore(SENDS_AT_ONCE)

        async def one(notice_id: str) -> bool:
            async with gate:
                return await self._send(notice_id, now)

        return sum(await asyncio.gather(*(one(i) for i in mine)))

    async def _send(self, notice_id: str, now: datetime) -> bool:
        async with self.db.sessionmaker() as s:
            row = await s.get(ApprovalNotice, notice_id)
            if row is None:
                return False
            channel, attempts = row.channel, row.attempts
            notice = await self.notice(s, row.interrupt_id, row.event)
            channels = await load(s, self.keyring, row.org_id)
            to = (
                await self._recipients(s, row.interrupt_id, row.org_id, channels, row.event)
                if channel == "email"
                else []
            )
        outcome, error = "sent", None
        if notice is None:
            outcome = "dropped"  # decided, expired or deleted before it was sent: nothing to tell
        else:
            try:
                await self._deliver(channel, notice, notice_id, channels, to)
            except DeliveryError as exc:
                error = str(exc)
            except Exception as exc:  # a channel's failure must not stop the others
                error = f"{type(exc).__name__} (see the server log)"
                log.exception("notifier.send_crashed", notice_id=notice_id, channel=channel)
        values: dict[str, Any]
        if error is None:
            values = {"status": outcome, "sent_at": now if outcome == "sent" else None, "last_error": None}
        elif attempts >= MAX_ATTEMPTS:
            outcome, values = "failed", {"status": "failed", "last_error": error}
            log.warning("notifier.gave_up", notice_id=notice_id, channel=channel, attempts=attempts, error=error)
        else:
            outcome = "retry"
            delay = BACKOFF_S[min(attempts, len(BACKOFF_S)) - 1]
            values = {"next_at": now + timedelta(seconds=delay), "last_error": error}
            log.info("notifier.retry", notice_id=notice_id, channel=channel, attempts=attempts, error=error)
        async with self.db.sessionmaker() as s:
            await s.execute(update(ApprovalNotice).where(ApprovalNotice.id == notice_id).values(**values))
            await s.commit()
        NOTICES.labels(channel=channel, outcome=outcome).inc()
        return outcome == "sent"

    async def _deliver(self, channel: str, n: Notice, notice_id: str, channels: Channels | None, to: list[str]) -> None:
        if channels is None or channel not in channels.enabled(smtp_ready(self.settings)):
            raise DeliveryError(f"the organisation no longer sends {channel} notifications")
        if channel == "email":
            if not to:
                raise DeliveryError("nobody to send it to: no member may decide it")
            await asyncio.to_thread(self.mailer, self.settings, to, n.subject, n.text)
        elif channel == "slack":
            assert channels.slack_url
            body = json.dumps(slack_body(n)).encode()
            await post(channels.slack_url, body, {"Content-Type": "application/json"}, False, self.transport)
        else:
            assert channels.webhook_url and channels.webhook_secret
            body = json.dumps(n.payload(notice_id)).encode()
            stamp = str(int(time.time()))
            headers = {
                "Content-Type": "application/json", "User-Agent": "AgentCanvas-Notifier/1.0",
                "X-AgentCanvas-Event": f"approval.{n.event}", "X-AgentCanvas-Delivery": notice_id,
                "X-AgentCanvas-Timestamp": stamp, "X-AgentCanvas-Signature": sign(channels.webhook_secret, body, stamp),
            }  # fmt: skip
            await post(channels.webhook_url, body, headers, self.settings.notify_allow_private, self.transport)

    async def _recipients(
        self, s: AsyncSession, interrupt_id: str, org_id: str, channels: Channels | None, event: str
    ) -> list[str]:
        item = await s.get(InterruptTask, interrupt_id)
        if item is None:
            return []
        policy = Approvers.of(item.approvers)
        if event == "escalated" and item.assignee_role:  # the role it went to: the others were told already
            policy = Approvers(roles=[item.assignee_role], not_initiator=policy.not_initiator)
        elif not policy.named and channels is not None and channels.email_to:
            return channels.email_to[:MAX_RECIPIENTS]  # the organisation's team address for steps naming nobody
        started_by = item.requested_by or await s.scalar(select(Thread.created_by).where(Thread.id == item.thread_id))
        ids = await deciders_of(s, org_id, policy, item.assignee_role, started_by)
        emails = sorted({e for e in (await s.scalars(select(AppUser.email).where(AppUser.id.in_(ids)))).all() if e})
        if len(emails) > MAX_RECIPIENTS:
            log.warning("notifier.too_many_recipients", interrupt_id=interrupt_id, recipients=len(emails))
        return emails[:MAX_RECIPIENTS]

    async def notice(self, s: AsyncSession, interrupt_id: str, event: str) -> Notice | None:
        """What to say about a waiting Inbox item; None once it is no longer waiting."""
        q = (
            select(InterruptTask, Thread.workflow_id, WorkflowRow.name, WorkflowRow.draft_ir, Project)
            .join(Thread, Thread.id == InterruptTask.thread_id)
            .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
            .join(Project, Project.id == WorkflowRow.project_id)
            .where(InterruptTask.id == interrupt_id, InterruptTask.status == "pending")
        )
        found = (await s.execute(q)).first()
        if found is None:
            return None
        item, workflow_id, workflow_name, ir, project = found
        node = next((n for n in (ir or {}).get("nodes", []) if n.get("id") == item.canvas_node_id), None) or {}
        policy, days = Approvers.of(item.approvers), self.settings.approval_expiry_days
        expires = (item.escalated_at or item.created_at) + timedelta(days=days) if days > 0 else None
        escalates = None if item.escalated_at or not policy.escalate_to else item.escalate_at or expires
        return Notice(
            interrupt_id=item.id, event=event, kind=item.kind, org_id=project.org_id, project_id=project.id,
            project_name=project.name, workflow_id=workflow_id, workflow_name=workflow_name,
            step=node.get("label") or str(node.get("name") or item.canvas_node_id).replace("_", " "),
            thread_id=item.thread_id, run_id=item.run_id,
            deciders=policy.summary(item.assignee_role), created_at=item.created_at,
            expires_at=None if escalates else expires, link=f"{self.base_url}/inbox?item={item.id}",
            escalates_at=escalates, escalate_to=policy.escalate_to,
        )  # fmt: skip

    async def test(self, s: AsyncSession, org_id: str, email: str | None) -> list[dict[str, Any]]:
        """Send a sample notification on each channel the organisation has on (e-mail to the admin asking); returns
        what each said."""
        channels = await load(s, self.keyring, org_id)
        if channels is None:
            return []
        n = Notice(
            interrupt_id="test", event="waiting", kind="approval", org_id=org_id, project_id="-",
            project_name="(a test)", workflow_id="-", workflow_name="Notification test", step="Ask for approval",
            thread_id="-", run_id="-", deciders="you", created_at=datetime.now(UTC), expires_at=None,
            link=f"{self.base_url}/inbox",
        )  # fmt: skip
        out = []
        for channel in channels.enabled(smtp_ready(self.settings)):
            try:
                await self._deliver(channel, n, new_id("ntc"), channels, [email] if email else [])
                out.append({"channel": channel, "ok": True, "error": None})
            except DeliveryError as exc:
                out.append({"channel": channel, "ok": False, "error": str(exc)})
        return out
