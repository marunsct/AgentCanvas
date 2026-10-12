"""Workflow -> a Python module with a LlamaIndex flow (S35).

Each AI block is a LlamaIndex `FunctionAgent` (an `OpenAILike` LLM, a system prompt, tools, an `output_cls` for typed
answers) and each block of the canvas an `async def`; `FLOW` says which step follows which and the flow engine runs it
as the canvas does (see `flowgen` and `runtime`).
"""

from __future__ import annotations

from agentcanvas.adapters import flowgen
from agentcanvas.adapters.openai_agents.codegen import _py
from agentcanvas.ir.models import Workflow

LLAMA_INDEX_CORE_VERSION = "0.14.25"
OPENAI_LIKE_VERSION = "0.8.1"
RUNTIME_IMPORT = "from agentcanvas.adapters.llamaindex import runtime as rt"
STEP_TYPES = flowgen.STEP_TYPES
RESOURCE_TYPES = flowgen.RESOURCE_TYPES


class LlamaIndex(flowgen.Dialect):
    def text_agent(self, name: str, model: str) -> str:
        return f"FunctionAgent(name={_py(name)}, llm={model})"

    def agent(self, a: flowgen.Agentic) -> tuple[list[str], str]:
        args = [f"name={_py(a.name)}", f"llm={a.model}", f"system_prompt={a.instructions}", *flowgen.tools_arg(a)]
        if a.answer:
            args.append(f"output_cls={a.answer}")
        run_kw = ""
        if a.call_limit is not None:  # the agent's own max_iterations: model calls in one run
            run_kw = f", max_calls={a.call_limit}" + (", stop_at_limit=False" if a.fail_at_limit else "")
        return args, run_kw


DIALECT = LlamaIndex(
    label="LlamaIndex",
    runtime_import=RUNTIME_IMPORT,
    imports=["from llama_index.core.agent.workflow import FunctionAgent"],
    doc="AI blocks are LlamaIndex `FunctionAgent`s run with `agent.run`;",
    agent_class="FunctionAgent",
)


def generate(wf: Workflow) -> str:
    return flowgen.generate(wf, DIALECT)
