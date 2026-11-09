"""G-8: the model gateway's answer cache is shared by every API instance that uses the database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import update
from test_durable_state import _events, _workflow, instance
from test_gateway import Counting, _llm_ir

from agentcanvas.adapters.langchain import nodes
from agentcanvas.adapters.langchain.answer_cache import DatabaseAnswerCache
from agentcanvas.db.models import AnswerCacheRow


@pytest.fixture
def counting(monkeypatch) -> Counting:
    """Every provider model is one counting stand-in, so calls add up across API instances."""
    model = Counting()
    monkeypatch.setattr(nodes, "init_chat_model", lambda *a, **kw: model)
    return model


async def _ask(api, wf: str, q: str = "hi") -> dict[str, Any]:
    return _events((await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"q": q}})).text)[-1]


async def test_an_answer_cached_on_one_instance_is_reused_on_another(settings, counting):
    ir = _llm_ir({"model": "openai:gpt-5-mini", "cache_ttl": 600})
    async with instance(settings) as (_, api):
        wf = await _workflow(api, ir)
        first = await _ask(api, wf)
        assert first["type"] == "RUN_FINISHED" and first["usage"]["cache_hits"] == 0, first
        assert (await _ask(api, wf))["usage"]["cache_hits"] == 1  # the same instance, as before
    assert counting.calls == 1
    async with instance(settings) as (_, api):  # another replica (or this one after a restart): nothing in memory
        again = await _ask(api, wf)
        assert again["output"] == first["output"], again
        assert again["usage"]["cache_hits"] == 1 and again["usage"]["input_tokens"] == 0
        other = await _ask(api, wf, "something else")  # another request is another key
        assert other["usage"]["cache_hits"] == 0
    assert counting.calls == 2


async def test_cached_answers_expire_and_are_capped(settings):
    async with instance(settings) as (app, _):
        db = app.state.db
        cache = DatabaseAnswerCache(db, "prj_default", size=3)
        for i in range(5):
            await cache.aput(f"k{i}", AIMessage(content=f"a{i}"), 60)
        await cache.prune()
        assert await cache.count() == 3  # the oldest two were dropped
        assert (await DatabaseAnswerCache(db, "prj_default").aget("k4", 60)).content == "a4"  # another instance
        assert await DatabaseAnswerCache(db, "prj_default").aget("k0", 60) is None
        async with db.sessionmaker() as s:  # time passes: stored 2 minutes ago, for 60 s
            then = datetime.now(UTC) - timedelta(minutes=2)
            await s.execute(update(AnswerCacheRow).values(created_at=then, expires_at=then + timedelta(seconds=60)))
            await s.commit()
        fresh = DatabaseAnswerCache(db, "prj_default")
        assert await fresh.aget("k4", 60) is None and await fresh.aget("k4", 600) is None  # expired for good
        await cache.prune()
        assert await cache.count() == 0
