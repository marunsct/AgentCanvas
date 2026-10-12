"""Framework adapters (spec doc 11/12): LangChain/LangGraph, the OpenAI Agents SDK (S25), CrewAI (S27), Google ADK
(S29), Pydantic AI and Agno (S32), the Microsoft Agent Framework (S33-S34) and the Echo demo engine that proves the
seams (S20).

`get_adapter(id)` is the process-wide adapter for a framework; `create_adapter(id)` a fresh one (tests, the
conformance kit). Which ones a server offers is `AGENTCANVAS_ADAPTERS` (`registry.adapters.installed_ids`).
"""

from __future__ import annotations

from functools import cache

from agentcanvas.adapters.spi import FrameworkAdapter, ModelService
from agentcanvas.errors import Conflict
from agentcanvas.registry import adapters as manifests

KNOWN = (
    "langchain",
    "echo",
    "openai_agents",
    "crewai",
    "google_adk",
    "pydantic_ai",
    "agno",
    "microsoft_agent_framework",
)


def create_adapter(framework: str = "langchain") -> FrameworkAdapter:
    if framework == "langchain":
        from agentcanvas.adapters.langchain.adapter import LangChainAdapter

        return LangChainAdapter()
    if framework == "echo":
        from agentcanvas.adapters.echo import EchoAdapter

        return EchoAdapter()
    if framework == "openai_agents":
        from agentcanvas.adapters.openai_agents import OpenAIAgentsAdapter

        return OpenAIAgentsAdapter()
    if framework == "crewai":
        from agentcanvas.adapters.crewai import CrewAIAdapter

        return CrewAIAdapter()
    if framework == "google_adk":
        from agentcanvas.adapters.google_adk import GoogleADKAdapter

        return GoogleADKAdapter()
    if framework == "pydantic_ai":
        from agentcanvas.adapters.pydantic_ai import PydanticAIAdapter

        return PydanticAIAdapter()
    if framework == "agno":
        from agentcanvas.adapters.agno import AgnoAdapter

        return AgnoAdapter()
    if framework == "microsoft_agent_framework":
        from agentcanvas.adapters.microsoft_agent_framework import AgentFrameworkAdapter

        return AgentFrameworkAdapter()
    raise ValueError(f"no adapter installed for framework '{framework}'")


@cache
def get_adapter(framework: str = "langchain") -> FrameworkAdapter:
    return create_adapter(framework)


def installed_adapters() -> dict[str, FrameworkAdapter]:
    """The adapters this server offers, by id (the default framework first)."""
    return {i: get_adapter(i) for i in manifests.installed_ids() if i in KNOWN}


def model_service() -> ModelService:
    """Model calls outside workflows (Copilot, judges, setup). Provider integrations come from LangChain today."""
    from agentcanvas.adapters.langchain.models import LangChainModels

    return LangChainModels()


def framework_adapter(framework: str) -> FrameworkAdapter:
    """The installed adapter for a workflow's framework; a plain 409 when this server does not offer it."""
    adapters = installed_adapters()
    if framework not in adapters:
        raise Conflict(
            f"this workflow is designed for '{framework}', which this server does not have; "
            f"installed: {', '.join(adapters)}"
        )
    return adapters[framework]
