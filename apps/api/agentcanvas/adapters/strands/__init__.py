"""The Strands Agents adapter (S35): workflows designed for Strands, run and exported as Strands flows.

`codegen` writes the workflow as plain async Python with Strands `Agent`s for the AI blocks and a `FLOW` that the flow
engine (`supersteps`) runs as the canvas draws it; `runtime` adds the Strands parts (models, the `AskBefore` hook that
interrupts tool calls for a person, typed answers, call limits). Strands cannot be installed next to the server's own
packages, so the flows run in a worker process started from the Python environment for Strands
(`AGENTCANVAS_STRANDS_PYTHON`, or `.frameworks/strands` next to the API: `make framework FW=strands`), as the other
flow frameworks do (see `flowadapter`). Without one, Strands workflows can still be designed, checked and exported;
runs say how to install it.
"""

from __future__ import annotations

from typing import ClassVar

from agentcanvas.adapters.crewai import CrewWorker
from agentcanvas.adapters.flowadapter import FlowAdapter, worker_files
from agentcanvas.adapters.strands import codegen
from agentcanvas.registry.adapters import all_manifests

INSTALL_HINT = (
    "Strands Agents is not installed on this server: run `make framework FW=strands` (a separate Python environment "
    f"with strands-agents=={codegen.STRANDS_VERSION}) or set AGENTCANVAS_STRANDS_PYTHON, then restart the server"
)


class StrandsWorker(CrewWorker):
    label = "Strands Agents"
    package = "strands"
    files = worker_files("strands")
    extra_env: ClassVar[dict[str, str]] = {"OTEL_SDK_DISABLED": "true"}


class StrandsAdapter(FlowAdapter):
    id = "strands"
    manifest = all_manifests()["strands"]
    install_hint = INSTALL_HINT
    worker_class = StrandsWorker
    generate_source = staticmethod(codegen.generate)
