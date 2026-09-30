"""LangSmith tracing of runs (M6-T6).

Every run's root trace id is derived from its AgentCanvas run id, so "Open trace" can find it without
listening to callbacks. Tracing itself is switched on the LangChain way (`LANGSMITH_TRACING=true` and a key).
"""

from __future__ import annotations

import os
import uuid
from functools import lru_cache

NAMESPACE = uuid.UUID("5b2a6c1e-7d0f-4c2a-9b8e-a9c0d3e4f501")
PREFIX = "langsmith:"


def trace_id(run_id: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, run_id)


def tracing_enabled() -> bool:
    return any(os.getenv(v, "").lower() == "true" for v in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"))


def trace_ref(run_id: str) -> str | None:
    """What the run row stores in `trace_url` (resolved to a web URL on demand)."""
    return f"{PREFIX}{trace_id(run_id)}" if tracing_enabled() else None


@lru_cache(maxsize=1)
def _project_url() -> str:
    from langsmith import Client, utils

    client = Client()
    project = client.read_project(project_name=utils.get_tracer_project())
    return f"{client._host_url}/o/{client._get_tenant_id()}/projects/p/{project.id}"


def web_url(ref: str) -> str:
    """The LangSmith page of a stored trace reference (calls LangSmith once per process for the project)."""
    return f"{_project_url()}/r/{ref.removeprefix(PREFIX)}?poll=true" if ref.startswith(PREFIX) else ref
