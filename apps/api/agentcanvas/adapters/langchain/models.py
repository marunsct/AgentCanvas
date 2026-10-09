"""Framework-neutral model calls (S20 `ModelService`), served by LangChain's provider integrations.

LLM judges, the Copilot and the setup page's connection test call models outside any workflow; they use this
service, never a framework adapter, so a workflow's framework does not decide which providers they can reach.
"""

from __future__ import annotations

import json
from typing import Any


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
            usage["input_tokens"] = usage.get("input_tokens", 0) + int(meta.get("input_tokens", 0))
            usage["output_tokens"] = usage.get("output_tokens", 0) + int(meta.get("output_tokens", 0))
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
            usage["input_tokens"] = usage.get("input_tokens", 0) + int(meta.get("input_tokens", 0))
            usage["output_tokens"] = usage.get("output_tokens", 0) + int(meta.get("output_tokens", 0))
        content = reply.content
        if isinstance(content, list):  # content blocks: keep the text ones
            content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
        calls = [
            {"id": c.get("id") or f"call_{i}", "name": c["name"], "args": c.get("args") or {}}
            for i, c in enumerate(getattr(reply, "tool_calls", None) or [])
        ]
        return {"content": str(content or ""), "tool_calls": calls}
