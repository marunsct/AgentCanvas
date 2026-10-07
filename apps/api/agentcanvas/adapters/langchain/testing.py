"""Fake models for tests and the key-less dev server.

Two gotchas with LangChain's fake chat models (verified with langchain-core 1.6):
1. they do not implement bind_tools() -> agents fail with NotImplementedError;
2. when a run streams tokens (stream_mode="messages") a reply that only contains
   tool calls yields no chunks -> "ValueError: No generations found in stream".
ToolFakeModel fixes (1); scripted() sets disable_streaming=True to fix (2).

`DemoChatModel` is *stateless* (unlike scripted fakes, FAQ P-03), so the dev server can use one
instance for every thread: it answers from simple prompt rules and calls the first bound tool once.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field

from agentcanvas.adapters.langchain.nodes import Resources, tag_model


class ToolFakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):  # tools are ignored: replies are scripted
        return self


def scripted(*replies: AIMessage | str) -> ToolFakeModel:
    msgs = [r if isinstance(r, AIMessage) else AIMessage(content=r) for r in replies]
    return ToolFakeModel(messages=iter(msgs), disable_streaming=True)


def tool_call(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


class MockChatModel(BaseChatModel):
    """Scripted replies for debug "mock mode" (M4-T9), stateless: the n-th AI turn of the conversation gets
    reply n (the last one repeats), so resumes and later chat turns continue the script instead of restarting it."""

    replies: list[AIMessage]

    @property
    def _llm_type(self) -> str:
        return "agentcanvas-mock"

    def __init__(self, **kwargs: Any):
        super().__init__(**{"disable_streaming": True, **kwargs})

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> MockChatModel:  # type: ignore[override]
        return self

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        turn = sum(1 for m in messages if m.type == "ai")
        reply = self.replies[min(turn, len(self.replies) - 1)]
        return ChatResult(generations=[ChatGeneration(message=reply.model_copy())])


RULES: list[tuple[str, str]] = [
    ("'high' or 'low'", "low"),
    ("question, complaint or other", "question"),
]


class DemoChatModel(BaseChatModel):
    """Deterministic, stateless stand-in for a real model (no API key needed)."""

    tool_specs: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "agentcanvas-demo"

    def __init__(self, **kwargs: Any):
        super().__init__(**{"disable_streaming": True, **kwargs})

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> DemoChatModel:  # type: ignore[override]
        specs = [convert_to_openai_tool(t)["function"] for t in tools]
        return DemoChatModel(tool_specs=specs, metadata=self.metadata)

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        reply = self._reply(messages)
        # approximate usage (characters / 4) so the demo shows tokens per node like a real provider would
        tokens_in = max(1, sum(len(str(m.content)) for m in messages) // 4)
        tokens_out = max(1, (len(str(reply.content)) + len(str(reply.tool_calls))) // 4)
        reply.usage_metadata = {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "total_tokens": tokens_in + tokens_out,
        }
        return ChatResult(generations=[ChatGeneration(message=reply)])

    def _reply(self, messages: list[BaseMessage]) -> AIMessage:
        if self.tool_specs and not any(isinstance(m, ToolMessage) for m in messages):
            # a Deep Agent offers file tools: saving a note shows its long-term /memories/ folder at work
            spec = next((t for t in self.tool_specs if t["name"] == "write_file"), self.tool_specs[0])
            props = spec.get("parameters", {}).get("properties", {})
            asked = _last_human(messages)
            args = {k: _demo_value(v, k, asked) for k, v in props.items()}
            return AIMessage(content="", tool_calls=[{"name": spec["name"], "args": args, "id": "demo_call_1"}])
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="Done — the tool call finished (demo model).")
        text = str(messages[-1].content) if messages else ""
        for needle, answer in RULES:
            if needle in text:
                return AIMessage(content=answer)
        return AIMessage(content=f"Demo reply (no model key configured): {text[:120]}")


_DEMO_GREETER = [
    {"op": "SetChannels", "channels": [{"name": "who", "io": "in"}, {"name": "greeting", "io": "out"}]},
    {"op": "AddResource", "resource": {"id": "r_demo_model", "type": "model.chat", "name": "demo_model",
                                       "config": {"model": "anthropic:claude-haiku-4-5"}}},
    {"op": "AddNode", "node": {"id": "n_greet", "type": "core.llm", "name": "greet", "label": "Greet",
                               "config": {"prompt": "Greet {{ state.who }} warmly.", "output": "greeting"}}},
    {"op": "Connect", "edge": {"id": "e_start_greet", "kind": "flow", "source": {"node": "START"},
                               "target": {"node": "n_greet"}}},
    {"op": "Connect", "edge": {"id": "e_greet_end", "kind": "flow", "source": {"node": "n_greet"},
                               "target": {"node": "END"}}},
    {"op": "Connect", "edge": {"id": "w_demo_model_greet", "kind": "wiring", "source": {"node": "r_demo_model"},
                               "target": {"node": "n_greet", "port": "model"}}},
]  # fmt: skip


class DemoCopilotModel(BaseChatModel):
    """The key-less dev server's Copilot: asked to build, it proposes a one-step greeter; otherwise it explains."""

    tool_names: list[str] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "agentcanvas-demo-copilot"

    def __init__(self, **kwargs: Any):
        super().__init__(**{"disable_streaming": True, **kwargs})

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> DemoCopilotModel:  # type: ignore[override]
        names = [t["function"]["name"] if "function" in t else t.get("name", "") for t in tools]
        return DemoCopilotModel(tool_names=names)

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        last = max((i for i, m in enumerate(messages) if m.type == "human"), default=-1)
        if any(isinstance(m, ToolMessage) for m in messages[last + 1 :]):
            reply = AIMessage(content="I proposed a Greet step that writes a friendly greeting (demo Copilot).")
        elif "propose_changes" in self.tool_names and "build" in _last_human(messages).lower():
            args = {"summary": "Add a Greet step", "commands": _DEMO_GREETER}
            reply = AIMessage(content="Plan: one Ask AI step.", tool_calls=[
                {"name": "propose_changes", "args": args, "id": "demo_copilot_1"}])  # fmt: skip
        else:
            reply = AIMessage(content="This is the demo Copilot (no model key configured). Ask me to build something.")
        return ChatResult(generations=[ChatGeneration(message=reply)])


def _last_human(messages: list[BaseMessage]) -> str:
    return next((str(m.content) for m in reversed(messages) if m.type == "human"), "")


_DEMO_ARGS = {"file_path": "/memories/findings.md"}


def _demo_value(schema: dict[str, Any], name: str = "", asked: str = "") -> Any:
    if name in _DEMO_ARGS:
        return _DEMO_ARGS[name]
    if name == "content":
        return f"Notes (demo model):\n{asked[:500]}"
    return {"string": "demo", "integer": 1, "number": 1.0, "boolean": True, "array": [], "object": {}}.get(
        schema.get("type", "string"), "demo"
    )


class DemoResources(Resources):
    """Every chat model resolves to the stateless demo model; tools stay real (workspace code)."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        # one per resource: usage is attributed to the right catalog model, replies to the right resource
        self._demos: dict[str, DemoChatModel] = {}

    def model(self, name: str, config: dict[str, Any]) -> BaseChatModel:
        if name in self._overrides:
            return super().model(name, config)
        if name == "copilot":
            return DemoCopilotModel()
        model_id = str(config.get("model", "demo"))
        if name not in self._demos:
            self._demos[name] = tag_model(DemoChatModel(), model_id, name)
        return self._demos[name]
