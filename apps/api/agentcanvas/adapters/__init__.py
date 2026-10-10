"""Framework adapters (spec doc 11/12): LangChain/LangGraph, the OpenAI Agents SDK (S25), CrewAI (S27) and the Echo
demo engine that proves the seams (S20).

`get_adapter(id)` is the process-wide adapter for a framework; `create_adapter(id)` a fresh one (tests, the
conformance kit). Which ones a server offers is `AGENTCANVAS_ADAPTERS` (`registry.adapters.installed_ids`).
"""

from __future__ import annotations

from functools import cache

from agentcanvas.adapters.spi import FrameworkAdapter, ModelService
from agentcanvas.errors import Conflict
from agentcanvas.registry import adapters as manifests

KNOWN = ("langchain", "echo", "openai_agents", "crewai")


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
