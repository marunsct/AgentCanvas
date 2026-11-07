"""Runtime review fixes: graph caching, hosted addresses, inbox items, usage, sandbox output and more."""

from __future__ import annotations

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
