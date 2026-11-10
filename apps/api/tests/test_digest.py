"""F-11: weekly run digests: last week's runs, failures grouped, spend, latency and the most flagged runs, made once
per project and week whatever the number of servers, shown on the Runs page."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from test_run_find import CONVERSATIONS, NOW, make_chat, talk

from agentcanvas.db.models import AnnotationItem, AnnotationQueue, Run, RunDigest, RunScore
from agentcanvas.services.digest import DigestJob, week_of

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
