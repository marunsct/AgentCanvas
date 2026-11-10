"""E-6: the people who may decide an Inbox item are told it waits (e-mail, Slack, a signed webhook), once whatever
the number of servers, with failures retried and counted."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from test_approvers import _member, _waiting
from test_roles import act_as
from test_runs_v1 import triage_api  # noqa: F401 - fixture
from test_versions import edit_node

from agentcanvas.api.deps import current_user
from agentcanvas.db.models import ApprovalNotice, InterruptTask, OrgNotifier
from agentcanvas.services import notifier
from agentcanvas.services.notifier import NOTICES, NotifierJob
from agentcanvas.services.triggers import sign

SLACK = "https://hooks.slack.com/services/T000/B000/XXXXSECRETTOKEN"
HOOK = "https://hooks.example.com/approvals"


@pytest.fixture
def smtp(app, monkeypatch):
    monkeypatch.setattr(app.state.settings, "smtp_host", "mail.example.com")
    monkeypatch.setattr(app.state.settings, "smtp_from", "AgentCanvas <approvals@example.com>")


class Wire:
    """What the job sent: HTTP requests through a mock transport, e-mails through a fake mailer."""

    def __init__(self, job: NotifierJob):
        self.requests: list[httpx.Request] = []
        self.mails: list[tuple[list[str], str, str]] = []
        self.status = 200
        job.transport = httpx.MockTransport(self._answer)
        job.mailer = lambda settings, to, subject, text: self.mails.append((to, subject, text))

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status)

    def to(self, url: str) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url) == url]


async def _configure(api, **body) -> dict:
    r = await api.put("/v1/org/notifications", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_an_admin_sets_where_notifications_go_and_secrets_stay_hidden(app, api_client, smtp):
    api = api_client
    assert (await api.get("/v1/org/notifications")).json()["email"] == {"enabled": False, "to": [], "available": True}
    bad = await api.put("/v1/org/notifications", json={"slack_url": "https://evil.example.com/services/x"})
    assert bad.status_code == 422 and "hooks.slack.com" in bad.text
    for url in ("http://10.0.0.5/hook", "https://169.254.169.254/latest", "https://user:pw@hooks.example.com/x"):
        assert (await api.put("/v1/org/notifications", json={"webhook_url": url})).status_code == 422, url
    saved = await _configure(api, email=True, email_to=["team@example.com"], slack_url=SLACK, webhook_url=HOOK)
    secret = saved["webhook_secret"]
    assert secret and secret.startswith("whsec_")  # made by the server, shown this once
    shown = (await api.get("/v1/org/notifications")).json()
    assert shown["slack"] is True and shown["webhook_url"] == HOOK and shown["webhook_secret_set"] is True
    assert "webhook_secret" not in shown and SLACK not in json.dumps(shown)
    async with app.state.db.sessionmaker() as s:
        row = await s.get(OrgNotifier, "org_default")
        assert (
            row is not None
            and b"SECRETTOKEN" not in (row.slack_url or b"")
            and secret.encode() not in (row.webhook_secret or b"")
        )  # encrypted with the organisation's key
    again = await _configure(api, email=False)  # a partial change keeps the rest, and the secret
    assert again["webhook_secret"] is None and again["slack"] is True and again["webhook_secret_set"] is True
    gone = await _configure(api, slack_url=None, webhook_url=None)
    assert gone["slack"] is False and gone["webhook_url"] is None and gone["webhook_secret_set"] is False
    audit = (await api.get("/v1/org/audit", params={"action": "put_notifications"})).json()
    assert audit and SLACK not in json.dumps(audit) and secret not in json.dumps(audit)
    act_as(app, "reviewer")
    try:
        assert (await api.get("/v1/org/notifications")).status_code == 403  # admins only, reads too
    finally:
        app.dependency_overrides.pop(current_user, None)


async def test_email_needs_the_servers_mail_server(api_client):
    r = await api_client.put("/v1/org/notifications", json={"email": True})
    assert r.status_code == 409 and "AGENTCANVAS_SMTP_HOST" in r.text


async def test_the_people_who_may_decide_are_told_once(app, triage_api, smtp):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    secret = (await _configure(api, email=True, slack_url=SLACK, webhook_url=HOOK))["webhook_secret"]
    await _member(app, "usr_lee", "lee@example.com", "reviewer")
    await edit_node(api, wf, "n_ag", approvers={"people": ["Lee@example.com"]})
    item = await _waiting(api, wf, "th_told")
    job, now = app.state.notifier, datetime.now(UTC)
    wire = Wire(job)
    assert await job.tick(now) == 3
    [(to, subject, text)] = wire.mails
    assert to == ["lee@example.com"] and subject.startswith("Approval waiting: Support triage")
    assert f"/inbox?item={item['id']}" in text and "Who may decide: Lee@example.com" in text
    assert "expires on" in text and "Invoice?" not in text  # never what the run asks
    [slack] = wire.to(SLACK)
    assert "Support triage" in slack.content.decode() and f"item={item['id']}" in slack.content.decode()
    [hook] = wire.to(HOOK)
    body = hook.content
    assert hook.headers["x-agentcanvas-signature"] == sign(secret, body, hook.headers["x-agentcanvas-timestamp"])
    sent = json.loads(body)
    assert sent["type"] == "approval.waiting" and sent["interrupt_id"] == item["id"] and sent["expires_at"]
    assert "Invoice?" not in body.decode()
    assert await job.tick(now + timedelta(minutes=5)) == 0 and len(wire.requests) == 2  # once
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(InterruptTask, item["id"])).noticed == "waiting"
        statuses = (await s.scalars(select(ApprovalNotice.status))).all()
        assert sorted(statuses) == ["sent", "sent", "sent"]


async def test_a_draft_run_its_starter_watches_is_not_announced(app, triage_api, smtp):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    await _configure(api, slack_url=SLACK)
    item = await _waiting(api, wf, "th_quiet")  # its step names nobody: whoever ran it in the editor decides
    wire = Wire(app.state.notifier)
    assert await app.state.notifier.tick(datetime.now(UTC)) == 0 and not wire.requests
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(InterruptTask, item["id"])).noticed == "quiet"


async def test_failures_are_retried_with_backoff_counted_and_given_up(app, triage_api, monkeypatch):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    await _configure(api, slack_url=SLACK)
    await edit_node(api, wf, "n_ag", approvers={"roles": ["admin"]})
    await _waiting(api, wf, "th_retry")
    job, now = app.state.notifier, datetime.now(UTC)
    wire = Wire(job)
    wire.status = 500
    retries = NOTICES.labels(channel="slack", outcome="retry")._value.get()
    assert await job.tick(now) == 0
    async with app.state.db.sessionmaker() as s:
        row = (await s.scalars(select(ApprovalNotice))).one()
        assert (row.status, row.attempts, row.last_error) == ("pending", 1, "hooks.slack.com answered HTTP 500")
        assert row.next_at > now  # not before its backoff
    assert NOTICES.labels(channel="slack", outcome="retry")._value.get() == retries + 1
    assert await job.tick(now + timedelta(seconds=10)) == 0 and len(wire.requests) == 1  # waits for the backoff
    wire.status = 200
    assert await job.tick(now + timedelta(seconds=31)) == 1
    assert len(wire.requests) == 2

    item = await _waiting(api, wf, "th_gives_up")  # this one never gets through
    wire.status = 503
    failed = NOTICES.labels(channel="slack", outcome="failed")._value.get()
    t = now
    for _ in range(notifier.MAX_ATTEMPTS):
        await job.tick(t)
        t += timedelta(hours=3)
    async with app.state.db.sessionmaker() as s:
        q = select(ApprovalNotice).where(ApprovalNotice.interrupt_id == item["id"])
        row = (await s.scalars(q)).one()
        assert (row.status, row.attempts) == ("failed", notifier.MAX_ATTEMPTS)
    assert NOTICES.labels(channel="slack", outcome="failed")._value.get() == failed + 1


async def test_an_item_decided_before_its_notice_went_out_is_dropped(app, triage_api):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    await _configure(api, slack_url=SLACK)
    await edit_node(api, wf, "n_ag", approvers={"roles": ["admin"]})
    item = await _waiting(api, wf, "th_dropped")
    job, now = app.state.notifier, datetime.now(UTC)
    wire = Wire(job)
    await job.announce(now)
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(InterruptTask).where(InterruptTask.id == item["id"]).values(status="decided"))
        await s.commit()
    assert await job.deliver(now) == 0 and not wire.requests
    async with app.state.db.sessionmaker() as s:
        assert (await s.scalars(select(ApprovalNotice.status))).one() == "dropped"


async def test_two_servers_send_each_notice_once(app, triage_api):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    await _configure(api, slack_url=SLACK, webhook_url=HOOK)
    await edit_node(api, wf, "n_ag", approvers={"roles": ["admin"]})
    for i in range(3):
        await _waiting(api, wf, f"th_two{i}")
    other = NotifierJob(app.state.db, app.state.keyring, app.state.settings)
    wire_a, wire_b = Wire(app.state.notifier), Wire(other)
    now = datetime.now(UTC)
    sent = await asyncio.gather(app.state.notifier.tick(now), other.tick(now))
    assert sum(sent) == 6 and len(wire_a.requests) + len(wire_b.requests) == 6
    assert await other.tick(now + timedelta(minutes=10)) == 0


async def test_an_admin_sends_a_test_notification(app, api_client, smtp):
    await _configure(api_client, email=True, slack_url=SLACK)
    wire = Wire(app.state.notifier)
    wire.status = 404  # the Slack address was revoked
    results = (await api_client.post("/v1/org/notifications/test")).json()
    assert {r["channel"]: r["ok"] for r in results} == {"email": True, "slack": False}
    assert "answered HTTP 404" in next(r["error"] for r in results if r["channel"] == "slack")
    [(to, subject, _)] = wire.mails
    assert to == ["dev@localhost"] and "Notification test" in subject  # e-mail goes to the admin who asked


async def test_an_item_nobody_decides_in_its_steps_time_is_escalated_and_told(app, triage_api, smtp):  # noqa: F811
    from agentcanvas.db.models import AuditLog
    from agentcanvas.services.notifier import escalate

    api, wf = triage_api, triage_api.wf_id
    await _configure(api, email=True, slack_url=SLACK)
    await _member(app, "usr_lee", "lee@example.com", "reviewer")
    await edit_node(
        api, wf, "n_ag", approvers={"people": ["lee@example.com"], "escalate_to": "admin", "escalate_after_hours": 4}
    )
    item = await _waiting(api, wf, "th_escalate")
    async with app.state.db.sessionmaker() as s:
        row = await s.get(InterruptTask, item["id"])
        assert row is not None and row.escalate_at is not None
        due = row.escalate_at
    job = app.state.notifier
    wire = Wire(job)
    await job.tick(due - timedelta(hours=1))
    [(to, _, text)] = wire.mails
    assert to == ["lee@example.com"] and "it goes to the admin role" in text
    r = await api.post(f"/v1/inbox/{item['id']}/decision", json={"value": {"decisions": [{"type": "approve"}]}})
    assert r.status_code == 403  # the admin (the default user here) is not named yet

    await job.tick(due + timedelta(minutes=1))
    async with app.state.db.sessionmaker() as s:
        row = await s.get(InterruptTask, item["id"])
        assert row is not None and (row.assignee_role, row.status) == ("admin", "pending") and row.escalated_at
        audit = (await s.scalars(select(AuditLog).where(AuditLog.action == "approval_escalated"))).one()
        assert audit.target == f"interrupt_id={item['id']}" and audit.detail["role"] == "admin"
        assert await escalate(s, [(item["id"], "admin", "again")], datetime.now(UTC), True) == 0  # once
    (to, subject, text) = wire.mails[-1]
    assert to == ["dev@localhost"] and subject.startswith("Approval escalated") and "admin role" in text
    assert "Approval escalated" in wire.to(SLACK)[-1].content.decode()
    [mine] = [i for i in (await api.get("/v1/inbox")).json() if i["id"] == item["id"]]
    assert mine["deciders"] == "admin role; lee@example.com" and mine["may_decide"] is True
    r = await api.post(f"/v1/inbox/{item['id']}/decision", json={"value": {"decisions": [{"type": "approve"}]}})
    assert r.status_code == 202, r.text  # the run continues


async def test_an_escalation_made_by_a_background_job_reaches_the_audit_outbox(app, triage_api, monkeypatch):  # noqa: F811
    """No request writes it, so it is forwarded only because `approval_escalated` is a security action."""
    from agentcanvas.db.models import AuditLog, AuditOutbox
    from agentcanvas.services import audit_sink
    from agentcanvas.services.notifier import escalate

    monkeypatch.setattr(audit_sink, "_forwarding", audit_sink.Forwarding(("webhook",)))
    api, wf = triage_api, triage_api.wf_id
    await edit_node(api, wf, "n_ag", approvers={"roles": ["admin"], "escalate_to": "builder"})
    item = await _waiting(api, wf, "th_escalate_forwarded")
    async with app.state.db.sessionmaker() as s:
        assert await escalate(s, [(item["id"], "builder", "nobody decided")], datetime.now(UTC), False) == 1
        await s.commit()
        entry = (await s.scalars(select(AuditLog).where(AuditLog.action == "approval_escalated"))).one()
        [queued] = (await s.scalars(select(AuditOutbox).where(AuditOutbox.audit_id == entry.id))).all()
    assert (queued.destination, queued.org_id) == ("webhook", "org_default")


async def test_a_reminder_goes_once_before_an_item_escalates_and_again_before_it_expires(app, triage_api, smtp):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    await _configure(api, email=True, slack_url=SLACK)
    await _member(app, "usr_lee", "lee@example.com", "reviewer")
    approvers = {"people": ["lee@example.com"], "escalate_to": "admin", "escalate_after_hours": 72}
    await edit_node(api, wf, "n_ag", approvers=approvers)
    item = await _waiting(api, wf, "th_remind")
    async with app.state.db.sessionmaker() as s:
        due = (await s.get(InterruptTask, item["id"])).escalate_at
    job = app.state.notifier
    other = NotifierJob(app.state.db, app.state.keyring, app.state.settings)  # a second API server
    wire, wire_b = Wire(job), Wire(other)
    await job.tick(datetime.now(UTC))
    assert len(wire.mails) == 1 and len(wire.to(SLACK)) == 1  # the announcement
    await job.tick(due - timedelta(hours=25))
    assert len(wire.mails) == 1  # not yet: the reminder goes 24 hours before

    await asyncio.gather(job.tick(due - timedelta(hours=23)), other.tick(due - timedelta(hours=23)))
    mails, posts = wire.mails[1:] + wire_b.mails, wire.to(SLACK)[1:] + wire_b.to(SLACK)
    assert len(mails) == 1 and len(posts) == 1  # once, whatever the number of servers
    [(to, subject, text)] = mails
    assert to == ["lee@example.com"] and subject.startswith("Approval still waiting: Support triage")
    assert text.startswith("A reminder") and "it goes to the admin role" in text
    assert "Approval still waiting" in posts[0].content.decode()
    await job.tick(due - timedelta(hours=1))
    assert len(wire.mails) + len(wire_b.mails) == 2  # not again

    await job.tick(due + timedelta(minutes=1))  # escalated: the expiry now counts from here, with its own reminder
    async with app.state.db.sessionmaker() as s:
        row = await s.get(InterruptTask, item["id"])
        assert row.escalated_at is not None and row.reminded_at is None
        expires = row.escalated_at + timedelta(days=app.state.settings.approval_expiry_days)
    await job.tick(expires - timedelta(hours=23))
    (_, subject, text) = wire.mails[-1]
    assert subject.startswith("Approval still waiting") and "It expires on" in text


async def test_no_reminder_for_a_short_wait_or_with_reminders_off(app, triage_api, smtp, monkeypatch):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    await _configure(api, email=True)
    short = {"roles": ["admin"], "escalate_to": "builder", "escalate_after_hours": 30}
    await edit_node(api, wf, "n_ag", approvers=short)
    item = await _waiting(api, wf, "th_short")
    job = app.state.notifier
    wire = Wire(job)
    async with app.state.db.sessionmaker() as s:
        due = (await s.get(InterruptTask, item["id"])).escalate_at
    await job.tick(datetime.now(UTC))
    await job.tick(due - timedelta(hours=23))
    assert len(wire.mails) == 1  # 30 hours in all: a reminder 6 hours after the first message would be noise
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(InterruptTask, item["id"])).reminded_at is not None  # claimed, so not looked at again

    monkeypatch.setattr(app.state.settings, "approval_reminder_hours", 0)
    monkeypatch.setattr(app.state.settings, "approval_expiry_days", 3)
    await edit_node(api, wf, "n_ag", approvers={"roles": ["admin"]})
    other = await _waiting(api, wf, "th_off")
    await job.tick(datetime.now(UTC))
    await job.tick(datetime.now(UTC) + timedelta(days=2, hours=12))
    assert [m for m in wire.mails if "reminder" in m[2].lower()] == []
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(InterruptTask, other["id"])).reminded_at is None


async def test_a_reminder_before_an_item_expires(app, triage_api, smtp, monkeypatch):  # noqa: F811
    monkeypatch.setattr(app.state.settings, "approval_expiry_days", 3)
    api, wf = triage_api, triage_api.wf_id
    await _configure(api, email=True)
    await edit_node(api, wf, "n_ag", approvers={"roles": ["admin"]})
    item = await _waiting(api, wf, "th_expiring")
    job = app.state.notifier
    wire = Wire(job)
    await job.tick(datetime.now(UTC))
    await job.tick(datetime.now(UTC) + timedelta(days=1, hours=12))
    assert len(wire.mails) == 1
    await job.tick(datetime.now(UTC) + timedelta(days=2, hours=1))
    (to, subject, text) = wire.mails[-1]
    assert len(wire.mails) == 2 and to == ["dev@localhost"] and "It expires on" in text
    assert f"/inbox?item={item['id']}" in text and subject.startswith("Approval still waiting")


async def test_at_the_expiry_a_step_that_escalates_is_handed_on_instead_of_cancelled(app, triage_api):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    await edit_node(api, wf, "n_ag", approvers={"roles": ["admin"], "escalate_to": "builder"})
    item = await _waiting(api, wf, "th_expiry_escalates")
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(InterruptTask, item["id"])).escalate_at is None  # at the expiry
    job, later = app.state.retention, datetime.now(UTC) + timedelta(days=91)
    assert await job.expire_approvals(later) == 0
    async with app.state.db.sessionmaker() as s:
        row = await s.get(InterruptTask, item["id"])
        assert row is not None and (row.status, row.assignee_role) == ("pending", "builder")
    assert (await api.get("/v1/threads/th_expiry_escalates")).json()["status"] == "interrupted"  # the run waits on
    assert await job.expire_approvals(later + timedelta(days=89)) == 0  # a full expiry from the escalation
    assert await job.expire_approvals(later + timedelta(days=91)) == 1  # then it expires like any other
    [run] = (await api.get("/v1/threads/th_expiry_escalates/runs")).json()
    assert run["status"] == "cancelled" and "expired" in run["error"]


def test_escalating_after_hours_needs_a_role():
    from test_approvers import _wf

    from agentcanvas.registry import default_registry
    from agentcanvas.validation.engine import validate

    ask = {"id": "n_a", "type": "core.approval", "name": "sign",
           "config": {"show": "out", "output": "ok", "approvers": {"escalate_after_hours": 4}}}  # fmt: skip
    assert "E004" in {d.code for d in validate(_wf("v", [ask], ["out", "ok"]), default_registry())}
    ask["config"]["approvers"]["escalate_to"] = "admin"
    assert "E004" not in {d.code for d in validate(_wf("v", [ask], ["out", "ok"]), default_registry())}


async def test_deleting_a_conversation_deletes_its_notices(app, triage_api):  # noqa: F811
    """SQLite does not enforce ON DELETE CASCADE: the outbox rows go with the conversation explicitly."""
    api, wf = triage_api, triage_api.wf_id
    await _configure(api, slack_url=SLACK)
    await edit_node(api, wf, "n_ag", approvers={"roles": ["admin"]})
    await _waiting(api, wf, "th_deleted")
    assert await app.state.notifier.announce(datetime.now(UTC)) == 1
    assert (await api.delete("/v1/threads/th_deleted")).status_code == 204
    async with app.state.db.sessionmaker() as s:
        assert (await s.scalars(select(ApprovalNotice))).all() == []
