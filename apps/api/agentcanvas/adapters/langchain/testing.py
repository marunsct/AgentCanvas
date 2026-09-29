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

from agentcanvas.adapters.langchain.nodes import Resources


class ToolFakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):  # tools are ignored: replies are scripted
        return self


def scripted(*replies: AIMessage | str) -> ToolFakeModel:
    msgs = [r if isinstance(r, AIMessage) else AIMessage(content=r) for r in replies]
    return ToolFakeModel(messages=iter(msgs), disable_streaming=True)


def tool_call(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


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
        return DemoChatModel(tool_specs=[convert_to_openai_tool(t)["function"] for t in tools])

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    def _reply(self, messages: list[BaseMessage]) -> AIMessage:
        if self.tool_specs and not any(isinstance(m, ToolMessage) for m in messages):
            spec = self.tool_specs[0]
            props = spec.get("parameters", {}).get("properties", {})
            args = {k: _demo_value(v) for k, v in props.items()}
            return AIMessage(content="", tool_calls=[{"name": spec["name"], "args": args, "id": "demo_call_1"}])
        if any(isinstance(m, ToolMessage) for m in messages):
            return AIMessage(content="Done — the tool call finished (demo model).")
        text = str(messages[-1].content) if messages else ""
        for needle, answer in RULES:
            if needle in text:
                return AIMessage(content=answer)
        return AIMessage(content=f"Demo reply (no model key configured): {text[:120]}")


def _demo_value(schema: dict[str, Any]) -> Any:
    return {"string": "demo", "integer": 1, "number": 1.0, "boolean": True, "array": [], "object": {}}.get(
        schema.get("type", "string"), "demo"
    )


class DemoResources(Resources):
    """Every chat model resolves to the stateless demo model; tools stay real (workspace code)."""

    def __init__(self) -> None:
        super().__init__()
        self._demo = DemoChatModel()

    def model(self, name: str, config: dict[str, Any]) -> BaseChatModel:
        return self._demo
