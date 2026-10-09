"""support: a Strands Agents agent designed on AgentCanvas.

Generated from the block "support" of the workflow "Support desk".
Change the agent on the canvas and generate it again rather than editing this file.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field
from strands import Agent, tool
from strands.models.openai import OpenAIModel

NAME = "support"
INSTRUCTIONS = """You are a support agent.
Answer {briefly}."""
MODEL = "gpt-5.5"
BASE_URL = "https://api.openai.com/v1"
API_KEY_ENV = "OPENAI_API_KEY"
TEMPERATURE = 0.2
MAX_TOKENS = 500


class AnswerOrder(BaseModel):
    id: str | None = None
    items: list[str] | None = None


class Answer(BaseModel):
    answer: str = Field(description="The reply")
    mood: Literal["happy", "sad"] | None = None
    order: AnswerOrder | None = None


def build(tools=(), *, model: str = MODEL, base_url: str | None = None, api_key: str | None = None):
    """The agent. `tools` are plain Python functions it may call: their names, docstrings and type hints
    tell the model what they do."""
    base_url = base_url or os.environ.get("MODEL_BASE_URL") or BASE_URL
    api_key = api_key or os.environ.get(API_KEY_ENV) or "not-needed"
    return Agent(
        name=NAME,
        model=OpenAIModel(
            client_args={"base_url": base_url, "api_key": api_key},
            model_id=model,
            params={"temperature": TEMPERATURE, "max_tokens": MAX_TOKENS},
        ),
        system_prompt=INSTRUCTIONS,
        tools=[tool(t) for t in tools],
        structured_output_model=Answer,
        callback_handler=None,  # quiet: no printing while it works
    )


async def ask(agent, text: str):
    """One run of the agent on `text`: its answer (an Answer)."""
    result = await agent.invoke_async(text)
    return result.structured_output
