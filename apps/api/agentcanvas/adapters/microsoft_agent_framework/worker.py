"""The Microsoft Agent Framework worker (S33-S34): runs Agent Framework flows for an AgentCanvas server (see
`flowworker`, imported first: it moves anything printed to stderr before the framework loads)."""

from __future__ import annotations

import asyncio
from importlib.metadata import version
from typing import Any

from agentcanvas.adapters import flowworker


def model(base_url: str, api_key: str, **settings: Any) -> Any:
    """A canvas model through the server's model bridge, as an Agent Framework chat client."""
    from agent_framework.openai import OpenAIChatCompletionClient
    from openai import AsyncOpenAI

    from agentcanvas.adapters.microsoft_agent_framework.runtime import Settings

    client = AsyncOpenAI(base_url=base_url, api_key=api_key, max_retries=0)
    middleware = [Settings(settings)] if settings else None
    return OpenAIChatCompletionClient("canvas", async_client=client, middleware=middleware)


if __name__ == "__main__":
    from agentcanvas.adapters.microsoft_agent_framework import runtime

    asyncio.run(flowworker.main(runtime, model, version("agent-framework-core")))
