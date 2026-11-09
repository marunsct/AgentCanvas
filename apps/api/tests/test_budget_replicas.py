"""G-8: day and month budgets hold across API instances. Runs going at once on two instances each saved their cost
only when they finished, so each saw only its own spending and together they passed the limit by whole runs."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import func, select
from test_durable_state import _events, _workflow, instance
from test_gateway import Counting

from agentcanvas.adapters.langchain import nodes
from agentcanvas.db.models import Run
from agentcanvas.registry import default_registry

CALLS = 4  # model calls in one run


class Slow(Counting):
    """A provider that takes a moment, so runs on two instances really go at the same time."""

    async def _agenerate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        await asyncio.sleep(0.1)
        self.calls += 1
        reply = AIMessage(content="ok", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        return ChatResult(generations=[ChatGeneration(message=reply)])


def _chain(daily: float) -> dict[str, Any]:
    names = [f"n{i}" for i in range(CALLS)]
    flow = [("START", names[0]), *zip(names, [*names[1:], "END"], strict=True)]
    return {
        "name": "Chain", "state": {"channels": [{"name": "q"}, {"name": "out"}]},
        "resources": [{"id": "r_m", "type": "model.chat", "name": "m", "config": {"model": "openai:gpt-5-mini"}}],
        "nodes": [{"id": n, "type": "core.llm", "name": n, "config": {"prompt": f"{n}: {{{{ state.q }}}}",
                                                                      "output": "out"}} for n in names],
        "edges": [{"id": f"e{i}", "kind": "flow", "source": {"node": a}, "target": {"node": b}}
                  for i, (a, b) in enumerate(flow)]
        + [{"id": f"w{i}", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": n, "port": "model"}}
           for i, n in enumerate(names)],
        "settings": {"budget": {"daily_max_cost_usd": daily}},
    }  # fmt: skip


async def test_concurrent_runs_on_two_instances_stop_at_the_day_budget(settings, monkeypatch):
    info = default_registry().model("openai:gpt-5-mini")
    monkeypatch.setattr(info, "price_in_per_mtok", 1000.0)
    monkeypatch.setattr(info, "price_out_per_mtok", 1000.0)
    call = (10 * 1000.0 + 5 * 1000.0) / 1_000_000  # USD per model call
    monkeypatch.setattr(nodes, "init_chat_model", lambda *a, **kw: Slow())
    limit = 5.5 * call  # two runs of four calls want 8 calls; the day allows five and a half
    async with instance(settings) as (app, one), instance(settings) as (_, two):
        wf = await _workflow(one, _chain(limit))
        runs = await asyncio.gather(
            *(
                api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"q": q}})
                for api, q in ((one, "a"), (two, "b"))
            )
        )
        ends = [_events(r.text)[-1] for r in runs]
        async with app.state.db.sessionmaker() as s:
            spent = float(await s.scalar(select(func.sum(Run.cost_usd))) or 0)
    stopped = [e for e in ends if e["type"] == "RUN_ERROR"]
    assert stopped and all(e["code"] == "budget_exceeded" and "this day" in e["message"] for e in stopped), ends
    # a run notices the others' spending after its next call: at most one call each beyond the limit, never a run
    assert limit <= spent <= limit + 2 * call + 1e-9, (spent, limit)
    assert spent < 8 * call - 1e-9  # unchecked, the two runs would have spent all eight calls


@pytest.mark.parametrize("period", ["daily_max_cost_usd", "monthly_max_cost_usd"])
async def test_a_run_going_on_another_instance_counts_when_a_run_starts(settings, monkeypatch, period):
    info = default_registry().model("openai:gpt-5-mini")
    monkeypatch.setattr(info, "price_in_per_mtok", 1000.0)
    monkeypatch.setattr(info, "price_out_per_mtok", 1000.0)
    monkeypatch.setattr(nodes, "init_chat_model", lambda *a, **kw: Slow())
    call = (10 * 1000.0 + 5 * 1000.0) / 1_000_000
    ir = _chain(1.0)
    ir["settings"] = {"budget": {period: 2.5 * call}}
    async with instance(settings) as (app, one), instance(settings) as (_, two):
        wf = await _workflow(one, ir)
        first = asyncio.create_task(one.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"q": "a"}}))
        for _ in range(100):  # until the first run has spent three calls' worth and says so in the database
            await asyncio.sleep(0.02)
            async with app.state.db.sessionmaker() as s:
                if float(await s.scalar(select(func.sum(Run.cost_usd))) or 0) >= 2 * call - 1e-9:
                    break
        assert not first.done(), "the first run was still going when the second started"
        blocked = _events((await two.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"q": "b"}})).text)[-1]
        await first
    assert blocked["type"] == "RUN_ERROR" and blocked["code"] == "budget_exceeded", blocked
    assert blocked["usage"]["cost_usd"] <= call + 1e-9, blocked  # alone it would have made three calls first
