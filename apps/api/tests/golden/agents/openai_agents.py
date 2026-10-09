"""support: an OpenAI Agents SDK agent designed on AgentCanvas.

Generated from the block "support" of the workflow "Support desk".
Change the agent on the canvas and generate it again rather than editing this file.
"""

from __future__ import annotations

import os
from typing import Literal

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, Runner, function_tool, set_tracing_disabled
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

set_tracing_disabled(True)  # runs are traced by AgentCanvas or your own tracer, not OpenAI's

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
    client = AsyncOpenAI(base_url=base_url, api_key=api_key)
    return Agent(
        name=NAME,
        instructions=INSTRUCTIONS,
        model=OpenAIChatCompletionsModel(model=model, openai_client=client),
        model_settings=ModelSettings(temperature=TEMPERATURE, max_tokens=MAX_TOKENS),
        tools=[function_tool(t) for t in tools],
        output_type=Answer,
    )


async def ask(agent, text: str):
    """One run of the agent on `text`: its answer (an Answer)."""
    return (await Runner.run(agent, text)).final_output
