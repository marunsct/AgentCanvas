"""Model prices: checked list prices in the catalog, and AGENTCANVAS_MODEL_PRICES for the rest (W015's remedy)."""

import pytest

from agentcanvas.registry import ModelInfo, default_registry, priced
from agentcanvas.runtime.runner import price

CATALOG = [ModelInfo(id="openai:gpt-5.5", label="GPT-5.5", provider="openai")]


def test_the_catalog_prices_the_claude_models_it_lists():
    assert price("anthropic:claude-haiku-4-5", 1_000_000, 1_000_000) == pytest.approx(6.0)
    assert default_registry().model("anthropic:claude-opus-5-5").price_out_per_mtok == 20.0  # type: ignore[union-attr]


def test_an_operator_prices_any_catalog_model():
    [m] = priced(CATALOG, '{"openai:gpt-5.5": [1.25, 10]}')
    assert (m.price_in_per_mtok, m.price_out_per_mtok) == (1.25, 10.0)
    assert priced(CATALOG, None) == CATALOG


@pytest.mark.parametrize(
    ("raw", "says"),
    [("not json", "must be JSON"), ('{"openai:gpt-5.5": 3}', "must be JSON"), ('{"openai:gpt-5.5": [1, 2, 3]}', "or"),
     ('{"acme:x": [1, 2]}', "not in the catalog"), ('{"openai:gpt-5.5": [-1, 2]}', "negative")],
)  # fmt: skip
def test_a_bad_price_table_is_refused_not_ignored(raw, says):
    with pytest.raises(ValueError, match=says):
        priced(CATALOG, raw)


def test_the_server_does_not_start_with_a_bad_price_table(monkeypatch):
    from agentcanvas.api.main import create_app
    from agentcanvas.settings import Settings

    monkeypatch.setenv("AGENTCANVAS_MODEL_PRICES", '{"acme:x": [1, 2]}')
    default_registry.cache_clear()
    try:
        with pytest.raises(ValueError, match="not in the catalog"):
            create_app(Settings(database_url="sqlite+aiosqlite://"))
    finally:
        monkeypatch.delenv("AGENTCANVAS_MODEL_PRICES")
        default_registry.cache_clear()


def test_prompt_cache_reads_and_writes_are_priced_apart():
    """E-1: LangChain's input count includes the cached part; reads cost a fraction, writes a little more."""
    # Sonnet 5.5: $2 in, $0.10 cache read, $2.50 cache write, $10 out per million
    assert price("anthropic:claude-sonnet-5-5", 1_000_000, 0, cache_read=900_000) == pytest.approx(0.2 + 0.09)
    assert price("anthropic:claude-sonnet-5-5", 1_000_000, 0, cache_write=1_000_000) == pytest.approx(2.5)
    [m] = priced(CATALOG, '{"openai:gpt-5.5": [5, 30, 0.5, 5]}')
    assert (m.price_cache_read_per_mtok, m.price_cache_write_per_mtok) == (0.5, 5.0)
    [m] = priced(CATALOG, '{"openai:gpt-5.5": [5, 30]}')
    assert m.price_cache_read_per_mtok is None  # unknown: charged as ordinary input, never as free


def test_cache_reads_do_not_use_up_a_token_cap():
    from agentcanvas.registry import billed_tokens, token_counts

    meta = {"input_tokens": 15_000, "output_tokens": 300,
            "input_token_details": {"cache_read": 14_000, "cache_creation": 0}}  # fmt: skip
    counts = token_counts(meta)
    assert counts == {"input_tokens": 15_000, "output_tokens": 300, "cache_read_tokens": 14_000,
                      "cache_write_tokens": 0}  # fmt: skip
    assert billed_tokens(counts) == 1_300
    assert token_counts(None) == {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0,
                                  "cache_write_tokens": 0}  # fmt: skip


async def test_the_copilot_counts_what_came_from_the_prompt_cache():
    from langchain_core.messages import AIMessage

    from agentcanvas.adapters.langchain.models import LangChainModels

    class Cached:
        def bind_tools(self, specs):
            return self

        async def ainvoke(self, messages):
            return AIMessage(content="ok", usage_metadata={
                "input_tokens": 15_000, "output_tokens": 200, "total_tokens": 15_200,
                "input_token_details": {"cache_read": 14_000, "cache_creation": 500},
            })  # fmt: skip

    class Res:
        def model(self, name, config):
            return Cached()

    usage: dict[str, int] = {}
    await LangChainModels().chat(Res(), "anthropic:claude-sonnet-5-5", [{"role": "user", "content": "hi"}], [], usage)
    assert usage == {"input_tokens": 15_000, "output_tokens": 200, "cache_read_tokens": 14_000,
                     "cache_write_tokens": 500}  # fmt: skip


def test_models_that_refuse_sampling_settings_are_not_sent_them():
    """Current Claude models answer 400 to a non-default temperature, and GPT-5 takes only the default: every template
    set temperature 0, so with a Claude or GPT-5 key the first model call failed."""
    from agentcanvas.adapters.langchain.nodes import chat_model_args
    from agentcanvas.registry import without_sampling
    from agentcanvas.services.workflows import with_default_model

    _, args = chat_model_args("anthropic:claude-sonnet-5-5", {"temperature": 0, "max_tokens": 900}, {})
    assert "temperature" not in args and args["max_tokens"] == 900
    _, args = chat_model_args("anthropic:claude-haiku-4-5", {"temperature": 0}, {})
    assert args["temperature"] == 0  # a model that takes it keeps it
    _, args = chat_model_args("ollama:llama3.3", {"temperature": 0.3}, {})
    assert args["temperature"] == 0.3
    assert without_sampling("openai:gpt-5-mini", {"top_p": 0.5, "x": 1}) == ({"x": 1}, ["top_p"])
    doc = {"type": "model.chat", "config": {"model": "lmstudio:m", "temperature": 0, "max_tokens": 5}}
    swapped = with_default_model(doc, {"model": "anthropic:claude-sonnet-5-5"})
    assert swapped["config"] == {"max_tokens": 5, "model": "anthropic:claude-sonnet-5-5"}


def test_w020_names_a_sampling_setting_a_model_ignores():
    from test_frameworks import GREETER

    from agentcanvas.ir.models import Workflow
    from agentcanvas.validation import validate

    model = {"id": "r", "type": "model.chat", "name": "m",
             "config": {"model": "anthropic:claude-sonnet-5-5", "temperature": 0}}  # fmt: skip
    wf = Workflow.model_validate({"id": "wf_s", "name": "s", **GREETER, "resources": [model]})
    found = [d for d in validate(wf) if d.code == "W020"]
    assert len(found) == 1 and "temperature" in found[0].message
