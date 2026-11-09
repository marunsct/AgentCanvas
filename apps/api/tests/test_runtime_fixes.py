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


# ------------------------------------------------------------------ RT-13 ----
class BackupFailed:
    """A graph whose model gateway reports a failed backup whose error echoes the key."""

    async def astream(self, graph_input: Any, cfg: Any, **kw: Any):
        from uuid import uuid4

        from langchain_core.messages import AIMessage
        from langchain_core.outputs import ChatGeneration, LLMResult

        usage, run_id = cfg["callbacks"][0], uuid4()
        usage.on_chat_model_start({}, [], run_id=run_id, metadata={"ac_gateway": True, "ac_model": "openai:gpt-5.5"})
        note = {"model": "anthropic:claude-haiku-4-5",
                "failed": [{"model": "openai:gpt-5.5", "error": "401: key sk-live-secret-42 refused"}]}  # fmt: skip
        reply = AIMessage("x", response_metadata={"ac_gateway": note})
        usage.on_llm_end(LLMResult(generations=[[ChatGeneration(message=reply)]]), run_id=run_id)
        raise RuntimeError("stop")
        yield {}


async def test_usage_events_are_masked_like_every_other_event(monkeypatch):
    """RT-13: `ac.usage` events were the one kind yielded without masking, and a failed backup's error text in them
    can echo a key."""
    adapter = LangChainAdapter()

    async def graph(wf, resources):
        return BackupFailed()

    monkeypatch.setattr(adapter, "graph", graph)
    res = adapter.new_resources(secrets={"OPENAI_API_KEY": "sk-live-secret-42"})
    evs = [ev async for ev in adapter.execute(_spec(), res)]
    usage = next(e for e in evs if e.get("name") == "ac.usage")
    assert usage["value"]["failed"] and "sk-live-secret-42" not in str(evs)


# ------------------------------------------------------------------ RT-15 ----
HIDDEN_PRIVATE = [
    "64:ff9b::a9fe:a9fe",  # NAT64 of the cloud metadata address
    "64:ff9b::a00:1",  # NAT64 of 10.0.0.1
    "64:ff9b:1::a00:1",  # local-use NAT64
    "::127.0.0.1",  # IPv4-compatible
    "::a9fe:a9fe",
    "::ffff:10.0.0.1",  # IPv4-mapped
    "2002:a9fe:a9fe::1",  # 6to4
    "2001:0:4136:e378:8000:63bf:3fff:fdd2",  # Teredo
]


@pytest.mark.parametrize("address", HIDDEN_PRIVATE)
async def test_every_outbound_check_sees_through_ipv6_forms_of_private_addresses(address, monkeypatch):
    """RT-15: NAT64 and IPv4-compatible addresses count as "global" for `ipaddress`, yet reach the IPv4 address
    inside them; each outbound check had its own copy of the test. One shared helper is used everywhere."""
    import socket

    from agentcanvas.adapters.langchain import pinned
    from agentcanvas.sandbox import egress
    from agentcanvas.security.addresses import is_public

    assert not is_public(address) and egress._private(address)
    with pytest.raises(pinned.RefusedAddress):
        await pinned.public_address(address, 443)

    async def getaddrinfo(host, port, *a, **k):
        return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, port, 0, 0))]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", getaddrinfo)
    from langchain_core.tools import ToolException

    with pytest.raises(ToolException, match="private or internal"):
        await nodes._public_address("rebinding.example", 443)


@pytest.mark.parametrize("address", ["8.8.8.8", "2606:4700:4700::1111", "64:ff9b::808:808", "::ffff:8.8.8.8"])
def test_public_addresses_stay_public(address):
    from agentcanvas.security.addresses import is_public

    assert is_public(address)


def test_nat64_of_the_metadata_address_is_never_reachable_from_a_sandbox():
    from agentcanvas.sandbox import egress

    assert egress._never("64:ff9b::a9fe:a9fe")


# ------------------------------------------------- RT-4 on the other adapters ----
def _spend(usage: Any, tokens: int = 120) -> None:
    from uuid import uuid4

    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    run_id = uuid4()
    usage.on_chat_model_start({}, [], run_id=run_id, metadata={"ac_model": "openai:gpt-5.5"})
    reply = AIMessage("x", usage_metadata={"input_tokens": tokens, "output_tokens": 30, "total_tokens": tokens + 30})
    usage.on_llm_end(LLMResult(generations=[[ChatGeneration(message=reply)]]), run_id=run_id)


async def test_openai_agents_usage_of_a_stopped_run_still_counts(monkeypatch):
    """A cancelled or timed-out OpenAI Agents run lost the usage of the model call it was in when it stopped."""
    from agentcanvas.adapters import create_adapter
    from agentcanvas.adapters.openai_agents import runtime as rt

    adapter = create_adapter("openai_agents")

    async def run(flow, host, **kw):
        yield {"type": "RUN_STARTED"}
        _spend(host.usage)
        await asyncio.sleep(30)  # stopped here
        yield {"type": "RUN_FINISHED"}

    monkeypatch.setattr(rt, "run", run)
    late: list[dict[str, Any]] = []
    spec = _spec(on_usage=late.append, mocks={"model": ["x"]})
    spec.workflow = Workflow.model_validate({**llm_wf().model_dump(mode="json"), "framework": "openai_agents"})
    events = adapter.execute(spec, adapter.default_resources())
    assert (await anext(events))["type"] == "RUN_STARTED"
    pending = asyncio.ensure_future(anext(events))
    await asyncio.sleep(0.05)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await events.aclose()
    assert [r["input_tokens"] for r in late] == [120]


async def test_worker_frameworks_usage_of_a_stopped_run_still_counts(monkeypatch):
    """The same for CrewAI, ADK and the flow frameworks: usage the worker's model calls reported before the stop."""
    from agentcanvas.adapters.crewai import CrewAIAdapter

    adapter = CrewAIAdapter()
    hosts: list[Any] = []

    class Worker:
        async def run(self, run_id, request, handle):
            yield {"type": "RUN_STARTED"}
            _spend(hosts[0].usage)
            _spend(hosts[0].usage, 7)
            await asyncio.sleep(30)
            yield {"type": "RUN_FINISHED"}

    import agentcanvas.adapters.crewai as crew

    real_host = crew.ServerHost

    def host(*a, **kw):
        hosts.append(real_host(*a, **kw))
        return hosts[-1]

    monkeypatch.setattr(crew, "ServerHost", host)
    monkeypatch.setattr(adapter, "python", lambda: "python")
    monkeypatch.setattr(adapter, "worker", lambda python: Worker())
    monkeypatch.setattr(adapter, "source", lambda wf: "")
    monkeypatch.setattr(adapter, "_blocked", lambda wf: [])
    late: list[dict[str, Any]] = []
    spec = _spec(on_usage=late.append, mocks={"model": ["x"]})
    spec.workflow = Workflow.model_validate({**llm_wf().model_dump(mode="json"), "framework": "crewai"})
    events = adapter.execute(spec, adapter.default_resources())
    assert (await anext(events))["type"] == "RUN_STARTED"
    pending = asyncio.ensure_future(anext(events))
    await asyncio.sleep(0.05)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await events.aclose()
    assert [r["input_tokens"] for r in late] == [120, 7]
