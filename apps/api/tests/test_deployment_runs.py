"""E-2: a caller starts a deployment run without waiting, reads its result later and can stop it, on any server."""

from __future__ import annotations

import asyncio

from test_kill_switch import SLOW
from test_protocols import _publish
from test_publish import bearer, make_greeter
from test_redeploy import run_row
from test_replicas import replica
from test_runs_v1 import make

from agentcanvas.runtime.runner import CANCELLED_BY_USER


async def _until(client, url: str, auth: dict, status: str) -> dict:
    for _ in range(200):
        res = (await client.get(url, headers=auth)).json()
        if res["status"] == status:
            return res
        await asyncio.sleep(0.05)
    raise AssertionError(f"{url} never reached {status}: {res}")


async def _running(app, run_id: str) -> None:
    for _ in range(200):
        if run_id in app.state.runner.active() and (await run_row(app, run_id)).status == "running":
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the run did not start")


async def test_start_then_read_the_result_later_on_another_server(app, api_client, settings):
    dep, key = await _publish(api_client, await make_greeter(api_client))
    auth = bearer(key)
    started = await api_client.post(f"/v1/deployments/{dep}/runs", json={"input": {"who": "Ada"}}, headers=auth)
    assert started.status_code == 202, started.text
    body = started.json()
    assert body["status"] == "running" and body["run_id"] and body["thread_id"]
    url = f"/v1/deployments/{dep}/runs/{body['run_id']}"
    assert started.headers["location"] == f"http://test{url}"
    done = await _until(api_client, url, auth, "success")
    assert done["output"]["out"] == "Hello Ada" and done["interrupts"] == []
    app.state.runner.handles.clear()  # gone from this server's memory, as finished runs go
    async with replica(settings) as (_, b):
        again = (await b.get(url, headers=auth)).json()
        assert again == done
    assert (await api_client.get(url)).status_code == 401
    # nothing about the run itself is written to the audit log: polling and starting are high volume
    assert not (await api_client.get("/v1/org/audit", params={"action": "api_run_start"})).json()


async def test_another_deployments_run_is_not_found(api_client):
    dep_a, key_a = await _publish(api_client, await make_greeter(api_client))
    dep_b, key_b = await _publish(api_client, await make_greeter(api_client, "Hi"))
    ran = (await api_client.post(f"/v1/deployments/{dep_a}/runs/wait", json={"input": {"who": "Ada"}},
                                 headers=bearer(key_a))).json()  # fmt: skip
    for path in ("", "/cancel"):
        method = "GET" if not path else "POST"
        theirs = await api_client.request(method, f"/v1/deployments/{dep_b}/runs/{ran['run_id']}{path}",
                                          headers=bearer(key_b))  # fmt: skip
        unknown = await api_client.request(method, f"/v1/deployments/{dep_b}/runs/run_nope{path}",
                                           headers=bearer(key_b))  # fmt: skip
        assert theirs.status_code == unknown.status_code == 404
        assert theirs.json()["detail"].replace(ran["run_id"], "X") == unknown.json()["detail"].replace("run_nope", "X")
        # the key of A on B's address is refused before anything is looked up
        assert (await api_client.request(method, f"/v1/deployments/{dep_b}/runs/{ran['run_id']}{path}",
                                         headers=bearer(key_a))).status_code == 401  # fmt: skip


async def test_cancel_a_run_here_and_one_running_on_another_server(app, api_client, settings):
    dep, key = await _publish(api_client, await make(api_client, SLOW))
    auth = bearer(key)
    first = (await api_client.post(f"/v1/deployments/{dep}/runs", json={"input": {"seconds": 30}},
                                   headers=auth)).json()  # fmt: skip
    await _running(app, first["run_id"])
    here = await api_client.post(f"/v1/deployments/{dep}/runs/{first['run_id']}/cancel", headers=auth)
    assert here.status_code == 202 and here.json() == {"cancelled": True, "pending": False}
    res = await _until(api_client, f"/v1/deployments/{dep}/runs/{first['run_id']}", auth, "cancelled")
    assert res["error"] == CANCELLED_BY_USER
    [entry] = (await api_client.get("/v1/org/audit", params={"action": "api_run_cancel"})).json()
    assert entry["target"] == f"run_id={first['run_id']}" and entry["detail"]["caller"] == "deployment key"

    second = (await api_client.post(f"/v1/deployments/{dep}/runs", json={"input": {"seconds": 30}},
                                    headers=auth)).json()  # fmt: skip
    await _running(app, second["run_id"])
    async with replica(settings) as (_, b):
        there = await b.post(f"/v1/deployments/{dep}/runs/{second['run_id']}/cancel", headers=auth)
        assert there.json() == {"cancelled": True, "pending": True}
        assert (await b.get(f"/v1/deployments/{dep}/runs/{second['run_id']}", headers=auth)).json()["status"] == (
            "running"
        )
        await app.state.runner.heartbeat()  # the server running it stops it at its next heartbeat
        await _until(b, f"/v1/deployments/{dep}/runs/{second['run_id']}", auth, "cancelled")
    over = await api_client.post(f"/v1/deployments/{dep}/runs/{second['run_id']}/cancel", headers=auth)
    assert over.json() == {"cancelled": False, "pending": False}


async def test_a2a_cancels_a_working_task_here_or_on_another_server(app, api_client, settings):
    from test_protocols import rpc

    dep, key = await _publish(api_client, await make(api_client, SLOW))
    auth, url = bearer(key), f"/v1/deployments/{dep}/a2a"
    v1 = {**auth, "A2A-Version": "1.0"}

    async def slow() -> str:
        run_id = (await api_client.post(f"/v1/deployments/{dep}/runs", json={"input": {"seconds": 30}},
                                        headers=auth)).json()["run_id"]  # fmt: skip
        await _running(app, run_id)
        return run_id

    here = await slow()
    task = (await api_client.post(url, json=rpc("CancelTask", {"id": here}), headers=v1)).json()["result"]
    assert task["id"] == here and task["status"]["state"] == "TASK_STATE_CANCELED"
    again = (await api_client.post(url, json=rpc("CancelTask", {"id": here}), headers=v1)).json()
    assert again["error"]["code"] == -32002 and "has ended" in again["error"]["message"]
    there = await slow()
    async with replica(settings) as (_, b):
        working = (await b.post(url, json=rpc("tasks/cancel", {"id": there}), headers=auth)).json()["result"]
        assert working["status"]["state"] == "working"  # the server running it stops it at its next heartbeat
        await app.state.runner.heartbeat()
        await asyncio.wait_for(app.state.runner.wait(there), 10)
        done = (await b.post(url, json=rpc("tasks/get", {"id": there}), headers=auth)).json()["result"]
        assert done["status"]["state"] == "canceled"
    missing = (await api_client.post(url, json=rpc("tasks/cancel", {"id": "run_nope"}), headers=auth)).json()
    assert missing["error"]["code"] == -32001


async def test_an_mcp_call_past_the_wait_limit_names_where_to_read_the_result(app, api_client, monkeypatch):
    from test_protocols import rpc

    from agentcanvas.api.v1 import deployments

    monkeypatch.setattr(deployments, "WAIT_TIMEOUT_S", 0.2)
    dep, key = await _publish(api_client, await make(api_client, SLOW))
    url = f"/v1/deployments/{dep}/mcp"
    [tool] = (await api_client.post(url, json=rpc("tools/list"), headers=bearer(key))).json()["result"]["tools"]
    call = rpc("tools/call", {"name": tool["name"], "arguments": {"seconds": 30}})
    res = (await api_client.post(url, json=call, headers=bearer(key))).json()["result"]
    run_id = res["structuredContent"]["run_id"]
    assert res["structuredContent"]["status"] == "running" and res["isError"] is False
    assert f"GET http://test/v1/deployments/{dep}/runs/{run_id}" in res["content"][0]["text"]
    await app.state.runner.cancel(run_id)
