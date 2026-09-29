"""ADR 0015: every node callable is async, so LangGraph per-node timeouts work on all node types."""

import inspect

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, TimeoutPolicy
from typing_extensions import TypedDict

from agentcanvas.runtime import nodes as ac
from agentcanvas.testing import scripted


class S(TypedDict, total=False):
    text: str
    ok: bool
    out: str


BUILDERS = {
    "llm": lambda: ac.llm_node(model=scripted("hi"), prompt="Say hi to {{ state.text }}", output="out"),
    "approval": lambda: ac.approval_node(node_id="n1", show="text", output="ok"),
    "set": lambda: ac.set_node(values={"out": "x={{ state.text }}"}),
    "script_sync": lambda: ac.script_node(import_path="examples.scripts:shout"),
    "passthrough": lambda: ac.passthrough,
}


@pytest.mark.parametrize("kind", sorted(BUILDERS))
def test_node_callables_are_async(kind):
    assert inspect.iscoroutinefunction(BUILDERS[kind]())


@pytest.mark.parametrize("kind", sorted(BUILDERS))
@pytest.mark.asyncio
async def test_timeout_policy_accepted_for_every_node_type(kind):
    b = StateGraph(S)
    b.add_node("n", BUILDERS[kind](), timeout=TimeoutPolicy(run_timeout=5))
    b.add_edge(START, "n")
    b.add_edge("n", END)
    graph = b.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": kind}}
    await graph.ainvoke({"text": "bob"}, cfg)
    if (await graph.aget_state(cfg)).interrupts:  # approval node pauses; resume must work too
        await graph.ainvoke(Command(resume={"approved": True}), cfg)
    assert (await graph.aget_state(cfg)).next == ()


@pytest.mark.asyncio
async def test_sync_script_can_interrupt_inside_worker_thread():
    b = StateGraph(S)
    b.add_node("ask", ac.script_node(import_path="examples.scripts:ask_human"), timeout=TimeoutPolicy(run_timeout=5))
    b.add_edge(START, "ask")
    b.add_edge("ask", END)
    graph = b.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "t"}}
    await graph.ainvoke({"text": "q"}, cfg)
    assert (await graph.aget_state(cfg)).interrupts[0].value == {"question": "q"}
    final = await graph.ainvoke(Command(resume="yes"), cfg)
    assert final["out"] == "yes"
