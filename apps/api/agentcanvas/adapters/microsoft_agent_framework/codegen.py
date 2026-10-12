"""Workflow -> a Python module with a Microsoft Agent Framework flow (S33-S34).

Each AI block is an Agent Framework `Agent` (an `OpenAIChatCompletionClient`, instructions, tools, a
`response_format` for typed answers) and each block of the canvas an `async def`; `FLOW` says which step follows which
and the flow engine runs it as the canvas does (see `flowgen` and `runtime`).
"""

from __future__ import annotations

from agentcanvas.adapters import flowgen
from agentcanvas.adapters.openai_agents.codegen import _py
from agentcanvas.ir.models import Workflow

AGENT_FRAMEWORK_VERSION = "1.20.0"
AGENT_FRAMEWORK_OPENAI_VERSION = "1.16.0"
RUNTIME_IMPORT = "from agentcanvas.adapters.microsoft_agent_framework import runtime as rt"
STEP_TYPES = flowgen.STEP_TYPES
RESOURCE_TYPES = flowgen.RESOURCE_TYPES


class AgentFramework(flowgen.Dialect):
    def text_agent(self, name: str, model: str) -> str:
        return f"Agent({model}, name={_py(name)})"

    def agent(self, a: flowgen.Agentic) -> tuple[list[str], str]:
        args = [a.model, f"name={_py(a.name)}", f"instructions={a.instructions}", *flowgen.tools_arg(a)]
        if a.answer:
            args.append(f'default_options={{"response_format": {a.answer}}}')
        run_kw = ""
        if a.call_limit is not None:  # model calls in one run of the agent, counted by the runtime's chat middleware
            run_kw = f", max_calls={a.call_limit}" + (", stop_at_limit=False" if a.fail_at_limit else "")
        return args, run_kw


DIALECT = AgentFramework(
    label="Microsoft Agent Framework",
    runtime_import=RUNTIME_IMPORT,
    imports=["from agent_framework import Agent"],
    doc="AI blocks are Agent Framework `Agent`s run with `agent.run`;",
)


def generate(wf: Workflow) -> str:
    return flowgen.generate(wf, DIALECT)
