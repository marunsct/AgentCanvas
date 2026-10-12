"""The LlamaIndex adapter (S35): workflows designed for LlamaIndex, run and exported as LlamaIndex flows.

`codegen` writes the workflow as plain async Python with LlamaIndex `FunctionAgent`s for the AI blocks and a `FLOW`
that the flow engine (`supersteps`) runs as the canvas draws it; `runtime` adds the LlamaIndex parts (LLMs, tools that
wait for a person through the agent workflow's `InputRequiredEvent`, typed answers, call limits). The flows run in a
worker process started from the Python environment for LlamaIndex (`AGENTCANVAS_LLAMAINDEX_PYTHON`, or
`.frameworks/llamaindex` next to the API: `make framework FW=llamaindex`), as the other flow frameworks do (see
`flowadapter`). Without one, LlamaIndex workflows can still be designed, checked and exported; runs say how to install
it.
"""

from __future__ import annotations

from agentcanvas.adapters.crewai import CrewWorker
from agentcanvas.adapters.flowadapter import FlowAdapter, worker_files
from agentcanvas.adapters.llamaindex import codegen
from agentcanvas.registry.adapters import all_manifests

INSTALL_HINT = (
    "LlamaIndex is not installed on this server: run `make framework FW=llamaindex` (a separate Python environment "
    f"with llama-index-core=={codegen.LLAMA_INDEX_CORE_VERSION}) or set AGENTCANVAS_LLAMAINDEX_PYTHON, then restart "
    "the server"
)


class LlamaIndexWorker(CrewWorker):
    label = "LlamaIndex"
    package = "llamaindex"
    files = worker_files("llamaindex")


class LlamaIndexAdapter(FlowAdapter):
    id = "llamaindex"
    manifest = all_manifests()["llamaindex"]
    install_hint = INSTALL_HINT
    worker_class = LlamaIndexWorker
    generate_source = staticmethod(codegen.generate)
