"""The Microsoft Agent Framework adapter (S33-S34): workflows designed for the Agent Framework, run and exported as
Agent Framework flows.

`codegen` writes the workflow as plain async Python with Agent Framework `Agent`s for the AI blocks and a `FLOW` that
the flow engine (`supersteps`) runs as the canvas draws it; `runtime` adds the Agent Framework parts (chat clients,
tools that wait for a person's approval, typed answers). The flows run in a worker process started from the Python
environment for the Agent Framework (`AGENTCANVAS_MICROSOFT_AGENT_FRAMEWORK_PYTHON`, or
`.frameworks/microsoft_agent_framework` next to the API: `make framework FW=microsoft_agent_framework`), as the
Pydantic AI and Agno workflows do (see `flowadapter`). Without one, these workflows can still be designed, checked and
exported; runs say how to install it.
"""

from __future__ import annotations

from agentcanvas import designs
from agentcanvas.adapters.crewai import CrewWorker
from agentcanvas.adapters.flowadapter import FlowAdapter, worker_files
from agentcanvas.adapters.microsoft_agent_framework import codegen
from agentcanvas.registry.adapters import all_manifests

INSTALL_HINT = (
    "The Microsoft Agent Framework is not installed on this server: run `make framework FW=microsoft_agent_framework` "
    f"(a separate Python environment with agent-framework-core=={codegen.AGENT_FRAMEWORK_VERSION}) or set "
    "AGENTCANVAS_MICROSOFT_AGENT_FRAMEWORK_PYTHON, then restart the server"
)


def agent_framework_python() -> str | None:
    """The Python with the Agent Framework that runs the flows (None: not installed here)."""
    return designs.part_python("microsoft_agent_framework")


class AgentFrameworkWorker(CrewWorker):
    label = "Microsoft Agent Framework"
    package = "microsoft_agent_framework"
    files = worker_files("microsoft_agent_framework")


class AgentFrameworkAdapter(FlowAdapter):
    id = "microsoft_agent_framework"
    manifest = all_manifests()["microsoft_agent_framework"]
    install_hint = INSTALL_HINT
    worker_class = AgentFrameworkWorker
    generate_source = staticmethod(codegen.generate)
