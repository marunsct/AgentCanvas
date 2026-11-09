"""Canaries for upstream behaviour we work around. A strict xfail turns red when upstream fixes the bug,
telling us to remove the workaround (doc 14 D16: no beta/buggy upstream behaviour on the critical path)."""

import pytest
from langgraph.errors import NodeError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from typing_extensions import TypedDict


class S(TypedDict, total=False):
    out: str


async def boom(state):
    raise ValueError("boom")


def handler(state, error: NodeError) -> Command:
    return Command(goto="recover", update={"out": "handled"})


async def recover(state):
    return {}


def graph():
    b = StateGraph(S)
    b.add_node("n", boom, error_handler=handler, destinations=("recover",))
    b.add_node("recover", recover)
    b.add_edge(START, "n")
    b.add_edge("n", END)
    b.add_edge("recover", END)
    return b.compile()


async def test_error_handler_works_with_updates_stream():
    parts = [p async for p in graph().astream({}, stream_mode="updates")]
    assert parts[-1] == {"recover": None} or any("n" in p for p in parts)


@pytest.mark.parametrize("subgraphs", [False, True])
async def test_upstream_error_handler_with_every_stream_mode(subgraphs):
    """FAQ P-15, fixed in langgraph 1.2.20: error handlers ran only with "updates"/"tasks", so workflows with error
    routes streamed no tokens. The adapter now streams every mode for them; this fails if the bug comes back."""
    modes = ["tasks", "updates", "messages", "custom"]
    parts = [p async for p in graph().astream({}, stream_mode=modes, subgraphs=subgraphs)]  # used to raise "boom"
    assert any("recover" in str(p) for p in parts)


def test_upstream_savers_do_not_implement_copy_thread():
    """FAQ P-14: we ship a generic copy; switch to acopy_thread when savers implement it."""
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    assert all(
        c.acopy_thread is BaseCheckpointSaver.acopy_thread
        for c in (InMemorySaver, AsyncPostgresSaver, AsyncSqliteSaver)
    )
