"""Workflow -> a Python module with an Agno flow (S32).

Each AI block is an Agno `Agent` (an `OpenAILike` model, instructions, tools, an `output_schema` for typed answers)
and each block of the canvas an `async def`; `FLOW` says which step follows which and the flow engine runs it as the
canvas does (see `flowgen` and `runtime`).
"""

from __future__ import annotations

from agentcanvas.adapters import flowgen
from agentcanvas.adapters.openai_agents.codegen import _py
from agentcanvas.ir.models import Workflow

AGNO_VERSION = "3.1.11"
OPENAI_VERSION = "2.54.0"
RUNTIME_IMPORT = "from agentcanvas.adapters.agno import runtime as rt"
STEP_TYPES = flowgen.STEP_TYPES
RESOURCE_TYPES = flowgen.RESOURCE_TYPES


class Agno(flowgen.Dialect):
    def text_agent(self, name: str, model: str) -> str:
        return f"Agent(name={_py(name)}, model={model})"

    def agent(self, a: flowgen.Agentic) -> tuple[list[str], str]:
        args = [f"name={_py(a.name)}", f"model={a.model}", f"instructions={a.instructions}", *flowgen.tools_arg(a)]
        if a.answer:
            args.append(f"output_schema={a.answer}")
        args.append("resolve_in_context=False")  # the instructions are text, not a template of session state
        run_kw = ""
        if a.call_limit is not None:  # model calls in one run of the agent, counted by the runtime
            run_kw = f", max_calls={a.call_limit}" + (", stop_at_limit=False" if a.fail_at_limit else "")
        return args, run_kw


DIALECT = Agno(
    label="Agno",
    runtime_import=RUNTIME_IMPORT,
    imports=["from agno.agent import Agent"],
    doc="AI blocks are Agno `Agent`s run with `agent.arun`;",
)


def generate(wf: Workflow) -> str:
    return flowgen.generate(wf, DIALECT)
