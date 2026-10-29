"""Stop (`POST …/copilot/stop`): the server ends the turn, so the model call is cancelled and no more tokens are spent,
and what was said is kept as a stopped answer. Both engines."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from test_copilot import ask, new_workflow
from test_roles import act_as

from agentcanvas.testing import DemoResources


@pytest.fixture(autouse=True, params=["loop", "deepagents"])
def engine(request, settings):
    settings.copilot_engine = request.param
    return request.param


class Hanging(BaseChatModel):
    """Says a few words, then waits for ever, like a model call that is slow to finish; counts its calls."""

    calls: list[int]
    started: Any = None

    @property
    def _llm_type(self) -> str:
        return "hanging"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Hanging:  # type: ignore[override]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(1)
        self.started.set()
        await asyncio.sleep(3600)
        raise AssertionError("a stopped turn never gets its reply")

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append(1)
        yield ChatGenerationChunk(message=AIMessageChunk(content="Looking at it "))
        self.started.set()
        await asyncio.sleep(3600)
        yield ChatGenerationChunk(message=AIMessageChunk(content="never said"))


def hang(app) -> Hanging:
    model = Hanging(calls=[], started=asyncio.Event())
    app.state.resources_factory = lambda thread_id, wf: DemoResources(overrides={"copilot": model})
    return model


async def test_stop_ends_the_turn_on_the_server_and_keeps_what_was_said(app, api_client, engine):
    wf_id = (await new_workflow(api_client, template="ticket_triage"))["id"]
    model = hang(app)
    sending = asyncio.create_task(ask(api_client, wf_id, "what does it do?", mode="ask"))
    await asyncio.wait_for(model.started.wait(), 10)
    busy = await api_client.post(f"/v1/workflows/{wf_id}/copilot/messages", json={"text": "again", "mode": "ask"})
    assert busy.status_code == 409  # still answering

    stopped = await api_client.post(f"/v1/workflows/{wf_id}/copilot/stop")
    assert stopped.status_code == 200 and stopped.json() == {"stopped": True}
    events = await asyncio.wait_for(sending, 10)  # the stream ends by itself, with done last
    assert events[-1]["type"] == "done" and events[-1]["stopped"] is True
    assert len(model.calls) == 1  # the model was not called again: no more tokens are spent

    convo = (await api_client.get(f"/v1/workflows/{wf_id}/copilot")).json()
    assert [m["role"] for m in convo["messages"]] == ["user", "assistant"]
    assert convo["messages"][-1]["stopped"] is True
    if engine == "deepagents":  # streamed text: what was said so far stays
        assert convo["messages"][-1]["content"].startswith("Looking at it")

    # the lease is released: the next message is taken, and a second Stop finds nothing to stop
    again = await api_client.post(f"/v1/workflows/{wf_id}/copilot/stop")
    assert again.json() == {"stopped": False}
    model.started.clear()
    sending = asyncio.create_task(ask(api_client, wf_id, "and now?", mode="ask"))
    await asyncio.wait_for(model.started.wait(), 10)
    assert (await api_client.post(f"/v1/workflows/{wf_id}/copilot/stop")).json() == {"stopped": True}
    await asyncio.wait_for(sending, 10)


async def test_stop_with_nothing_answering_is_harmless(api_client):
    wf_id = (await new_workflow(api_client))["id"]
    r = await api_client.post(f"/v1/workflows/{wf_id}/copilot/stop")
    assert r.status_code == 200 and r.json() == {"stopped": False}


async def test_nobody_else_can_stop_your_answer(app, api_client):
    wf_id = (await new_workflow(api_client, template="ticket_triage"))["id"]
    model = hang(app)
    sending = asyncio.create_task(ask(api_client, wf_id, "what does it do?", mode="ask"))
    await asyncio.wait_for(model.started.wait(), 10)
    act_as(app, "viewer", user_id="usr_other")
    try:
        r = await api_client.post(f"/v1/workflows/{wf_id}/copilot/stop")
        assert r.json() == {"stopped": False}  # their conversation, not yours
    finally:
        app.dependency_overrides.clear()
    assert (await api_client.post(f"/v1/workflows/{wf_id}/copilot/stop")).json() == {"stopped": True}
    await asyncio.wait_for(sending, 10)
