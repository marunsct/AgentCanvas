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
    [("not json", "must be JSON"), ('{"openai:gpt-5.5": 3}', "must be JSON"),
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
