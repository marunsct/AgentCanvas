"""Framework adapters. R1 ships the LangChain adapter only (spec doc 11/12)."""

from __future__ import annotations

from functools import cache

from agentcanvas.adapters.spi import FrameworkAdapter


@cache
def get_adapter(framework: str = "langchain") -> FrameworkAdapter:
    if framework == "langchain":
        from agentcanvas.adapters.langchain.adapter import LangChainAdapter

        return LangChainAdapter()
    raise ValueError(f"no adapter installed for framework '{framework}'")
