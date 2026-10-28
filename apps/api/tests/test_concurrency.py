"""Races: many runs at once, two starts on one thread, two decisions on one approval.

Each must end in exactly one outcome the user can understand, with the database consistent afterwards. They run on
Postgres in CI (TEST_DATABASE_URL), where the claims are real row updates and not SQLite's single writer.
"""

from __future__ import annotations

import asyncio

from test_frameworks import greeter
from test_runs_v1 import events, triage_api  # noqa: F401 - `triage_api` is a fixture

from agentcanvas.errors import Conflict
from agentcanvas.ir.models import Workflow


async def test_many_runs_at_once_all_finish_and_none_leaks_a_thread(app, api_client):
    wf = Workflow.model_validate((await greeter(api_client))["ir"])
    runner = app.state.runner
    handles = await asyncio.gather(*(runner.start(wf, input={"who": f"user {i}"}) for i in range(25)))
    await asyncio.wait_for(asyncio.gather(*(h.done.wait() for h in handles)), 60)
    assert {h.status for h in handles} == {"success"}
    assert len({h.thread_id for h in handles}) == 25
    assert runner.active_by_thread == {}
    for h in handles:
        assert (await api_client.get(f"/v1/threads/{h.thread_id}")).json()["status"] == "idle"


async def test_two_starts_on_one_thread_exactly_one_wins(app, api_client):
    wf = Workflow.model_validate((await greeter(api_client))["ir"])
    runner = app.state.runner
    outcomes = await asyncio.gather(
        *(runner.start(wf, thread_id="th_race", input={"who": f"racer {i}"}) for i in range(6)), return_exceptions=True
    )
    winners = [o for o in outcomes if not isinstance(o, BaseException)]
    losers = [o for o in outcomes if isinstance(o, BaseException)]
    assert len(winners) == 1 and all(isinstance(o, Conflict) for o in losers), outcomes
    await asyncio.wait_for(winners[0].done.wait(), 30)
    runs = (await api_client.get("/v1/threads/th_race/runs")).json()
    assert len(runs) == 1 and runs[0]["status"] == "success"
    assert (await api_client.get("/v1/threads/th_race")).json()["status"] == "idle"


async def test_two_decisions_on_one_approval_one_applies_and_the_other_is_told(triage_api):  # noqa: F811
    api = triage_api
    r = await api.post(
        f"/v1/workflows/{api.wf_id}/runs/stream", json={"input": {"ticket": "Invoice?"}, "thread_id": "th_low"}
    )
    assert events(r.text)[-1]["outcome"] == "interrupt"
    item = (await api.get("/v1/inbox", params={"workflow_id": api.wf_id})).json()[0]["id"]
    approve = {"value": {"decisions": [{"type": "approve"}]}}
    first, second = await asyncio.gather(
        api.post(f"/v1/inbox/{item}/decision", params={"stream": "true"}, json=approve),
        api.post(f"/v1/inbox/{item}/decision", params={"stream": "true"}, json=approve),
    )
    assert sorted([first.status_code, second.status_code]) == [200, 409], (first.text[:200], second.text[:200])
    thread = (await api.get("/v1/threads/th_low")).json()
    assert thread["status"] == "idle"
    runs = (await api.get("/v1/threads/th_low/runs")).json()
    assert [x["status"] for x in runs] == ["interrupted", "success"]  # applied once, not twice
