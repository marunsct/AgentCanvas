"""The Agno worker (S32): runs Agno flows for an AgentCanvas server (see `flowworker`, imported first: it moves
anything printed to stderr before Agno loads)."""

from __future__ import annotations

import asyncio
from importlib.metadata import version
from typing import Any

from agentcanvas.adapters import flowworker


def model(base_url: str, api_key: str, **settings: Any) -> Any:
    """A canvas model through the server's model bridge, as an Agno OpenAI-compatible model."""
    from agno.models.openai.like import OpenAILike

    return OpenAILike(id="canvas", base_url=base_url, api_key=api_key, max_retries=0, **settings)


if __name__ == "__main__":
    from agentcanvas.adapters.agno import runtime

    asyncio.run(flowworker.main(runtime, model, version("agno")))
