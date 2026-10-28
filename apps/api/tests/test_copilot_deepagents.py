"""What the Deep Agents engine adds over the loop (PLAN.md section 2): text as it is generated, the agent's plan as a
live to-do list, the full workflow as a file, the whole conversation instead of the last 24 messages, and no shell."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from test_copilot import ask, new_workflow, of

from agentcanvas.testing import DemoResources


class Streamer(BaseChatModel):
    """Scripted replies, streamed word by word; remembers the tools it was offered and the messages it was sent."""

    replies: list[AIMessage]
    offered: list[list[str]] = []
    received: list[list[BaseMessage]] = []

    @property
    def _llm_type(self) -> str:
        return "streamer"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Streamer:  # type: ignore[override]
        self.offered.append([getattr(t, "name", None) or t.get("name") or t["function"]["name"] for t in tools])
        return self

    def _reply(self, messages: list[BaseMessage]) -> AIMessage:
        self.received.append(list(messages))
        turn = sum(1 for m in messages if m.type == "ai" and m.response_metadata.get("streamer"))
        reply = self.replies[min(turn, len(self.replies) - 1)]
        return reply.model_copy(update={"response_metadata": {"streamer": True}})

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        reply = self._reply(messages)
        if reply.tool_calls:
            chunks = [{"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                      for i, c in enumerate(reply.tool_calls)]  # fmt: skip
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=chunks,
                                                             response_metadata={"streamer": True}))  # fmt: skip
            return
        words = reply.content.split(" ")
        for i, word in enumerate(words):
            piece = word if i == len(words) - 1 else word + " "
            yield ChatGenerationChunk(message=AIMessageChunk(content=piece, response_metadata={"streamer": True}))


def use(app, *replies: AIMessage | str) -> Streamer:
    model = Streamer(replies=[r if isinstance(r, AIMessage) else AIMessage(content=r) for r in replies])
    model.offered, model.received = [], []
    app.state.resources_factory = lambda thread_id, wf: DemoResources(overrides={"copilot": model})
    return model


def tool(name: str, call_id: str = "c1", **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


@pytest.fixture(autouse=True)
def deepagents(settings):
    settings.copilot_engine = "deepagents"


async def test_text_streams_and_the_plan_is_live(app, api_client):
    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]
    todos = [
        {"content": "Read the workflow", "status": "completed"},
        {"content": "Explain it", "status": "in_progress"},
    ]
    model = use(app, tool("write_todos", todos=todos), "It sorts support tickets into three queues.")
    events = await ask(api_client, wf, "what does this do?", mode="ask")
    assert events[0]["engine"] == "deepagents"
    assert of(events, "todos")[0]["todos"] == todos  # the agent's plan, for the panel's checklist
    deltas = "".join(e["text"] for e in of(events, "text_delta"))
    assert len(of(events, "text_delta")) > 3 and deltas == "It sorts support tickets into three queues."
    assert of(events, "text")[-1]["text"] == deltas and events[-1]["type"] == "done"
    assert "execute" not in model.offered[0] and {"write_todos", "read_file", "get_workflow"} <= set(model.offered[0])
    assert "propose_changes" not in model.offered[0]  # Ask mode: the tools that change things are not offered
    convo = (await api_client.get(f"/v1/workflows/{wf}/copilot")).json()
    assert convo["messages"][-1] == {**convo["messages"][-1], "role": "assistant", "content": deltas}


async def test_the_full_workflow_is_a_file_and_the_whole_conversation_is_sent(app, api_client):
    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]
    for i in range(30):  # more than the loop's 24-message window
        use(app, f"answer {i}")
        await ask(api_client, wf, f"question {i}", mode="ask")
    model = use(app, tool("read_file", file_path="/workflow.json"), "Read it.")
    events = await ask(api_client, wf, "read the whole workflow", mode="ask")
    assert of(events, "text")[-1]["text"] == "Read it."
    sent = model.received[0]
    assert sum(1 for m in sent if m.type == "human") == 31  # every earlier question, then this one
    read = [m for m in model.received[-1] if m.type == "tool"]
    assert read and '"n_' in read[0].content  # the file holds the workflow's nodes
