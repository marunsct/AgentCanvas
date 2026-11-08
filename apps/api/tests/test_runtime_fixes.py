"""Runtime review fixes: graph caching, hosted addresses, inbox items, usage, sandbox output and more."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentcanvas.adapters.langchain import nodes
from agentcanvas.adapters.langchain.adapter import LangChainAdapter
from agentcanvas.ir.models import Workflow


def llm_wf(model: str = "openai:gpt-5.5", **config: Any) -> Workflow:
    return Workflow.model_validate({
        "id": "wf_rt", "name": "rt", "framework": "langchain",
        "state": {"channels": [{"name": "who", "io": "in"}, {"name": "greeting", "io": "out"}]},
        "resources": [{"id": "r_m", "type": "model.chat", "name": "model", "config": {"model": model, **config}}],
        "nodes": [{"id": "n_g", "type": "core.llm", "name": "greet",
                   "config": {"prompt": "Greet {{ state.who }}", "output": "greeting"}}],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_g"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_g"}, "target": {"node": "END"}},
            {"id": "w1", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n_g", "port": "model"}},
        ],
    })  # fmt: skip


# ------------------------------------------------------------------ RT-1 ----
async def test_a_rotated_secret_rebuilds_the_cached_graph(monkeypatch):
    """RT-1: the graph cache ignored secret values, so a rotated key never reached the cached graph's clients."""
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    built: list[str | None] = []

    def init(model_id, **kw):
        built.append(kw.get("api_key"))
        return FakeListChatModel(responses=["ok"])

    monkeypatch.setattr(nodes, "init_chat_model", init)
    adapter = LangChainAdapter()
    secrets = {"OPENAI_API_KEY": "sk-old-123456"}
    res = adapter.new_resources(secrets=secrets)
    wf = llm_wf()
    async with adapter.persistence(None):
        first = await adapter.graph(wf, res)
        assert await adapter.graph(wf, res) is first  # cached while nothing changed
        secrets["OPENAI_API_KEY"] = "sk-new-654321"  # SecretCache._set updates the dict in place
        assert await adapter.graph(wf, res) is not first
    assert built[-1] == "sk-new-654321"


# ------------------------------------------------------------------ RT-2 ----
def _hosted() -> nodes.Resources:
    res = nodes.Resources(secrets={"ANTHROPIC_API_KEY": "a" * 8, "OPENAI_API_KEY": "o" * 8})
    res.public_only = True
    return res


@pytest.mark.parametrize(
    "config",
    [
        {"model": "anthropic:claude-haiku-4-5", "base_url": "http://169.254.169.254/",
         "api_key": {"$secret": "ANTHROPIC_API_KEY"}},
        {"model": "anthropic:claude-haiku-4-5", "anthropic_api_url": "http://10.0.0.1/"},
        {"model": "ollama:llama3.3"},  # its default address is the server's own localhost
        {"model": "openai:gpt-5.5", "fallbacks": ["ollama:llama3.3"]},
    ],
)  # fmt: skip
def test_the_hosted_edition_refuses_model_addresses_it_cannot_pin(config):
    """RT-2: only `openai:` models with a base URL got the public-address client; other providers, backups and
    local defaults could reach private addresses."""
    with pytest.raises(ValueError, match="hosted edition"):
        _hosted().model("m", config)


def _pinned(client: Any) -> bool:
    return type(client._transport._pool._network_backend).__name__ == "PublicOnly"


def test_the_hosted_edition_pins_openai_clients_whatever_key_names_the_address():
    model: Any = _hosted().model("m", {"model": "openai:gpt-5.5", "openai_api_base": "http://10.0.0.1/v1"})
    assert _pinned(model.http_async_client)


def test_the_hosted_edition_checks_knowledge_embedders(monkeypatch):
    import langchain.embeddings as emb

    with pytest.raises(ValueError, match="hosted edition"):
        nodes.knowledge_embedder({"model": "ollama:nomic-embed-text"}, {}, public_only=True)
    seen: dict[str, Any] = {}

    def init(model_id, **kw):
        seen.update(kw)
        return object()

    monkeypatch.setattr(emb, "init_embeddings", init)
    nodes.knowledge_embedder({"model": "ollama:nomic-embed-text"}, {})  # the local edition: as before
    assert "http_async_client" not in seen
    nodes.knowledge_embedder({"model": "lmstudio:e5", "base_url": "http://10.0.0.1/v1"}, {}, public_only=True)
    assert _pinned(seen["http_async_client"])


# ------------------------------------------------------------------ RT-7 ----
def test_a_mocked_run_keeps_the_servers_knowledge_files_and_answers():
    """RT-7: `with_mocks` built a fresh Resources, so a mock-mode run lost the server's knowledge bases, files and
    answer cache (a knowledge search fell back to files on disk)."""

    async def knowledge(*a: Any, **k: Any) -> list[dict[str, Any]]:
        return []

    res = nodes.Resources(overrides={"tool": object()}, secrets={"K": "v" * 8})
    res.knowledge, res.files, res.public_only = knowledge, object(), True  # type: ignore[assignment]
    clone = res.with_mocks({"model": ["hi"]})
    assert clone.knowledge is res.knowledge and clone.files is res.files and clone.answers is res.answers
    assert clone.public_only and clone._kb_files is res._kb_files and clone._secrets is res._secrets
    assert "model" in clone._overrides and "model" not in res._overrides  # the original is untouched


# ------------------------------------------------------------------ RT-8 ----
async def test_the_http_tool_reads_only_what_the_model_may_see():
    """RT-8: the HTTP tool read the whole answer into memory and then kept `max_response_chars` of it."""
    import httpx

    sent = 0

    async def endless():
        nonlocal sent
        for _ in range(10_000):  # 640 MB if read whole
            sent += 1
            yield "é".encode() * 32_768

    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=endless()))
    config = {"url": "https://api.example.com/x", "allowed_domains": ["example.com"], "max_response_chars": 1000}
    tool = nodes.http_tool(name="big", config=config, transport=transport)
    out = await tool.ainvoke({})
    assert out == "é" * 1000 and sent < 5


class SpendsThen:
    """A graph whose model call reports usage, then fails or hangs before the next update."""

    def __init__(self, then: str):
        self.then = then

    async def astream(self, graph_input: Any, cfg: Any, **kw: Any):
        from uuid import uuid4

        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, LLMResult

        usage, run_id = cfg["callbacks"][0], uuid4()
        usage.on_chat_model_start({}, [], run_id=run_id, metadata={"ac_model": "openai:gpt-5.5"})
        reply = AIMessage("x", usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150})
        usage.on_llm_end(LLMResult(generations=[[ChatGeneration(message=reply)]]), run_id=run_id)
        if self.then == "raise":
            raise RuntimeError("the provider went away")
        await asyncio.sleep(30)
        yield {}


def _spec(**kw: Any) -> Any:
    from agentcanvas.runtime.types import RunSpec

    return RunSpec(workflow=llm_wf(), thread_id="th_u", run_id="run_u", input={"who": "Ada"}, **kw)


async def test_usage_before_a_failure_is_reported(monkeypatch):
    """RT-4: the usage tracker was drained only after a clean update, so a failed run lost its tokens and cost."""
    adapter = LangChainAdapter()

    async def graph(wf, resources):
        return SpendsThen("raise")

    monkeypatch.setattr(adapter, "graph", graph)
    evs = [ev async for ev in adapter.execute(_spec(), adapter.default_resources())]
    assert [e.get("name") or e["type"] for e in evs] == ["ac.usage", "RUN_ERROR"]
    assert evs[0]["value"]["input_tokens"] == 120


async def test_usage_of_a_cancelled_run_is_recorded(app, api_client, monkeypatch):
    """RT-4: a cancelled (or timed out) run's last model call never reached the run's tokens and cost."""
    from test_publish import make_greeter

    real = app.state.runner.adapters["langchain"]

    async def graph(wf, resources):
        return SpendsThen("hang")

    monkeypatch.setattr(real, "graph", graph)
    wf = await make_greeter(api_client)
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "Ada"}, "stream": False})
    run_id = r.json()["run_id"]
    await asyncio.sleep(0.1)
    assert (await api_client.post(f"/v1/runs/{run_id}/cancel")).json() == {"cancelled": True}
    await app.state.runner.wait(run_id)
    run = (await api_client.get(f"/v1/runs/{run_id}")).json()
    assert run["status"] == "cancelled" and run["tokens_in"] == 120 and run["tokens_out"] == 30
    assert run["cost_usd"] > 0


# ------------------------------------------------------------------ RT-12 ----
async def test_a_run_saved_the_bare_way_keeps_its_usage(app, api_client, monkeypatch):
    """RT-12: when saving a run in full failed, the fallback kept its status but dropped its tokens and cost, so
    budgets and spending reports missed what it spent."""
    from test_publish import make_greeter

    from agentcanvas.runtime import runner as runner_module

    def broken(*a: Any, **k: Any) -> Any:
        raise ValueError("the events cannot be stored")

    monkeypatch.setattr(runner_module, "compact", broken)  # the full save fails
    real = app.state.runner.adapters["langchain"]

    async def graph(wf, resources):
        return SpendsThen("raise")

    monkeypatch.setattr(real, "graph", graph)
    wf = await make_greeter(api_client)
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "Ada"}, "stream": False})
    await app.state.runner.wait(r.json()["run_id"])
    run = (await api_client.get(f"/v1/runs/{r.json()['run_id']}")).json()
    assert run["status"] == "error" and run["tokens_in"] == 120 and run["tokens_out"] == 30 and run["cost_usd"] > 0
