"""Workflow -> a Python module with a Strands Agents flow (S35).

Each AI block is a Strands `Agent` (an `OpenAIModel`, a system prompt, tools, a `structured_output_model` for typed
answers, an `AskBefore` hook for tools a person approves first) and each block of the canvas an `async def`; `FLOW`
says which step follows which and the flow engine runs it as the canvas does (see `flowgen` and `runtime`).
"""

from __future__ import annotations

from agentcanvas.adapters import flowgen
from agentcanvas.adapters.openai_agents.codegen import _py
from agentcanvas.ir.models import Workflow

STRANDS_VERSION = "1.64.0"
RUNTIME_IMPORT = "from agentcanvas.adapters.strands import runtime as rt"
STEP_TYPES = flowgen.STEP_TYPES
RESOURCE_TYPES = flowgen.RESOURCE_TYPES


class Strands(flowgen.Dialect):
    def text_agent(self, name: str, model: str) -> str:
        return f"Agent(name={_py(name)}, model={model}, callback_handler=None)"

    def agent(self, a: flowgen.Agentic) -> tuple[list[str], str]:
        args = [f"name={_py(a.name)}", f"model={a.model}", f"system_prompt={a.instructions}"]
        if a.tools:
            args.append(f"tools=run.tools({_py(a.tools)})")
        if a.approve:
            args.append(f"hooks=[rt.AskBefore({_py(a.approve)})]")
        if a.answer:
            args.append(f"structured_output_model={a.answer}")
        args.append("callback_handler=None")  # quiet: nothing printed while it works
        run_kw = ""
        if a.call_limit is not None:  # Strands' own Limits(turns=...): model calls in one invocation
            run_kw = f", max_calls={a.call_limit}" + (", stop_at_limit=False" if a.fail_at_limit else "")
        return args, run_kw


DIALECT = Strands(
    label="Strands Agents",
    runtime_import=RUNTIME_IMPORT,
    imports=["from strands import Agent"],
    doc="AI blocks are Strands `Agent`s run with `agent.invoke_async`;",
)


def generate(wf: Workflow) -> str:
    return flowgen.generate(wf, DIALECT)
