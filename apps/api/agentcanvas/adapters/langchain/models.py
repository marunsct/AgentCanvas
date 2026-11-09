"""Framework-neutral model calls (S20 `ModelService`), served by LangChain's provider integrations.

LLM judges, the Copilot and the setup page's connection test call models outside any workflow; they use this
service, never a framework adapter, so a workflow's framework does not decide which providers they can reach.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from agentcanvas.registry import token_counts
from agentcanvas.runtime.runner import add_call


class LangChainModels:
    async def complete(
        self,
        resources: Any,
        model: str,
        system: str,
        prompt: str,
        usage: dict[str, int] | None = None,
        config: dict[str, Any] | None = None,
    ) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        reply = await resources.model("judge", {**(config or {}), "model": model}).ainvoke(
            [SystemMessage(content=system), HumanMessage(content=prompt)]
        )
        if usage is not None and (meta := getattr(reply, "usage_metadata", None)):
            add_call(usage, model, token_counts(meta))  # cache reads and writes too: they are priced apart
        return reply.content if isinstance(reply.content, str) else json.dumps(reply.content)

    async def chat(
        self,
        resources: Any,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        usage: dict[str, int] | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        cacheable = model.startswith("anthropic:")
        converted: list[Any] = []
        for m in messages:
            role, content = m["role"], str(m.get("content") or "")
            if role == "system":
                if m.get("cache") and cacheable:  # prompt caching: the knowledge pack is the same on every turn
                    converted.append(
                        SystemMessage(
                            content=[{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
                        )
                    )
                else:
                    converted.append(SystemMessage(content=content))
            elif role == "user":
                converted.append(HumanMessage(content=content))
            elif role == "assistant":
                calls = [
                    {"name": c["name"], "args": c.get("args") or {}, "id": c["id"]} for c in m.get("tool_calls") or []
                ]
                converted.append(AIMessage(content=content, tool_calls=calls))
            elif role == "tool":
                converted.append(ToolMessage(content=content, tool_call_id=m["tool_call_id"]))
        specs = [
            {
                "type": "function",
                "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]},
            }
            for t in tools
        ]
        llm = resources.model("copilot", {**(config or {}), "model": model})
        reply = await (llm.bind_tools(specs) if specs else llm).ainvoke(converted)
        if usage is not None and (meta := getattr(reply, "usage_metadata", None)):
            add_call(usage, model, token_counts(meta))  # cache reads and writes too: they are priced apart
        content = reply.content
        if isinstance(content, list):  # content blocks: keep the text ones
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        calls = [
            {"id": c.get("id") or f"call_{i}", "name": c["name"], "args": c.get("args") or {}}
            for i, c in enumerate(getattr(reply, "tool_calls", None) or [])
        ]
        return {"content": str(content or ""), "tool_calls": calls}

    async def stream(
        self,
        resources: Any,
        model: str,
        messages: list[dict[str, Any]],
        usage: dict[str, int] | None = None,
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        kinds = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}
        converted = [kinds[m["role"]](content=str(m.get("content") or "")) for m in messages if m["role"] in kinds]
        llm = resources.model("copilot", {**(config or {}), "model": model})
        call: dict[str, int] = {}  # the chunks' counts add up to the call's, priced once it ends
        try:
            async for chunk in llm.astream(converted):
                if meta := getattr(chunk, "usage_metadata", None):
                    for k, n in token_counts(meta).items():  # cache reads and writes too: they are priced apart
                        call[k] = call.get(k, 0) + n
                content = chunk.content
                if isinstance(content, list):  # content blocks: keep the text ones
                    content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
                if content:
                    yield str(content)
        finally:  # a stopped stream counts what it used too
            if usage is not None and call:
                add_call(usage, model, call)

    def tool_spec(self, tool: Any) -> dict[str, Any]:
        from langchain_core.utils.function_calling import convert_to_openai_tool

        fn = convert_to_openai_tool(tool)["function"]
        parameters = fn.get("parameters") or {"type": "object", "properties": {}}
        return {"name": fn["name"], "description": fn.get("description") or "", "parameters": parameters}

    async def call_tool(self, tool: Any, args: dict[str, Any]) -> str:
        out = await tool.ainvoke(args)
        content = getattr(out, "content", out)  # a tool may answer with a ToolMessage
        return content if isinstance(content, str) else json.dumps(content, default=str)
