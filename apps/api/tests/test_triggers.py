"""Sprint 8 triggers (M6-T5): cron schedules, signed webhooks, deployment-pinned trigger runs."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest
from test_publish import make_greeter, set_greeting

from agentcanvas.services.triggers import Cron, sign


def at(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def test_cron_matching():
    assert Cron("* * * * *").matches(at("2026-09-29T13:07"))
    every5 = Cron("*/5 9-17 * * 1-5")
    assert every5.matches(at("2026-09-29T09:05"))  # a Tuesday
    assert not every5.matches(at("2026-09-29T09:06")) and not every5.matches(at("2026-09-29T18:00"))
    assert not every5.matches(at("2026-10-04T10:00"))  # Sunday
    assert Cron("0 0 * * 7").matches(at("2026-10-04T00:00")) and Cron("0 0 * * 0").matches(at("2026-10-04T00:00"))
    both = Cron("0 12 1 * 1")  # day-of-month OR day-of-week when both are restricted
    assert both.matches(at("2026-10-01T12:00")) and both.matches(at("2026-10-05T12:00"))
    assert not both.matches(at("2026-10-06T12:00"))
    assert Cron("15,45 * * * *").matches(at("2026-01-01T03:45"))
    for bad in ("* * * *", "61 * * * *", "*/0 * * * *", "5-1 * * * *"):
        with pytest.raises(ValueError):
            Cron(bad)


async def test_signed_webhook_starts_a_run(app, api_client):
    """M6-T5 AC: an invalid signature → 401; a signed delivery maps the body to the input and runs."""
    wf = await make_greeter(api_client)
    r = await api_client.post(
        f"/v1/workflows/{wf}/triggers",
        json={"kind": "webhook", "config": {"input_mapping": {"who": "{{ body.user.login }}"}}},
    )
    assert r.status_code == 201, r.text
    trig = r.json()
    assert trig["secret"].startswith("whsec_") and trig["url"] == f"/v1/hooks/{trig['id']}"
    assert "secret" not in trig["config"]
    listed = (await api_client.get(f"/v1/workflows/{wf}/triggers")).json()
    assert "secret" not in listed[0] and "secret" not in listed[0]["config"]

    body = json.dumps({"user": {"login": "octocat"}}).encode()
    hook = trig["url"]
    assert (await api_client.post(hook, content=body)).status_code == 401
    bad = await api_client.post(hook, content=body, headers={"X-AgentCanvas-Signature": sign("nope", body)})
    assert bad.status_code == 401
    ok = await api_client.post(hook, content=body, headers={"X-AgentCanvas-Signature": sign(trig["secret"], body)})
    assert ok.status_code == 202, ok.text
    await asyncio.wait_for(app.state.runner.handles[ok.json()["run_id"]].done.wait(), 10)
    thread = (await api_client.get(f"/v1/threads/{ok.json()['thread_id']}")).json()
    assert thread["values"]["out"] == "Hello octocat"

    await api_client.patch(f"/v1/triggers/{trig['id']}", json={"enabled": False})
    sig = {"X-AgentCanvas-Signature": sign(trig["secret"], body)}
    assert (await api_client.post(hook, content=body, headers=sig)).status_code == 404
    await api_client.delete(f"/v1/triggers/{trig['id']}")
    assert (await api_client.get(f"/v1/workflows/{wf}/triggers")).json() == []


async def test_cron_trigger_on_a_deployment(app, api_client):
    """M6-T5 AC: a cron trigger creates runs (visible in the run list), running the deployment's version."""
    wf = await make_greeter(api_client)
    pub = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()
    await set_greeting(api_client, wf, "Draft")
    bad = await api_client.post(f"/v1/workflows/{wf}/triggers", json={"kind": "cron", "config": {"schedule": "* *"}})
    assert bad.status_code == 422 and "5 fields" in bad.json()["detail"]
    r = await api_client.post(
        f"/v1/workflows/{wf}/triggers",
        json={"kind": "cron", "deployment_id": pub["deployment"]["id"],
              "config": {"schedule": "*/1 * * * *", "input": {"who": "cron"}}},
    )  # fmt: skip
    assert r.status_code == 201 and r.json()["secret"] is None

    cron = app.state.cron
    first = await cron.tick(at("2026-09-29T10:00"))
    assert len(first) == 1 and await cron.tick(at("2026-09-29T10:00")) == []  # once per minute
    second = await cron.tick(at("2026-09-29T10:01"))
    for rid in first + second:
        await asyncio.wait_for(app.state.runner.handles[rid].done.wait(), 10)
    threads = (await api_client.get("/v1/threads", params={"workflow_id": wf})).json()["items"]
    assert len(threads) == 2
    values = (await api_client.get(f"/v1/threads/{threads[0]['id']}")).json()["values"]
    assert values["out"] == "Hello cron"  # the published version, not the draft ("Draft cron")

    await api_client.delete(f"/v1/deployments/{pub['deployment']['id']}")
    assert await cron.tick(at("2026-09-29T10:02")) == []  # unpublished: nothing runs


async def test_replicas_fire_each_cron_minute_once(app, api_client):
    """S11: with several API replicas (one scheduler each), a due minute starts one run, not one per replica."""
    from agentcanvas.services.triggers import CronScheduler

    wf = await make_greeter(api_client)
    r = await api_client.post(
        f"/v1/workflows/{wf}/triggers", json={"kind": "cron", "config": {"schedule": "* * * * *", "input": {}}}
    )
    assert r.status_code == 201, r.text
    replicas = [app.state.cron, CronScheduler(app.state.db, app.state.runner, app.state.settings.secret_key)]
    fired = await asyncio.gather(*(c.tick(at("2026-10-06T09:00")) for c in replicas))
    assert sorted(len(f) for f in fired) == [0, 1]
    assert [len(await c.tick(at("2026-10-06T09:00"))) for c in replicas] == [0, 0]
    later = [rid for c in reversed(replicas) for rid in await c.tick(at("2026-10-06T09:01"))]
    assert len(later) == 1  # whichever ticks first takes the next minute
    for rid in [*fired[0], *fired[1], *later]:
        await asyncio.wait_for(app.state.runner.handles[rid].done.wait(), 10)
