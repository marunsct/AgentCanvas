"""Second round of fixes from the 2026-10-18 deep review (see CHANGELOG v0.29.0)."""

from __future__ import annotations

from agentcanvas.runtime import paused as paused_runs
from agentcanvas.runtime.paused import DatabasePaused


async def test_paused_runs_live_with_their_conversation(app, api_client):
    """H7: paused runs of frameworks without checkpoints were kept in one process's memory, so another replica or a
    restarted server could not continue them. They are now kept on the thread row."""
    from agentcanvas.db.models import Thread

    wf = (await api_client.post("/v1/projects/prj_default/workflows", json={"name": "Paused"})).json()
    async with app.state.db.sessionmaker() as s:
        s.add(Thread(id="th_paused", workflow_id=wf["id"]))
        await s.commit()
    one, other = DatabasePaused(app.state.db), DatabasePaused(app.state.db)  # two replicas
    await one.put("th_paused", '{"step": 3}')
    assert await other.get("th_paused") == '{"step": 3}'
    async with app.state.db.sessionmaker() as s:
        row = await s.get(Thread, "th_paused")
        assert row is not None and row.paused_state == '{"step": 3}'
    await other.drop("th_paused")
    assert await one.get("th_paused") is None
    await one.put("th_nowhere", "{}")  # no row (a run outside the runner): this process keeps it
    assert await one.get("th_nowhere") == "{}"


async def test_the_server_installs_the_database_store(app):
    assert isinstance(paused_runs.store(), DatabasePaused)
    await paused_runs.save("th_x", {"a": 1})
    assert await paused_runs.load("th_x") == {"a": 1}


def _setter(name: str):
    async def step(state, run):
        return {"log": [name], "n": 1 if name == "J" else 0}

    return step


async def _openai_finish(flow, **kw):
    from agentcanvas.adapters.openai_agents import runtime as rt

    return [ev async for ev in rt.run(flow, None, **kw)][-1]  # type: ignore[arg-type]


async def test_the_openai_engine_waits_for_every_branch_of_a_join_in_a_loop():
    """H8: the OpenAI Agents SDK runtime kept the old superstep engine; a join inside a loop fired early."""
    from agentcanvas.adapters.openai_agents import runtime as rt

    steps = {k: rt.Step(k, "core.set", _setter(k)) for k in "ABCD"}
    steps["J"] = rt.Step("J", "core.join", _setter("J"))
    steps["R"] = rt.Step("R", "core.router", None, cases=[("state.n < 2", "loop")], default="done")
    nxt = {"A": ["B", "C"], "B": ["J"], "C": ["D"], "D": ["J"], "J": ["R"], "R": {"loop": ["A"], "done": ["END"]}}
    flow = rt.Flow("x", steps, ["A"], nxt, {"log": ("append", []), "n": ("sum", 0)}, ["log"], recursion_limit=50)
    end = await _openai_finish(flow, input={})
    assert end["outcome"] == "success" and end["output"]["log"] == ["A", "B", "C", "D", "J"] * 2


async def test_the_openai_engine_ends_a_run_whose_join_lost_a_branch():
    from agentcanvas.adapters.openai_agents import runtime as rt

    steps = {k: rt.Step(k, "core.set", _setter(k)) for k in "AB"}
    steps["R"] = rt.Step("R", "core.router", None, cases=[("true", "a")])
    steps["J"] = rt.Step("J", "core.join", _setter("J"))
    flow = rt.Flow("y", steps, ["R"], {"R": {"a": ["A"], "b": ["B"]}, "A": ["J"], "B": ["J"]}, {"log": ("append", [])},
                   ["log"])  # fmt: skip
    end = await _openai_finish(flow, input={})
    assert end["outcome"] == "success" and end["output"]["log"] == ["A"]  # was a recursion_limit error


async def test_the_openai_engine_resumes_without_running_the_next_round_twice():
    from agentcanvas.adapters.openai_agents import runtime as rt

    async def ask(state, run):
        if run._answer is rt._MISSING:
            raise rt.Interrupted({"kind": "approval"})
        return {"log": ["H"]}

    steps = {"A": rt.Step("A", "core.set", _setter("A")), "H": rt.Step("H", "core.approval", ask),
             "X": rt.Step("X", "core.set", _setter("X"))}  # fmt: skip
    flow = rt.Flow("x", steps, ["A", "H"], {"A": ["X"], "H": ["X"]}, {"log": ("append", []), "n": ("sum", 0)}, ["log"])
    paused = (await _openai_finish(flow, input={}))["paused"]
    end = await _openai_finish(flow, resume=(rt.Paused.from_json(paused.to_json()), True))
    assert end["output"]["log"] == ["A", "H", "X"]  # X ran twice before


def _edge(i: int, src: str, dst: str, port: str | None = None) -> dict:
    return {"id": f"e{i}", "kind": "flow", "source": {"node": src, **({"port": port} if port else {})},
            "target": {"node": dst}}  # fmt: skip


async def test_the_echo_engine_waits_for_every_branch_of_a_join_in_a_loop():
    """H8: Echo kept the old superstep engine too."""
    from agentcanvas.adapters.echo import EchoAdapter
    from agentcanvas.ir.models import Workflow
    from agentcanvas.runtime.types import RunSpec

    def setter(node_id: str, name: str) -> dict:
        return {"id": node_id, "type": "core.set", "name": name, "config": {"values": {"log": name}}}

    wf = Workflow.model_validate({
        "id": "wf_loop", "name": "Loop", "framework": "echo", "settings": {"recursion_limit": 50},
        "state": {"channels": [{"name": "log", "type": {"type": "array"}, "reducer": "append", "default": []}]},
        "nodes": [setter("n_a", "a"), setter("n_b", "b"), setter("n_c", "c"), setter("n_d", "d"),
                  {"id": "n_j", "type": "core.join", "name": "j", "config": {}},
                  {"id": "n_r", "type": "core.router", "name": "r",
                   "config": {"cases": [{"when": "size(state.log) < 8", "route": "loop"}], "default": "done"}}],
        "edges": [_edge(1, "START", "n_a"), _edge(2, "n_a", "n_b"), _edge(3, "n_a", "n_c"), _edge(4, "n_c", "n_d"),
                  _edge(5, "n_b", "n_j"), _edge(6, "n_d", "n_j"), _edge(7, "n_j", "n_r"),
                  _edge(8, "n_r", "n_a", "route:loop"), _edge(9, "n_r", "END", "route:done")],
    })  # fmt: skip
    echo = EchoAdapter()
    spec = RunSpec(workflow=wf, thread_id="th_echo_loop", run_id="run_1", input={})
    events = [ev async for ev in echo.execute(spec, echo.default_resources())]
    assert events[-1]["outcome"] == "success", events[-1]
    assert events[-1]["output"]["log"] == ["a", "b", "c", "d"] * 2  # j ran once per pass, after both branches
    joins = [ev for ev in events if ev["type"] == "STEP_FINISHED" and ev["canvas_node_id"] == "n_j"]
    assert len(joins) == 2
