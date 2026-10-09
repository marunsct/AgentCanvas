"""support: a Google ADK agent designed on AgentCanvas.

Generated from the block "support" of the workflow "Support desk".
Change the agent on the canvas and generate it again rather than editing this file.
"""

from __future__ import annotations

import os
from typing import Literal

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.runners import InMemoryRunner
from google.genai import types
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


def instructions(context) -> str:
    """The instructions as a function, so ADK does not read {braces} in them as session state."""
    return INSTRUCTIONS


def build(tools=(), *, model: str = MODEL, base_url: str | None = None, api_key: str | None = None):
    """The agent. `tools` are plain Python functions it may call: their names, docstrings and type hints
    tell the model what they do."""
    base_url = base_url or os.environ.get("MODEL_BASE_URL") or BASE_URL
    api_key = api_key or os.environ.get(API_KEY_ENV) or "not-needed"
    return LlmAgent(
        name=NAME,
        model=LiteLlm(
            model=f"openai/{model}",
            api_base=base_url,
            api_key=api_key,
            drop_params=True,  # LiteLLM leaves out settings a model does not take
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
        ),
        instruction=instructions,
        tools=list(tools),
        output_schema=Answer,
    )


async def ask(agent, text: str):
    """One run of the agent on `text`: its answer (an Answer)."""
    runner = InMemoryRunner(agent=agent, app_name="agentcanvas")
    session = await runner.session_service.create_session(app_name="agentcanvas", user_id="user")
    message = types.Content(role="user", parts=[types.Part(text=text)])
    answer = ""
    async for event in runner.run_async(user_id="user", session_id=session.id, new_message=message):
        if event.is_final_response() and event.content and event.content.parts:
            answer = "".join(part.text or "" for part in event.content.parts)
    return Answer.model_validate_json(answer)
