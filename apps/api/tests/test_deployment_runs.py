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
