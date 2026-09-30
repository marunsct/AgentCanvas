"""Sprint 5 node catalog: middleware widgets, structured output, Deep Agent, human input, map and join.

Every behaviour is checked in both modes (interpreter and generated code) — the WYSIWYG guarantee.
"""

from __future__ import annotations

import importlib.util
import itertools
import re
import sys
from typing import Any, ClassVar

import pytest
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from agentcanvas.adapters.langchain.codegen import generate
from agentcanvas.adapters.langchain.interpreter import build_graph
from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.adapters.langchain.plan import lower
from agentcanvas.ir.models import Workflow
from agentcanvas.testing import DemoChatModel, ToolFakeModel, scripted, tool_call
from agentcanvas.validation import validate

MESSAGES = {"name": "messages", "type": {"type": "array"}, "reducer": "add_messages"}


def ch(name: str, type_: str = "string", reducer: str = "replace", **kw: Any) -> dict[str, Any]:
    return {"name": name, "type": {"type": type_}, "reducer": reducer, **kw}


def workflow(
    *,
    channels: list[dict],
    nodes: list[tuple[str, str, dict]],  # (id=name, type, config)
    flow: list[tuple[str, str] | tuple[str, str, str]],  # (source, target[, source port])
    resources: list[tuple[str, str, dict]] = (),  # type: ignore[assignment]
    wiring: list[tuple[str, str, str]] = (),  # type: ignore[assignment]  # (resource, target, port)
    context: list[dict] | None = None,
) -> Workflow:
    edges: list[dict[str, Any]] = []
    for i, f in enumerate(flow):
        src: dict[str, Any] = {"node": f[0]}
        if len(f) == 3:
            src["port"] = f[2]
        edges.append({"id": f"f{i}", "kind": "flow", "source": src, "target": {"node": f[1]}})
    for i, (r, t, port) in enumerate(wiring):
        edges.append(
            {"id": f"w{i}", "kind": "wiring", "source": {"node": r}, "target": {"node": t, "port": port}, "order": i}
        )
    return Workflow.model_validate(
        {
            "ir_version": "1.1",
            "id": "wf_catalog",
            "name": "catalog",
            "state": {"channels": channels},
            "context": {"fields": context or []},
            "resources": [{"id": i, "type": t, "name": i, "config": c} for i, t, c in resources],
            "nodes": [{"id": i, "type": t, "name": i, "config": c} for i, t, c in nodes],
            "edges": edges,
        }
    )


def errors(wf: Workflow) -> set[str]:
    return {d.code for d in validate(wf) if d.severity == "error"}


def codes(wf: Workflow) -> set[str]:
    return {d.code for d in validate(wf)}


_modules = itertools.count(1)


def compiled(wf: Workflow, tmp_path) -> Any:
    """Import the generated module (a fresh module name each time)."""
    name = f"gen_catalog_{next(_modules)}"
    path = tmp_path / f"{name}.py"
    path.write_text(generate(wf))
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


async def both(wf: Workflow, tmp_path, make_resources, run) -> tuple[Any, Any]:
    """Run `run(graph)` against the interpreted and the generated graph (fresh resources for each)."""
    assert not errors(wf), validate(wf)
    mod = compiled(wf, tmp_path)
    interpreted = await run(build_graph(wf, make_resources(), checkpointer=InMemorySaver(), store=InMemoryStore()))
    generated = await run(mod.build_graph(make_resources(), checkpointer=InMemorySaver(), store=InMemoryStore()))
    return interpreted, generated


def cfg(thread: str = "t1") -> dict:
    return {"configurable": {"thread_id": thread}}


class RecordingModel(ToolFakeModel):
    """Scripted fake that remembers what it was asked (to see what middleware changed)."""

    seen: ClassVar[list[list[BaseMessage]]] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        type(self).seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


class BrokenModel(ToolFakeModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise ConnectionError("provider is down")


def agent_wf(*, resources, wiring, config=None, channels=None) -> Workflow:
    return workflow(
        channels=channels or [MESSAGES],
        resources=[("model", "model.chat", {"model": "fake:scripted"}), *resources],
        nodes=[("agent", "core.agent", config or {})],
        flow=[("START", "agent"), ("agent", "END")],
        wiring=[("model", "agent", "model"), *wiring],
    )


def last_text(out: dict) -> str:
    return str(out["messages"][-1].content)


# ------------------------------------------------------------------------------------------ middleware ----
@pytest.mark.asyncio
async def test_pii_middleware_redacts_model_input(tmp_path):
    wf = agent_wf(
        resources=[("pii", "mw.pii", {"pii_type": "email", "strategy": "redact"})],
        wiring=[("pii", "agent", "middleware")],
    )

    def res():
        RecordingModel.seen = []
        return Resources(overrides={"model": RecordingModel(messages=iter([AIMessage("ok")]), disable_streaming=True)})

    async def run(graph):
        await graph.ainvoke({"messages": [("user", "mail me at jane@example.com")]}, cfg())
        return str(RecordingModel.seen[-1][-1].content)

    a, b = await both(wf, tmp_path, res, run)
    assert a == b
    assert "jane@example.com" not in a and "[REDACTED_EMAIL]" in a


@pytest.mark.asyncio
async def test_model_call_limit_ends_the_agent(tmp_path):
    wf = agent_wf(
        resources=[
            ("limit", "mw.model_call_limit", {"run_limit": 1}),
            ("echo", "tool.python", {"import_path": "examples.tools:send_reply"}),
        ],
        wiring=[("limit", "agent", "middleware"), ("echo", "agent", "tools")],
    )

    def res():
        return Resources(overrides={"model": scripted(tool_call("send_reply", {"text": "A1"}), "never reached")})

    async def run(graph):
        out = await graph.ainvoke({"messages": [("user", "where is A1?")]}, cfg())
        return last_text(out)

    a, b = await both(wf, tmp_path, res, run)
    assert a == b and "limit" in a.lower() and "never reached" not in a


@pytest.mark.asyncio
async def test_model_fallback_uses_wired_models_in_order(tmp_path):
    wf = agent_wf(
        resources=[
            ("backup", "model.chat", {"model": "fake:scripted"}),
            ("fallback", "mw.model_fallback", {}),
        ],
        wiring=[("backup", "fallback", "models"), ("fallback", "agent", "middleware")],
    )

    def res():
        return Resources(
            overrides={"model": BrokenModel(messages=iter([])), "backup": scripted("answer from the backup model")}
        )

    async def run(graph):
        return last_text(await graph.ainvoke({"messages": [("user", "hi")]}, cfg()))

    a, b = await both(wf, tmp_path, res, run)
    assert a == b == "answer from the backup model"
    code = generate(wf)
    assert code.index("backup = resources.model(") < code.index("fallback = resources.middleware(")


def test_middleware_order_follows_edge_order():
    wf = agent_wf(
        resources=[
            ("retry", "mw.model_retry", {"max_retries": 1}),
            ("limit", "mw.model_call_limit", {"run_limit": 3}),
        ],
        wiring=[("limit", "agent", "middleware"), ("retry", "agent", "middleware")],
    )
    assert lower(wf).nodes[0].wired["middleware"] == ["limit", "retry"]
    assert "middleware=[limit,retry]" in re.sub(r"\s", "", generate(wf)).replace(",]", "]")


def test_middleware_rules():
    dup = agent_wf(
        resources=[("a", "mw.model_call_limit", {}), ("b", "mw.model_call_limit", {})],
        wiring=[("a", "agent", "middleware"), ("b", "agent", "middleware")],
    )
    assert "E035" in errors(dup)
    two_pii = agent_wf(
        resources=[("a", "mw.pii", {"pii_type": "email"}), ("b", "mw.pii", {"pii_type": "url"})],
        wiring=[("a", "agent", "middleware"), ("b", "agent", "middleware")],
    )
    assert "E035" not in errors(two_pii)
    no_model = agent_wf(resources=[("fb", "mw.model_fallback", {})], wiring=[("fb", "agent", "middleware")])
    assert "E001" in errors(no_model)  # fallback needs at least one wired model
    wrong = agent_wf(resources=[("fb", "mw.todo", {})], wiring=[("fb", "agent", "tools")])
    assert "E002" in errors(wrong)  # a Middleware cannot go into a Tool port


# ---------------------------------------------------------------------------------- structured output ----
TRIAGE_SCHEMA = {
    "title": "Triage",
    "type": "object",
    "properties": {"urgency": {"type": "string", "enum": ["low", "high"]}},
    "required": ["urgency"],
}


@pytest.mark.asyncio
async def test_agent_structured_output_writes_structured_response(tmp_path):
    wf = agent_wf(
        resources=[],
        wiring=[],
        config={"system_prompt": "Triage the ticket.", "response_format": TRIAGE_SCHEMA},
        channels=[MESSAGES, ch("structured_response", "object")],
    )

    def res():
        return Resources(overrides={"model": scripted(tool_call("Triage", {"urgency": "high"}))})

    async def run(graph):
        return (await graph.ainvoke({"messages": [("user", "site down")]}, cfg()))["structured_response"]

    a, b = await both(wf, tmp_path, res, run)
    assert a == b == {"urgency": "high"}


def test_structured_output_rules():
    missing = agent_wf(resources=[], wiring=[], config={"response_format": TRIAGE_SCHEMA})
    assert "E021" in errors(missing)  # structured_response channel not declared
    bad = agent_wf(
        resources=[],
        wiring=[],
        config={"response_format": {"type": "object", "properties": {"x": {"type": "nope"}}}},
        channels=[MESSAGES, ch("structured_response", "object")],
    )
    assert "E004" in errors(bad)
    weak = agent_wf(
        resources=[],
        wiring=[],
        config={"response_format": TRIAGE_SCHEMA},
        channels=[MESSAGES, ch("structured_response", "object")],
    )
    weak.resource("model").config["model"] = "fake:no-tools"  # type: ignore[union-attr]
    assert "E030" in errors(weak)


# ----------------------------------------------------------------------------------------- deep agent ----
def deep_wf(*, config=None, extra_resources=(), extra_wiring=(), context=True) -> Workflow:
    return workflow(
        channels=[MESSAGES],
        resources=[
            ("model", "model.chat", {"model": "fake:scripted"}),
            ("memories", "backend.store", {"mount": "/memories/", "scope": "user"}),
            *extra_resources,
        ],
        nodes=[("researcher_agent", "agent.deep", {"system_prompt": "Research and take notes.", **(config or {})})],
        flow=[("START", "researcher_agent"), ("researcher_agent", "END")],
        wiring=[("model", "researcher_agent", "model"), ("memories", "researcher_agent", "backend"), *extra_wiring],
        context=[{"name": "user_id", "type": {"type": "string"}, "source": "user"}] if context else None,
    )


@pytest.mark.asyncio
async def test_deep_agent_writes_long_term_memory_for_the_user(tmp_path):
    """M3-T4 AC: a file under /memories/ lands in the store namespace of the run's user, not in state."""
    wf = deep_wf()

    def res():
        return Resources(
            overrides={
                "model": scripted(
                    tool_call("write_file", {"file_path": "/memories/notes.md", "content": "likes tea"}),
                    tool_call("write_file", {"file_path": "/scratch.md", "content": "draft"}, "call_2"),
                    "Saved.",
                )
            }
        )

    async def run(graph):
        out = await graph.ainvoke({"messages": [("user", "remember I like tea")]}, cfg(), context={"user_id": "usr_a"})
        store = graph.store
        mine = await store.asearch(("memories", "user", "usr_a"))
        other = await store.asearch(("memories", "user", "usr_b"))
        return last_text(out), [(i.key, i.value["content"]) for i in mine], other

    a, b = await both(wf, tmp_path, res, run)
    assert a == b
    text, mine, other = a
    assert text == "Saved." and other == []
    assert [k for k, _ in mine] == ["/notes.md"] and "likes tea" in str(mine[0][1])


@pytest.mark.asyncio
async def test_deep_agent_delegates_to_a_subagent(tmp_path):
    wf = deep_wf(
        config={"planning": False},
        extra_resources=[
            ("cheap", "model.chat", {"model": "fake:scripted"}),
            ("scout", "agent.subagent", {"description": "Finds facts", "system_prompt": "Find facts."}),
        ],
        extra_wiring=[("cheap", "scout", "model"), ("scout", "researcher_agent", "subagents")],
    )

    def res():
        return Resources(
            overrides={
                "model": scripted(
                    tool_call("task", {"description": "find the capital of France", "subagent_type": "scout"}),
                    "Paris, says the scout.",
                ),
                "cheap": scripted("The capital is Paris."),
            }
        )

    async def run(graph):
        out = await graph.ainvoke({"messages": [("user", "capital of France?")]}, cfg(), context={"user_id": "u1"})
        tool_msgs = [m.content for m in out["messages"] if m.type == "tool"]
        return last_text(out), tool_msgs

    a, b = await both(wf, tmp_path, res, run)
    assert a == b
    assert a[0] == "Paris, says the scout." and "The capital is Paris." in str(a[1])


@pytest.mark.asyncio
async def test_deep_agent_approval_on_builtin_tool(tmp_path):
    wf = deep_wf(config={"approve_tools": ["write_file"], "planning": False})

    def res():
        return Resources(
            overrides={
                "model": scripted(tool_call("write_file", {"file_path": "/memories/a.md", "content": "x"}), "Done.")
            }
        )

    async def run(graph):
        await graph.ainvoke({"messages": [("user", "save")]}, cfg(), context={"user_id": "u1"})
        state = await graph.aget_state(cfg())
        pending = state.interrupts[0].value["action_requests"][0]["name"]
        out = await graph.ainvoke(
            Command(resume={"decisions": [{"type": "approve"}]}), cfg(), context={"user_id": "u1"}
        )
        return pending, last_text(out)

    a, b = await both(wf, tmp_path, res, run)
    assert a == b == ("write_file", "Done.")


def test_deep_agent_rules():
    assert not errors(deep_wf(config={"approve_tools": ["write_file", "task"]}))  # built-in tools are fine
    assert "E033" in errors(deep_wf(config={"approve_tools": ["send_email"]}))
    assert "E071" in errors(deep_wf(context=False))  # per-user folder without a user context field
    bad_perm = deep_wf(config={"permissions": [{"operations": ["write"], "paths": ["../etc/*"]}]})
    assert "E034" in errors(bad_perm)
    ok_perm = deep_wf(config={"permissions": [{"operations": ["write"], "paths": ["/memories/**"], "mode": "deny"}]})
    assert "E034" not in errors(ok_perm)
    summ = deep_wf(
        extra_resources=[("summ", "mw.summarization", {})],
        extra_wiring=[("model", "summ", "model"), ("summ", "researcher_agent", "middleware")],
    )
    assert "E035" in errors(summ)  # Deep Agents summarize on their own
    todo = deep_wf(extra_resources=[("todo", "mw.todo", {})], extra_wiring=[("todo", "researcher_agent", "middleware")])
    assert "E035" in errors(todo)  # planning is on by default
    two = deep_wf(
        extra_resources=[("more", "backend.store", {"mount": "/memories/", "scope": "workflow"})],
        extra_wiring=[("more", "researcher_agent", "backend")],
    )
    assert "E036" in errors(two)
    sub = deep_wf(
        extra_resources=[("s", "agent.subagent", {"description": "  ", "system_prompt": "x"})],
        extra_wiring=[("s", "researcher_agent", "subagents"), ("s", "researcher_agent", "subagents")],
    )
    assert "E032" in errors(sub)
    assert "I010" in codes(deep_wf())  # Deep Agents are marked beta


# ---------------------------------------------------------------------------------------- human input ----
@pytest.mark.asyncio
async def test_human_input_validates_and_reasks(tmp_path):
    schema = {"type": "object", "properties": {"plan": {"enum": ["basic", "pro"]}}, "required": ["plan"]}
    wf = workflow(
        channels=[ch("customer"), ch("choice", "object")],
        nodes=[
            (
                "ask",
                "core.human_input",
                {"prompt": "Plan for {{ state.customer }}?", "response_schema": schema, "output": "choice"},
            )
        ],
        flow=[("START", "ask"), ("ask", "END")],
    )

    async def run(graph):
        await graph.ainvoke({"customer": "ACME"}, cfg())
        first = (await graph.aget_state(cfg())).interrupts[0].value
        await graph.ainvoke(Command(resume={"plan": "gold"}), cfg())
        second = (await graph.aget_state(cfg())).interrupts[0].value
        out = await graph.ainvoke(Command(resume={"plan": "pro"}), cfg())
        return first, second.get("error"), out["choice"]

    a, b = await both(wf, tmp_path, Resources, run)
    assert a == b
    first, error, choice = a
    assert first["kind"] == "input" and first["prompt"] == "Plan for ACME?" and first["canvas_node_id"] == "ask"
    assert "gold" in error and choice == {"plan": "pro"}


# ------------------------------------------------------------------------------------------------ map ----
def map_wf(*, worker_output: str = "notes", empty_target: str | None = "summary") -> Workflow:
    flow: list = [
        ("START", "fan"),
        ("fan", "research", "each"),
        ("research", "summary"),
        ("summary", "END"),
    ]
    if empty_target:
        flow.append(("fan", empty_target, "empty"))
    return workflow(
        channels=[
            ch("topics", "array"),
            ch("topic", io="private"),
            ch("question"),
            ch("notes", "array", "append"),
            ch("done"),
        ],
        resources=[("model", "model.chat", {"model": "fake:scripted"})],
        nodes=[
            ("fan", "core.map", {"items": "topics", "item": "topic", "carry": ["question"]}),
            ("research", "core.llm", {"prompt": "{{ state.question }}: {{ state.topic }}", "output": worker_output}),
            ("summary", "core.set", {"values": {"done": "{{ state.notes | length }} notes"}}),
        ],
        flow=flow,
        wiring=[("model", "research", "model")],
    )


@pytest.mark.asyncio
async def test_map_runs_the_worker_once_per_item(tmp_path):
    wf = map_wf()

    def res():
        return Resources(overrides={"model": DemoChatModel()})

    async def run(graph):
        out = await graph.ainvoke({"topics": ["tea", "coffee", "cocoa"], "question": "Facts"}, cfg())
        empty = await graph.ainvoke({"topics": [], "question": "Facts"}, cfg("t2"))
        return sorted(out["notes"]), out["done"], empty["done"]

    a, b = await both(wf, tmp_path, res, run)
    assert a == b
    notes, done, empty_done = a
    assert len(notes) == 3 and all(n.startswith("Demo reply") for n in notes)
    assert any("Facts: cocoa" in n for n in notes)
    assert done == "3 notes" and empty_done == "0 notes"


def test_map_rules():
    assert not errors(map_wf())
    assert "E022" in errors(map_wf(worker_output="done"))  # parallel workers writing a replace channel
    no_each = map_wf()
    no_each.edges = [e for e in no_each.edges if e.source.port != "each"]
    assert "E015" in errors(no_each)
    bad_port = map_wf()
    bad_port.edges[1].source.port = "every"
    assert "E006" in errors(bad_port)


# ----------------------------------------------------------------------------------------------- join ----
def join_wf() -> Workflow:
    return workflow(
        channels=[ch("log", "array", "append"), ch("a"), ch("b")],
        nodes=[
            ("left", "core.set", {"values": {"a": "A", "log": "left"}}),
            ("right", "core.set", {"values": {"log": "right"}}),
            ("right_more", "core.set", {"values": {"b": "B", "log": "right_more"}}),
            ("wait", "core.join", {}),
            ("after", "core.set", {"values": {"log": "after {{ state.a }}{{ state.b }}"}}),
        ],
        flow=[
            ("START", "left"),
            ("START", "right"),
            ("right", "right_more"),
            ("left", "wait"),
            ("right_more", "wait"),
            ("wait", "after"),
            ("after", "END"),
        ],
    )


@pytest.mark.asyncio
async def test_join_waits_for_every_branch(tmp_path):
    async def run(graph):
        return (await graph.ainvoke({}, cfg()))["log"]

    a, b = await both(join_wf(), tmp_path, Resources, run)
    assert a == b
    assert a.count("after AB") == 1 and a[-1] == "after AB"  # ran once, after both branches
    assert 'builder.add_edge(["left", "right_more"], "wait")' in generate(join_wf())


def test_join_rules():
    assert not errors(join_wf())
    lonely = workflow(
        channels=[ch("a")],
        nodes=[("one", "core.set", {"values": {"a": "x"}}), ("wait", "core.join", {})],
        flow=[("START", "one"), ("one", "wait"), ("wait", "END")],
    )
    assert "W013" in codes(lonely)
    racy = join_wf()
    racy.edges = [e for e in racy.edges if not (e.source.node == "START" and e.target.node == "left")]
    racy.edges.append(
        racy.edges[0].model_copy(
            update={"id": "fs", "target": racy.edges[0].target.model_copy(update={"node": "wait"})}
        )
    )
    assert "E016" in errors(racy)


@pytest.mark.asyncio
async def test_research_template_equivalence(tmp_path):
    import json
    import pathlib

    path = pathlib.Path(__file__).parents[1] / "agentcanvas/registry/templates/research_brief.ir.json"
    wf = Workflow.model_validate(json.loads(path.read_text()))

    def res():
        demo = DemoChatModel()
        return Resources(overrides={"fast_model": demo, "smart_model": demo})

    async def run(graph):
        out = await graph.ainvoke(
            {"question": "Hot drinks", "topics": ["tea", "coffee"]}, cfg(), context={"user_id": "usr_x"}
        )
        items = await graph.store.asearch(("memories", "user", "usr_x"))
        return [m.content for m in out["messages"] if m.type != "tool"], [i.key for i in items]

    a, b = await both(wf, tmp_path, res, run)
    assert a == b and a[1] == ["/findings.md"]
