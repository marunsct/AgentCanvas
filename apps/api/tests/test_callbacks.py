"""E-2: a deployment run's completion callback: signed, sent once the run ended, tried again, by any server once."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update
from test_kill_switch import SLOW
from test_protocols import _publish
from test_publish import bearer, make_greeter
from test_replicas import replica
from test_runs_v1 import make
from test_versions import HIGH_CASE, new_triage

from agentcanvas.db.models import Deployment, RunCallback, Thread
from agentcanvas.services import callbacks

HOOK = "https://hooks.example.com/agentcanvas"


class Receiver:
    """The caller's endpoint: answers with the statuses it is given, in turn (then 200), and keeps what it got."""

    def __init__(self, *statuses: int):
        self.statuses, self.got = list(statuses), []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.got.append(request)
        return httpx.Response(self.statuses.pop(0) if self.statuses else 200)


@pytest.fixture
def receiver(monkeypatch) -> Receiver:
    r = Receiver()
    monkeypatch.setattr(
        callbacks, "client_factory", lambda public_only: httpx.AsyncClient(transport=httpx.MockTransport(r.handle))
    )
    return r


async def _greeter(api) -> tuple[str, str, str]:
    """A published greeter with a callback secret: (deployment, key, secret)."""
    dep, key = await _publish(api, await make_greeter(api))
    res = await api.post(f"/v1/deployments/{dep}/callback-secret")
    assert res.status_code == 200, res.text
    return dep, key, res.json()["callback_secret"]


async def _called_back(api, dep: str, key: str, route: str = "runs/wait", given: dict | None = None) -> dict:
    """A run of the deployment that asks for a callback to HOOK."""
    body = {"input": given or {"who": "Ada"}, "callback_url": HOOK}
    res = await api.post(f"/v1/deployments/{dep}/{route}", json=body, headers=bearer(key))
    assert res.status_code in (200, 202), res.text
    return res.json()


async def _row(app, run_id: str) -> RunCallback:
    async with app.state.db.sessionmaker() as s:
        row = await s.get(RunCallback, run_id)
        assert row is not None
        return row


def _verified(request: httpx.Request, secret: str) -> dict:
    stamp = request.headers["x-agentcanvas-timestamp"]
    expected = "sha256=" + hmac.new(secret.encode(), f"{stamp}.".encode() + request.content, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(request.headers["x-agentcanvas-signature"], expected)
    assert abs(int(stamp) - datetime.now(UTC).timestamp()) < 60
    return json.loads(request.content)


async def test_a_finished_run_calls_back_signed_with_its_result(app, api_client, receiver):
    dep, key, secret = await _greeter(api_client)
    assert secret.startswith("whsec_")
    async with app.state.db.sessionmaker() as s:  # kept encrypted with the organisation's key, never in clear
        stored = await s.scalar(select(Deployment.callback_secret).where(Deployment.id == dep))
        assert stored and secret not in stored
    ran = await _called_back(api_client, dep, key)
    assert ran["status"] == "success"
    assert await app.state.callbacks.tick() == [ran["run_id"]]
    [got] = receiver.got
    assert str(got.url) == HOOK and got.method == "POST" and got.headers["x-agentcanvas-delivery"] == ran["run_id"]
    body = _verified(got, secret)
    assert body["type"] == "run.finished" and body["deployment_id"] == dep
    assert {k: body[k] for k in ran} == ran  # what GET .../runs/{run_id} answers
    row = await _row(app, ran["run_id"])
    assert (row.status, row.attempts, row.last_status) == ("delivered", 1, 200) and row.delivered_at
    assert await app.state.callbacks.tick() == []  # once
    detail = (await api_client.get(f"/v1/runs/{ran['run_id']}")).json()["callback"]
    assert detail["status"] == "delivered" and detail["url"] == HOOK and detail["last_status"] == 200
    [entry] = (await api_client.get("/v1/org/audit", params={"action": "rotate_callback_secret"})).json()
    assert entry["target"] == f"dep_id={dep}"


async def test_a_run_asks_for_a_callback_only_with_a_secret_and_a_safe_address(app, api_client):
    dep, key = await _publish(api_client, await make_greeter(api_client))
    url = f"/v1/deployments/{dep}/runs"
    refused = await api_client.post(url, json={"input": {"who": "Ada"}, "callback_url": HOOK}, headers=bearer(key))
    assert refused.status_code == 422 and "callback secret" in refused.json()["detail"]
    await api_client.post(f"/v1/deployments/{dep}/callback-secret")
    for bad, why in [
        ("http://hooks.example.com/x", "https"),
        ("https://user:pw@hooks.example.com/x", "password"),
        ("https://169.254.169.254/latest/meta-data", "metadata"),
        ("https://[fd00:ec2::254]/x", "metadata"),
        ("ftp://hooks.example.com/x", "https"),
        ("https://" + "a" * 2000 + ".example.com/", "2000"),
    ]:
        res = await api_client.post(url, json={"input": {"who": "Ada"}, "callback_url": bad}, headers=bearer(key))
        assert res.status_code == 422 and why in res.text, (bad, res.text)
    async with app.state.db.sessionmaker() as s:  # nothing was started for a refused request
        assert (await s.scalars(select(Thread.id).where(Thread.deployment_id == dep))).all() == []
    app.state.settings = app.state.settings.model_copy(update={"edition": "hosted"})
    private = await api_client.post(url, json={"input": {}, "callback_url": "https://10.1.2.3/x"}, headers=bearer(key))
    assert private.status_code == 422 and "public addresses only" in private.json()["detail"]


async def test_a_failed_callback_is_tried_again_later_then_given_up(app, api_client, receiver, monkeypatch):
    monkeypatch.setattr(callbacks, "MAX_ATTEMPTS", 3)
    receiver.statuses = [500, 503, 502]
    dep, key, _ = await _greeter(api_client)
    ran = await _called_back(api_client, dep, key)
    job = app.state.callbacks
    assert await job.tick() == [ran["run_id"]]
    row = await _row(app, ran["run_id"])
    assert (row.status, row.attempts, row.last_status, row.last_error) == ("pending", 1, 500, "answered 500")
    assert row.next_at > datetime.now(UTC) + timedelta(seconds=20)  # 30 s, give or take
    assert await job.tick() == []  # not due yet
    for attempt in (2, 3):
        async with app.state.db.sessionmaker() as s:
            await s.execute(update(RunCallback).values(next_at=datetime.now(UTC) - timedelta(seconds=1)))
            await s.commit()
        assert await job.tick() == [ran["run_id"]]
        assert (await _row(app, ran["run_id"])).attempts == attempt
    row = await _row(app, ran["run_id"])
    assert (row.status, row.last_status) == ("failed", 502)
    assert len(receiver.got) == 3 and len({r.headers["x-agentcanvas-delivery"] for r in receiver.got}) == 1


async def test_an_unreachable_receiver_is_a_failed_attempt(app, api_client, monkeypatch):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(
        callbacks, "client_factory", lambda public_only: httpx.AsyncClient(transport=httpx.MockTransport(down))
    )
    dep, key, _ = await _greeter(api_client)
    ran = await _called_back(api_client, dep, key)
    await app.state.callbacks.tick()
    row = await _row(app, ran["run_id"])
    assert row.status == "pending" and row.last_status is None and "connection refused" in (row.last_error or "")


async def test_the_callback_waits_for_the_run_to_end(app, api_client, receiver):
    dep, key = await _publish(api_client, await make(api_client, SLOW))
    await api_client.post(f"/v1/deployments/{dep}/callback-secret")
    started = await _called_back(api_client, dep, key, "runs", {"seconds": 30})
    assert await app.state.callbacks.tick() == []  # still running
    await api_client.post(f"/v1/deployments/{dep}/runs/{started['run_id']}/cancel", headers=bearer(key))
    await asyncio.wait_for(app.state.runner.wait(started["run_id"]), 10)
    assert await app.state.callbacks.tick() == [started["run_id"]]
    assert json.loads(receiver.got[0].content)["status"] == "cancelled"


async def test_an_answered_pause_calls_back_too(app, api_client, receiver):
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    dep, key = await _publish(api_client, (await new_triage(api_client))["id"])
    await api_client.post(f"/v1/deployments/{dep}/callback-secret")
    paused = (await api_client.post(f"/v1/deployments/{dep}/runs/wait",
                                    json={"input": {"ticket": "down"}, "callback_url": HOOK},
                                    headers=bearer(key))).json()  # fmt: skip
    assert paused["status"] == "interrupted"
    done = (await api_client.post(f"/v1/deployments/{dep}/threads/{paused['thread_id']}/resume",
                                  json={"value": {"approved": True}, "callback_url": HOOK},
                                  headers=bearer(key))).json()  # fmt: skip
    assert done["status"] == "success"
    assert sorted(await app.state.callbacks.tick()) == sorted([paused["run_id"], done["run_id"]])
    sent = {json.loads(r.content)["run_id"]: json.loads(r.content) for r in receiver.got}
    assert sent[paused["run_id"]]["status"] == "interrupted" and sent[paused["run_id"]]["interrupts"]
    assert sent[done["run_id"]]["status"] == "success"


async def test_two_servers_deliver_a_callback_once(app, api_client, settings, receiver):
    dep, key, _ = await _greeter(api_client)
    ran = await _called_back(api_client, dep, key)
    app.state.runner.handles.clear()
    async with replica(settings) as (other, _):
        done = await asyncio.gather(app.state.callbacks.tick(), other.state.callbacks.tick())
    assert sorted(done, key=len) == [[], [ran["run_id"]]]
    assert len(receiver.got) == 1 and json.loads(receiver.got[0].content)["output"]["out"] == "Hello Ada"


async def test_a_new_secret_replaces_the_old_one(app, api_client, receiver):
    dep, key, old = await _greeter(api_client)
    new = (await api_client.post(f"/v1/deployments/{dep}/callback-secret")).json()["callback_secret"]
    assert new != old
    await api_client.post(f"/v1/deployments/{dep}/runs/wait", json={"input": {"who": "Ada"}, "callback_url": HOOK},
                          headers=bearer(key))  # fmt: skip
    await app.state.callbacks.tick()
    _verified(receiver.got[0], new)
