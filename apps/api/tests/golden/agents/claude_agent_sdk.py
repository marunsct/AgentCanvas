"""support: a Claude Agent SDK agent designed on AgentCanvas.

Generated from the block "support" of the workflow "Support desk".
Change the agent on the canvas and generate it again rather than editing this file.
"""

from __future__ import annotations

import inspect
import os
import typing
from typing import Literal

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    query,
    tool,
)
from pydantic import BaseModel, Field

NAME = "support"
INSTRUCTIONS = """You are a support agent.
Answer {briefly}."""
MODEL = "gpt-5.5"
BASE_URL = "https://api.anthropic.com"
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


def sdk_tool(fn):
    """A plain function as a tool of the in-process MCP server the Claude Agent SDK talks to."""
    hints = typing.get_type_hints(fn)
    params = {p: hints.get(p, str) for p in inspect.signature(fn).parameters}

    async def handler(args: dict) -> dict:
        result = fn(**args)
        if inspect.isawaitable(result):
            result = await result
        return {"content": [{"type": "text", "text": str(result)}]}

    return tool(fn.__name__, inspect.getdoc(fn) or fn.__name__, params)(handler)


def build(tools=(), *, model: str = MODEL, base_url: str | None = None, api_key: str | None = None):
    """The agent. `tools` are plain Python functions it may call: their names, docstrings and type hints
    tell the model what they do."""
    base_url = base_url or os.environ.get("MODEL_BASE_URL") or BASE_URL
    api_key = api_key or os.environ.get(API_KEY_ENV) or "not-needed"
    server = create_sdk_mcp_server("canvas", tools=[sdk_tool(t) for t in tools])
    return ClaudeAgentOptions(
        system_prompt=INSTRUCTIONS,
        model=model,
        tools=[],  # no built-in tools (files, shell, web): only the ones designed on the canvas
        mcp_servers={"canvas": server},
        allowed_tools=[f"mcp__canvas__{t.__name__}" for t in tools],
        setting_sources=[],  # ignore any Claude settings files on this machine
        output_format={"type": "json_schema", "schema": Answer.model_json_schema()},
        env={"ANTHROPIC_BASE_URL": base_url, "ANTHROPIC_API_KEY": api_key},
    )


async def ask(agent, text: str):
    """One run of the agent on `text`: its answer (an Answer)."""
    said: list[str] = []
    async for message in query(prompt=text, options=agent):
        if isinstance(message, AssistantMessage):
            said += [block.text for block in message.content if isinstance(block, TextBlock)]
        elif isinstance(message, ResultMessage):
            if message.is_error:
                raise RuntimeError(f"the agent stopped with an error: {message.result or message.subtype}")
            if message.structured_output is not None:
                return Answer.model_validate(message.structured_output)
            return Answer.model_validate_json(message.result or "")
    raise RuntimeError("the agent gave no answer")
