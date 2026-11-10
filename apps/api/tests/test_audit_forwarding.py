"""Audit forwarding (E-3): which entries count as security events, and how they reach a SIEM: an outbox written with
the entry, a worker on every server that retries with a growing delay and gives up into a dead state, and metrics."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.routing import iter_route_contexts
from sqlalchemy import select, update

from agentcanvas import audit as audit_cli
from agentcanvas.api.audit import SKIPPED
from agentcanvas.api.main import create_app
from agentcanvas.db.models import AuditLog, AuditOutbox
from agentcanvas.services import audit_sink
from agentcanvas.services.audit_sink import SECURITY_ACTIONS, AuditForwarder
from agentcanvas.settings import Settings
from agentcanvas.vendor.matura.signatures import verify

SECRET = "audit-sink-secret-0123456789"
HEC_TOKEN = "11111111-2222-3333-4444-555555555555"

# writes that are audited but are not security events: building, testing and running workflows, content. A new
# route that changes something must be added here or to SECURITY_ACTIONS on purpose (the test below fails until then)
ROUTINE = {
    # workflows, versions, canvases and their code
    "create_workflow", "replace_workflow", "rename_workflow", "duplicate_workflow", "apply_commands", "create_version",
    "restore_version", "import_file", "import_agent_spec", "refactor_inline", "refactor_to_subgraph", "pull",
    "finetune_export",
    # projects and their files, defaults and first-run setup
    "create_project", "rename_project", "save_file", "delete_file", "move_file", "new_script", "upload_file",
    "put_copilot_settings", "put_knowledge_defaults", "put_model_defaults", "put_setup", "skip_setup", "test_model",
    "server_models", "test_trace_export", "test_notifications",
    "index_runs",
    # running, conversations, feedback (approvals are security events, above)
    "start_run", "cancel_run", "api_run_cancel", "continue_thread", "fork_thread", "delete_thread", "api_feedback",
    "api_upload",
    "share_feedback", "share_upload", "share_close_conversation", "a2a_endpoint", "mcp_endpoint", "api_ag_ui",
    "send_event", "ask_about_runs",
    "delete_trigger", "chat", "chat_stream", "make_workflow",
    # evaluation: datasets, test cases, evaluators, experiments, annotation, red team
    "create_dataset", "update_dataset", "delete_dataset", "add_examples", "add_runs", "add_test_cases",
    "import_examples", "update_example", "delete_example", "pull_from_langsmith", "push_to_langsmith",
    "add_test_case", "delete_test_case", "run_test_cases", "test_case_from_run", "create_evaluator",
    "update_evaluator", "delete_evaluator", "try_evaluator", "start_experiment", "cancel_experiment",
    "delete_experiment", "add_online", "update_online", "delete_online", "create_queue", "update_queue",
    "delete_queue", "add_items", "remove_item", "annotate", "start_red_team",
    # knowledge bases
    "create_knowledge_base", "update_knowledge_base", "delete_knowledge_base", "add_text", "add_url", "add_sitemap",
    "upload_documents", "delete_document", "reindex_knowledge_base", "recrawl_now", "set_recrawl", "search_knowledge",
    # the Copilot, presence, the person's own preferences and invitations, memory items
    "send_message", "stop_turn", "code_help", "decide_proposal", "choose_chat_model", "new_chat", "open_thread",
    "rename_thread", "heartbeat", "leave", "put_preferences", "decline_invitation", "delete_item",
    # starting an OAuth sign-in (the connection itself is made by `callback`)
    "authorize",
    # Matura's own feed of changes (its rows are audited by what they change)
    "matura_event",
}  # fmt: skip


def _audited_writes() -> set[str]:
    """Every route whose successful calls `api/audit.record` writes to the audit log."""
    app = create_app(Settings(env="test", database_url="sqlite:///:memory:"))
    return {
        r.name
        for r in iter_route_contexts(app.routes)
        if r.methods and r.methods - {"GET", "HEAD", "OPTIONS"} and (r.path or "").startswith("/v1/")
        if r.name is not None and r.name not in SKIPPED
    }


def test_every_audited_write_is_classified_as_a_security_event_or_routine():
    """The list of security actions was written before the kill switch, share links, OAuth clients and git pushes
    existed, and missed them. Every write route now has to be one or the other."""
    writes = _audited_writes()
    unclassified = writes - SECURITY_ACTIONS - ROUTINE
    assert not unclassified, f"add to SECURITY_ACTIONS (services/audit_sink.py) or ROUTINE here: {sorted(unclassified)}"
    assert not SECURITY_ACTIONS & ROUTINE, SECURITY_ACTIONS & ROUTINE
    assert not ROUTINE - writes, f"no such route any more: {sorted(ROUTINE - writes)}"
    # the security routes named in the review, each one classified as a security event
    assert {
        "stop_all_runs", "update_share", "regenerate_share", "put_client", "delete_client", "delete_connection",
        "put_sandbox", "delete_project", "push_to_github", "create_trigger", "erase", "accept_invitation",
        "create_token", "revoke_org_token", "create_scim_token", "set_member", "create_policy", "put_org_settings",
        "put_secret", "rotate_key",
    } <= SECURITY_ACTIONS  # fmt: skip


def test_security_actions_name_routes_or_the_rows_services_write():
    """A misspelt action would never match a row: each is a route name or one SCIM and sign-in groups write."""
    written_by_services = {
        "scim_create_user", "scim_set_active", "scim_delete_user", "scim_role_sync", "scim_create_group",
        "scim_replace_group", "scim_patch_group", "scim_delete_group", "sso_role_sync", "sso_member_removed",
        "audit_outbox_retried", "audit_outbox_discarded",  # the command line (agentcanvas/audit.py)
        "mcp_tool_drift",  # the runner, when a pinned app connector's tools changed
        "spend_cap_warning", "spend_cap_reached",  # the spending caps, as a run's spend crosses one
        "purge_organization", "download_org_export",  # the offboarding job; an export's (token-only) download link
        "export_member",  # a person's data export (record_read)
        "approval_escalated",  # the notifier and retention jobs, when nobody decided an approval in time
    }  # fmt: skip
    assert SECURITY_ACTIONS - written_by_services <= _audited_writes()


class Receiver:
    """A SIEM endpoint: records what it was sent, and answers `status` (a list is taken in order, then its last)."""

    def __init__(self, status: int | list[int] = 200):
        self.requests: list[httpx.Request] = []
        self.status = status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.status, list):
            code = self.status.pop(0) if len(self.status) > 1 else self.status[0]
        else:
            code = self.status
        return httpx.Response(code, json={"text": "ok" if code < 300 else "no", "code": 0})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture
def settings(settings):
    return settings.model_copy(
        update={
            "audit_sink_url": "https://siem.example.com/ingest", "audit_sink_secret": SECRET,
            "audit_splunk_hec_url": "https://splunk.example.com:8088/services/collector/event",
            "audit_splunk_hec_token": HEC_TOKEN,
        }
    )  # fmt: skip


@pytest.fixture
def receivers(app) -> dict[str, Receiver]:
    out = {"webhook": Receiver(202), "splunk": Receiver(200)}
    for name, rec in out.items():
        app.state.audit_forward.destinations[name].client = rec.client()
    return out


async def outbox(app) -> list[AuditOutbox]:
    async with app.state.db.sessionmaker() as s:
        return list((await s.scalars(select(AuditOutbox).order_by(AuditOutbox.id))).all())


async def test_a_security_event_is_queued_for_each_destination_with_its_entry(app, api_client, receivers):
    r = await api_client.put("/v1/projects/prj_default/secrets/API_TOKEN", json={"value": "s3cret"})
    assert r.status_code == 204
    r = await api_client.post("/v1/projects/prj_default/workflows", json={"name": "not a security action"})
    assert r.status_code == 201
    rows = await outbox(app)
    assert sorted(r.destination for r in rows) == ["splunk", "webhook"]
    assert {r.state for r in rows} == {"pending"} and rows[0].audit_id == rows[1].audit_id
    async with app.state.db.sessionmaker() as s:
        entry = await s.get(AuditLog, rows[0].audit_id)
    assert entry is not None and entry.action == "put_secret" and rows[0].org_id == entry.org_id

    assert await app.state.audit_forward.drain() == 2
    assert await outbox(app) == []  # delivered rows go
    [hook] = receivers["webhook"].requests
    verify(SECRET, hook.headers["agentcanvas-signature"], hook.content)
    event = json.loads(hook.content)
    assert event["id"] == f"audit-{entry.id}" and event["type"] == "agentcanvas.audit.put_secret"
    assert event["data"]["target"] == "name=API_TOKEN" and "s3cret" not in hook.content.decode()
    assert event["data"]["row_hash"] == entry.row_hash  # the SIEM keeps the chain's links (services/audit_chain.py)
    [hec] = receivers["splunk"].requests
    assert hec.headers["authorization"] == f"Splunk {HEC_TOKEN}"
    body = receivers["splunk"].bodies[0]
    assert body["sourcetype"] == "agentcanvas:audit" and body["event"]["action"] == "put_secret"
    assert body["event"]["id"] == entry.id and body["time"] == pytest.approx(entry.created_at.timestamp(), abs=0.01)


async def test_forwarding_every_entry_is_a_setting(app, api_client, receivers, monkeypatch):
    monkeypatch.setattr(audit_sink, "_forwarding", audit_sink.Forwarding(("webhook",), forward_all=True))
    r = await api_client.post("/v1/projects/prj_default/workflows", json={"name": "routine"})
    assert r.status_code == 201
    assert [(r.destination, r.state) for r in await outbox(app)] == [("webhook", "pending")]
    await app.state.audit_forward.drain()
    assert receivers["webhook"].bodies[0]["type"] == "agentcanvas.audit.create_workflow"


async def test_a_failed_delivery_is_retried_later_and_given_up_on_after_the_last_attempt(app, api_client, receivers):
    fwd: AuditForwarder = app.state.audit_forward
    fwd.max_attempts = 3
    receivers["webhook"].status = 503
    for name in ("KEY_A", "KEY_B"):
        assert (
            await api_client.put(f"/v1/projects/prj_default/secrets/{name}", json={"value": "s3cret-value"})
        ).status_code == 204
    now = datetime.now(UTC)
    before = audit_sink.FORWARD_FAILED.labels(destination="webhook")._value.get()
    await fwd.drain(now)
    hooks = {r.id: r for r in await outbox(app) if r.destination == "webhook"}
    assert len(receivers["webhook"].requests) == 1  # down: the second entry was not tried
    first, second = sorted(hooks)
    assert hooks[first].attempts == 1 and hooks[first].last_error == "HTTP 503"
    assert hooks[second].attempts == 0  # not an attempt
    wait = hooks[first].next_attempt_at - now
    assert timedelta(seconds=3) <= wait <= timedelta(seconds=7)  # 5 s, give or take a fifth
    assert hooks[second].next_attempt_at == hooks[first].next_attempt_at
    assert audit_sink.FORWARD_FAILED.labels(destination="webhook")._value.get() == before + 1
    assert all(r.destination == "webhook" for r in await outbox(app))  # Splunk took both

    await fwd.drain(now)  # not due yet: nothing is sent
    assert len(receivers["webhook"].requests) == 1
    dead_before = audit_sink.FORWARD_DEAD.labels(destination="webhook")._value.get()
    for _ in range(3):
        now += timedelta(hours=2)
        await fwd.drain(now)
    rows = {r.id: r for r in await outbox(app)}
    assert rows[first].state == "dead" and rows[first].attempts == 3
    assert rows[second].state == "pending" and rows[second].attempts == 1  # tried once the first gave up
    assert audit_sink.FORWARD_DEAD.labels(destination="webhook")._value.get() == dead_before + 1
    assert await fwd.count() == {("webhook", "pending"): 1, ("webhook", "dead"): 1, ("splunk", "pending"): 0,
                                 ("splunk", "dead"): 0}  # fmt: skip
    assert audit_sink.OUTBOX.labels(destination="webhook", state="dead")._value.get() == 1

    # the endpoint is back: the waiting entry goes, and an operator sends the dead one again
    receivers["webhook"].status = 202
    assert await fwd.drain(now + timedelta(hours=2)) == 1
    assert await audit_cli.retry(app.state.db, "webhook") == 1
    [again] = [r for r in await outbox(app) if r.id == first]  # the record of the retry is forwarded too
    assert (again.state, again.attempts, again.last_error) == ("pending", 0, None)
    assert await fwd.drain() == 3 and await outbox(app) == []
    retried = (await api_client.get("/v1/org/audit", params={"action": "audit_outbox_retried"})).json()
    assert retried[0]["detail"]["entries"] == 1 and retried[0]["detail"]["caller"] == "command line"


async def test_entries_for_a_destination_no_longer_configured_wait_and_can_be_discarded(app, api_client, receivers):
    async with app.state.db.sessionmaker() as s:
        row = AuditLog(org_id="org_default", action="put_secret", target="name=X", detail={})
        s.add(row)
        await s.flush()
        s.add(AuditOutbox(audit_id=row.id, org_id="org_default", destination="syslog",
                          next_attempt_at=datetime.now(UTC)))  # fmt: skip
        await s.commit()
    assert await app.state.audit_forward.drain() == 0
    assert [(r.destination, r.state) for r in await outbox(app)] == [("syslog", "pending")]
    assert (await app.state.audit_forward.count())[("syslog", "pending")] == 1  # seen, so it can be alerted on
    assert await audit_cli.outbox(app.state.db) == [("syslog", "pending", 1)]
    assert await audit_cli.discard(app.state.db, "syslog") == 0  # dead ones only, unless asked
    assert await audit_cli.discard(app.state.db, "syslog", pending=True) == 1
    assert await outbox(app) == [] or {r.destination for r in await outbox(app)} <= {"webhook", "splunk"}


async def test_two_servers_never_send_the_same_entry(app, api_client, receivers):
    """Every API server runs the worker on the same outbox: a row one has claimed is leased (and locked while it is
    claimed, skipping rows another holds), so each entry is delivered once."""
    for i in range(30):
        assert (
            await api_client.put(f"/v1/projects/prj_default/secrets/S{i}", json={"value": "s3cret-value"})
        ).status_code == 204
    hook = audit_sink.WebhookDestination(
        "https://siem.example.com/ingest", SECRET, client=receivers["webhook"].client()
    )
    other = AuditForwarder(app.state.db, [hook])
    mine = app.state.audit_forward
    mine.destinations.pop("splunk")  # only the webhook's rows here
    await asyncio.gather(mine.drain(), other.drain(), mine.drain(), other.drain())
    ids = [b["id"] for b in receivers["webhook"].bodies]
    assert len(ids) == 30 and len(set(ids)) == 30


async def test_a_delivery_claimed_by_a_server_that_stopped_is_sent_by_another_after_the_lease(app, api_client,
                                                                                             receivers):  # fmt: skip
    assert (
        await api_client.put("/v1/projects/prj_default/secrets/LOST", json={"value": "s3cret-value"})
    ).status_code == 204
    fwd = app.state.audit_forward
    now = datetime.now(UTC)
    claimed = await fwd._claim(now)  # then the server stops before it sends
    assert len(claimed) == 2
    assert await fwd.drain(now + timedelta(seconds=10)) == 0  # leased
    assert await fwd.drain(now + timedelta(seconds=audit_sink.LEASE_S + 1)) == 2
    assert await outbox(app) == []


async def test_deleting_old_entries_deletes_what_still_waits_for_them(app, api_client, receivers):
    assert (
        await api_client.put("/v1/projects/prj_default/secrets/OLD", json={"value": "s3cret-value"})
    ).status_code == 204
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(AuditLog).values(created_at=datetime.now(UTC) - timedelta(days=401)))
        await s.commit()
    await app.state.retention.expire_audit(datetime.now(UTC))
    assert await outbox(app) == []


def test_half_configured_or_clear_text_destinations_stop_the_start():
    with pytest.raises(ValueError, match="HEC_TOKEN"):
        audit_sink.destinations_for(Settings(audit_splunk_hec_url="https://splunk.example.com/services/collector"))
    plain = Settings(
        env="prod", audit_splunk_hec_url="http://splunk:8088/services/collector", audit_splunk_hec_token="t"
    )
    with pytest.raises(ValueError, match="https"):
        audit_sink.destinations_for(plain)
    assert [d.name for d in audit_sink.destinations_for(plain.model_copy(update={"env": "dev"}))] == ["splunk"]


def test_retry_delays_double_up_to_an_hour():
    assert 4 <= audit_sink.retry_delay(1) <= 6
    assert 8 <= audit_sink.retry_delay(2) <= 12
    assert 2880 <= audit_sink.retry_delay(30) <= 4320
