"""Workflow -> a Python module with a Pydantic AI flow (S32).

Each AI block is a Pydantic AI `Agent` (an `OpenAIChatModel`, instructions, `Tool`s, a `NativeOutput` for typed
answers) and each block of the canvas an `async def`; `FLOW` says which step follows which and the flow engine runs
it as the canvas does (see `flowgen` and `runtime`).
"""

from __future__ import annotations

from agentcanvas.adapters import flowgen
from agentcanvas.adapters.openai_agents.codegen import _py
from agentcanvas.ir.models import Workflow

PYDANTIC_AI_VERSION = "2.63.0"
RUNTIME_IMPORT = "from agentcanvas.adapters.pydantic_ai import runtime as rt"
STEP_TYPES = flowgen.STEP_TYPES
RESOURCE_TYPES = flowgen.RESOURCE_TYPES


class PydanticAI(flowgen.Dialect):
    def text_agent(self, name: str, model: str) -> str:
        return f"Agent({model}, name={_py(name)})"

    def agent(self, a: flowgen.Agentic) -> tuple[list[str], str]:
        args = [a.model, f"name={_py(a.name)}", f"instructions={a.instructions}", *flowgen.tools_arg(a)]
        if a.answer:
            args.append(f"output_type=NativeOutput({a.answer})")
        run_kw = ""
        if a.call_limit is not None:  # UsageLimits(request_limit=...): model calls in one run of the agent
            run_kw = f", max_calls={a.call_limit}" + (", stop_at_limit=False" if a.fail_at_limit else "")
        return args, run_kw


DIALECT = PydanticAI(
    label="Pydantic AI",
    runtime_import=RUNTIME_IMPORT,
    imports=["from pydantic_ai import Agent"],
    structured_imports=["from pydantic_ai import Agent, NativeOutput"],
    doc="AI blocks are Pydantic AI `Agent`s run with `agent.run`;",
)


def generate(wf: Workflow) -> str:
    return flowgen.generate(wf, DIALECT)
