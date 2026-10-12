"""The LlamaIndex worker (S35): runs LlamaIndex flows for an AgentCanvas server (see `flowworker`, imported first: it
moves anything printed to stderr before LlamaIndex loads)."""

from __future__ import annotations

import asyncio
from importlib.metadata import version
from typing import Any

from agentcanvas.adapters import flowworker


def model(base_url: str, api_key: str, **settings: Any) -> Any:
    """A canvas model through the server's model bridge, as a LlamaIndex `OpenAILike` LLM."""
    from llama_index.llms.openai_like import OpenAILike

    return OpenAILike(model="canvas", api_base=base_url, api_key=api_key, is_chat_model=True,
                      is_function_calling_model=True, max_retries=0, **settings)  # fmt: skip


if __name__ == "__main__":
    from agentcanvas.adapters.llamaindex import runtime

    asyncio.run(flowworker.main(runtime, model, version("llama-index-core")))
