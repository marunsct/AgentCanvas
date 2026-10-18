"""Second round of fixes from the 2026-10-18 deep review (see CHANGELOG v0.29.0)."""

from __future__ import annotations

from agentcanvas.runtime import paused as paused_runs
from agentcanvas.runtime.paused import DatabasePaused


async def test_paused_runs_live_with_their_conversation(app, api_client):
    """H7: paused runs of frameworks without checkpoints were kept in one process's memory, so another replica or a
    restarted server could not continue them. They are now kept on the thread row."""
    from agentcanvas.db.models import Thread

    wf = (await api_client.post("/v1/projects/prj_default/workflows", json={"name": "Paused"})).json()
    async with app.state.db.sessionmaker() as s:
        s.add(Thread(id="th_paused", workflow_id=wf["id"]))
        await s.commit()
    one, other = DatabasePaused(app.state.db), DatabasePaused(app.state.db)  # two replicas
    await one.put("th_paused", '{"step": 3}')
    assert await other.get("th_paused") == '{"step": 3}'
    async with app.state.db.sessionmaker() as s:
        row = await s.get(Thread, "th_paused")
        assert row is not None and row.paused_state == '{"step": 3}'
    await other.drop("th_paused")
    assert await one.get("th_paused") is None
    await one.put("th_nowhere", "{}")  # no row (a run outside the runner): this process keeps it
    assert await one.get("th_nowhere") == "{}"


async def test_the_server_installs_the_database_store(app):
    assert isinstance(paused_runs.store(), DatabasePaused)
    await paused_runs.save("th_x", {"a": 1})
    assert await paused_runs.load("th_x") == {"a": 1}
