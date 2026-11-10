"""Spending caps per organisation and project (OWASP LLM10): an administrator caps what the whole organisation, or one
project, spends a day and a month; runs of every kind are refused at a cap and runs in flight stop."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from test_budget_replicas import CALLS, Slow, _chain
from test_durable_state import _events, _workflow, instance
from test_runs_v1 import triage_api  # noqa: F401 - fixture

from agentcanvas.adapters.langchain import nodes
from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.db.models import AuditLog, Run, SpendArchive, SpendCapAlert, Thread
from agentcanvas.registry import default_registry

CALL = (10 * 1000.0 + 5 * 1000.0) / 1_000_000  # USD per model call of the chain below


def _priced(monkeypatch) -> None:
    info = default_registry().model("openai:gpt-5-mini")
    monkeypatch.setattr(info, "price_in_per_mtok", 1000.0)
    monkeypatch.setattr(info, "price_out_per_mtok", 1000.0)
    monkeypatch.setattr(nodes, "init_chat_model", lambda *a, **kw: Slow())


def _plain_chain() -> dict[str, Any]:
    ir = _chain(1.0)
    ir["settings"] = {}  # no workflow budget: only the caps
    return ir


async def _spent(app) -> float:
    async with app.state.db.sessionmaker() as s:
        return float(await s.scalar(select(func.sum(Run.cost_usd))) or 0)


async def _stream(api, wf: str, q: str = "a", **extra: Any):
    return await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"q": q}, **extra})


async def test_the_organisation_cap_is_one_of_its_limits_within_the_server_cap(app, api_client):
    r = await api_client.put("/v1/org/settings", json={"limits": {"max_cost_usd_month": 50, "cost_warn_percent": 90}})
    assert r.status_code == 200, r.text
    eff = r.json()["limits"]["effective"]
    assert eff["max_cost_usd_month"] == 50 and eff["max_cost_usd_day"] == 0 and eff["cost_warn_percent"] == 90
    app.state.settings.max_cost_usd_month = 20.5  # the operator's cap for every organisation
    try:
        r = await api_client.put("/v1/org/settings", json={"limits": {"max_cost_usd_month": 30}})
        assert r.status_code == 422 and "20.5 USD a month" in r.json()["detail"], r.text
        assert (await api_client.get("/v1/org/settings")).json()["limits"]["effective"]["max_cost_usd_month"] == 20.5
    finally:
        app.state.settings.max_cost_usd_month = 0
    assert (await api_client.put("/v1/org/settings", json={"limits": {"max_cost_usd_day": 0}})).status_code == 422


async def test_a_run_stops_at_the_organisation_cap_with_alerts_and_audit_entries(app, api_client, monkeypatch):
    _priced(monkeypatch)
    wf = await _workflow(api_client, _plain_chain())
    cap = 2.5 * CALL  # the run would make four calls
    assert (await api_client.put("/v1/org/settings", json={"limits": {"max_cost_usd_day": cap}})).status_code == 200
    events = _events((await _stream(api_client, wf)).text)
    end = events[-1]
    assert end["type"] == "RUN_ERROR" and end["code"] == "spending_cap", end
    assert "the organisation spent" in end["message"] and "this day" in end["message"]
    assert end["usage"]["cost_usd"] == pytest.approx(3 * CALL)  # stopped after the call that reached it
    alerts = [e["value"] for e in events if e.get("name") == "ac.cost_alert"]
    assert [(a["scope"], a["share"]) for a in alerts] == [("organisation", 0.8), ("organisation", 1.0)], alerts
    assert "used 80% of its day spending cap" in alerts[0]["message"]
    async with app.state.db.sessionmaker() as s:
        rows = (await s.scalars(select(SpendCapAlert).order_by(SpendCapAlert.share))).all()
        audit = (await s.scalars(select(AuditLog.action).where(AuditLog.action.like("spend_cap%")))).all()
    assert [(r.scope, r.period, r.share) for r in rows] == [("", "day", 0.8), ("", "day", 1.0)]
    assert sorted(audit) == ["spend_cap_reached", "spend_cap_warning"]

    # the next run is refused before it starts, with the reason and when to retry
    r = await _stream(api_client, wf, "b")
    assert r.status_code == 429, r.text
    body = r.json()
    assert body["type"].endswith("/spending-cap") and body["scope"] == "organisation" and body["period"] == "day"
    assert "the organisation reached its day spending cap" in body["detail"] and int(r.headers["Retry-After"]) > 0
    # debug runs and the test cases' runs are refused alike; nothing more was spent
    assert (await _stream(api_client, wf, "c", mode="debug")).status_code == 429
    assert await _spent(app) == pytest.approx(3 * CALL)
    # the alerts and what was spent show on the Spending page
    caps = (await api_client.get("/v1/org/spending/caps")).json()
    assert caps["organisation"]["day"]["limit_usd"] == pytest.approx(cap)
    assert caps["organisation"]["day"]["spent_usd"] == pytest.approx(3 * CALL)
    assert [a["share"] for a in caps["alerts"]] == [1.0, 0.8] and caps["warn_percent"] == 80


async def test_runs_of_every_kind_are_refused_at_a_cap(app, api_client, monkeypatch):
    _priced(monkeypatch)
    wf = await _workflow(api_client, _plain_chain())
    assert _events((await _stream(api_client, wf)).text)[-1]["type"] == "RUN_FINISHED"
    pub = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()
    r = await api_client.put("/v1/org/settings", json={"limits": {"max_cost_usd_month": CALLS * CALL}})
    assert r.status_code == 200
    # a deployment's API callers
    r = await api_client.post(
        f"/v1/deployments/{pub['deployment']['id']}/runs/wait", json={"input": {"q": "x"}},
        headers={"Authorization": f"Bearer {pub['api_key']}"},
    )  # fmt: skip
    assert r.status_code == 429 and "month spending cap" in r.json()["detail"], r.text
    # whatever starts a run without a request (schedules, events, experiments, hand-overs) goes through the runner
    from agentcanvas.errors import SpendingCapReached
    from agentcanvas.ir.models import Workflow

    ir = Workflow.model_validate((await api_client.get(f"/v1/workflows/{wf}")).json()["ir"])
    for origin in ("schedule", "event", "test"):
        with pytest.raises(SpendingCapReached):
            await app.state.runner.start(ir, input={"q": "y"}, origin=origin)
    assert not app.state.runner.active_by_thread  # nothing left reserved
    assert await _spent(app) == pytest.approx(CALLS * CALL)


async def test_a_project_cap_binds_only_its_project_and_never_exceeds_the_organisations(app, api_client, monkeypatch):
    _priced(monkeypatch)
    other = (await api_client.post("/v1/projects", json={"name": "Other"})).json()["id"]
    wf = await _workflow(api_client, _plain_chain())
    assert _events((await _stream(api_client, wf)).text)[-1]["type"] == "RUN_FINISHED"
    assert (await api_client.put("/v1/org/settings", json={"limits": {"max_cost_usd_day": 10}})).status_code == 200
    r = await api_client.put("/v1/projects/prj_default/spending-caps", json={"max_cost_usd_day": 11})
    assert r.status_code == 422 and "cannot be higher" in r.json()["detail"], r.text
    r = await api_client.put("/v1/projects/prj_default/spending-caps", json={"max_cost_usd_day": CALL})
    assert r.status_code == 200 and r.json() == {"max_cost_usd_day": CALL, "max_cost_usd_month": None}
    r = await _stream(api_client, wf, "b")
    assert r.status_code == 429, r.text
    assert "project 'Default project' reached its day spending cap" in r.json()["detail"]
    assert r.json()["scope"] == "project"
    # another project of the organisation still runs
    r = await api_client.post(f"/v1/projects/{other}/workflows", json={"name": "Elsewhere"})
    elsewhere = r.json()["id"]
    ir = {**_plain_chain(), "id": elsewhere}
    r = await api_client.put(f"/v1/workflows/{elsewhere}", json={"ir": ir}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    assert _events((await _stream(api_client, elsewhere)).text)[-1]["type"] == "RUN_FINISHED"
    caps = (await api_client.get("/v1/org/spending/caps")).json()
    mine = next(p for p in caps["projects"] if p["id"] == "prj_default")
    assert mine["own"] == {"max_cost_usd_day": CALL} and mine["day"]["limit_usd"] == CALL
    assert caps["organisation"]["day"]["spent_usd"] == pytest.approx(2 * CALLS * CALL)
    # null removes the cap; the project's other settings stay
    r = await api_client.put("/v1/projects/prj_default/spending-caps", json={"max_cost_usd_day": None})
    assert r.status_code == 200 and r.json()["max_cost_usd_day"] is None
    assert _events((await _stream(api_client, wf, "c")).text)[-1]["type"] == "RUN_FINISHED"


async def test_what_deleted_conversations_spent_still_counts(app, api_client, monkeypatch):
    _priced(monkeypatch)
    wf = await _workflow(api_client, _plain_chain())
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    async with app.state.db.sessionmaker() as s:
        s.add(SpendArchive(workflow_id=wf, hour=hour, runs=3, cost_usd=Decimal("2.5")))
        await s.commit()
    assert (await api_client.put("/v1/org/settings", json={"limits": {"max_cost_usd_day": 2}})).status_code == 200
    r = await _stream(api_client, wf)
    assert r.status_code == 429 and "2.5 of 2 USD" in r.json()["detail"], r.text


async def test_only_admins_set_or_read_the_caps(app, api_client):
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="usr_dev", org_id="org_default", role="builder", memberships={"org_default": "builder"}
    )
    try:
        r = await api_client.put("/v1/projects/prj_default/spending-caps", json={"max_cost_usd_day": 1})
        assert r.status_code == 403, r.text
        assert (await api_client.get("/v1/org/spending/caps")).status_code == 403
        r = await api_client.put("/v1/org/settings", json={"limits": {"max_cost_usd_day": 1}})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)
    r = await api_client.put("/v1/projects/prj_default/spending-caps", json={"max_cost_usd_month": 3})
    assert r.status_code == 200
    async with app.state.db.sessionmaker() as s:
        row = await s.scalar(select(AuditLog).where(AuditLog.action == "put_spending_caps"))
    assert row is not None and row.target == "project_id=prj_default" and row.detail["max_cost_usd_month"] == 3


async def test_no_cap_no_spending_query(app, api_client, monkeypatch):
    """An organisation without caps behaves as before: nothing is read for them."""
    _priced(monkeypatch)
    wf = await _workflow(api_client, _plain_chain())
    from agentcanvas.services import spend_caps

    async def boom(*a: Any, **kw: Any) -> Any:
        raise AssertionError("no caps: nothing to read")

    monkeypatch.setattr(spend_caps, "usage_of", boom)
    assert _events((await _stream(api_client, wf)).text)[-1]["type"] == "RUN_FINISHED"


async def test_runs_on_two_servers_stop_at_the_organisation_cap(settings, monkeypatch):
    _priced(monkeypatch)
    limit = 5.5 * CALL  # two runs of four calls want eight; the cap allows five and a half
    async with instance(settings) as (app, one), instance(settings) as (app_two, two):
        wf = await _workflow(one, _plain_chain())
        assert (await one.put("/v1/org/settings", json={"limits": {"max_cost_usd_month": limit}})).status_code == 200
        app_two.state.spend_caps.forget()  # the other server reads the new cap within seconds; here at once
        runs = await asyncio.gather(*(_stream(api, wf, q) for api, q in ((one, "a"), (two, "b"))))
        ends = [_events(r.text)[-1] for r in runs]
        spent = await _spent(app)
        async with app.state.db.sessionmaker() as s:
            reached = await s.scalar(select(func.count()).select_from(SpendCapAlert).where(SpendCapAlert.share == 1))
    stopped = [e for e in ends if e["type"] == "RUN_ERROR"]
    assert stopped and all(e["code"] == "spending_cap" for e in stopped), ends
    # each run notices the other's spending after its next call: at most one call each beyond the cap
    assert limit <= spent <= limit + 2 * CALL + 1e-9, (spent, limit)
    assert reached == 1  # raised once, whichever server crossed it first


async def test_the_spending_page_lists_every_project(app, api_client):
    await api_client.post("/v1/projects", json={"name": "Second"})
    async with app.state.db.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(Thread)) == 0
    caps = (await api_client.get("/v1/org/spending/caps")).json()
    assert [p["name"] for p in caps["projects"]] == ["Default project", "Second"]
    assert caps["organisation"]["month"] == {
        "period": "month", "period_start": datetime.now(UTC).strftime("%Y-%m"), "limit_usd": None, "spent_usd": 0.0,
    }  # fmt: skip


async def test_an_answer_that_would_continue_a_run_is_refused_and_the_item_stays_open(app, triage_api):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": "Invoice?"}})
    item = (await api.get("/v1/inbox")).json()[0]
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    async with app.state.db.sessionmaker() as s:
        s.add(SpendArchive(workflow_id=wf, hour=hour, runs=1, cost_usd=Decimal("1")))
        await s.commit()
    assert (await api.put("/v1/projects/prj_default/spending-caps", json={"max_cost_usd_month": 1})).status_code == 200
    r = await api.post(f"/v1/inbox/{item['id']}/decision", json={"value": {"decisions": [{"type": "approve"}]}})
    assert r.status_code == 429 and "month spending cap" in r.json()["detail"], r.text
    assert [i["id"] for i in (await api.get("/v1/inbox")).json()] == [item["id"]]  # still waiting for an answer


async def test_copilot_answers_count_against_the_caps_and_are_refused_at_one(app, api_client):
    from langchain_core.messages import AIMessage
    from test_copilot import ask, new_workflow, script

    from agentcanvas.db.models import ModelSpend

    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]  # its model, claude-haiku, has a price
    spend = {"input_tokens": 100_000, "output_tokens": 20_000, "total_tokens": 120_000}
    script(app, AIMessage(content="Hi.", usage_metadata=spend))  # type: ignore[arg-type]
    done = (await ask(api_client, wf, "hi", mode="ask"))[-1]
    cost = done["cost_usd"]
    assert cost > 0
    async with app.state.db.sessionmaker() as s:
        [row] = (await s.scalars(select(ModelSpend))).all()
    assert (row.project_id, row.kind, row.calls, row.tokens_in) == ("prj_default", "copilot", 1, 100_000)
    assert float(row.cost_usd) == pytest.approx(cost, abs=1e-6)
    report = (await api_client.get("/v1/org/spending")).json()
    assert report["outside_runs"] == [
        {"kind": "copilot", "calls": 1, "tokens_in": 100_000, "tokens_out": 20_000, "cost_usd": pytest.approx(cost)}
    ]
    assert report["total"]["cost_usd"] == 0  # runs only
    caps = (await api_client.get("/v1/org/spending/caps")).json()
    assert caps["organisation"]["month"]["spent_usd"] == pytest.approx(cost, abs=1e-6)

    r = await api_client.put("/v1/org/settings", json={"limits": {"max_cost_usd_day": cost / 2}})
    assert r.status_code == 200
    script(app, "Hello again.")
    r = await api_client.post(f"/v1/workflows/{wf}/copilot/messages", json={"text": "hi", "mode": "ask"})
    assert r.status_code == 429 and "day spending cap" in r.json()["detail"], r.text
    code = {"action": "explain", "path": "a.py", "content": "x = 1"}
    r = await api_client.post(f"/v1/workflows/{wf}/copilot/code", json=code)
    assert r.status_code == 429, r.text
    # and the editor's runs too: the Copilot's spending reached the cap
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {}})
    assert r.status_code == 429, r.text


async def test_ai_judges_count_against_the_caps(app, api_client, monkeypatch):
    from agentcanvas.db.models import ModelSpend
    from agentcanvas.ir.models import Workflow
    from agentcanvas.runtime.runner import add_call
    from agentcanvas.services.experiments import scorers

    info = default_registry().model("openai:gpt-5-mini")
    monkeypatch.setattr(info, "price_in_per_mtok", 1000.0)
    monkeypatch.setattr(info, "price_out_per_mtok", 1000.0)

    async def complete(resources: Any, model: str, system: str, prompt: str, usage: Any = None) -> str:
        add_call(usage, model, {"input_tokens": 10, "output_tokens": 5})
        return "PASS"

    monkeypatch.setattr(app.state.runner.models, "complete", complete)
    wf_id = await _workflow(api_client, _plain_chain())
    wf = Workflow.model_validate((await api_client.get(f"/v1/workflows/{wf_id}")).json()["ir"])
    seen: dict[str, Any] = {}
    judge = scorers(app.state.runner, wf, seen)["complete"]
    for _ in range(2):
        assert await judge("grade", "it", "openai:gpt-5-mini") == "PASS"
    assert seen["openai:gpt-5-mini"]["input_tokens"] == 20  # the caller still sees what its judge used
    async with app.state.db.sessionmaker() as s:
        [row] = (await s.scalars(select(ModelSpend))).all()
    assert (row.kind, row.calls, float(row.cost_usd)) == ("judge", 2, pytest.approx(2 * CALL))
    assert (await api_client.put("/v1/org/settings", json={"limits": {"max_cost_usd_day": CALL}})).status_code == 200
    assert (await _stream(api_client, wf_id)).status_code == 429
