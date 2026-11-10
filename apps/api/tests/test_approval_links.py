"""F-9: Approve and Deny from a notification. Links are signed, bound to their item, action and recipient, expire,
decide once, never decide on a GET or when opened, and go through the Inbox's checks and audit."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from test_approvers import _member, _waiting
from test_notifier import HOOK, SLACK, Wire, _configure
from test_runs_v1 import triage_api  # noqa: F401 - fixture
from test_versions import edit_node

from agentcanvas.db.models import AppUser, AuditLog, InterruptTask, Organization
from agentcanvas.services import approval_links
from agentcanvas.services.approval_links import InvalidLink, Link

SECRET = "a-server-secret-key-of-thirty-two-chars"


@pytest.fixture
def smtp(app, monkeypatch):
    monkeypatch.setattr(app.state.settings, "smtp_host", "mail.example.com")
    monkeypatch.setattr(app.state.settings, "smtp_from", "AgentCanvas <approvals@example.com>")


def _tokens(text: str) -> dict[str, str]:
    """The Approve and Deny tokens in a message."""
    return dict(re.findall(r"(Approve|Deny): \S+/approve#(\S+)", text))


async def _emailed(app, api, thread: str) -> tuple[dict, dict[str, str], Wire]:
    """An item waiting for lee@example.com, announced by e-mail with links; returns it and lee's tokens."""
    await _configure(api, email=True, actions=True)
    await _member(app, "usr_lee", "lee@example.com", "reviewer")
    await edit_node(api, api.wf_id, "n_ag", approvers={"people": ["lee@example.com"]})
    item = await _waiting(api, api.wf_id, thread)
    wire = Wire(app.state.notifier)
    await app.state.notifier.tick(datetime.now(UTC))
    [(to, _, text)] = wire.mails
    assert to == ["lee@example.com"]
    return item, _tokens(text), wire


async def _status(app, interrupt_id: str) -> tuple[str, str | None]:
    async with app.state.db.sessionmaker() as s:
        row = await s.get(InterruptTask, interrupt_id)
        assert row is not None
        return row.status, row.decided_by


# ----------------------------------------------------------------------------------------------- the token ---
def test_a_link_is_signed_and_bound_to_its_item_action_and_recipient():
    later = datetime.now(UTC) + timedelta(hours=1)
    token = approval_links.issue(SECRET, "int_1", "approve", "usr_lee", later)
    assert approval_links.verify(SECRET, token) == Link("int_1", "approve", "usr_lee", int(later.timestamp()))
    body, tag = token.split(".")
    for field, value in (("u", "usr_mallory"), ("a", "deny"), ("i", "int_2"), ("x", int(later.timestamp()) + 9999)):
        data = json.loads(approval_links._unb64(body))
        forged = approval_links._b64(json.dumps({**data, field: value}, separators=(",", ":")).encode())
        with pytest.raises(InvalidLink, match="not one AgentCanvas made"):
            approval_links.verify(SECRET, f"{forged}.{tag}")
    with pytest.raises(InvalidLink, match="not one AgentCanvas made"):
        approval_links.verify("another-servers-secret-key-0123456789", token)
    for junk in (None, "", "x", "a.b.c", "!!.??", token + "x" * 3000):
        with pytest.raises(InvalidLink):
            approval_links.verify(SECRET, junk)


def test_a_link_expires_with_its_item_and_within_a_week():
    now = datetime.now(UTC)
    assert approval_links.expiry(now + timedelta(days=2), now) == now + timedelta(days=2)
    assert approval_links.expiry(now + timedelta(days=90), now) == now + timedelta(days=approval_links.MAX_DAYS)
    assert approval_links.expiry(None, now) == now + timedelta(days=approval_links.MAX_DAYS)
    token = approval_links.issue(SECRET, "int_1", "deny", None, now - timedelta(seconds=1))
    with pytest.raises(InvalidLink, match="expired"):
        approval_links.verify(SECRET, token)


def test_the_decision_is_what_the_inbox_buttons_send():
    asked = {"action_requests": [{"name": "a"}, {"name": "b"}]}
    assert approval_links.decision("tool_approval", asked, "approve", None) == {
        "decisions": [{"type": "approve"}, {"type": "approve"}]
    }
    denied = approval_links.decision("tool_approval", asked, "deny", "not this customer")
    assert denied["decisions"][1] == {"type": "reject", "message": "not this customer"}
    assert approval_links.decision("approval", {}, "approve", None) == {"approved": True}
    assert approval_links.decision("approval", {}, "deny", None) == {"approved": False}


# -------------------------------------------------------------------------------------------------- e-mail ---
async def test_an_emailed_link_decides_once_as_its_recipient_and_is_audited(app, triage_api, smtp):  # noqa: F811
    api = triage_api
    item, tokens, wire = await _emailed(app, api, "th_link")
    text = wire.mails[0][2]
    assert set(tokens) == {"Approve", "Deny"} and "works once and for you only" in text

    # opening the link changes nothing, however often (a mail scanner, a preview)
    seen = [await api.post("/v1/approval-links/inspect", json={"token": tokens["Approve"]}) for _ in range(2)][-1]
    assert seen.status_code == 200, seen.text
    info = seen.json()
    assert info["interrupt_id"] == item["id"] and info["action"] == "approve" and info["status"] == "pending"
    assert info["recipient"] == "lee@example.com" and info["sign_in"] is False
    assert info["workflow_name"] == "Support triage" and info["inbox_path"] == f"/inbox?item={item['id']}"
    assert "Invoice?" not in seen.text  # never what the run asks
    for path in ("/v1/approval-links/inspect", "/v1/approval-links/decision"):  # nothing answers a GET
        assert (await api.get(path, params={"token": tokens["Approve"]})).status_code == 405
    assert await _status(app, item["id"]) == ("pending", None)

    r = await api.post("/v1/approval-links/decision", json={"token": tokens["Approve"]})
    assert r.status_code == 202, r.text
    assert r.json()["action"] == "approve"
    assert await _status(app, item["id"]) == ("decided", "usr_lee")  # as lee, not as whoever clicked
    again = await api.post("/v1/approval-links/decision", json={"token": tokens["Approve"]})
    other = await api.post("/v1/approval-links/decision", json={"token": tokens["Deny"]})
    assert again.status_code == other.status_code == 409  # once
    assert (await api.post("/v1/approval-links/inspect", json={"token": tokens["Deny"]})).json()["status"] == (
        "decided"
    )
    async with app.state.db.sessionmaker() as s:
        [entry] = (await s.scalars(select(AuditLog).where(AuditLog.action == "decide_by_link"))).all()
        assert (entry.actor_id, entry.target) == ("usr_lee", f"interrupt_id={item['id']}")
        assert entry.detail["via"] == "e-mail link" and entry.detail["action"] == "approve"
        assert tokens["Approve"] not in json.dumps(entry.detail)  # the token is never logged
        assert not (await s.scalars(select(AuditLog).where(AuditLog.action == "inspect_approval_link"))).all()


async def test_a_denied_link_rejects_the_tool_call_with_the_reason(app, triage_api, smtp):  # noqa: F811
    item, tokens, _ = await _emailed(app, triage_api, "th_deny")
    r = await triage_api.post("/v1/approval-links/decision", json={"token": tokens["Deny"], "reason": "wrong customer"})
    assert r.status_code == 202, r.text
    async with app.state.db.sessionmaker() as s:
        row = await s.get(InterruptTask, item["id"])
        assert row is not None and row.decision == {"decisions": [{"type": "reject", "message": "wrong customer"}]}


async def test_a_link_decides_only_for_its_recipient_and_with_their_rights(app, triage_api, smtp):  # noqa: F811
    api = triage_api
    item, _, _ = await _emailed(app, api, "th_bound")
    later = datetime.now(UTC) + timedelta(hours=1)
    await _member(app, "usr_kim", "kim@example.com", "reviewer")  # a reviewer the step does not name
    kim = approval_links.issue(app.state.settings.secret_key, item["id"], "approve", "usr_kim", later)
    r = await api.post("/v1/approval-links/decision", json={"token": kim})
    assert r.status_code == 403 and "lee@example.com" in r.json()["detail"]
    async with app.state.db.sessionmaker() as s:  # someone with no membership in the item's organisation
        s.add(AppUser(id="usr_out", oidc_subject="usr_out", email="out@example.com"))
        await s.commit()
    outsider = approval_links.issue(app.state.settings.secret_key, item["id"], "approve", "usr_out", later)
    r = await api.post("/v1/approval-links/decision", json={"token": outsider})
    assert r.status_code == 403 and "no longer a member" in r.json()["detail"]
    await _member(app, "usr_vi", "vi@example.com", "viewer")
    await edit_node(api, api.wf_id, "n_ag", approvers={"people": ["lee@example.com", "vi@example.com"]})
    viewer = approval_links.issue(app.state.settings.secret_key, item["id"], "approve", "usr_vi", later)
    r = await api.post("/v1/approval-links/decision", json={"token": viewer})
    assert r.status_code == 403 and "reviewer" in r.json()["detail"]
    assert await _status(app, item["id"]) == ("pending", None)


async def test_an_expired_or_foreign_link_decides_nothing(app, triage_api, smtp):  # noqa: F811
    api = triage_api
    item, _, _ = await _emailed(app, api, "th_expired")
    gone = approval_links.issue(app.state.settings.secret_key, item["id"], "approve", "usr_lee", datetime.now(UTC))
    foreign = approval_links.issue("another-servers-secret-key-0123456789", item["id"], "approve", "usr_lee",
                                   datetime.now(UTC) + timedelta(hours=1))  # fmt: skip
    for token, why in ((gone, "expired"), (foreign, "not one AgentCanvas made")):
        for path in ("/v1/approval-links/inspect", "/v1/approval-links/decision"):
            r = await api.post(path, json={"token": token})
            assert r.status_code == 404 and why in r.json()["detail"], r.text
    assert await _status(app, item["id"]) == ("pending", None)


async def test_links_need_a_sign_in_or_an_allowed_network_as_the_organisation_says(app, triage_api, smtp):  # noqa: F811
    api = triage_api
    item, tokens, _ = await _emailed(app, api, "th_mfa")
    async with app.state.db.sessionmaker() as s:
        await s.execute(
            update(Organization).where(Organization.id == "org_default").values(settings={"require_mfa": True})
        )
        await s.commit()
    info = (await api.post("/v1/approval-links/inspect", json={"token": tokens["Approve"]})).json()
    assert info["sign_in"] is True and "multi-factor" in info["why_sign_in"]
    r = await api.post("/v1/approval-links/decision", json={"token": tokens["Approve"]})
    assert r.status_code == 403 and "open the item in the Inbox" in r.json()["detail"]
    async with app.state.db.sessionmaker() as s:  # only from the organisation's allowed networks, like a sign-in
        await s.execute(
            update(Organization)
            .where(Organization.id == "org_default")
            .values(settings={"allowed_networks": ["10.9.0.0/16"]})
        )
        await s.commit()
    for path in ("/v1/approval-links/inspect", "/v1/approval-links/decision"):
        r = await api.post(path, json={"token": tokens["Approve"]})
        assert r.status_code == 403 and "allowed networks" in r.json()["detail"]
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Organization).where(Organization.id == "org_default").values(settings={}))
        await s.commit()
    await _configure(api, actions=False)  # links sent before an admin switched them off stop deciding
    r = await api.post("/v1/approval-links/decision", json={"token": tokens["Approve"]})
    assert r.status_code == 403 and "Inbox only" in r.json()["detail"]
    assert await _status(app, item["id"]) == ("pending", None)


async def test_without_links_switched_on_messages_carry_none(app, triage_api, smtp):  # noqa: F811
    api = triage_api
    await _configure(api, email=True, slack_url=SLACK)
    await edit_node(api, api.wf_id, "n_ag", approvers={"roles": ["admin"]})
    await _waiting(api, api.wf_id, "th_plain")
    wire = Wire(app.state.notifier)
    await app.state.notifier.tick(datetime.now(UTC))
    [(_, _, text)] = wire.mails
    assert "/approve#" not in text and "/approve#" not in wire.to(SLACK)[0].content.decode()


# ------------------------------------------------------------------------------------------ Slack, webhook ---
async def test_slack_buttons_and_the_webhook_open_the_item_and_need_a_sign_in(app, triage_api):  # noqa: F811
    api = triage_api
    await _configure(api, slack_url=SLACK, webhook_url=HOOK, actions=True)
    await edit_node(api, api.wf_id, "n_ag", approvers={"roles": ["admin"]})
    item = await _waiting(api, api.wf_id, "th_slack")
    wire = Wire(app.state.notifier)
    await app.state.notifier.tick(datetime.now(UTC))
    body = json.loads(wire.to(SLACK)[0].content)
    [actions] = [b for b in body["blocks"] if b["type"] == "actions"]
    urls = {e["action_id"]: e["url"] for e in actions["elements"]}
    assert set(urls) == {"approve", "deny", "open"} and urls["open"].endswith(f"/inbox?item={item['id']}")
    hook = json.loads(wire.to(HOOK)[0].content)
    assert set(hook["actions"]) == {"approve", "deny"}
    for url in (urls["approve"], hook["actions"]["deny"]):
        token = url.split("/approve#", 1)[1]
        link = approval_links.verify(app.state.settings.secret_key, token)
        assert link.interrupt_id == item["id"] and link.recipient is None  # a channel, not a person
        info = (await api.post("/v1/approval-links/inspect", json={"token": token})).json()
        assert info["sign_in"] is True and info["recipient"] is None and "channel" in info["why_sign_in"]
        r = await api.post("/v1/approval-links/decision", json={"token": token})
        assert r.status_code == 403 and "open the item in the Inbox" in r.json()["detail"]
    assert await _status(app, item["id"]) == ("pending", None)
