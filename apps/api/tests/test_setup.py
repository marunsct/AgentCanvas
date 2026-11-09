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


def test_openai_client_models_ask_for_their_streamed_usage():
    """A streamed answer reports its tokens only when the client asks (stream_options.include_usage); LangChain asks
    by itself only for OpenAI's own address, so runs and the Copilot on LM Studio, vLLM or llama.cpp counted no tokens
    and no cost (found by the Copilot evaluation on LM Studio: the streaming engine read 0 tokens)."""
    from agentcanvas.adapters.langchain.nodes import chat_model_args

    for model in ("lmstudio:m", "vllm:m", "openai_compatible:m", "openai:gpt-5.5", "llamacpp:m"):
        params = {"base_url": "http://127.0.0.1:1234/v1"} if model.startswith(("openai_compatible", "vllm")) else {}
        assert chat_model_args(model, params, {"OPENAI_API_KEY": "sk-1234567"})[1]["stream_usage"] is True, model
    off = chat_model_args("lmstudio:m", {"stream_usage": False}, {})[1]
    assert off["stream_usage"] is False  # a workflow that turns it off keeps it off
    assert "stream_usage" not in chat_model_args("anthropic:claude-haiku-5-5", {}, {})[1]  # reports usage anyway


async def test_the_hosted_edition_lists_models_from_public_addresses_only(api_client, app, monkeypatch):
    """S-5: in the hosted edition a builder's base URL must not make the API server read the cluster network or the
    cloud metadata address (a `?x=` suffix makes the whole URL theirs), and there is no local server to ask."""
    import http.server
    import threading

    hits: list[str] = []

    class Internal(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"data": [{"id": "internal-secret"}], "models": [{"name": "internal-secret"}]}')

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Internal)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}/internal?x="
    monkeypatch.setattr(app.state.settings, "edition", "hosted")
    try:
        url = f"{URL}/server-models"
        for provider in ("openai_compatible", "ollama", "lmstudio"):
            r = (await api_client.post(url, json={"provider": provider, "base_url": base})).json()
            assert r["ok"] is False and r["models"] == [], (provider, r)
        assert "your own machine" in r["error"]
        models = (await api_client.get("/v1/projects/prj_default/chat-models")).json()
        assert not any("internal-secret" in m["id"] for m in models)
        assert hits == []
        monkeypatch.setattr(app.state.settings, "edition", "dev")  # the self-hosted editions: as before
        r = (await api_client.post(url, json={"provider": "openai_compatible", "base_url": base})).json()
        assert r["models"] == ["internal-secret"] and hits
    finally:
        server.shutdown()
