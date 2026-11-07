"""Runtime review fixes: graph caching, hosted addresses, inbox items, usage, sandbox output and more."""

from __future__ import annotations

from typing import Any

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
