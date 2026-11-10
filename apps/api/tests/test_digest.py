"""F-11: weekly run digests: last week's runs, failures grouped, spend, latency and the most flagged runs, made once
per project and week whatever the number of servers, shown on the Runs page."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, update
from test_run_find import CONVERSATIONS, NOW, make_chat, talk

from agentcanvas.db.models import AnnotationItem, AnnotationQueue, DigestNotice, Run, RunDigest, RunScore
from agentcanvas.services.digest import DigestJob, week_of
from agentcanvas.services.triggers import sign

P = "/v1/projects/prj_default"
LAST_WEEK = datetime(2026, 11, 2, tzinfo=UTC)  # NOW is Tuesday 2026-11-10: the week that ended began Monday 11-02


@pytest.fixture
async def week(app, api_client):
    wf = await make_chat(api_client)
    await talk(app, api_client, wf, CONVERSATIONS)
    async with app.state.db.sessionmaker() as s:
        hotel = await s.scalar(select(Run).where(Run.thread_id == "th_hotel"))
        refund = await s.scalar(select(Run).where(Run.thread_id == "th_refund_1"))
        assert hotel and refund
        for evaluator in ("politeness", "user feedback"):
            s.add(RunScore(id=f"rsc_{evaluator[:4]}", run_id=hotel.id, thread_id=hotel.thread_id, workflow_id=wf,
                           evaluator=evaluator, source="online", score=0.1, passed=False))  # fmt: skip
        s.add(RunScore(id="rsc_ok", run_id=refund.id, thread_id=refund.thread_id, workflow_id=wf, evaluator="tone",
                       source="online", score=0.9, passed=True))  # fmt: skip
        s.add(AnnotationQueue(id="aq_1", workflow_id=wf, name="Escalations", rubric=[], rules={}))
        await s.flush()
        item = AnnotationItem(id="aqi_1", queue_id="aq_1", run_id=refund.id, thread_id=refund.thread_id,
                              reason="failed")  # fmt: skip
        s.add(item)
        await s.commit()
    return wf


def job(app) -> DigestJob:
    return DigestJob(app.state.db, 3600)


def test_a_week_starts_on_monday_utc():
    assert week_of(NOW) == datetime(2026, 11, 9, tzinfo=UTC)
    assert week_of(datetime(2026, 11, 9, tzinfo=UTC)) == datetime(2026, 11, 9, tzinfo=UTC)
    assert week_of(datetime(2026, 11, 8, 23, 59, tzinfo=UTC)) == datetime(2026, 11, 2, tzinfo=UTC)


async def test_last_weeks_digest_counts_groups_and_flags(app, api_client, week):
    assert await job(app).tick(NOW) == 1
    r = await api_client.get(f"{P}/run-digests")
    assert r.status_code == 200, r.text
    [d] = r.json()
    assert d["week_start"].startswith("2026-11-02T00:00") and d["week_end"].startswith("2026-11-09T00:00")
    assert d["current"] is False
    s = d["summary"]
    # refund_old (20 days ago) is from another week: three runs, two failed
    assert (s["runs"], s["failed"], s["failure_rate"]) == (3, 2, 0.6667)
    assert s["cost_usd"] == pytest.approx(0.60) and s["latency_p50_ms"] == 2000 and s["latency_p95_ms"] == 2000
    [group] = s["failure_groups"]
    assert group["reason"] == "Model said … (HTTP N)" and group["runs"] == 2 and group["workflows"] == ["Chat"]
    assert group["example"]["thread_id"] == "th_refund_1"  # the newest
    assert s["workflows"] == [{"id": week, "name": "Chat", "runs": 3, "failed": 2, "cost_usd": pytest.approx(0.6)}]
    flagged = {f["thread_id"]: f for f in s["flagged"]}
    assert flagged["th_hotel"]["flags"] == 2 and set(flagged["th_hotel"]["why"]) == {"politeness", "user feedback"}
    assert flagged["th_refund_1"]["why"] == ["review: Escalations"]  # its passing score is not a flag
    assert s["flagged"][0]["thread_id"] == "th_hotel"
    assert "kettle" not in json.dumps(d)  # counts and names only: no input or answer

    assert await job(app).tick(NOW) == 0  # made once
    assert await job(app).tick(NOW + timedelta(days=3)) == 0  # still the same week


async def test_several_servers_make_it_once(app, week):
    a, b = job(app), job(app)
    made = await asyncio.gather(a.make("prj_default", LAST_WEEK), b.make("prj_default", LAST_WEEK))
    assert sorted(made) == [False, True]
    assert await b.make("prj_default", LAST_WEEK) is False
    async with app.state.db.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(RunDigest)) == 1


async def test_only_projects_that_ran_get_one_and_old_ones_go(app, api_client, week):
    r = await api_client.post("/v1/projects", json={"name": "Quiet team"})
    quiet = r.json()["id"]
    async with app.state.db.sessionmaker() as s:
        s.add(RunDigest(id="rdg_old", project_id=quiet, week_start=LAST_WEEK - timedelta(days=400), summary={}))
        await s.commit()
    assert await job(app).tick(NOW) == 1  # the default project only
    assert (await api_client.get(f"/v1/projects/{quiet}/run-digests")).json() == []  # and the old one is gone
    async with app.state.db.sessionmaker() as s:
        assert {d.project_id for d in (await s.scalars(select(RunDigest))).all()} == {"prj_default"}


async def test_test_runs_and_other_projects_are_left_out(app, api_client, week):
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Run).where(Run.thread_id == "th_hotel").values(origin="test"))
        await s.commit()
    r = await api_client.post("/v1/projects", json={"name": "Other team"})
    other = r.json()["id"]
    wf = await make_chat(api_client, other)
    await talk(app, api_client, wf, {"th_other": ("Hi", "Hello.", "error", "5", 3, {})})
    assert await job(app).tick(NOW) == 2
    mine = (await api_client.get(f"{P}/run-digests")).json()[0]["summary"]
    assert mine["runs"] == 2 and mine["cost_usd"] == pytest.approx(0.30)
    assert [f["thread_id"] for f in mine["flagged"]] == ["th_refund_1"]  # the test run's flags are left out too
    theirs = (await api_client.get(f"/v1/projects/{other}/run-digests")).json()[0]["summary"]
    assert theirs["runs"] == 1 and theirs["cost_usd"] == pytest.approx(5.0)


async def test_a_secret_in_an_error_is_masked_in_the_digest(app, api_client, week):
    secret = "sk-live-0123456789abcdef"
    app.state.projects.secrets["prj_default"] = {"API_KEY": secret}
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Run).where(Run.status == "error").values(error=f"bad key {secret}"))
        await s.commit()
    made = DigestJob(app.state.db, 3600, lambda project_id: app.state.projects.secrets.get(project_id, {}))
    assert await made.tick(NOW) == 1
    kept = (await api_client.get(f"{P}/run-digests")).json()[0]
    assert kept["summary"]["failure_groups"] and secret not in json.dumps(kept)
    async with app.state.db.sessionmaker() as s:  # this week so far, computed on request, is masked too
        await s.execute(update(Run).where(Run.thread_id == "th_refund_1").values(started_at=datetime.now(UTC)))
        await s.commit()
    current = (await api_client.get(f"{P}/run-digests/current")).json()
    assert current["summary"]["failure_groups"] and secret not in json.dumps(current)


async def test_this_week_so_far_is_computed_on_request(app, api_client, week):
    from test_run_find import DemoResources

    app.state.resources_factory = lambda thread_id, w: DemoResources().with_mocks({"model": ["Hello."]})
    body = {"input": {"messages": [{"role": "user", "content": "hi"}]}, "thread_id": "th_now"}
    assert (await api_client.post(f"/v1/workflows/{week}/runs/stream", json=body)).status_code == 200
    r = await api_client.get(f"{P}/run-digests/current")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["current"] is True and d["summary"]["runs"] == 1 and d["summary"]["failed"] == 0
    async with app.state.db.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(RunDigest)) == 0  # not kept


async def test_no_digests_when_switched_off(app):
    off = DigestJob(app.state.db, 0)
    off.start()
    assert off._task is None
    await off.stop()


# ------------------------------------------------------------------------- sent through the E-6 channels ---
SLACK = "https://hooks.slack.com/services/T000/B000/XXXXSECRETTOKEN"
HOOK = "https://hooks.example.com/digests"


@pytest.fixture
def smtp(app, monkeypatch):
    monkeypatch.setattr(app.state.settings, "smtp_host", "mail.example.com")
    monkeypatch.setattr(app.state.settings, "smtp_from", "AgentCanvas <runs@example.com>")


async def _channels(api, **body) -> dict:
    r = await api.put("/v1/org/notifications", json={"email": True, "slack_url": SLACK, "webhook_url": HOOK, **body})
    assert r.status_code == 200, r.text
    return r.json()


async def _notices(app) -> list:
    async with app.state.db.sessionmaker() as s:
        return list((await s.scalars(select(DigestNotice).order_by(DigestNotice.channel))).all())


async def test_an_organisation_that_opted_in_gets_each_digest_on_its_channels(app, api_client, week, smtp):
    from test_approvers import _member
    from test_notifier import Wire

    secret = (await _channels(api_client))["webhook_secret"]
    assert (await api_client.get("/v1/org/notifications")).json()["digest"] is False  # off until an admin opts in
    assert (await api_client.put("/v1/org/notifications", json={"digest": True})).json()["digest"] is True
    await _member(app, "usr_builder", "builder@example.com", "builder")
    await _member(app, "usr_viewer", "viewer@example.com", "viewer")
    wire = Wire(app.state.notifier)
    assert await app.state.digests.tick(NOW) == 1
    assert [n.channel for n in await _notices(app)] == ["email", "slack", "webhook"]
    assert await app.state.notifier.tick(NOW) == 3  # the notifier's loop sends them
    assert {n.status for n in await _notices(app)} == {"sent"}

    [(to, subject, text)] = wire.mails
    assert to == ["builder@example.com", "dev@localhost"]  # admins and builders, not viewers
    assert subject == "Weekly runs: Default project, 02 Nov to 08 Nov 2026"
    assert "3 runs, 2 failed (67%), $0.60 spent" in text and "1 kind of error, most often 2 runs in Chat" in text
    assert "Flagged most: Chat (2); Chat (1)." in text and text.rstrip().endswith("/runs")
    [slack] = wire.to(SLACK)
    [hook] = wire.to(HOOK)
    for request in (slack, hook):  # counts and names: never the errors' text or what a run said
        assert b"Model said" not in request.content and b"kettle" not in request.content
    assert b"3 runs, 2 failed" in slack.content
    payload = json.loads(hook.content)
    assert payload["type"] == "digest.weekly" and payload["runs"] == 3
    assert payload["project"]["name"] == "Default project"
    assert payload["failure_groups"] == [{"runs": 2, "workflows": ["Chat"]}] and len(payload["flagged"]) == 2
    stamp = hook.headers["X-AgentCanvas-Timestamp"]
    assert hook.headers["X-AgentCanvas-Event"] == "digest.weekly"
    assert hook.headers["X-AgentCanvas-Signature"] == sign(secret, hook.content, stamp)
    assert await app.state.notifier.tick(NOW) == 0  # sent once


async def test_team_addresses_failures_and_opting_out(app, api_client, week, smtp):
    from test_notifier import Wire

    await _channels(api_client, email_to=["runs@example.com"], digest=True)
    wire = Wire(app.state.notifier)
    wire.status = 500
    await app.state.digests.tick(NOW)
    assert await app.state.notifier.tick(NOW) == 1  # the e-mail; Slack and the webhook answered 500
    assert wire.mails[0][0] == ["runs@example.com"]
    retry = {n.channel: n for n in await _notices(app)}
    slack = retry["slack"]
    assert slack.status == "pending" and slack.attempts == 1 and "HTTP 500" in (slack.last_error or "")
    assert slack.next_at > NOW  # tried again later
    await api_client.put("/v1/org/notifications", json={"digest": False})
    assert await app.state.notifier.tick(NOW + timedelta(hours=1)) == 0
    assert {n.channel: n.status for n in await _notices(app)} == {"email": "sent", "slack": "dropped",
                                                                  "webhook": "dropped"}  # fmt: skip


async def test_without_the_opt_in_nothing_is_sent_and_a_race_queues_once(app, api_client, week, smtp):
    await _channels(api_client)
    await app.state.digests.tick(NOW)
    assert await _notices(app) == []  # channels set for approvals do not get digests unasked
    async with app.state.db.sessionmaker() as s:
        await s.execute(delete(RunDigest))
        await s.commit()
    await api_client.put("/v1/org/notifications", json={"digest": True})
    a = DigestJob(app.state.db, 3600, notifier=app.state.notifier)
    b = DigestJob(app.state.db, 3600, notifier=app.state.notifier)
    made = await asyncio.gather(a.make("prj_default", LAST_WEEK, NOW), b.make("prj_default", LAST_WEEK, NOW))
    assert sorted(made) == [False, True]
    assert [n.channel for n in await _notices(app)] == ["email", "slack", "webhook"]  # the loser's went with it
