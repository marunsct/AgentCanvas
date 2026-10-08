"""Stand-ins for the agent frameworks (S16), with the same entry points the wrappers call.

The real frameworks run in CI's `frameworks` job (tests/frameworks); these keep the main suite free of them. Each
agent answers "<framework>: <input>".
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from typing import Any


def _module(name: str, **attrs: Any) -> types.ModuleType:
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    return mod


@dataclass
class Result:
    final_output: Any = None
    output: Any = None
    content: Any = None
    text: Any = None


class OpenAIAgent:
    def __init__(self, name: str = "oa"):
        self.name = name


class Runner:
    @staticmethod
    async def run(agent: OpenAIAgent, input: str) -> Result:
        return Result(final_output=f"openai_agents: {input}")


@dataclass
class CrewOutput:
    raw: str


@dataclass
class Crew:
    agents: list = field(default_factory=list)
    tasks: list = field(default_factory=list)
    seen: list = field(default_factory=list)

    async def kickoff_async(self, inputs: dict) -> CrewOutput:
        self.seen.append(inputs)
        return CrewOutput(raw=f"crewai: {inputs['input']}")


class PydanticAgent:
    async def run(self, text: str) -> Result:
        return Result(output=f"pydantic_ai: {text}")


class AgnoAgent:
    async def arun(self, text: str) -> Result:
        return Result(content=f"agno: {text}")


class StrandsResult:
    def __init__(self, text: str):
        self._text = text

    def __str__(self) -> str:
        return self._text


class StrandsAgent:
    async def invoke_async(self, text: str) -> StrandsResult:
        return StrandsResult(f"strands: {text}")


class LlamaWorkflow:
    async def run(self, input: str, **kwargs: Any) -> str:
        return f"llamaindex: {input}"


class MafAgent:
    async def run(self, text: str) -> Result:
        return Result(text=f"microsoft_agent_framework: {text}")


# Google ADK: InMemoryRunner + google.genai.types
@dataclass
class Part:
    text: str | None = None


@dataclass
class Content:
    role: str = "user"
    parts: list = field(default_factory=list)


@dataclass
class Event:
    content: Content
    final: bool

    def is_final_response(self) -> bool:
        return self.final


class AdkAgent:
    name = "adk"


class _Sessions:
    async def create_session(self, app_name: str, user_id: str) -> Any:
        return types.SimpleNamespace(id="s1")


class InMemoryRunner:
    def __init__(self, agent: Any, app_name: str):
        self.agent, self.session_service = agent, _Sessions()

    async def run_async(self, user_id: str, session_id: str, new_message: Content):
        text = new_message.parts[0].text
        yield Event(Content("model", [Part("thinking")]), False)
        yield Event(Content("model", [Part(f"google_adk: {text}")]), True)


# Claude Agent SDK: query(prompt, options) streams messages
@dataclass
class TextBlock:
    text: str


@dataclass
class AssistantMessage:
    content: list


@dataclass
class ResultMessage:
    result: str | None
    is_error: bool = False
    subtype: str = "success"


@dataclass
class ClaudeAgentOptions:
    system_prompt: str = ""


async def query(prompt: str, options: Any = None):
    yield AssistantMessage([TextBlock("working on it")])
    yield ResultMessage(result=f"claude_agent_sdk: {prompt}")


def install(monkeypatch: Any) -> None:
    """Register the stand-in modules for one test."""
    google = sys.modules.get("google") or _module("google")
    if not hasattr(google, "__path__"):
        google.__path__ = []  # type: ignore[attr-defined]
    adk = _module("google.adk")
    genai = _module("google.genai")
    mods = {
        "agents": _module("agents", Runner=Runner, Agent=OpenAIAgent),
        "crewai": _module("crewai", Crew=Crew),
        "google": google,
        "google.adk": adk,
        "google.adk.runners": _module("google.adk.runners", InMemoryRunner=InMemoryRunner),
        "google.genai": genai,
        "google.genai.types": _module("google.genai.types", Content=Content, Part=Part),
        "pydantic_ai": _module("pydantic_ai", Agent=PydanticAgent),
        "agno": _module("agno", Agent=AgnoAgent),
        "strands": _module("strands", Agent=StrandsAgent),
        "workflows": _module("workflows", Workflow=LlamaWorkflow),
        "agent_framework": _module("agent_framework", Agent=MafAgent),
        "claude_agent_sdk": _module(
            "claude_agent_sdk", query=query, AssistantMessage=AssistantMessage, ResultMessage=ResultMessage,
            TextBlock=TextBlock, ClaudeAgentOptions=ClaudeAgentOptions,
        ),  # fmt: skip
    }
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(genai, "types", mods["google.genai.types"], raising=False)
    monkeypatch.setattr(adk, "runners", mods["google.adk.runners"], raising=False)


WORKSPACE_AGENTS = '''"""Agents built with each framework (stand-ins in this test)."""
import agents, crewai, pydantic_ai, agno, strands, workflows, agent_framework, claude_agent_sdk


class AdkAgent:
    name = "adk"


openai_agents = agents.Agent(name="support")
crew = crewai.Crew(agents=["writer"], tasks=["write about {input}"])
google_adk = AdkAgent()
pydantic_ai_agent = pydantic_ai.Agent()
agno_agent = agno.Agent()
strands_agent = strands.Agent()
llamaindex = workflows.Workflow()
microsoft = agent_framework.Agent()
claude = claude_agent_sdk.ClaudeAgentOptions(system_prompt="Be brief")
BUILT = []


def fresh_agent():
    """A factory: a new agent per run."""
    BUILT.append(1)
    return pydantic_ai.Agent()


async def shout(input, ctx):
    return {"reply": input["input"].upper(), "loud": True}
'''

TARGETS = {
    "openai_agents": "tools.fw:openai_agents",
    "crewai": "tools.fw:crew",
    "google_adk": "tools.fw:google_adk",
    "pydantic_ai": "tools.fw:pydantic_ai_agent",
    "agno": "tools.fw:agno_agent",
    "strands": "tools.fw:strands_agent",
    "llamaindex": "tools.fw:llamaindex",
    "microsoft_agent_framework": "tools.fw:microsoft",
    "claude_agent_sdk": "tools.fw:claude",
}
