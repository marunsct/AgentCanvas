"""support: a CrewAI agent designed on AgentCanvas.

Generated from the block "support" of the workflow "Support desk".
Change the agent on the canvas and generate it again rather than editing this file.
"""

from __future__ import annotations

import os
from typing import Literal

from crewai import LLM, Agent
from crewai.tools import tool
from pydantic import BaseModel, Field

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
    llm = LLM(
        model=f"openai/{model}",
        base_url=base_url,
        api_key=api_key,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )
    return Agent(
        role=NAME.replace("_", " "),
        goal=INSTRUCTIONS or "Help the user.",
        backstory="Designed on AgentCanvas.",
        llm=llm,
        tools=[tool(t.__name__)(t) for t in tools],
        verbose=False,
    )


async def ask(agent, text: str):
    """One run of the agent on `text`: its answer (an Answer)."""
    return (await agent.kickoff_async(text, response_format=Answer)).pydantic
