"""Simulated users (S13, FR-EVAL-07): an experiment drives a multi-turn conversation with a chat workflow.

A catalog model plays the user: it has a persona and a goal (the experiment's, or the example's `metadata.goal`),
writes the opening message (unless the example's input carries one in `messages`) and each next message after
reading the conversation so far, and says the stop phrase when its goal is met or clearly cannot be. The
conversation also ends at `max_turns` user messages, or when a run of the workflow does not succeed. Every turn
runs on the same thread; the whole conversation is one experiment result, whose output carries the transcript
so evaluators see every turn.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agentcanvas.evaluation import Complete
from agentcanvas.ir.models import Workflow

MAX_TURNS = 20

SYSTEM = (
    "You are role-playing a user of an AI assistant, to test the assistant. Stay in character.\n"
    "Persona: {persona}\nYour goal: {goal}\n"
    "Write only your next message to the assistant, the way this user would type it. When your goal has been met, "
    "or clearly cannot be met, reply with exactly {stop} and nothing else."
)


class Simulation(BaseModel):
    model: str = Field(description="catalog model that plays the user")
    persona: str = Field(min_length=1, max_length=4000, description="who the user is and how they write")
    goal: str = Field(default="", max_length=4000, description="what they want; an example's metadata.goal wins")
    max_turns: int = Field(default=5, ge=1, le=MAX_TURNS, description="user messages at most")
    stop_phrase: str = Field(default="DONE", min_length=1, max_length=40)


def is_chat(wf: Workflow) -> bool:
    """A chat workflow keeps its conversation in a `messages` channel that appends."""
    return any(c.name == "messages" and c.reducer == "add_messages" for c in wf.state.channels)


def _content(message: Any) -> str:
    content = message.get("content", "") if isinstance(message, dict) else message
    if isinstance(content, list):  # content blocks
        return " ".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in content).strip()
    return str(content)


def reply_of(output: dict[str, Any]) -> str:
    """The assistant's last message in a run's final output."""
    for m in reversed(output.get("messages") or []):
        if isinstance(m, dict) and m.get("type") in ("ai", "AIMessage", "assistant"):
            return _content(m)
    return ""


def opener(example: dict[str, Any]) -> str | None:
    """The example's own first user message, if its input has one."""
    messages = (example.get("input") or {}).get("messages")
    if isinstance(messages, str):
        return messages
    if isinstance(messages, list) and messages:
        return _content(messages[-1])
    return None


def _render(transcript: list[dict[str, str]]) -> str:
    if not transcript:
        return "The conversation has not started. Write your opening message."
    lines = [f"{'You' if t['role'] == 'user' else 'Assistant'}: {t['content']}" for t in transcript]
    return "The conversation so far:\n\n" + "\n\n".join(lines) + "\n\nWrite your next message."


async def next_message(sim: Simulation, goal: str, transcript: list[dict[str, str]], complete: Complete) -> str | None:
    """The simulated user's next message, or None when they stop."""
    system = SYSTEM.format(persona=sim.persona, goal=goal or "(none given: act naturally)", stop=sim.stop_phrase)
    reply = (await complete(system, _render(transcript), sim.model)).strip()
    if not reply or reply.rstrip(".!").strip().upper() == sim.stop_phrase.upper():
        return None
    return reply
