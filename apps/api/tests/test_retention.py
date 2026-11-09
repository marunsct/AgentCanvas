"""Conversation retention (S11 M8-T4)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from test_publish import make_greeter
from test_runs_v1 import events, triage_api  # noqa: F401 - a fixture

from agentcanvas.db.models import Organization, Run, Thread


async def converse(api, wf: str, thread_id: str) -> None:
    r = await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "Ada"}, "thread_id": thread_id})
    assert r.status_code == 200 and "RUN_FINISHED" in r.text


async def age(app, thread_id: str, days: int, status: str | None = None) -> None:
    values: dict = {"updated_at": datetime.now(UTC) - timedelta(days=days)}
    if status:
        values["status"] = status
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Thread).where(Thread.id == thread_id).values(**values))
        await s.commit()


async def test_inactive_conversations_are_deleted_with_their_checkpoints(app, api_client):
    wf = await make_greeter(api_client)
    for t in ("th_old", "th_recent", "th_waiting"):
        await converse(api_client, wf, t)
    await age(app, "th_old", 91)
    await age(app, "th_recent", 89)
    await age(app, "th_waiting", 400, status="interrupted")  # a person still owes a decision: kept

    assert await app.state.retention.tick(datetime.now(UTC)) == ["th_old"]
    assert (await api_client.get("/v1/threads/th_old")).status_code == 404
    assert (await api_client.get("/v1/threads/th_recent")).status_code == 200
    checkpointer = app.state.runner.adapter._persist().checkpointer
    assert await checkpointer.aget_tuple({"configurable": {"thread_id": "th_old"}}) is None
    assert await checkpointer.aget_tuple({"configurable": {"thread_id": "th_recent"}}) is not None
    async with app.state.db.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(Run).where(Run.thread_id == "th_old")) == 0
    assert await app.state.retention.tick(datetime.now(UTC)) == []  # idempotent


async def test_organisations_choose_their_retention(app, api_client):
    wf = await make_greeter(api_client)
    await converse(api_client, wf, "th_a")
    await age(app, "th_a", 20)
    got = (await api_client.get("/v1/org/settings")).json()
    assert got["retention_days"] is None and got["effective_retention_days"] == 90

    keep = await api_client.put("/v1/org/settings", json={"retention_days": 0})  # forever
    assert keep.status_code == 200 and keep.json()["effective_retention_days"] == 0
    assert await app.state.retention.tick(datetime.now(UTC)) == []
    await api_client.put("/v1/org/settings", json={"retention_days": 14})
    assert await app.state.retention.tick(datetime.now(UTC)) == ["th_a"]

    assert (await api_client.put("/v1/org/settings", json={"retention_days": -1})).status_code == 422
    [entry] = (await api_client.get("/v1/org/audit", params={"action": "put_org_settings"})).json()[:1]
    assert entry["detail"]["retention_days"] == 14
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(Organization, "org_default")).retention_days == 14  # type: ignore[union-attr]


async def test_audit_rows_and_memories_expire(app, api_client):
    """S12: audit rows older than the audit retention go; memories go only when memory retention is set."""
    from agentcanvas.db.models import AuditLog

    job = app.state.retention
    assert (job.audit_days, job.memory_days) == (400, 0)  # the defaults
    await make_greeter(api_client)  # audited
    async with app.state.db.sessionmaker() as s:
        s.add(
            AuditLog(org_id="org_default", action="old", target="-", created_at=datetime.now(UTC) - timedelta(days=401))
        )
        await s.commit()
    await job.tick(datetime.now(UTC))
    actions = [e["action"] for e in (await api_client.get("/v1/org/audit")).json()]
    assert "old" not in actions and "create_workflow" in actions

    store = app.state.runner.adapter.store
    await store.aput(("org:org_default", "users", "u1"), "likes", {"text": "tea"})
    later = datetime.now(UTC) + timedelta(days=30)
    assert await job.expire_memory(later, ["org_default"]) == 0  # off by default
    job.memory_days = 7
    try:
        assert await job.expire_memory(datetime.now(UTC), ["org_default"]) == 0  # still fresh
        assert await job.expire_memory(later, ["org_default"]) == 1
        assert await store.aget(("org:org_default", "users", "u1"), "likes") is None
    finally:
        job.memory_days = 0


async def test_an_organisation_is_renamed_and_a_partial_save_keeps_the_rest(api_client):
    """Settings > Organisation > General: the name, with the retention left as it was; the server's own retentions
    are shown, not changed."""
    await api_client.put("/v1/org/settings", json={"retention_days": 30})
    r = await api_client.put("/v1/org/settings", json={"name": "  Acme Support  "})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["name"] == "Acme Support" and out["retention_days"] == 30 and out["name_editable"] is True
    assert out["audit_retention_days"] == 400 and out["memory_retention_days"] == 0
    assert (await api_client.put("/v1/org/settings", json={"name": "   "})).status_code == 422
    assert (await api_client.put("/v1/org/settings", json={"retention_days": None})).json()["retention_days"] is None


async def test_a_tick_deletes_every_expired_conversation_a_batch_at_a_time(app, api_client, monkeypatch):
    """OP-7: not only the first batch of an organisation's expired conversations per tick."""
    from agentcanvas.services import retention

    monkeypatch.setattr(retention, "BATCH", 2)
    wf = await make_greeter(api_client)
    old = [f"th_old{i}" for i in range(5)]
    for t in [*old, "th_new"]:
        await converse(api_client, wf, t)
    for t in old:
        await age(app, t, 91)
    assert sorted(await app.state.retention.tick(datetime.now(UTC))) == old
    assert (await api_client.get("/v1/threads/th_new")).status_code == 200


async def test_a_conversation_touched_after_the_scan_is_kept(app, api_client, monkeypatch):
    """OP-7: the age and status are checked again when the conversation is deleted, not only when it was found."""
    wf = await make_greeter(api_client)
    await converse(api_client, wf, "th_back")
    await age(app, "th_back", 91)
    job = app.state.retention
    scan = job._expired

    async def scan_then_touch(*args):
        found = await scan(*args)
        await age(app, "th_back", 0)  # someone continued the conversation in between
        return found

    monkeypatch.setattr(job, "_expired", scan_then_touch)
    assert await job.tick(datetime.now(UTC)) == []
    assert (await api_client.get("/v1/threads/th_back")).status_code == 200


async def test_a_conversation_another_replica_holds_is_skipped(app, api_client):
    """OP-7: replicas share the work: one held by another (locked row, Postgres) is left to it, without waiting."""
    if not app.state.db.is_postgres:
        pytest.skip("row locks need Postgres")
    wf = await make_greeter(api_client)
    for t in ("th_held", "th_free"):
        await converse(api_client, wf, t)
        await age(app, t, 91)
    async with app.state.db.sessionmaker() as other:
        await other.execute(select(Thread.id).where(Thread.id == "th_held").with_for_update())
        got = await asyncio.wait_for(app.state.retention.tick(datetime.now(UTC)), 10)
        assert got == ["th_free"]


async def test_event_payloads_and_handler_dedup_values_expire(app, api_client):
    """The event log keeps what other systems sent (webhook bodies, customer records): it follows conversation
    retention. A handler's dedup values go after the dedup window, never later than conversation retention."""
    from agentcanvas.db.models import EventDedup, EventLog

    await api_client.put("/v1/org/settings", json={"retention_days": 30})
    now = datetime.now(UTC)
    async with app.state.db.sessionmaker() as s:
        for days in (40, 1):
            at = now - timedelta(days=days)
            s.add(EventLog(id=f"evt_{days}", project_id="prj_default", type="crm.lead", data={"email": "a@b.c"},
                           status="handled", created_at=at))  # fmt: skip
            s.add(EventDedup(id=f"dd_{days}", project_id="prj_default", handler="events.leads:qualify",
                             value=f"lead-{days}", event_id=f"evt_{days}", created_at=at))  # fmt: skip
        await s.commit()
    job = app.state.retention
    await job.tick(now)
    async with app.state.db.sessionmaker() as s:
        assert list((await s.scalars(select(EventLog.id))).all()) == ["evt_1"]
        assert list((await s.scalars(select(EventDedup.value))).all()) == ["lead-1"]

    job.server = job.server.model_copy(update={"event_dedup_days": 7})  # a shorter dedup window than retention
    await job.tick(now + timedelta(days=8))
    async with app.state.db.sessionmaker() as s:
        assert list((await s.scalars(select(EventLog.id))).all()) == ["evt_1"]  # still within retention
        assert list((await s.scalars(select(EventDedup.value))).all()) == []

    await api_client.put("/v1/org/settings", json={"retention_days": 0})  # forever: the event log is kept
    await job.tick(now + timedelta(days=400))
    async with app.state.db.sessionmaker() as s:
        assert list((await s.scalars(select(EventLog.id))).all()) == ["evt_1"]


async def test_an_approval_nobody_answers_expires_and_its_conversation_then_follows_retention(
    app,
    triage_api,  # noqa: F811 - the fixture
):
    """A conversation waiting for a person is kept, but not forever: its approval expires after
    AGENTCANVAS_APPROVAL_EXPIRY_DAYS (90), the run is cancelled, and retention then deletes it like any other."""
    from agentcanvas.db.models import InterruptTask

    api, wf = triage_api, triage_api.wf_id
    for thread_id in ("th_low_abandoned", "th_low_fresh"):
        r = await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": "?"}, "thread_id": thread_id})
        assert events(r.text)[-1]["status"] == "interrupted"
    await age(app, "th_low_abandoned", 100)
    async with app.state.db.sessionmaker() as s:
        await s.execute(
            update(InterruptTask)
            .where(InterruptTask.thread_id == "th_low_abandoned")
            .values(created_at=datetime.now(UTC) - timedelta(days=100))
        )
        await s.commit()
    job = app.state.retention
    assert job.server.approval_expiry_days == 90

    assert await job.tick(datetime.now(UTC)) == []  # nothing deleted yet: the expiry starts its retention
    [expired] = (await api.get("/v1/inbox", params={"status": "expired"})).json()
    assert expired["thread_id"] == "th_low_abandoned"
    assert [x["thread_id"] for x in (await api.get("/v1/inbox")).json()] == ["th_low_fresh"]
    assert (await api.get("/v1/threads/th_low_abandoned")).json()["status"] == "idle"
    [run] = (await api.get("/v1/threads/th_low_abandoned/runs")).json()
    assert run["status"] == "cancelled" and "expired" in run["error"] and run["ended_at"]
    late = await api.post(f"/v1/inbox/{expired['id']}/decision", json={"value": {"decisions": [{"type": "approve"}]}})
    assert late.status_code == 409 and "expired" in late.text
    late = await api.post("/v1/threads/th_low_abandoned/resume", json={"value": {"decisions": [{"type": "approve"}]}})
    assert late.status_code == 409 and "expired" in late.text  # nor by the conversation: its run was cancelled
    assert (await api.get("/v1/threads/th_low_fresh")).json()["status"] == "interrupted"

    assert "th_low_abandoned" in await job.tick(datetime.now(UTC) + timedelta(days=91))
