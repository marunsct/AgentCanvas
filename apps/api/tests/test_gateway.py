"""The model gateway and cost controls (S26): backup models, the answer cache, day and month budgets, cost alerts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from agentcanvas.adapters.langchain import nodes
from agentcanvas.adapters.langchain.events import UsageTracker
from agentcanvas.adapters.langchain.gateway import AnswerCache, GatewayModel
from agentcanvas.adapters.langchain.nodes import Resources, tag_model
from agentcanvas.ir.models import Budget, Workflow
from agentcanvas.registry import default_registry
from agentcanvas.runtime.runner import BudgetExceeded, Spent, check_budget, periods
from agentcanvas.testing import DemoChatModel, scripted
from agentcanvas.validation import validate

P = "/v1/projects/prj_default/workflows"
DOWN = "http://127.0.0.1:9/v1"  # nothing listens here: a provider that is down


class Down(BaseChatModel):
    """A provider that is down."""

    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "down"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Down:  # type: ignore[override]
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        self.calls += 1
        raise ConnectionError("provider unavailable")


class Counting(BaseChatModel):
    """Answers with the number of calls it has had; reports usage like a provider."""

    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "counting"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Counting:  # type: ignore[override]
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        self.calls += 1
        reply = AIMessage(content=f"answer {self.calls}",
                          usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})  # fmt: skip
        return ChatResult(generations=[ChatGeneration(message=reply)])


def gateway(*models: BaseChatModel, ttl: float | None = None, cache: AnswerCache | None = None) -> GatewayModel:
    names = [f"test:m{i}" for i in range(len(models))]
    tagged = [tag_model(m, n) for m, n in zip(models, names, strict=True)]
    return GatewayModel(models=tagged, names=names, cache_ttl=ttl, answers=cache or AnswerCache())


# ------------------------------------------------------------------ backups ----
async def test_a_failing_model_falls_over_to_its_backup():
    down, backup = Down(), Counting()
    reply = await gateway(down, backup).ainvoke("hi")
    assert reply.content == "answer 1" and down.calls == 1
    note = reply.response_metadata["ac_gateway"]
    assert note["model"] == "test:m1" and not note["cached"]
    assert note["failed"] == [{"model": "test:m0", "error": "ConnectionError: provider unavailable"}]


async def test_when_every_model_fails_the_last_error_names_them_all():
    with pytest.raises(ConnectionError) as err:
        await gateway(Down(), Down()).ainvoke("hi")
    assert "models tried: test:m0" in "".join(err.value.__notes__)


def test_sync_calls_fall_over_too():
    assert gateway(Down(), Counting()).invoke("hi").content == "answer 1"


async def test_backups_get_the_tools_and_answer_with_tool_calls():
    @tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    reply = await gateway(Down(), DemoChatModel()).bind_tools([add]).ainvoke("add 2 and 3")
    assert reply.tool_calls and reply.tool_calls[0]["name"] == "add"


async def test_structured_output_works_through_the_gateway():
    schema = {"title": "Pick", "type": "object", "properties": {"label": {"type": "string"}}, "required": ["label"]}
    out = await gateway(Down(), DemoChatModel()).with_structured_output(schema).ainvoke("classify this")
    assert set(out) == {"label"}


async def test_usage_is_counted_once_for_the_model_that_answered():
    usage = UsageTracker({})
    await gateway(Down(), Counting()).ainvoke("hi", config={"callbacks": [usage]})
    records = usage.drain()
    spent = [r for r in records if r["input_tokens"]]
    assert spent == [{"canvas_node_id": None, "model": "test:m1", "input_tokens": 10, "output_tokens": 5}]
    notes = [r for r in records if not r["input_tokens"]]
    assert notes[0]["failed"][0]["model"] == "test:m0" and notes[0]["cached"] is False


async def test_a_stream_falls_over_before_its_first_token():
    chunks = [c async for c in gateway(Down(), Counting()).astream("hi")]
    assert "".join(str(c.content) for c in chunks) == "answer 1"


# ------------------------------------------------------------- answer cache ----
async def test_an_identical_request_is_answered_from_the_cache():
    model = Counting()
    gw = gateway(model, ttl=60)
    usage = UsageTracker({})
    first = await gw.ainvoke([HumanMessage("hi", id="a")], config={"callbacks": [usage]})
    second = await gw.ainvoke([HumanMessage("hi", id="b")], config={"callbacks": [usage]})  # ids do not matter
    assert first.content == second.content == "answer 1" and model.calls == 1
    assert second.response_metadata["ac_gateway"]["cached"] and second.usage_metadata is None
    records = usage.drain()
    assert sum(r["input_tokens"] for r in records) == 10  # the cached answer cost nothing
    assert [r.get("cached") for r in records if "cached" in r] == [True]
    third = await gw.ainvoke("something else")
    assert third.content == "answer 2"


async def test_cached_answers_expire_and_tools_change_the_request(monkeypatch):
    @tool
    def lookup(q: str) -> str:
        """Look something up."""
        return q

    model = Counting()
    gw = gateway(model, ttl=10)
    await gw.ainvoke("hi")
    await gw.bind_tools([lookup]).ainvoke("hi")  # other tools: another request
    assert model.calls == 2
    clock = __import__("time").monotonic() + 11
    monkeypatch.setattr("agentcanvas.adapters.langchain.gateway.time.monotonic", lambda: clock)
    await gw.ainvoke("hi")
    assert model.calls == 3


def test_the_cache_keeps_the_newest_answers():
    cache = AnswerCache(size=2)
    for i in range(3):
        cache.put(str(i), AIMessage(content=str(i)))
    assert len(cache) == 2 and cache.get("0", 60) is None and cache.get("2", 60).content == "2"


async def test_a_streamed_answer_is_cached():
    model = Counting()
    gw = gateway(model, ttl=60)
    [c async for c in gw.astream("hi")]
    chunks = [c async for c in gw.astream("hi")]
    assert model.calls == 1 and "".join(str(c.content) for c in chunks) == "answer 1"


# ------------------------------------------------------------ model blocks ----
def test_a_model_block_without_backups_or_cache_is_the_plain_model():
    model = Resources().model("m", {"model": "openai:gpt-5-mini", "api_key": "k"})
    assert not isinstance(model, GatewayModel) and model.metadata["ac_resource"] == "m"


def test_backups_share_the_block_settings_but_not_its_key_or_address():
    res = Resources(secrets={"ANTHROPIC_API_KEY": "anthropic-key"})
    config = {"model": "openai:gpt-5-mini", "api_key": "openai-key", "base_url": DOWN, "temperature": 0.2,
              "max_tokens": 50, "fallbacks": ["anthropic:claude-haiku-4-5"], "cache_ttl": 30}  # fmt: skip
    model = res.model("m", config)
    assert isinstance(model, GatewayModel) and model.names == ["openai:gpt-5-mini", "anthropic:claude-haiku-4-5"]
    assert model.cache_ttl == 30 and model.answers is res.answers
    assert model.metadata["ac_resource"] == "m" and "ac_resource" not in (model.models[0].metadata or {})
    backup = model.models[1]
    assert backup.temperature is None and backup.max_tokens == 50  # shared, less what Claude refuses (W020)
    assert backup.anthropic_api_key.get_secret_value() == "anthropic-key"
    assert "127.0.0.1" not in str(getattr(backup, "anthropic_api_url", ""))
    assert res.model("m", config) is model  # built once per config


def test_validation_warns_about_repeated_backups_and_unpriced_ones():
    wf = Workflow.model_validate(
        {
            "id": "wf_g", "name": "g", "settings": {"budget": {"daily_max_cost_usd": 1}},
            "state": {"channels": [{"name": "out"}]},
            "resources": [{"id": "r_m", "type": "model.chat", "name": "m",
                           "config": {"model": "openai:gpt-5-mini", "fallbacks": ["openai:gpt-5-mini", "x:unknown"]}}],
            "nodes": [{"id": "n", "type": "core.llm", "name": "ask", "config": {"prompt": "hi", "output": "out"}}],
            "edges": [
                {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n"}},
                {"id": "e2", "kind": "flow", "source": {"node": "n"}, "target": {"node": "END"}},
                {"id": "w", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n", "port": "model"}},
            ],
        }
    )  # fmt: skip
    diags = validate(wf, default_registry())
    assert [d.path for d in diags if d.code == "W016"] == ["config.fallbacks[0]"]
    assert any(d.code == "W015" and "x:unknown" in d.message for d in diags)


# ------------------------------------------------------------------ budgets ----
def test_day_and_month_budgets_stop_runs():
    budget = Budget(daily_max_cost_usd=1.0, monthly_max_cost_usd=10.0)
    usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.5}
    check_budget(budget, usage, Spent(day_cost=0.4, month_cost=5))
    with pytest.raises(BudgetExceeded, match="this day"):
        check_budget(budget, usage, Spent(day_cost=0.6))
    with pytest.raises(BudgetExceeded, match="this month"):
        check_budget(budget, {**usage, "cost_usd": 0}, Spent(month_cost=10))  # a spent month does not start runs
    assert Budget(alert_at=[0.9, 0.5]).alerts() == [0.5, 0.9, 1.0] and Budget().alerts() == [0.8, 1.0]
    with pytest.raises(ValueError):
        Budget(alert_at=[80])


def test_periods_are_utc_days_and_months():
    p = periods(datetime(2026, 10, 10, 23, 59, tzinfo=UTC))
    assert p["day"][0] == "2026-10-10" and p["month"][0] == "2026-10" and p["month"][1].day == 1


def _llm_ir(model_config: dict, budget: dict | None = None, *, framework: str = "langchain") -> dict:
    ir = {
        "name": "g", "framework": framework, "state": {"channels": [{"name": "q"}, {"name": "out"}]},
        "resources": [{"id": "r_m", "type": "model.chat", "name": "m", "config": model_config}],
        "nodes": [{"id": "n_ask", "type": "core.llm", "name": "ask",
                   "config": {"prompt": "{{ state.q }}", "output": "out"}}],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_ask"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_ask"}, "target": {"node": "END"}},
            {"id": "w", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n_ask", "port": "model"}},
        ],
    }  # fmt: skip
    if budget:
        ir["settings"] = {"budget": budget}
    return ir


async def _make(api, ir: dict) -> str:
    wf = (await api.post(P, json={"name": "g"})).json()
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": {**ir, "id": wf["id"]}}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    return wf["id"]


def _events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


async def _run(api, wf: str, q: str = "hi") -> list[dict]:
    return _events((await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"q": q}})).text)


@pytest.fixture
def demo_backups(monkeypatch):
    """Anthropic models answer as the demo model; everything else is real (and the primary's address is down)."""
    real = nodes.init_chat_model

    def init(model: str, **kwargs: Any) -> Any:
        return DemoChatModel() if model.startswith("anthropic:") else real(model, **kwargs)

    monkeypatch.setattr(nodes, "init_chat_model", init)


@pytest.mark.parametrize("framework", ["langchain", "openai_agents"])
async def test_a_provider_outage_fails_over(app, api_client, demo_backups, framework):
    """S26 exit: a provider outage fails over (on the canvas's own framework and on the SDK)."""
    res = Resources(secrets={"OPENAI_API_KEY": "k"})
    app.state.resources_factory = lambda thread_id, wf: res
    config = {"model": "openai:gpt-5-mini", "base_url": DOWN, "api_key": {"$secret": "OPENAI_API_KEY"}, "timeout": 5,
              "fallbacks": ["anthropic:claude-haiku-4-5"]}  # fmt: skip
    wf = await _make(api_client, _llm_ir(config, framework=framework))
    evs = await _run(api_client, wf)
    fin = evs[-1]
    assert fin["type"] == "RUN_FINISHED", fin
    assert fin["output"]["out"].startswith("Demo reply") and fin["usage"]["fallbacks"] == 1
    notes = [e["value"] for e in evs if e.get("name") == "ac.usage" and e["value"].get("failed")]
    assert notes[0]["model"] == "anthropic:claude-haiku-4-5" and notes[0]["failed"][0]["model"] == "openai:gpt-5-mini"
    spend = (await api_client.get(f"/v1/workflows/{wf}/spend")).json()
    assert spend["day"]["fallbacks"] == 1 and spend["day"]["runs"] == 1


async def test_repeated_runs_reuse_cached_answers_at_no_cost(app, api_client, monkeypatch):
    from agentcanvas.testing import DemoResources

    demo = DemoResources()
    app.state.resources_factory = lambda thread_id, wf: demo
    info = default_registry().model("openai:gpt-5-mini")
    monkeypatch.setattr(info, "price_in_per_mtok", 1.0)
    monkeypatch.setattr(info, "price_out_per_mtok", 4.0)
    wf = await _make(api_client, _llm_ir({"model": "openai:gpt-5-mini", "cache_ttl": 600}))
    first, second = (await _run(api_client, wf))[-1], (await _run(api_client, wf))[-1]
    assert first["output"] == second["output"]
    assert first["usage"]["cost_usd"] > 0 and first["usage"]["cache_hits"] == 0
    assert second["usage"]["cost_usd"] == 0 and second["usage"]["cache_hits"] == 1
    assert second["usage"]["input_tokens"] == 0
    spend = (await api_client.get(f"/v1/workflows/{wf}/spend")).json()
    assert spend["month"]["cache_hits"] == 1 and spend["month"]["runs"] == 2
    assert spend["day"]["spent_usd"] == pytest.approx(first["usage"]["cost_usd"])


async def test_a_day_budget_stops_runs_and_raises_alerts_once(app, api_client, monkeypatch):
    """S26 exit: a budget stops a run (here the workflow's day budget, with its cost alerts)."""
    from agentcanvas.testing import DemoResources

    demo = DemoResources()
    app.state.resources_factory = lambda thread_id, wf: demo
    info = default_registry().model("openai:gpt-5-mini")
    monkeypatch.setattr(info, "price_in_per_mtok", 1000.0)  # the demo's few tokens cost a few cents per call
    monkeypatch.setattr(info, "price_out_per_mtok", 1000.0)
    wf = await _make(api_client, _llm_ir({"model": "openai:gpt-5-mini"}))
    cost = (await _run(api_client, wf, "a"))[-1]["usage"]["cost_usd"]
    ir = (await api_client.get(f"/v1/workflows/{wf}")).json()
    budget = {"daily_max_cost_usd": round(cost * 2.5, 6), "alert_at": [0.5]}
    r = await api_client.put(f"/v1/workflows/{wf}", json={"ir": {**ir["ir"], "settings": {"budget": budget}}},
                             headers={"If-Match": str(ir["version"])})  # fmt: skip
    assert r.status_code == 200, r.text
    runs = [await _run(api_client, wf, q) for q in ("a", "b", "c")]
    assert runs[0][-1]["type"] == "RUN_FINISHED"  # 2 calls spent: 80%
    stopped = [evs[-1] for evs in runs if evs[-1]["type"] == "RUN_ERROR"]
    assert stopped and all(e["code"] == "budget_exceeded" and "this day" in e["message"] for e in stopped)
    raised = [e["value"] for evs in runs for e in evs if e.get("name") == "ac.cost_alert"]
    assert [(a["period"], a["share"]) for a in raised] == [("day", 0.5), ("day", 1.0)]
    assert "reached its day budget" in raised[-1]["message"]
    spend = (await api_client.get(f"/v1/workflows/{wf}/spend")).json()
    assert spend["day"]["limit_usd"] == budget["daily_max_cost_usd"] and spend["month"]["limit_usd"] is None
    assert sorted(a["share"] for a in spend["alerts"]) == [0.5, 1.0]  # once each, however many runs crossed them
    blocked = (await _run(api_client, wf, "d"))[-1]
    assert blocked["code"] == "budget_exceeded" and not any(
        e["type"] == "STEP_STARTED" for e in await _run(api_client, wf, "e")
    )


async def test_spend_of_another_organisation_is_not_found(api_client):
    assert (await api_client.get("/v1/workflows/wf_nope/spend")).status_code == 404


# ------------------------------------------------------------------ exports ----
async def test_sdk_exports_fall_over_to_backup_models():
    from agents import ModelSettings, ModelTracing

    from agentcanvas.adapters.openai_agents.runtime import FallbackModel

    class Fake:
        def __init__(self, fail: bool):
            self.fail, self.calls = fail, 0

        async def get_response(self, *args: Any, **kwargs: Any) -> str:
            self.calls += 1
            if self.fail:
                raise ConnectionError("down")
            return "ok"

        async def stream_response(self, *args: Any, **kwargs: Any) -> Any:
            if self.fail:
                raise ConnectionError("down")
            yield "event"

    down, up = Fake(True), Fake(False)
    model = FallbackModel([down, up])
    args = (None, "hi", ModelSettings(), [], None, [], ModelTracing.DISABLED)
    assert await model.get_response(*args, previous_response_id=None, conversation_id=None, prompt=None) == "ok"
    assert [e async for e in model.stream_response(*args, previous_response_id=None, conversation_id=None,
                                                   prompt=None)] == ["event"]  # fmt: skip
    with pytest.raises(ConnectionError):
        await FallbackModel([Fake(True)]).get_response(*args, previous_response_id=None, conversation_id=None,
                                                       prompt=None)  # fmt: skip


def test_exports_carry_the_gateway():
    import io
    import zipfile

    from agentcanvas.adapters import create_adapter

    config = {"model": "openai:gpt-5-mini", "fallbacks": ["anthropic:claude-haiku-4-5"]}
    lc = Workflow.model_validate({**_llm_ir(config), "id": "wf_lc"})
    _, data = create_adapter("langchain").package(lc)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert any(n.endswith("src/agentcanvas/adapters/langchain/gateway.py") for n in names)
    sdk = Workflow.model_validate({**_llm_ir(config, framework="openai_agents"), "id": "wf_sdk"})
    _, data = create_adapter("openai_agents").package(sdk)
    files = zipfile.ZipFile(io.BytesIO(data))
    host = next(files.read(n).decode() for n in files.namelist() if n.endswith("/host.py"))
    assert "FallbackModel" in host and '"model": "claude-haiku-4-5"' in host
    env = next(files.read(n).decode() for n in files.namelist() if n.endswith(".env.example"))
    assert "ANTHROPIC_API_KEY" in env


def test_scripted_overrides_skip_the_gateway():
    res = Resources(overrides={"m": scripted("hi")})
    assert not isinstance(res.model("m", {"model": "openai:gpt-5-mini", "fallbacks": ["x:y"]}), GatewayModel)


def test_a_rotated_key_builds_a_new_model_client(monkeypatch):
    """R-3: model clients were cached by the secret's name, so after rotating a leaked key runs kept using the old
    value until 64 other configs evicted it or the server restarted."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    from agentcanvas.adapters.langchain.nodes import Resources

    built: list[str | None] = []

    def init(model_id, **kw):
        built.append(kw.get("api_key"))
        return FakeListChatModel(responses=["ok"])

    monkeypatch.setattr(nodes, "init_chat_model", init)
    secrets = {"OPENAI_API_KEY": "sk-old-123456"}
    res = Resources(secrets=secrets)
    res.model("m", {"model": "openai:gpt-5.5"})
    res.model("m", {"model": "openai:gpt-5.5"})
    assert built == ["sk-old-123456"]  # reused while nothing changed
    secrets["OPENAI_API_KEY"] = "sk-new-654321"  # the secrets cache updates this dict in place
    res.model("m", {"model": "openai:gpt-5.5"})
    assert built[-1] == "sk-new-654321"
