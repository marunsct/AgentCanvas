"""The shared flow engine (S32) and generated flow code: fixes from the review after the roadmap."""

from __future__ import annotations

import ast
from typing import Any

import pytest

from agentcanvas.adapters import supersteps as rt
from agentcanvas.adapters.crewai import codegen as crewai_codegen
from agentcanvas.adapters.google_adk import codegen as adk_codegen
from agentcanvas.adapters.openai_agents import codegen as openai_codegen
from agentcanvas.adapters.pydantic_ai import codegen as pydantic_codegen
from agentcanvas.ir.models import Workflow


def _setter(name: str) -> rt.StepFn:
    async def step(state: dict[str, Any], run: Any) -> dict[str, Any]:
        return {"log": [name], "n": 1 if name == "J" else 0}

    return step


async def _finish(flow: rt.Flow, **kw: Any) -> dict[str, Any]:
    return [ev async for ev in rt.run(flow, None, rt.Run, **kw)][-1]  # type: ignore[arg-type]


async def test_a_join_in_a_loop_waits_for_every_branch_on_each_pass() -> None:
    steps = {k: rt.Step(k, "core.set", _setter(k)) for k in "ABCD"}
    steps["J"] = rt.Step("J", "core.join", _setter("J"))
    steps["R"] = rt.Step("R", "core.router", None, cases=[("state.n < 2", "loop")], default="done")
    nxt: dict[str, Any] = {"A": ["B", "C"], "B": ["J"], "C": ["D"], "D": ["J"], "J": ["R"],
                           "R": {"loop": ["A"], "done": ["END"]}}  # fmt: skip
    flow = rt.Flow("x", steps, ["A"], nxt, {"log": ("append", []), "n": ("sum", 0)}, ["log"], recursion_limit=50)
    end = await _finish(flow, input={})
    assert end["outcome"] == "success"
    assert end["output"]["log"] == ["A", "B", "C", "D", "J"] * 2


async def test_a_join_after_a_path_not_taken_ends_the_run() -> None:
    steps = {k: rt.Step(k, "core.set", _setter(k)) for k in "AB"}
    steps |= {"R": rt.Step("R", "core.router", None, cases=[("true", "a")])}
    steps["J"] = rt.Step("J", "core.join", _setter("J"))
    nxt: dict[str, Any] = {"R": {"a": ["A"], "b": ["B"]}, "A": ["J"], "B": ["J"]}
    end = await _finish(rt.Flow("y", steps, ["R"], nxt, {"log": ("append", [])}, ["log"]), input={})
    assert end["outcome"] == "success"  # not a recursion_limit error
    assert end["output"]["log"] == ["A"]


async def test_a_resumed_round_does_not_run_the_next_round_twice() -> None:
    async def approve(state: dict[str, Any], run: rt.Run) -> dict[str, Any]:
        return {"log": ["H"], **run.approval(state, show="log", output="ok")}

    steps = {"A": rt.Step("A", "core.set", _setter("A")), "H": rt.Step("H", "core.approval", approve),
             "X": rt.Step("X", "core.set", _setter("X"))}  # fmt: skip
    flow = rt.Flow("x", steps, ["A", "H"], {"A": ["X"], "H": ["X"]},
                   {"log": ("append", []), "ok": ("replace", None), "n": ("sum", 0)}, ["log"])  # fmt: skip
    paused = (await _finish(flow, input={}))["paused"]
    end = await _finish(flow, resume=(rt.Paused.from_json(paused.to_json()), {"approved": True}))
    assert end["output"]["log"] == ["A", "H", "X"]


@pytest.mark.parametrize(
    ("answer", "count", "expected"),
    [
        ({"decisions": ["reject"]}, 1, ["reject"]),
        ({"decisions": ["approve", "yes"]}, 2, ["approve", "approve"]),
        ({"decisions": [{"approved": False}]}, 1, ["reject"]),
        ({"decisions": [{"type": "edit", "edited_action": {"args": {}}}]}, 2, ["edit", "reject"]),  # not copied
        ("no", 2, ["reject", "reject"]),
        ({"approved": "yes"}, 1, ["approve"]),
    ],
)
def test_tool_decisions_never_read_a_no_as_yes(answer: Any, count: int, expected: list[str]) -> None:
    assert [d["type"] for d in rt.decisions(answer, count)] == expected


def _wf(framework: str, name: str, node_id: str = "n_a", description: str = "") -> Workflow:
    return Workflow.model_validate({
        "id": "w", "name": name, "description": description, "framework": framework,
        "state": {"channels": [{"name": "x"}]},
        "nodes": [{"id": node_id, "type": "core.set", "name": "a", "config": {"values": {"x": "1"}}}],
        "edges": [{"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": node_id}}],
    })  # fmt: skip


HOSTILE = ['x"""; import os; print("INJECTED"); """', "Parse C:\\Users files", 'ends with "', "trailing \\"]
GENERATORS = [("pydantic_ai", pydantic_codegen), ("openai_agents", openai_codegen),
              ("crewai", crewai_codegen), ("google_adk", adk_codegen)]  # fmt: skip


@pytest.mark.parametrize(("framework", "codegen"), GENERATORS)
@pytest.mark.parametrize("name", HOSTILE)
def test_names_and_ids_stay_text_in_generated_code(framework: str, codegen: Any, name: str) -> None:
    src = codegen.generate(_wf(framework, name, node_id='n_a\nprint("ID-INJECTED")', description=name))
    tree = ast.parse(src)  # a name or description never breaks the module
    assert ast.get_docstring(tree, clean=False).startswith(name)  # type: ignore[union-attr]
    calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)]
    assert not [c for c in calls if c.func.id == "print"]  # type: ignore[union-attr] # the id stays in its comment


def test_a_name_cannot_end_the_mastra_header_comment() -> None:
    from agentcanvas.adapters.mastra import codegen as mastra_codegen

    src = mastra_codegen.generate(_wf("mastra", "Evil */ process.stdout.write('PWNED'); /*"))
    header = src.split("*/", 1)[0]  # the first `*/` closes the header comment
    assert "PWNED" in header


async def test_steps_side_by_side_run_at_the_same_time_on_the_same_values() -> None:
    """Review follow-up: a round's steps ran one after another, and a later one saw an earlier one's update."""
    import asyncio
    import time

    seen: dict[str, Any] = {}

    def waiter(name: str) -> rt.StepFn:
        async def step(state: dict[str, Any], run: Any) -> dict[str, Any]:
            seen[name] = state["n"]
            await asyncio.sleep(0.3)
            return {"log": [name], "n": 1}

        return step

    steps = {k: rt.Step(k, "core.set", waiter(k)) for k in "ABC"}
    flow = rt.Flow("p", steps, ["A", "B", "C"], {}, {"log": ("append", []), "n": ("sum", 0)}, ["log", "n"])
    started = time.perf_counter()
    end = await _finish(flow, input={})
    took = time.perf_counter() - started
    assert end["outcome"] == "success"
    assert took < 0.8  # three 0.3 s steps in parallel, not 0.9 s
    assert seen == {"A": 0, "B": 0, "C": 0}  # none saw a sibling's update
    assert end["output"] == {"log": ["A", "B", "C"], "n": 3}  # merged in canvas order


async def test_a_step_that_asks_keeps_its_siblings_finished_work() -> None:
    ran: list[str] = []

    async def asks(state: dict[str, Any], run: rt.Run) -> dict[str, Any]:
        return {"log": ["H"], **run.approval(state, show="log", output="ok")}

    async def works(state: dict[str, Any], run: Any) -> dict[str, Any]:
        ran.append("W")
        return {"log": ["W"]}

    async def also_asks(state: dict[str, Any], run: rt.Run) -> dict[str, Any]:
        return {"log": ["H2"], **run.approval(state, show="log", output="ok2")}

    steps = {"H": rt.Step("H", "core.approval", asks), "W": rt.Step("W", "core.set", works),
             "G": rt.Step("G", "core.approval", also_asks)}  # fmt: skip
    channels = {"log": ("append", []), "ok": ("replace", None), "ok2": ("replace", None)}
    flow = rt.Flow("q", steps, ["H", "W", "G"], {}, channels, ["log"])
    first = await _finish(flow, input={})
    assert first["outcome"] == "interrupt" and first["next"] == ["H"]
    paused = rt.Paused.from_json(first["paused"].to_json())
    assert paused.frontier == ["H", "G"]  # the other step that asked runs again; the finished one does not
    assert paused.values["log"] == ["W"]
    second = await _finish(flow, resume=(paused, {"approved": True}))
    assert second["outcome"] == "interrupt" and second["next"] == ["G"]
    third = await _finish(flow, resume=(rt.Paused.from_json(second["paused"].to_json()), {"approved": True}))
    assert third["outcome"] == "success" and ran == ["W"]  # W ran once
    assert sorted(third["output"]["log"]) == ["H", "H2", "W"]


async def test_the_first_failing_step_ends_the_run_after_the_earlier_ones_merge() -> None:
    async def boom(state: dict[str, Any], run: Any) -> dict[str, Any]:
        raise ValueError("bad")

    steps = {"A": rt.Step("A", "core.set", _setter("A")), "B": rt.Step("B", "core.set", boom),
             "C": rt.Step("C", "core.set", _setter("C"))}  # fmt: skip
    flow = rt.Flow("e", steps, ["A", "B", "C"], {}, {"log": ("append", []), "n": ("sum", 0)}, ["log"])
    events = [ev async for ev in rt.run(flow, None, rt.Run, input={})]  # type: ignore[arg-type]
    assert events[-1]["type"] == "RUN_ERROR" and "bad" in events[-1]["message"]
    finished = [(e["canvas_node_id"], e["status"]) for e in events if e["type"] == "STEP_FINISHED"]
    assert finished == [("A", "ok"), ("B", "error")]
