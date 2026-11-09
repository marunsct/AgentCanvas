"""Test helpers.

Two gotchas with LangChain's fake chat models (verified with langchain-core in 2026-09):
1. they do not implement bind_tools() -> agents fail with NotImplementedError;
2. when a run streams tokens (stream_mode="messages") a reply that only contains
   tool calls yields no chunks -> "ValueError: No generations found in stream".
ToolFakeModel fixes (1); scripted() sets disable_streaming=True to fix (2).
"""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


class ToolFakeModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):  # tools are ignored: replies are scripted
        return self


def scripted(*replies: AIMessage | str) -> ToolFakeModel:
    msgs = [r if isinstance(r, AIMessage) else AIMessage(content=r) for r in replies]
    return ToolFakeModel(messages=iter(msgs), disable_streaming=True)


def tool_call(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])
