"""support: a LlamaIndex agent designed on AgentCanvas.

Generated from the block "support" of the workflow "Support desk".
Change the agent on the canvas and generate it again rather than editing this file.
"""

from __future__ import annotations

import os
from typing import Literal

from llama_index.core.agent.workflow import FunctionAgent
from llama_index.core.tools import FunctionTool
from llama_index.llms.openai_like import OpenAILike
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
    llm = OpenAILike(
        model=model,
        api_base=base_url,
        api_key=api_key,
        is_chat_model=True,
        is_function_calling_model=True,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
    )
    return FunctionAgent(
        name=NAME,
        system_prompt=INSTRUCTIONS,
        llm=llm,
        tools=[FunctionTool.from_defaults(t) for t in tools],
        output_cls=Answer,
    )


async def ask(agent, text: str):
    """One run of the agent on `text`: its answer (an Answer)."""
    result = await agent.run(user_msg=text)
    answer = result.get_pydantic_model(Answer)
    return answer if answer is not None else Answer.model_validate_json(str(result))
