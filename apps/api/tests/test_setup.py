"""First-run setup: the project's default model, its key as a project secret, and a connection test."""

from __future__ import annotations

import httpx
import pytest

from agentcanvas.adapters.langchain.testing import DemoResources
from agentcanvas.services.workflows import with_default_model

URL = "/v1/projects/prj_default/setup"


async def test_setup_saves_the_model_and_key(api_client, app):
    assert (await api_client.get(URL)).json()["done"] is False
    r = await api_client.put(URL, json={"model": "mistralai:mistral-small-latest", "api_key": "m-secret-key"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["done"] and out["default_model"] == {"model": "mistralai:mistral-small-latest", "base_url": None,
                                                     "api_key": None}  # fmt: skip
    assert out["key_secret"] == "MISTRAL_API_KEY" and out["key_set"]
    names = [s["name"] for s in (await api_client.get("/v1/projects/prj_default/secrets")).json()]
    assert "MISTRAL_API_KEY" in names and app.state.projects.secrets["prj_default"]["MISTRAL_API_KEY"] == "m-secret-key"

    # templates created afterwards use the default model; other settings stay
    wf = (
        await api_client.post("/v1/projects/prj_default/workflows", json={"name": "t", "template": "ticket_triage"})
    ).json()
    models = [r["config"] for r in wf["ir"]["resources"] if r["type"] == "model.chat"]
    assert models and all(c["model"] == "mistralai:mistral-small-latest" for c in models)


async def test_local_and_compatible_servers(api_client):
    r = await api_client.put(URL, json={"model": "ollama:llama3.3", "base_url": "http://localhost:11434"})
    assert r.json()["default_model"]["base_url"] is None  # the provider's default is not stored
    r = await api_client.put(URL, json={"model": "openai_compatible:my-model"})
    assert r.status_code == 422 and "base URL" in r.json()["detail"]
    r = await api_client.put(
        URL, json={"model": "openai_compatible:m", "base_url": "https://openrouter.ai/api/v1", "api_key": "or-key-123"}
    )
    dm = r.json()["default_model"]
    assert dm["base_url"] == "https://openrouter.ai/api/v1" and dm["api_key"] == {
        "$secret": "OPENAI_COMPATIBLE_API_KEY"
    }
    assert (await api_client.put(URL, json={"model": "nowhere:x"})).status_code == 422


async def test_skip_and_connection_test(api_client, app):
    assert (await api_client.post(f"{URL}/skip")).json() == {
        "done": True, "default_model": None, "key_secret": None, "key_set": False,
    }  # fmt: skip
    app.state.projects.resources_class = DemoResources
    r = await api_client.post(f"{URL}/test", json={"model": "groq:llama-3.3-70b-versatile", "api_key": "g-key-123"})
    assert r.json()["ok"] and "demo" in r.json()["reply"].lower()
    r = await api_client.post(f"{URL}/test", json={"model": "openai_compatible:m"})
    assert r.json() == {"ok": False, "reply": None, "error": "Any OpenAI-compatible API needs a base URL"}


def test_with_default_model_keeps_other_settings():
    doc = {"resources": [{"type": "model.chat", "config": {"model": "a:b", "temperature": 0, "base_url": "x"}}],
           "nodes": [{"type": "core.subgraph", "config": {"workflow": {"resources": [
               {"type": "model.chat", "config": {"model": "a:c"}}]}}}]}  # fmt: skip
    out = with_default_model(doc, {"model": "ollama:qwen3"})
    assert out["resources"][0]["config"] == {"temperature": 0, "model": "ollama:qwen3"}
    assert out["nodes"][0]["config"]["workflow"]["resources"][0]["config"] == {"model": "ollama:qwen3"}


async def test_connection_errors_never_show_the_key(api_client, app):
    class Leaky(DemoResources):
        def model(self, name, config):
            raise RuntimeError(f"401 Unauthorized: bad key {config['api_key']}")

    app.state.projects.resources_class = Leaky
    body = {"model": "openai_compatible:m", "base_url": "https://llm.example.com/v1", "api_key": "sk-very-secret-1"}
    r = (await api_client.post(f"{URL}/test", json=body)).json()
    assert not r["ok"] and "401 Unauthorized" in r["error"] and "sk-very-secret-1" not in r["error"]


async def test_local_server_with_a_key(api_client, app):
    r = await api_client.put(URL, json={"model": "lmstudio:openai/gpt-oss-20b", "api_key": "lm-token-123"})
    out = r.json()
    assert r.status_code == 200 and out["key_secret"] == "LMSTUDIO_API_KEY" and out["key_set"]
    assert out["default_model"] == {"model": "lmstudio:openai/gpt-oss-20b", "base_url": None,
                                    "api_key": {"$secret": "LMSTUDIO_API_KEY"}}  # fmt: skip
    assert app.state.projects.secrets["prj_default"]["LMSTUDIO_API_KEY"] == "lm-token-123"
    # saving again without a key keeps the saved one; a server without a key stores no reference
    r = await api_client.put(URL, json={"model": "lmstudio:qwen3-8b"})
    assert r.json()["default_model"]["api_key"] == {"$secret": "LMSTUDIO_API_KEY"}
    r = await api_client.put(URL, json={"model": "llamacpp:m"})
    assert r.json()["default_model"]["api_key"] is None and r.json()["key_secret"] == "LLAMACPP_API_KEY"


async def test_server_models_lists_what_the_server_offers(api_client, app):
    seen: list[httpx.Request] = []

    def server(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}, {"name": "llama3.3:latest"}]})
        if req.headers.get("authorization") != "Bearer lm-token-123":
            return httpx.Response(401, json={"error": "unauthorized"})
        return httpx.Response(200, json={"data": [{"id": "openai/gpt-oss-20b"}, {"id": "qwen/qwen3-8b"}]})

    app.state.model_list_transport = httpx.MockTransport(server)
    url = f"{URL}/server-models"
    r = (await api_client.post(url, json={"provider": "lmstudio"})).json()
    assert not r["ok"] and "API key" in r["error"]
    r = (await api_client.post(url, json={"provider": "lmstudio", "api_key": "lm-token-123"})).json()
    assert r == {"ok": True, "models": ["openai/gpt-oss-20b", "qwen/qwen3-8b"], "error": None}
    assert str(seen[-1].url) == "http://localhost:1234/v1/models"
    # the saved key is used when none is typed
    await api_client.put(URL, json={"model": "lmstudio:qwen/qwen3-8b", "api_key": "lm-token-123"})
    assert (await api_client.post(url, json={"provider": "lmstudio"})).json()["ok"]
    r = (await api_client.post(url, json={"provider": "ollama", "base_url": "http://box:11434"})).json()
    assert r["models"] == ["llama3.3:latest", "qwen3:8b"] and str(seen[-1].url) == "http://box:11434/api/tags"
    r = await api_client.post(url, json={"provider": "openai"})
    assert r.status_code == 422
    r = (await api_client.post(url, json={"provider": "openai_compatible"})).json()
    assert r == {"ok": False, "models": [], "error": "Any OpenAI-compatible API needs a base URL"}


async def test_a_stored_key_goes_only_to_the_server_it_was_saved_for(api_client, app):
    """S-4: a builder who points a model at a server they run must not receive the project's stored key: the
    model-list probe and a connection test send it to the provider's own address or the saved server only."""
    seen: list[httpx.Request] = []

    def server(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"data": [{"id": "m"}]})

    app.state.model_list_transport = httpx.MockTransport(server)
    await api_client.put(
        URL, json={"model": "lmstudio:m", "base_url": "http://gpu-box:1234/v1", "api_key": "lm-1234567"}
    )
    url = f"{URL}/server-models"
    await api_client.post(url, json={"provider": "lmstudio", "base_url": "http://gpu-box:1234/v1/"})
    assert seen[-1].headers.get("authorization") == "Bearer lm-1234567"  # the saved server gets it
    await api_client.post(url, json={"provider": "lmstudio", "base_url": "https://collector.example.com/v1"})
    assert "authorization" not in seen[-1].headers  # anywhere else gets nothing
    await api_client.post(url, json={"provider": "lmstudio", "base_url": "https://collector.example.com/v1",
                                     "api_key": "typed-here"})  # fmt: skip
    assert seen[-1].headers.get("authorization") == "Bearer typed-here"  # unless the person types one


def test_a_run_sends_a_provider_key_only_to_the_provider():
    from agentcanvas.adapters.langchain.nodes import chat_model_args

    secrets = {"OPENAI_API_KEY": "sk-project", "LMSTUDIO_API_KEY": "lm-project"}
    assert chat_model_args("openai:gpt-5.5", {}, secrets)[1]["api_key"] == "sk-project"
    with pytest.raises(ValueError, match="needs its own key"):
        chat_model_args("openai:gpt-5.5", {"base_url": "https://collector.example.com/v1"}, secrets)
    named = chat_model_args("openai:gpt-5.5", {"base_url": "https://gw.example.com/v1", "api_key": "sk-x"}, secrets)
    assert named[1]["api_key"] == "sk-x"  # a key the workflow names on purpose
    assert chat_model_args("lmstudio:m", {}, secrets)[1]["api_key"] == "lm-project"
    elsewhere = chat_model_args("lmstudio:m", {"base_url": "https://collector.example.com/v1"}, secrets)
    assert elsewhere[1]["api_key"] == "not-needed"
