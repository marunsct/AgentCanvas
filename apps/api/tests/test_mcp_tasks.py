"""F-1: long or queued runs through the MCP Tasks extension (`io.modelcontextprotocol/tasks`, MCP 2026-07-28)."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from test_deployment_runs import _running
from test_kill_switch import SLOW
from test_mcp_input import FORMS, _triage, modern
from test_protocols import _publish
from test_publish import bearer, make_greeter
from test_replicas import replica
from test_run_queue import limited  # noqa: F401 - fixture
from test_runs_v1 import make

from agentcanvas.api.v1 import protocols
from agentcanvas.db.models import Run

TASKS = {"extensions": {"io.modelcontextprotocol/tasks": {}}}


async def _rpc(api, url: str, auth: dict, method: str, params: dict, caps: dict | None = None) -> dict:
    body, headers = modern(method, params, TASKS if caps is None else caps)
    return (await api.post(url, json=body, headers={**auth, **headers})).json()


async def _until(api, url: str, auth: dict, task_id: str, status: str, caps: dict | None = None) -> dict:
    for _ in range(200):
        got = (await _rpc(api, url, auth, "tasks/get", {"taskId": task_id}, caps))["result"]
        if got["status"] == status:
            return got
        await asyncio.sleep(0.05)
    raise AssertionError(f"task {task_id} never became {status}: {got}")


async def _slow(api) -> tuple[str, dict, str]:
    dep, key = await _publish(api, await make(api, SLOW))
    url = f"/v1/deployments/{dep}/mcp"
    tools = (await _rpc(api, url, bearer(key), "tools/list", {}))["result"]["tools"]
    return url, bearer(key), tools[0]["name"]


@pytest.fixture
def quick_tasks(monkeypatch):
    monkeypatch.setattr(protocols, "TASK_AFTER_S", 0.2)


async def test_a_long_run_becomes_a_task_to_poll_and_cancel(app, api_client, quick_tasks):
    url, auth, tool = await _slow(api_client)
    found = (await _rpc(api_client, url, auth, "server/discover", {}))["result"]
    assert "io.modelcontextprotocol/tasks" in found["capabilities"]["extensions"]
    made = (await _rpc(api_client, url, auth, "tools/call", {"name": tool, "arguments": {"seconds": 30}}))["result"]
    assert made["resultType"] == "task" and made["status"] == "working" and made["pollIntervalMs"] > 0
    task_id = made["taskId"]
    async with app.state.db.sessionmaker() as s:  # durably created: the task is the run
        assert await s.get(Run, task_id) is not None
    await _running(app, task_id)
    got = (await _rpc(api_client, url, auth, "tasks/get", {"taskId": task_id}))["result"]
    assert got["resultType"] == "complete" and got["status"] == "working" and got["taskId"] == task_id
    ack = (await _rpc(api_client, url, auth, "tasks/cancel", {"taskId": task_id}))["result"]
    assert ack == {"resultType": "complete", "_meta": ack["_meta"]}
    done = await _until(api_client, url, auth, task_id, "cancelled")
    assert "result" not in done
    again = (await _rpc(api_client, url, auth, "tasks/cancel", {"taskId": task_id}))["result"]
    assert again["resultType"] == "complete"  # acknowledged, nothing left to stop


async def test_a_quick_run_answers_at_once_even_for_a_task_client(api_client):
    dep, key = await _publish(api_client, await make_greeter(api_client))
    url = f"/v1/deployments/{dep}/mcp"
    res = await _rpc(api_client, url, bearer(key), "tools/call", {"name": "greeter", "arguments": {"who": "Ada"}})
    assert (
        res["result"]["resultType"] == "complete" and res["result"]["structuredContent"]["output"]["out"] == "Hello Ada"
    )


async def test_a_run_waiting_for_a_slot_becomes_a_task_at_once(app, api_client, limited):  # noqa: F811
    dep, key = await _publish(api_client, limited)
    url, auth = f"/v1/deployments/{dep}/mcp", bearer(key)
    tool = (await _rpc(api_client, url, auth, "tools/list", {}))["result"]["tools"][0]["name"]
    first = (await api_client.post(f"/v1/deployments/{dep}/runs", json={"input": {"seconds": 30}}, headers=auth)).json()
    await _running(app, first["run_id"])
    made = (await _rpc(api_client, url, auth, "tools/call", {"name": tool, "arguments": {"seconds": 0}}))["result"]
    assert made["resultType"] == "task" and made["statusMessage"] == "waiting for a run slot"
    await app.state.runner.cancel(first["run_id"])
    done = await _until(api_client, url, auth, made["taskId"], "completed")
    assert done["result"]["isError"] is False and done["result"]["structuredContent"]["status"] == "success"


async def test_a_task_that_pauses_asks_through_tasks_get_and_goes_on_after_tasks_update(
    app, api_client, settings, monkeypatch
):
    monkeypatch.setattr(protocols, "TASK_AFTER_S", 0)
    url, auth, tool = await _triage(app, api_client)
    both = {**TASKS, **FORMS}
    made = await _rpc(api_client, url, auth, "tools/call", {"name": tool, "arguments": {"ticket": "down"}}, both)
    task_id = made["result"]["taskId"]
    asked = await _until(api_client, url, auth, task_id, "input_required", both)
    [(key, form)] = asked["inputRequests"].items()
    assert key.startswith(f"{task_id}:") and form["params"]["requestedSchema"]["required"] == ["approve"]
    ignored = await _rpc(
        api_client, url, auth, "tasks/update", {"taskId": task_id, "inputResponses": {"nope": {}}}, both
    )
    assert ignored["result"]["resultType"] == "complete"  # a key it was not asked: ignored
    yes = {key: {"action": "accept", "content": {"approve": True}}}
    ack = await _rpc(api_client, url, auth, "tasks/update", {"taskId": task_id, "inputResponses": yes}, both)
    assert ack["result"]["resultType"] == "complete"
    async with replica(settings) as (_, b):  # polled from any server
        done = await _until(b, url, auth, task_id, "completed", both)
    assert done["result"]["structuredContent"]["output"]["approved"] is True
    async with app.state.db.sessionmaker() as s:
        labels = (await s.scalars(select(Run.labels).where(Run.thread_id == done["result"]["structuredContent"]
                                                           ["thread_id"]))).all()  # fmt: skip
    assert {"mcp_task": task_id} in [{k: v for k, v in (lbl or {}).items() if k == "mcp_task"} for lbl in labels]


async def test_a_paused_task_for_a_client_without_forms_completes_with_the_pause(app, api_client, monkeypatch):
    monkeypatch.setattr(protocols, "TASK_AFTER_S", 0)
    url, auth, tool = await _triage(app, api_client)
    made = (await _rpc(api_client, url, auth, "tools/call", {"name": tool, "arguments": {"ticket": "down"}}))["result"]
    done = await _until(api_client, url, auth, made["taskId"], "completed")
    assert done["result"]["structuredContent"]["status"] == "interrupted"
    assert "Call `answer`" in done["result"]["content"][0]["text"]


async def test_tasks_belong_to_the_deployment_and_need_the_capability(app, api_client, quick_tasks):
    url, auth, tool = await _slow(api_client)
    made = (await _rpc(api_client, url, auth, "tools/call", {"name": tool, "arguments": {"seconds": 30}}))["result"]
    other_dep, other_key = await _publish(api_client, await make_greeter(api_client))
    other = f"/v1/deployments/{other_dep}/mcp"
    theirs = await _rpc(api_client, other, bearer(other_key), "tasks/get", {"taskId": made["taskId"]})
    unknown = await _rpc(api_client, other, bearer(other_key), "tasks/get", {"taskId": "run_nope"})
    assert theirs["error"] == unknown["error"] == {"code": -32602, "message": "Failed to retrieve task: Task not found"}
    cancel = await _rpc(api_client, other, bearer(other_key), "tasks/cancel", {"taskId": made["taskId"]})
    assert cancel["error"]["code"] == -32602
    plain = await _rpc(api_client, url, auth, "tasks/get", {"taskId": made["taskId"]}, caps={})
    assert plain["error"]["code"] == -32021
    assert plain["error"]["data"]["requiredCapabilities"]["extensions"] == {"io.modelcontextprotocol/tasks": {}}
    body, headers = modern("tasks/get", {"taskId": made["taskId"]}, TASKS)
    headers["Mcp-Name"] = "run_other"  # a gateway routing on the header must see the task the body names
    assert (await api_client.post(url, json=body, headers={**auth, **headers})).json()["error"]["code"] == -32020
    no_tasks = await _rpc(api_client, url, auth, "tools/call", {"name": tool, "arguments": {"seconds": 0}}, {})
    assert no_tasks["result"]["resultType"] == "complete"  # a client without the extension never gets a task
    await app.state.runner.cancel(made["taskId"])
