"""The Agno adapter (S32): workflows designed for Agno, run and exported as Agno flows.

`codegen` writes the workflow as plain async Python with Agno `Agent`s for the AI blocks and a `FLOW` that the flow
engine (`supersteps`) runs as the canvas draws it; `runtime` adds the Agno parts (models, tools that wait for a
person's confirmation, typed answers). Agno cannot be installed next to the server's own packages (it needs an older
`openai`), so the flows run in a worker process started from the Python environment for Agno
(`AGENTCANVAS_AGNO_PYTHON`, or `.frameworks/agno` next to the API: `make framework FW=agno`), as the CrewAI and
Google ADK workflows do (see `flowadapter`). Without one, Agno workflows can still be designed, checked and exported;
runs say how to install it.
"""

from __future__ import annotations

from typing import ClassVar

from agentcanvas import designs
from agentcanvas.adapters.agno import codegen
from agentcanvas.adapters.crewai import CrewWorker
from agentcanvas.adapters.flowadapter import FlowAdapter, worker_files
from agentcanvas.registry.adapters import all_manifests

INSTALL_HINT = (
    "Agno is not installed on this server: run `make framework FW=agno` (a separate Python environment with "
    f"agno=={codegen.AGNO_VERSION}) or set AGENTCANVAS_AGNO_PYTHON, then restart the server"
)


def agno_python() -> str | None:
    """The Python with Agno that runs the flows (None: not installed here)."""
    return designs.part_python("agno")


class AgnoWorker(CrewWorker):
    label = "Agno"
    package = "agno"
    files = worker_files("agno")
    extra_env: ClassVar[dict[str, str]] = {"AGNO_TELEMETRY": "false"}


class AgnoAdapter(FlowAdapter):
    id = "agno"
    manifest = all_manifests()["agno"]
    install_hint = INSTALL_HINT
    worker_class = AgnoWorker
    generate_source = staticmethod(codegen.generate)
