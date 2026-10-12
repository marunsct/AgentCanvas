"""The Pydantic AI worker (S32): runs Pydantic AI flows for an AgentCanvas server (see `flowworker`, imported first:
it moves anything printed to stderr before Pydantic AI loads)."""

from __future__ import annotations

import asyncio
from importlib.metadata import version
from typing import Any

from agentcanvas.adapters import flowworker


def model(base_url: str, api_key: str, **settings: Any) -> Any:
    """A canvas model through the server's model bridge, as a Pydantic AI chat model."""
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(base_url=base_url, api_key=api_key)
    return OpenAIChatModel("canvas", provider=provider, settings=settings or None)  # type: ignore[arg-type]


if __name__ == "__main__":
    from agentcanvas.adapters.pydantic_ai import runtime

    asyncio.run(flowworker.main(runtime, model, version("pydantic-ai-slim")))
