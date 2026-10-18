"""Model providers: any provider's API key from the canvas or a project secret, and local models."""

from __future__ import annotations

import pytest

from agentcanvas.adapters.langchain.nodes import (
    OPENAI_COMPATIBLE,
    OPTIONAL_KEYS,
    PROVIDER_KEYS,
    Resources,
    chat_model_args,
)
from agentcanvas.registry import default_registry, installed_providers
from agentcanvas.validation import validate


def codes(wf):
    return sorted(d.code for d in validate(wf))


def test_runtime_tables_match_the_provider_registry():
    providers = {p.id: p for p in default_registry().providers()}
    assert {k: v for k, v in PROVIDER_KEYS.items()} == {p.id: p.key_env for p in providers.values() if p.key_env}
    compatible = {p.id: p.base_url for p in providers.values() if p.module == "langchain_openai" and p.id not in (
        "openai", "azure_openai")}  # fmt: skip
    assert compatible == OPENAI_COMPATIBLE
    assert OPTIONAL_KEYS == {p.id: p.optional_key_env for p in providers.values() if p.optional_key_env}
    assert all(m.provider in providers or m.provider == "fake" for m in default_registry().models())


def test_every_listed_provider_is_installed():
    assert [p.id for p in installed_providers() if not p.installed] == []


def test_project_secret_named_after_the_provider_is_the_key():
    model, params = chat_model_args("mistralai:mistral-small-latest", {}, {"MISTRAL_API_KEY": "m-key"})
    assert (model, params) == ("mistralai:mistral-small-latest", {"api_key": "m-key"})
    # an explicit key in the config wins; a provider without a key leaves the params alone
    assert chat_model_args("openai:gpt-5.5", {"api_key": "mine"}, {"OPENAI_API_KEY": "x"})[1] == {"api_key": "mine"}
    assert chat_model_args("ollama:llama3.3", {}, {"OPENAI_API_KEY": "x"}) == ("ollama:llama3.3", {})


def test_openai_compatible_local_servers():
    assert chat_model_args("lmstudio:qwen3-8b", {}, {}) == (
        "openai:qwen3-8b", {"base_url": "http://localhost:1234/v1", "api_key": "not-needed"},
    )  # fmt: skip
    model, params = chat_model_args("vllm:llama", {"base_url": "http://gpu:9000/v1", "api_key": "k"}, {})
    assert (model, params) == ("openai:llama", {"base_url": "http://gpu:9000/v1", "api_key": "k"})
    with pytest.raises(ValueError, match="needs a base URL"):
        chat_model_args("openai_compatible:some-model", {}, {})


@pytest.mark.parametrize(
    ("model", "config", "cls"),
    [
        ("openai:gpt-5.5", {"api_key": {"$secret": "MY_KEY"}}, "ChatOpenAI"),
        ("google_genai:gemini-2.5-flash", {"api_key": {"$secret": "MY_KEY"}}, "ChatGoogleGenerativeAI"),
        ("mistralai:mistral-large-latest", {}, "ChatMistralAI"),
        ("groq:llama-3.3-70b-versatile", {"api_key": {"$secret": "MY_KEY"}}, "ChatGroq"),
        ("deepseek:deepseek-chat", {"api_key": {"$secret": "MY_KEY"}}, "ChatDeepSeek"),
        ("xai:grok-4", {"api_key": {"$secret": "MY_KEY"}}, "ChatXAI"),
        ("ollama:llama3.3", {}, "ChatOllama"),
        ("lmstudio:local-model", {}, "ChatOpenAI"),
        (
            "openai_compatible:m",
            {"base_url": "https://openrouter.ai/api/v1", "api_key": {"$secret": "MY_KEY"}},
            "ChatOpenAI",
        ),
    ],
)
def test_resources_create_every_provider(monkeypatch, model, config, cls):
    for var in ("OPENAI_API_KEY", "MISTRAL_API_KEY", "GROQ_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    res = Resources(secrets={"MY_KEY": "secret-value", "MISTRAL_API_KEY": "m-key"})
    assert type(res.model("m", {"model": model, **config})).__name__ == cls


def test_validation_flags_a_missing_base_url(triage):
    r = triage.resource("r_fast")
    r.config = {"model": "openai_compatible:my-model"}  # type: ignore[union-attr]
    assert "E017" in codes(triage)
    r.config = {"model": "openai_compatible:my-model", "base_url": "https://openrouter.ai/api/v1"}  # type: ignore[union-attr]
    assert "E017" not in codes(triage) and "W011" not in codes(triage)


def test_judges_accept_any_known_provider():
    reg = default_registry()
    assert reg.usable_model("ollama:anything-pulled") and reg.usable_model("lmstudio:x")
    assert not reg.usable_model("openai_compatible:x") and not reg.usable_model("nowhere:x")


async def test_providers_endpoint(api_client):
    providers = {p["id"]: p for p in (await api_client.get("/v1/model-providers")).json()}
    assert providers["openai"]["key_env"] == "OPENAI_API_KEY" and providers["openai"]["installed"]
    assert providers["ollama"]["local"] and providers["ollama"]["base_url"] == "http://localhost:11434"


def test_local_servers_on_another_host(monkeypatch):
    """In Docker, models on the host computer are reached through AGENTCANVAS_LOCAL_MODELS_HOST."""
    monkeypatch.setenv("AGENTCANVAS_LOCAL_MODELS_HOST", "host.docker.internal")
    assert chat_model_args("ollama:qwen3", {}, {}) == (
        "ollama:qwen3",
        {"base_url": "http://host.docker.internal:11434"},
    )
    assert chat_model_args("lmstudio:m", {}, {})[1]["base_url"] == "http://host.docker.internal:1234/v1"
    assert chat_model_args("ollama:qwen3", {"base_url": "http://gpu:11434"}, {})[1] == {"base_url": "http://gpu:11434"}
    ollama = next(p for p in installed_providers() if p.id == "ollama")
    assert ollama.base_url == "http://host.docker.internal:11434"
    assert next(p for p in installed_providers() if p.id == "openai").base_url is None


def test_local_servers_use_their_optional_key(monkeypatch):
    monkeypatch.delenv("LMSTUDIO_API_KEY", raising=False)
    assert chat_model_args("lmstudio:qwen3-8b", {}, {"LMSTUDIO_API_KEY": "lm-token"})[1]["api_key"] == "lm-token"
    monkeypatch.setenv("VLLM_API_KEY", "from-env")
    assert chat_model_args("vllm:m", {}, {})[1]["api_key"] == "from-env"
    # an explicit key in the config wins; no key anywhere gives the placeholder
    assert chat_model_args("lmstudio:q", {"api_key": "own"}, {"LMSTUDIO_API_KEY": "x"})[1]["api_key"] == "own"
    assert chat_model_args("llamacpp:m", {}, {})[1]["api_key"] == "not-needed"
