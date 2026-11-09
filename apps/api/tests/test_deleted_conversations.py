"""A deleted workflow or project takes its conversations' saved state with it (the LangGraph checkpoints), and a
new conversation never starts from state some deleted one left under the same id.

Thread ids are one namespace across organisations and callers may choose them: before, a new workflow's run on the
id of a deleted workflow's conversation read that conversation back, from another organisation too.
"""

from __future__ import annotations

from sqlalchemy import delete
from test_publish import greeter, make_greeter
from test_retention import converse

from agentcanvas.db.models import WorkflowRow


def _saver(app):
    return app.state.runner.adapter._persist().checkpointer


async def _saved(app, thread_id: str) -> int:
    return len([c async for c in _saver(app).alist({"configurable": {"thread_id": thread_id}})])


async def test_deleting_a_workflow_deletes_its_conversations_checkpoints(app, api_client):
    wf = await make_greeter(api_client)
    await converse(api_client, wf, "support-42")
    assert await _saved(app, "support-42") > 0

    assert (await api_client.delete(f"/v1/workflows/{wf}")).status_code == 204
    assert await _saved(app, "support-42") == 0

    other = await make_greeter(api_client, "Hi")
    r = await api_client.post(
        f"/v1/workflows/{other}/runs/stream", json={"input": {"who": "Bo"}, "thread_id": "support-42"}
    )
    assert r.status_code == 200 and "RUN_FINISHED" in r.text
    history = (await api_client.get("/v1/threads/support-42/history")).text
    assert "Hello" not in history and "Ada" not in history  # nothing of the deleted conversation


async def test_deleting_a_project_deletes_its_conversations_checkpoints(app, api_client):
    pid = (await api_client.post("/v1/projects", json={"name": "Doomed"})).json()["id"]
    wf = (await api_client.post(f"/v1/projects/{pid}/workflows", json={"name": "Greeter"})).json()["id"]
    r = await api_client.put(f"/v1/workflows/{wf}", json={"ir": greeter(wf, "Hi")}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    await converse(api_client, wf, "th_project")
    assert await _saved(app, "th_project") > 0

    assert (await api_client.delete(f"/v1/projects/{pid}")).status_code == 204
    assert await _saved(app, "th_project") == 0


async def test_a_new_conversation_never_starts_from_state_left_under_its_id(app, api_client):
    """Checkpoints left behind before deletes took them (or by a run on another server as it was deleted)."""
    old = await make_greeter(api_client)
    await converse(api_client, old, "th_reused")
    async with app.state.db.sessionmaker() as s:  # the rows go, the checkpoints stay: as deletes used to leave them
        await s.execute(delete(WorkflowRow).where(WorkflowRow.id == old))
        await s.commit()
    assert await _saved(app, "th_reused") > 0

    new = await make_greeter(api_client, "Hi")
    r = await api_client.post(
        f"/v1/workflows/{new}/runs/stream", json={"input": {"who": "Bo"}, "thread_id": "th_reused"}
    )
    assert r.status_code == 200 and "RUN_FINISHED" in r.text
    history = (await api_client.get("/v1/threads/th_reused/history")).text
    assert "Ada" not in history and "Bo" in history
