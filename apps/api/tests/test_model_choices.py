"""Every model choice offers only models set up to answer here: a hosted one with its key, what a running local
server lists, and for knowledge bases only models that embed."""

from __future__ import annotations

import httpx
import pytest

from agentcanvas.registry import installed_providers

P = "/v1/projects/prj_default"


@pytest.fixture
def servers(app, monkeypatch):
    """No provider keys; Ollama and LM Studio run, each with a chat model and an embedding model; nothing else."""
    for p in installed_providers():
        for name in (p.key_env, p.optional_key_env):
            if name:
                monkeypatch.delenv(name, raising=False)
    secrets = app.state.projects.secrets.setdefault("prj_default", {})
    for name in list(secrets):
        monkeypatch.delitem(secrets, name)
    seen: list[str] = []

    def answer(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}, {"name": "nomic-embed-text:latest"}]})
        if req.url.port == 1234:
            ids = ["openai/gpt-oss-20b", "text-embedding-nomic-embed-text-v1.5"]
            return httpx.Response(200, json={"data": [{"id": i} for i in ids]})
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(app.state, "model_list_transport", httpx.MockTransport(answer), raising=False)
    return secrets, seen


async def test_chat_choices_are_the_keyed_and_served_models_without_embedding_ones(api_client, servers, monkeypatch):
    secrets, _ = servers
    ids = lambda r: {m["id"] for m in r.json()}  # noqa: E731
    offered = ids(await api_client.get(f"{P}/chat-models"))
    assert offered == {"ollama:qwen3:8b", "lmstudio:openai/gpt-oss-20b"}  # no key set: only the local servers
    monkeypatch.setitem(secrets, "ANTHROPIC_API_KEY", "sk-ant-test")
    offered = ids(await api_client.get(f"{P}/chat-models"))
    assert "anthropic:claude-haiku-4-5" in offered and not any(m.startswith("openai:") for m in offered)


async def test_embedding_choices_are_builtin_keyed_and_served_embedding_models(api_client, servers, monkeypatch):
    secrets, _ = servers
    r = (await api_client.get(f"{P}/embedding-models")).json()
    assert [m["id"] for m in r] == [
        "builtin", "ollama:nomic-embed-text:latest", "lmstudio:text-embedding-nomic-embed-text-v1.5"
    ]  # fmt: skip  # the chat models (qwen3, gpt-oss) cannot embed
    assert r[0] == {"id": "builtin", "label": "Built-in (offline)", "provider": "AgentCanvas", "local": True}
    monkeypatch.setitem(secrets, "OPENAI_API_KEY", "sk-test")
    assert "openai:text-embedding-3-small" in {m["id"] for m in (await api_client.get(f"{P}/embedding-models")).json()}


async def test_a_local_server_is_asked_at_the_projects_address(api_client, servers):
    _, seen = servers
    await api_client.put(f"{P}/setup", json={"model": "lmstudio:openai/gpt-oss-20b", "base_url": "http://gpu:1234/v1"})
    await api_client.get(f"{P}/embedding-models")
    assert "http://gpu:1234/v1/models" in seen


async def test_a_knowledge_base_reaches_an_embedding_model_on_the_projects_server(api_client, servers):
    await api_client.put(
        f"{P}/setup", json={"model": "lmstudio:openai/gpt-oss-20b", "base_url": "http://gpu:1234/v1", "api_key": "lm-k"}
    )
    model = "lmstudio:text-embedding-nomic-embed-text-v1.5"
    kb = (await api_client.post(f"{P}/knowledge", json={"name": "near", "embedding": {"model": model}})).json()
    assert kb["embedding"] == {"model": model, "base_url": "http://gpu:1234/v1",
                               "api_key": {"$secret": "LMSTUDIO_API_KEY"}}  # fmt: skip
    own = {"model": "ollama:nomic-embed-text:latest"}  # another provider keeps its own address
    kb = (await api_client.post(f"{P}/knowledge", json={"name": "other", "embedding": own})).json()
    assert kb["embedding"] == own


def test_an_openai_compatible_server_is_sent_text_not_token_ids(monkeypatch):
    import langchain.embeddings

    from agentcanvas.adapters.langchain.nodes import knowledge_embedder

    made: list[tuple[str, dict]] = []
    monkeypatch.setattr(langchain.embeddings, "init_embeddings", lambda model, **kw: made.append((model, kw)))
    knowledge_embedder({"model": "lmstudio:text-embedding-nomic-embed-text-v1.5"}, {})
    knowledge_embedder({"model": "openai:text-embedding-3-small"}, {"OPENAI_API_KEY": "sk"})
    assert made[0][0] == "openai:text-embedding-nomic-embed-text-v1.5"
    assert made[0][1]["check_embedding_ctx_length"] is False  # LM Studio refuses OpenAI's token-id input
    assert "check_embedding_ctx_length" not in made[1][1]  # OpenAI itself keeps it
