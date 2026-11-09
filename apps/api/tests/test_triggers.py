"""Sprint 8 triggers (M6-T5): cron schedules, signed webhooks, deployment-pinned trigger runs."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from test_publish import make_greeter, set_greeting

from agentcanvas.services.triggers import Cron, sign


def signed(secret: str, body: bytes, ts: int | None = None) -> dict[str, str]:
    ts = int(time.time()) if ts is None else ts
    return {"X-AgentCanvas-Signature": sign(secret, body, ts), "X-AgentCanvas-Timestamp": str(ts)}


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
    assert (await api_client.post(hook, content=body, headers=signed("nope", body))).status_code == 401
    stale = signed(trig["secret"], body, int(time.time()) - 301)  # a captured delivery replayed later
    assert (await api_client.post(hook, content=body, headers=stale)).status_code == 401
    moved = {**signed(trig["secret"], body), "X-AgentCanvas-Timestamp": str(int(time.time()) + 1)}
    assert (await api_client.post(hook, content=body, headers=moved)).status_code == 401  # timestamp is signed
    unknown = await api_client.post("/v1/hooks/trg_unknown", content=body, headers=signed(trig["secret"], body))
    assert unknown.status_code == 401  # same answer as a bad signature: trigger ids cannot be probed
    delivery = signed(trig["secret"], body)
    app.state.runner.draining = True  # a delivery whose run cannot start is not remembered: its retry works
    assert (await api_client.post(hook, content=body, headers=delivery)).status_code == 503
    app.state.runner.draining = False
    ok = await api_client.post(hook, content=body, headers=delivery)
    assert ok.status_code == 202, ok.text
    await asyncio.wait_for(app.state.runner.handles[ok.json()["run_id"]].done.wait(), 10)
    thread = (await api_client.get(f"/v1/threads/{ok.json()['thread_id']}")).json()
    assert thread["values"]["out"] == "Hello octocat"
    replay = await api_client.post(hook, content=body, headers=delivery)  # captured and replayed within 5 minutes
    assert replay.status_code == 409 and len((await api_client.get("/v1/threads")).json()["items"]) == 1

    await api_client.patch(f"/v1/triggers/{trig['id']}", json={"enabled": False})
    assert (await api_client.post(hook, content=body, headers=signed(trig["secret"], body))).status_code == 401
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


async def test_a_broken_stored_schedule_does_not_stop_the_other_triggers(app, api_client):
    """C-10: a schedule that no longer parses (saved by an older version, edited in the database) is skipped."""
    from sqlalchemy import update

    from agentcanvas.db.models import Trigger

    wf = await make_greeter(api_client)
    ids = []
    for _ in range(2):
        r = await api_client.post(
            f"/v1/workflows/{wf}/triggers", json={"kind": "cron", "config": {"schedule": "* * * * *", "input": {}}}
        )
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Trigger).where(Trigger.id == ids[0]).values(config={"schedule": "every minute"}))
        await s.commit()
    fired = await app.state.cron.tick(at("2026-10-06T09:00"))
    assert len(fired) == 1
    await asyncio.wait_for(app.state.runner.handles[fired[0]].done.wait(), 10)


async def test_replicas_fire_each_cron_minute_once(app, api_client):
    """S11: with several API replicas (one scheduler each), a due minute starts one run, not one per replica."""
    from agentcanvas.services.triggers import CronScheduler

    wf = await make_greeter(api_client)
    r = await api_client.post(
        f"/v1/workflows/{wf}/triggers", json={"kind": "cron", "config": {"schedule": "* * * * *", "input": {}}}
    )
    assert r.status_code == 201, r.text
    replicas = [app.state.cron, CronScheduler(app.state.db, app.state.runner, app.state.keyring)]
    fired = await asyncio.gather(*(c.tick(at("2026-10-06T09:00")) for c in replicas))
    assert sorted(len(f) for f in fired) == [0, 1]
    assert [len(await c.tick(at("2026-10-06T09:00"))) for c in replicas] == [0, 0]
    later = [rid for c in reversed(replicas) for rid in await c.tick(at("2026-10-06T09:01"))]
    assert len(later) == 1  # whichever ticks first takes the next minute
    for rid in [*fired[0], *fired[1], *later]:
        await asyncio.wait_for(app.state.runner.handles[rid].done.wait(), 10)


async def test_a_trigger_warns_when_a_connector_signs_in_as_the_person_who_started_the_run(api_client):
    wf = await make_greeter(api_client)
    cur = (await api_client.get(f"/v1/workflows/{wf}")).json()
    ir = cur["ir"]
    ir["resources"] = [
        {"id": "r_gh", "name": "github", "type": "mcp.server",
         "config": {"url": "https://mcp.example.com/mcp", "oauth": {"identity": "starter"}}},
        {"id": "r_slack", "name": "slack", "type": "mcp.server",
         "config": {"url": "https://slack.example.com/mcp", "oauth": {"identity": "service"}}},
    ]  # fmt: skip
    saved = await api_client.put(f"/v1/workflows/{wf}", json={"ir": ir}, headers={"If-Match": str(cur["version"])})
    assert saved.status_code == 200, saved.text
    made = await api_client.post(
        f"/v1/workflows/{wf}/triggers", json={"kind": "cron", "config": {"schedule": "0 9 * * *"}}
    )
    assert made.status_code == 201, made.text
    [warning] = made.json()["warnings"]  # only the connector that needs a person
    assert "github" in warning and "service connection" in warning and "slack" not in warning
    listed = (await api_client.get(f"/v1/workflows/{wf}/triggers")).json()
    assert [t["warnings"] for t in listed] == [[warning]]
    ir["resources"][0]["config"]["oauth"]["identity"] = "service"  # fixed: nothing to warn about
    cur = (await api_client.get(f"/v1/workflows/{wf}")).json()
    await api_client.put(f"/v1/workflows/{wf}", json={"ir": ir}, headers={"If-Match": str(cur["version"])})
    assert (await api_client.get(f"/v1/workflows/{wf}/triggers")).json()[0]["warnings"] == []


async def _cron(app, api_client, schedule: str, **config) -> str:
    wf = await make_greeter(api_client)
    r = await api_client.post(
        f"/v1/workflows/{wf}/triggers", json={"kind": "cron", "config": {"schedule": schedule, **config}}
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _finish(app, run_ids: list[str]) -> None:
    for rid in run_ids:
        await asyncio.wait_for(app.state.runner.handles[rid].done.wait(), 10)


async def test_a_schedule_missed_while_no_server_was_ticking_fires_once_when_one_comes_back(app, api_client):
    """A restart, an outage or a slow tick at the scheduled minute used to lose that occurrence: the scheduler only
    looked at the minute it ticked in. The latest occurrence missed within AGENTCANVAS_CRON_CATCHUP_S (1 h) fires
    once, on one replica, when ticking resumes; an older one is not run late."""
    from sqlalchemy import select

    from agentcanvas.db.models import Trigger
    from agentcanvas.services.triggers import CronScheduler

    due = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(days=1)  # after the trigger was made
    nightly = await _cron(app, api_client, f"{due.minute} {due.hour} * * *")
    replicas = [app.state.cron, CronScheduler(app.state.db, app.state.runner, app.state.keyring)]
    assert app.state.cron.catchup_s == 3600

    back = due + timedelta(minutes=2, seconds=5)  # the server was down at `due`
    fired = await asyncio.gather(*(c.tick(back) for c in replicas))
    assert sorted(len(f) for f in fired) == [0, 1]  # once, not once per replica
    await _finish(app, [*fired[0], *fired[1]])
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(Trigger, nightly)).last_fired == due.strftime("%Y-%m-%dT%H:%M")  # type: ignore[union-attr]
    assert [await c.tick(back + timedelta(minutes=1)) for c in replicas] == [[], []]  # and only once

    late = due + timedelta(days=1, hours=1, minutes=1)  # the next night's occurrence is over an hour old
    assert [await c.tick(late) for c in replicas] == [[], []]
    async with app.state.db.sessionmaker() as s:
        assert (await s.scalar(select(Trigger.last_fired).where(Trigger.id == nightly))) == due.strftime(
            "%Y-%m-%dT%H:%M"
        )


async def test_a_schedule_does_not_fire_for_a_time_before_it_existed_or_while_it_was_off(app, api_client):
    from sqlalchemy import update

    from agentcanvas.db.models import Trigger

    now = datetime.now(UTC)
    missed = now - timedelta(minutes=10)
    trig = await _cron(app, api_client, f"{missed.minute} * * * *")
    assert await app.state.cron.tick(now) == []  # its minute this hour came before it was made

    async with app.state.db.sessionmaker() as s:  # it last fired long ago and was then switched off
        await s.execute(update(Trigger).where(Trigger.id == trig).values(last_fired="2026-01-01T00:00"))
        await s.commit()
    assert (await api_client.patch(f"/v1/triggers/{trig}", json={"enabled": False})).status_code == 200
    assert (await api_client.patch(f"/v1/triggers/{trig}", json={"enabled": True})).status_code == 200
    assert await app.state.cron.tick(now) == []  # what it missed while off is not run when it is switched back on


def test_a_schedule_runs_in_its_time_zone_through_daylight_saving():
    """A schedule written for local business hours used to run in UTC only, an hour off for half the year."""
    from zoneinfo import ZoneInfo

    berlin = ZoneInfo("Europe/Berlin")
    eight = Cron("0 8 * * *")
    assert eight.fires_at(at("2027-01-15T07:00"), berlin) and eight.fires_at(at("2027-07-15T06:00"), berlin)
    assert not eight.fires_at(at("2027-07-15T07:00"), berlin)
    assert eight.fires_at(at("2027-07-15T08:00"), ZoneInfo("UTC"))
    # 2026-10-25: 02:00-03:00 happens twice in Berlin. A set time fires on the first pass only; a schedule that
    # runs every hour keeps running through the second pass too
    half_two = Cron("30 2 * * *")
    assert half_two.fires_at(at("2026-10-25T00:30"), berlin) and not half_two.fires_at(at("2026-10-25T01:30"), berlin)
    every = Cron("*/30 * * * *")
    assert every.fires_at(at("2026-10-25T00:30"), berlin) and every.fires_at(at("2026-10-25T01:30"), berlin)
    # 2027-03-28: 02:00-03:00 does not happen in Berlin, so a schedule set in it does not fire that day
    assert not any(half_two.fires_at(at(f"2027-03-28T{h:02}:30"), berlin) for h in range(24))


def test_a_schedule_may_name_days_and_months_and_use_the_usual_shorthands():
    weekdays = Cron("0 9 * * MON-FRI")
    assert weekdays.matches(at("2026-09-29T09:00")) and not weekdays.matches(at("2026-10-04T09:00"))  # Tue, Sun
    assert Cron("0 0 1 jan,Jul *").matches(at("2027-07-01T00:00")) and not Cron("0 0 1 JAN *").matches(
        at("2027-07-01T00:00")
    )
    assert Cron("0 0 * * sun").matches(at("2026-10-04T00:00"))
    assert Cron("@daily").matches(at("2027-07-01T00:00")) and not Cron("@daily").matches(at("2027-07-01T01:00"))
    assert Cron("@hourly").matches(at("2027-07-01T05:00")) and Cron("@weekly").matches(at("2026-10-04T00:00"))
    assert Cron("@monthly").matches(at("2027-07-01T00:00")) and Cron("@yearly").matches(at("2027-01-01T00:00"))
    for bad in ("0 9 * * MON-FRY", "@sometimes", "0 9 * FEBR *"):
        with pytest.raises(ValueError):
            Cron(bad)


async def test_a_cron_trigger_keeps_its_time_zone(app, api_client):
    wf = await make_greeter(api_client)
    made = await api_client.post(
        f"/v1/workflows/{wf}/triggers",
        json={"kind": "cron", "config": {"schedule": "0 8 * * *", "timezone": "Mars/Base"}},
    )
    assert made.status_code == 422 and "time zone" in made.json()["detail"]
    made = await api_client.post(
        f"/v1/workflows/{wf}/triggers",
        json={"kind": "cron", "config": {"schedule": "0 8 * * *", "timezone": "Europe/Berlin"}},
    )
    assert made.status_code == 201, made.text
    [listed] = (await api_client.get(f"/v1/workflows/{wf}/triggers")).json()
    assert listed["config"]["timezone"] == "Europe/Berlin"
    assert await app.state.cron.tick(at("2027-07-15T08:00")) == []  # 10:00 in Berlin
    fired = await app.state.cron.tick(at("2027-07-16T06:00"))  # 08:00 in Berlin
    assert len(fired) == 1
    await _finish(app, fired)
