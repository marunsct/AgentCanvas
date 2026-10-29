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
