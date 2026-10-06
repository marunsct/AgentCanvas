"""First-run setup: the project's default model, its key as a project secret, and a connection test."""

from __future__ import annotations

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
