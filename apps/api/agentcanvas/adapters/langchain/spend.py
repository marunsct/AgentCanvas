"""A run's spending limits outside AgentCanvas: the per-run part of the workflow budget (`settings.budget`).

The LangGraph deploy kit (`kit/app.py.tmpl`) gives every run a `SpendLimit` callback: it adds up the tokens each
model reply reports and prices them from the vendored model catalog (`registry/models.json`), and stops the run
with `BudgetExceeded` once `max_tokens` or `max_cost_usd` is passed, as the AgentCanvas runner does
(`runtime/runner.check_budget`). `max_seconds` is the service's run time limit. The limits for a conversation, a day
or a month need the spending of other runs, which a self-hosted service does not keep: GAPS.md lists them.
Standalone apart from LangChain's callbacks, because exported projects vendor it.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler

CATALOG = Path(__file__).resolve().parents[2] / "registry" / "models.json"
PER_RUN = ("max_tokens", "max_cost_usd", "max_seconds")  # the budget limits one run can check on its own


class BudgetExceeded(RuntimeError):
    """A run went over its workflow budget."""


@cache
def _catalog() -> dict[str, dict[str, Any]]:
    try:
        return {m["id"]: m for m in json.loads(CATALOG.read_text())}
    except (OSError, ValueError):
        return {}


def call_price(model_id: str | None, usage: dict[str, int]) -> float | None:
    """USD for one model reply from the catalog's prices (the long-context tier and prompt cache prices included);
    None when the model or its price is unknown."""
    m = _catalog().get(model_id or "")
    if m is None or m.get("price_in_per_mtok") is None or m.get("price_out_per_mtok") is None:
        return None
    tokens_in, out = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    p_in, p_out, scale = m["price_in_per_mtok"], m["price_out_per_mtok"], 1.0
    above = m.get("price_above_tokens")
    if above is not None and tokens_in > above and m.get("price_in_above_per_mtok") is not None:
        scale = m["price_in_above_per_mtok"] / p_in if p_in else 1.0
        p_in, p_out = m["price_in_above_per_mtok"], m.get("price_out_above_per_mtok", p_out)
    read, write = usage.get("cache_read_tokens", 0), usage.get("cache_write_tokens", 0)
    p_read = m["price_cache_read_per_mtok"] * scale if m.get("price_cache_read_per_mtok") is not None else p_in
    p_write = m["price_cache_write_per_mtok"] * scale if m.get("price_cache_write_per_mtok") is not None else p_in
    plain = max(0, tokens_in - read - write)
    return (plain * p_in + read * p_read + write * p_write + out * p_out) / 1_000_000


def _counts(meta: Any) -> dict[str, int]:
    meta = meta or {}
    details = meta.get("input_token_details") or {}
    return {"input_tokens": int(meta.get("input_tokens") or 0), "output_tokens": int(meta.get("output_tokens") or 0),
            "cache_read_tokens": int(details.get("cache_read") or 0),
            "cache_write_tokens": int(details.get("cache_creation") or 0)}  # fmt: skip


class SpendLimit(AsyncCallbackHandler):
    """Counts one run's tokens and cost; raises BudgetExceeded once `max_tokens` or `max_cost_usd` is passed (and
    before any later model call). `over` keeps the reason, for a step whose error route caught the exception."""

    raise_error = True  # the exception stops the model call instead of being logged

    def __init__(self, budget: dict[str, Any]):
        self.max_tokens = budget.get("max_tokens")
        self.max_cost = budget.get("max_cost_usd")
        self.tokens = 0
        self.cost = 0.0
        self.over: str | None = None
        self._models: dict[UUID, str | None] = {}
        self._gateways: set[UUID] = set()

    async def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: UUID, **kw: Any) -> None:
        if self.over:
            raise BudgetExceeded(self.over)
        md = kw.get("metadata") or {}
        self._models[run_id] = md.get("ac_model")
        if md.get("ac_gateway"):  # its inner calls report the usage, of the model that answered
            self._gateways.add(run_id)

    async def on_llm_end(self, response: Any, *, run_id: UUID, **kw: Any) -> None:
        model = self._models.pop(run_id, None)
        if run_id in self._gateways:
            self._gateways.discard(run_id)
            return
        for gens in response.generations:
            for g in gens:
                usage = _counts(getattr(getattr(g, "message", None), "usage_metadata", None))
                self.tokens += usage["input_tokens"] + usage["output_tokens"]
                self.cost += call_price(model, usage) or 0.0
        if self.max_tokens is not None and self.tokens > self.max_tokens:
            self.over = f"budget exceeded: {self.tokens:g} of {self.max_tokens:g} tokens for this run"
        elif self.max_cost is not None and self.cost > self.max_cost:
            self.over = f"budget exceeded: {self.cost:g} of {self.max_cost:g} USD for this run"
        if self.over:
            raise BudgetExceeded(self.over)

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kw: Any) -> None:
        self._models.pop(run_id, None)
        self._gateways.discard(run_id)
