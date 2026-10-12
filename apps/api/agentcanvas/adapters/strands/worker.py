"""The Strands Agents worker (S35): runs Strands flows for an AgentCanvas server (see `flowworker`, imported first: it
moves anything printed to stderr before Strands loads)."""

from __future__ import annotations

import asyncio
from importlib.metadata import version
from typing import Any

from agentcanvas.adapters import flowworker


def model(base_url: str, api_key: str, **settings: Any) -> Any:
    """A canvas model through the server's model bridge, as a Strands `OpenAIModel`."""
    from strands.models.openai import OpenAIModel

    client_args = {"base_url": base_url, "api_key": api_key, "max_retries": 0}
    return OpenAIModel(client_args=client_args, model_id="canvas", params=settings or None)


if __name__ == "__main__":
    from agentcanvas.adapters.strands import runtime

    asyncio.run(flowworker.main(runtime, model, version("strands-agents")))
