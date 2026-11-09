"""E-2: a deployment run's result outlives the server memory that ran it.

Requests are spread over the replicas with no affinity, and a finished run leaves a server's memory after a while:
whoever asks for the result later (A2A `GetTask`, the deployment API, MCP tasks) is answered from the run's row,
which keeps the answer and the pauses whole.
"""

from __future__ import annotations

from sqlalchemy import select
from test_protocols import _publish, rpc
from test_publish import bearer, make_greeter
from test_replicas import replica
from test_versions import HIGH_CASE, new_triage

from agentcanvas.db.models import Run, Thread
from agentcanvas.services.run_results import ResultRepo


async def _saved(app, run_id: str) -> tuple:
    async with app.state.db.sessionmaker() as s:
        return (await s.execute(select(Run.output, Run.interrupts).where(Run.id == run_id))).one()


async def test_a_deployment_run_keeps_its_answer_and_a_draft_run_does_not(app, api_client):
    wf = await make_greeter(api_client)
    dep, key = await _publish(api_client, wf)
    ran = (await api_client.post(f"/v1/deployments/{dep}/runs/wait", json={"input": {"who": "Ada"}},
                                 headers=bearer(key))).json()  # fmt: skip
    assert ran["status"] == "success"
    output, interrupts = await _saved(app, ran["run_id"])
    assert output["out"] == "Hello Ada" and interrupts is None
    draft = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "Bo"}, "stream": False})
    assert await _saved(app, draft.json()["run_id"]) == (None, None)  # read in the app, from its conversation


async def test_a2a_get_task_answers_on_another_replica(app, api_client, settings):
    """Finished on A, read on B: the output (not `{}`) and, for a paused run, what it waits for."""
    from agentcanvas.testing import DemoResources

    dep, key = await _publish(api_client, await make_greeter(api_client))
    url, auth = f"/v1/deployments/{dep}/a2a", bearer(key)
    msg = {"message": {"role": "user", "messageId": "m1", "parts": [{"kind": "data", "data": {"who": "Grace"}}]}}
    done = (await api_client.post(url, json=rpc("message/send", msg), headers=auth)).json()["result"]
    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    triage = await new_triage(api_client)
    tdep, tkey = await _publish(api_client, triage["id"])
    ask = {"message": {"role": "user", "messageId": "m2", "parts": [{"kind": "text", "text": "down"}]}}
    paused = (await api_client.post(f"/v1/deployments/{tdep}/a2a", json=rpc("message/send", ask),
                                    headers=bearer(tkey))).json()["result"]  # fmt: skip
    assert paused["status"]["state"] == "input-required"
    async with replica(settings) as (other, b):
        assert not other.state.runner.handles  # B never ran either
        got = (await b.post(url, json=rpc("tasks/get", {"id": done["id"]}), headers=auth)).json()["result"]
        assert got["status"]["state"] == "completed"
        assert got["artifacts"][0]["parts"][0]["data"]["out"] == "Hello Grace"
        task = rpc("tasks/get", {"id": paused["id"]})
        waiting = (await b.post(f"/v1/deployments/{tdep}/a2a", json=task, headers=bearer(tkey))).json()["result"]
        assert waiting["status"]["state"] == "input-required"
        interrupts = waiting["status"]["message"]["parts"][1]["data"]["interrupts"]
        assert interrupts and interrupts[0]["kind"] == "approval" and interrupts[0]["id"]


async def test_the_result_of_a_run_another_deployment_made_is_not_found(app, api_client):
    dep_a, key_a = await _publish(api_client, await make_greeter(api_client))
    dep_b, _ = await _publish(api_client, await make_greeter(api_client, "Hi"))
    ran = (await api_client.post(f"/v1/deployments/{dep_a}/runs/wait", json={"input": {"who": "Ada"}},
                                 headers=bearer(key_a))).json()  # fmt: skip
    async with app.state.db.sessionmaker() as s:
        repo = ResultRepo(s, app.state.runner)
        assert (await repo.of(ran["run_id"], dep_a)).output["out"] == "Hello Ada"  # type: ignore[union-attr]
        assert await repo.of(ran["run_id"], dep_b) is None
        assert await repo.of("run_nope", dep_a) is None


async def test_a_run_a_restart_stopped_answers_with_the_run_that_continues_it(app, api_client):
    """Stopped by a redeploy, a run is still running for its caller; once another server continued it (a new run on
    the conversation, holding its place in the queue), its result is that run's."""
    dep, key = await _publish(api_client, await make_greeter(api_client))
    ran = (await api_client.post(f"/v1/deployments/{dep}/runs/wait", json={"input": {"who": "Ada"}},
                                 headers=bearer(key))).json()  # fmt: skip
    app.state.runner.handles.clear()  # as on another server
    async with app.state.db.sessionmaker() as s:
        thread = await s.get(Thread, ran["thread_id"])
        assert thread is not None
        s.add(Run(id="run_cut", thread_id=thread.id, ir_hash="x", status="interrupted", handover=True,
                  error="paused for a server restart; it continues automatically"))  # fmt: skip
        await s.commit()
        repo = ResultRepo(s, app.state.runner)
        waiting = await repo.of("run_cut", dep)
        assert waiting is not None and (waiting.status, waiting.error) == ("running", None)
        s.add(Run(id="run_next", thread_id=thread.id, ir_hash="x", status="success", queue_key="run_cut",
                  output={"out": "Hello again"}))  # fmt: skip
        await s.commit()
        continued = await repo.of("run_cut", dep)
        assert continued is not None
        assert (continued.run_id, continued.status, continued.output) == ("run_next", "success", {"out": "Hello again"})
