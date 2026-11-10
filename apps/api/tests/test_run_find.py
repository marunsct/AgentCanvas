"""F-11: the Copilot's `search_runs`: the project's runs by status, time, cost, workflow, label and topic. The model
only fills in the filters; the runs come from SQL and the run-search index, never from another project."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update
from test_copilot import ask, call, of, script
from test_copilot_debug import separate_model
from test_simulation import chat

from agentcanvas.copilot.runs import RunFinder
from agentcanvas.copilot.tools import ToolContext, run_tool
from agentcanvas.db.models import Run
from agentcanvas.errors import InvalidInput
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry
from agentcanvas.services.run_search import RunFilters, when
from agentcanvas.testing import DemoResources

P = "/v1/projects/prj_default"
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
CONVERSATIONS = {  # thread: (asked, answered, status, cost, days ago, labels)
    "th_refund_1": ("I want a refund for the broken kettle", "Your refund is on its way.", "error", "0.25", 3,
                    {"tier": "gold"}),
    "th_refund_2": ("Refund my order please", "We sent the money back.", "error", "0.05", 4, {}),
    "th_refund_old": ("Refund for last month's kettle", "Done.", "error", "0.40", 20, {}),
    "th_hotel": ("Book a hotel in Rome", "Booked two nights.", "success", "0.30", 2, {"tier": "gold"}),
}  # fmt: skip


async def make_chat(api, project: str = "prj_default", name: str = "Support") -> str:
    wf = (await api.post(f"/v1/projects/{project}/workflows", json={"name": name})).json()
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": chat(wf["id"])}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    return wf["id"]


async def talk(app, api, wf: str, convos: dict[str, tuple]) -> None:
    """One run per conversation, then each run's status, cost, start and labels set as given."""
    answers = {t: c[1] for t, c in convos.items()}
    app.state.resources_factory = lambda thread_id, w: DemoResources().with_mocks({"model": [answers[thread_id]]})
    for thread_id, (asked, *_rest) in convos.items():
        body = {"input": {"messages": [{"role": "user", "content": asked}]}, "thread_id": thread_id}
        r = await api.post(f"/v1/workflows/{wf}/runs/stream", json=body)
        assert r.status_code == 200 and "RUN_FINISHED" in r.text, r.text
    await app.state.run_search.drain()
    async with app.state.db.sessionmaker() as s:
        for thread_id, (_a, _b, status, cost, days, labels) in convos.items():
            started = NOW - timedelta(days=days)
            await s.execute(
                update(Run).where(Run.thread_id == thread_id).values(
                    status=status, cost_usd=Decimal(cost), started_at=started, labels=labels or None,
                    ended_at=started + timedelta(seconds=2), error="Model said 'no' (HTTP 500)" if status == "error"
                    else None,
                )
            )  # fmt: skip
        await s.commit()


@pytest.fixture
async def runs(app, api_client):
    wf = await make_chat(api_client)
    await talk(app, api_client, wf, CONVERSATIONS)
    return wf


async def find(app, **filters):
    return await app.state.run_search.find("prj_default", RunFilters(**filters))


def threads(result) -> list[str]:
    return [r.thread_id for r in result.runs]


async def test_filters_each_narrow_the_projects_runs(app, runs):
    everything = await find(app)
    assert everything.matched == 4 and len(everything.runs) == 4
    assert everything.cost_usd == pytest.approx(1.0)
    assert threads(everything)[0] == "th_hotel"  # newest first

    failed = await find(app, status="error")
    assert set(threads(failed)) == {"th_refund_1", "th_refund_2", "th_refund_old"}
    week = await find(app, status="error", since=NOW - timedelta(days=7), until=NOW)
    assert set(threads(week)) == {"th_refund_1", "th_refund_2"} and week.cost_usd == pytest.approx(0.30)
    pricey = await find(app, status="error", since=NOW - timedelta(days=7), min_cost_usd=0.10)
    assert threads(pricey) == ["th_refund_1"]
    one = pricey.runs[0]
    assert one.workflow_name == "Chat" and one.cost_usd == pytest.approx(0.25) and one.duration_ms == 2000
    assert one.error == "Model said 'no' (HTTP 500)" and one.labels == {"tier": "gold"} and one.tokens >= 0
    assert threads(await find(app, max_cost_usd=0.05)) == ["th_refund_2"]

    assert set(threads(await find(app, label="tier=gold"))) == {"th_refund_1", "th_hotel"}
    assert set(threads(await find(app, label="tier"))) == {"th_refund_1", "th_hotel"}
    assert threads(await find(app, label="tier=silver")) == []
    assert (await find(app, workflow="chat")).matched == 4  # by name, any case
    assert (await find(app, workflow=runs)).matched == 4  # or id
    with pytest.raises(InvalidInput, match="no workflow 'Billing'"):
        await find(app, workflow="Billing")
    assert len((await find(app, limit=2)).runs) == 2 and (await find(app, limit=2)).matched == 4


async def test_text_ranks_only_the_runs_the_filters_keep(app, runs):
    hits = await find(app, text="refund kettle")
    assert threads(hits)[0] in ("th_refund_1", "th_refund_old") and hits.runs[0].score and hits.runs[0].snippet
    on_topic = await find(app, text="hotel in Rome", status="error")  # the hotel run succeeded: not ranked at all
    assert "th_hotel" not in threads(on_topic) and on_topic.matched == 3
    week = await find(app, text="refund", since=NOW - timedelta(days=7), min_cost_usd=0.10, status="error")
    assert threads(week) == ["th_refund_1"]


async def test_another_projects_runs_are_never_found(app, api_client, runs):
    r = await api_client.post("/v1/projects", json={"name": "Other team"})
    assert r.status_code == 201, r.text
    other = r.json()["id"]
    wf = await make_chat(api_client, other, "Support")
    await talk(app, api_client, wf, {"th_other_refund": ("Refund my kettle", "Refunded.", "error", "9", 1, {})})
    mine = await find(app, text="refund kettle", status="error")
    assert "th_other_refund" not in threads(mine) and mine.matched == 3
    assert (await find(app)).matched == 4
    theirs = await app.state.run_search.find(other, RunFilters())
    assert threads(theirs) == ["th_other_refund"]
    with pytest.raises(InvalidInput):  # a workflow id of another project is unknown here
        await find(app, workflow=wf)


async def test_test_runs_are_left_out(app, runs):
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Run).where(Run.thread_id == "th_hotel").values(origin="test"))
        await s.commit()
    assert "th_hotel" not in threads(await find(app))


def test_when_reads_dates_times_and_spans():
    assert when("7d", NOW) == NOW - timedelta(days=7)
    assert when("24h", NOW) == NOW - timedelta(hours=24) and when("2W", NOW) == NOW - timedelta(weeks=2)
    assert when("2026-11-03", NOW) == datetime(2026, 11, 3, tzinfo=UTC)
    assert when("2026-11-03T09:30:00Z", NOW) == datetime(2026, 11, 3, 9, 30, tzinfo=UTC)
    assert when(None, NOW) is None and when("", NOW) is None
    with pytest.raises(InvalidInput, match="not a date"):
        when("last week", NOW)


async def test_the_tool_turns_words_into_filters_and_explains_bad_ones(app, runs):
    finder = RunFinder(app.state.run_search, "prj_default", clock=lambda: NOW)

    async def check(wf: Workflow):
        return []

    ctx = ToolContext(Workflow(id="wf_x", name="x"), default_registry(), {}, check, run_finder=finder)
    out = await run_tool(ctx, "search_runs", {"status": "failed", "since": "7d", "min_cost_usd": 0.1,
                                              "text": "refund", "unknown": 1}, ["search_runs"])  # fmt: skip
    assert out["ok"], out
    assert out["filters"]["status"] == "error" and out["filters"]["since"].startswith("2026-11-03T12:00")
    assert [r["thread_id"] for r in out["runs"]] == ["th_refund_1"] and out["matched"] == 1
    assert out["now"].startswith("2026-11-10T12:00") and out["cost_usd"] == pytest.approx(0.25)
    assert [r["thread_id"] for r in finder.found] == ["th_refund_1"]  # kept for the Ask box's list

    bad = await run_tool(ctx, "search_runs", {"since": "last week"}, ["search_runs"])
    assert not bad["ok"] and "not a date" in bad["error"]
    bad = await run_tool(ctx, "search_runs", {"status": "exploded"}, ["search_runs"])
    assert not bad["ok"] and "status" in bad["error"]
    bad = await run_tool(ctx, "search_runs", {"workflow": "Billing"}, ["search_runs"])
    assert not bad["ok"] and "no workflow 'Billing'" in bad["error"]
    none = ToolContext(Workflow(id="wf_x", name="x"), default_registry(), {}, check)
    assert not (await run_tool(none, "search_runs", {}, ["search_runs"]))["ok"]


@pytest.mark.parametrize("engine", ["loop", "deepagents"])
async def test_the_editor_copilot_searches_the_projects_runs(app, api_client, runs, settings, engine):
    settings.copilot_engine = engine
    await separate_model(api_client)
    script(app, call("search_runs", status="error", text="refund", min_cost_usd=0.2), "One failed refund cost $0.25.")
    events = await ask(api_client, runs, "which failed refund runs cost over 20 cents?", mode="ask")
    tools = of(events, "tool")
    assert [t["name"] for t in tools] == ["search_runs"] and tools[0]["ok"], tools
    assert "One failed refund" in "".join(e["text"] for e in of(events, "text"))
