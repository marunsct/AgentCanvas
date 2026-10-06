"""Conversation retention (S11 M8-T4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from test_publish import make_greeter

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
    assert (await api_client.get("/v1/org/settings")).json() == {"retention_days": None, "effective_retention_days": 90}

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
