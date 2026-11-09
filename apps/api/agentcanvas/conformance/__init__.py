"""Adapter conformance kit (S20, spec doc 12 §C.8): the same workflows must behave the same on every framework.

Each case is a small workflow, an input, scripted model replies (the run `mocks` saved test cases use, so no model is
called) and the answers a person gives when the run pauses. `run_case` drives an adapter's `execute` through every
pause; `check` compares what happened with what the case expects:

* **C-FLOW**: sequence, three-way paths, parallel branches joined, a bounded loop: same final values and the same
  steps run (as a multiset, so parallel order does not matter).
* **C-AGENT**: an AI step's reply lands in its field and can pick a path. Tool-calling agents join the kit when
  agent design arrives (S21).
* **C-HITL**: approve, reject, approve with an edit, a question re-asked after an invalid answer; the pauses come in
  the expected order with the asking step named.
* **C-EVENTS** (checked on every run of every case): each step that starts finishes, names a block of the workflow,
  and the run ends with exactly one RUN_FINISHED or RUN_ERROR carrying the outcome, interrupts and output.

`tests/test_conformance.py` runs every case on every adapter (CI's api job), and an adapter must pass all cases its
manifest says it can run.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.registry.adapters import AdapterManifest
from agentcanvas.runtime.types import ResumeSpec, RunSpec

MODEL = {"id": "r_model", "type": "model.chat", "name": "model", "config": {"model": "anthropic:claude-haiku-4-5"}}


@dataclass
class Case:
    id: str
    suite: str  # C-FLOW | C-AGENT | C-HITL
    title: str
    workflow: dict[str, Any]
    input: dict[str, Any]
    expect_output: dict[str, Any]
    expect_steps: dict[str, int]  # node id -> times it ran to completion
    mocks: dict[str, list[Any]] = field(default_factory=dict)
    answers: list[Any] = field(default_factory=list)  # one per pause, in order
    expect_pauses: list[tuple[str, str]] = field(default_factory=list)  # (kind, node id) per pause
    exact_output: bool = False  # the output must hold exactly `expect_output`'s fields, nothing more

    def types(self) -> set[str]:
        return {n["type"] for n in self.workflow["nodes"]} | {r["type"] for r in self.workflow.get("resources", [])}


@dataclass
class Result:
    case: Case
    runs: list[list[dict[str, Any]]]  # events of each execute() call
    problems: list[str]

    @property
    def passed(self) -> bool:
        return not self.problems


# ---------------------------------------------------------------- builders ----
def _flow(src: str, dst: str, port: str | None = None) -> dict[str, Any]:
    source: dict[str, Any] = {"node": src} | ({"port": port} if port else {})
    return {"id": f"e_{src}_{dst}_{port or ''}".lower().replace(":", "_"), "kind": "flow", "source": source,
            "target": {"node": dst}}  # fmt: skip


def _wire(res: str, node: str) -> dict[str, Any]:
    return {
        "id": f"w_{res}_{node}",
        "kind": "wiring",
        "source": {"node": res},
        "target": {"node": node, "port": "model"},
    }


def _node(id_: str, type_: str, **config: Any) -> dict[str, Any]:
    return {"id": id_, "type": type_, "name": id_.removeprefix("n_"), "config": config}


def _set(id_: str, **values: str) -> dict[str, Any]:
    return _node(id_, "core.set", values=values)


def _wf(name: str, channels: list[str], nodes: list[dict[str, Any]], edges: list[dict[str, Any]], **kw: Any) -> dict:
    return {
        "id": f"wf_conf_{name}", "name": f"Conformance {name}",
        "state": {"channels": [{"name": c, "default": ""} for c in channels]},
        "nodes": nodes, "edges": edges, **kw,
    }  # fmt: skip


def _router(id_: str, cases: list[tuple[str, str]], default: str = "default") -> dict[str, Any]:
    return _node(id_, "core.router", cases=[{"when": w, "route": r} for w, r in cases], default=default)


def _routes_case(priority: str, lane: str, ran: str) -> Case:
    nodes = [
        _router("n_route", [('state.priority == "high"', "high"), ('state.priority == "low"', "low")]),
        _set("n_high", lane="urgent"), _set("n_low", lane="later"), _set("n_other", lane="normal"),
    ]  # fmt: skip
    edges = [
        _flow("START", "n_route"),
        _flow("n_route", "n_high", "route:high"), _flow("n_route", "n_low", "route:low"),
        _flow("n_route", "n_other", "route:default"),
        _flow("n_high", "END"), _flow("n_low", "END"), _flow("n_other", "END"),
    ]  # fmt: skip
    return Case(
        f"flow/paths-{priority or 'none'}", "C-FLOW", f"three paths: '{priority}' takes the {lane} one",
        _wf("paths", ["priority", "lane"], nodes, edges), {"priority": priority}, {"lane": lane}, {ran: 1},
    )  # fmt: skip


def _approval_case(name: str, answer: Any, expect: dict[str, Any], ran: str) -> Case:
    nodes = [
        _set("n_draft", draft="Dear {{ state.customer }}, your refund is on its way."),
        _node("n_review", "core.approval", show="draft", output="approved"),
        _router("n_decide", [("state.approved == true", "send")], default="drop"),
        _set("n_send", status="sent"), _set("n_drop", status="dropped"),
    ]  # fmt: skip
    edges = [
        _flow("START", "n_draft"), _flow("n_draft", "n_review"), _flow("n_review", "n_decide"),
        _flow("n_decide", "n_send", "route:send"), _flow("n_decide", "n_drop", "route:drop"),
        _flow("n_send", "END"), _flow("n_drop", "END"),
    ]  # fmt: skip
    wf = _wf("approval", ["customer", "draft", "status"], nodes, edges)
    wf["state"]["channels"].append({"name": "approved", "type": {"type": "boolean"}, "default": False})
    return Case(
        f"hitl/{name}", "C-HITL", f"approval: {name}", wf, {"customer": "Ada"}, expect,
        {"n_draft": 1, "n_review": 1, ran: 1}, answers=[answer], expect_pauses=[("approval", "n_review")],
    )  # fmt: skip


def _cases() -> list[Case]:
    seq = _wf(
        "sequence", ["name", "greeting", "shout"],
        [_set("n_greet", greeting="Hello {{ state.name }}"), _set("n_shout", shout="{{ state.greeting }}!")],
        [_flow("START", "n_greet"), _flow("n_greet", "n_shout"), _flow("n_shout", "END")],
    )  # fmt: skip
    parallel = _wf(
        "parallel", ["left", "right", "both"],
        [_set("n_left", left="L"), _set("n_right", right="R"), _node("n_join", "core.join"),
         _set("n_both", both="{{ state.left }}{{ state.right }}")],
        [_flow("START", "n_left"), _flow("START", "n_right"), _flow("n_left", "n_join"), _flow("n_right", "n_join"),
         _flow("n_join", "n_both"), _flow("n_both", "END")],
    )  # fmt: skip
    loop = _wf(
        "loop", ["count"],
        [_set("n_add", count="{{ (state.count or '0') | int + 1 }}"),
         _router("n_more", [("int(state.count) < 3", "again")], default="done")],
        [_flow("START", "n_add"), _flow("n_add", "n_more"), _flow("n_more", "n_add", "route:again"),
         _flow("n_more", "END", "route:done")],
    )  # fmt: skip
    reply = _wf(
        "model_reply", ["question", "answer"],
        [_node("n_ask", "core.llm", prompt="Answer briefly: {{ state.question }}", output="answer")],
        [_flow("START", "n_ask"), _flow("n_ask", "END"), _wire("r_model", "n_ask")], resources=[MODEL],
    )  # fmt: skip
    classify = _wf(
        "model_route", ["message", "category", "reply"],
        [_node("n_classify", "core.llm", prompt="One word, complaint or other: {{ state.message }}", output="category"),
         _router("n_route", [('state.category == "complaint"', "complaint")], default="other"),
         _set("n_sorry", reply="Sorry about that."), _set("n_thanks", reply="Thanks!")],
        [_flow("START", "n_classify"), _flow("n_classify", "n_route"), _flow("n_route", "n_sorry", "route:complaint"),
         _flow("n_route", "n_thanks", "route:other"), _flow("n_sorry", "END"), _flow("n_thanks", "END"),
         _wire("r_model", "n_classify")], resources=[MODEL],
    )  # fmt: skip
    io = _wf(
        "io", ["question", "answer", "notes", "scratch"],
        [_set("n_answer", answer="You asked {{ state.question }}", notes="{{ state.notes }}+", scratch="x")],
        [_flow("START", "n_answer"), _flow("n_answer", "END")],
    )  # fmt: skip
    for ch, kind in zip(io["state"]["channels"], ("in", "out", "inout", "private"), strict=True):
        ch["io"] = kind
    question = _wf(
        "question", ["name", "greeting"],
        [_node("n_ask", "core.human_input", prompt="What is your name?", output="name",
               response_schema={"type": "string", "minLength": 1}),
         _set("n_greet", greeting="Hi {{ state.name }}")],
        [_flow("START", "n_ask"), _flow("n_ask", "n_greet"), _flow("n_greet", "END")],
    )  # fmt: skip
    return [
        Case("flow/sequence", "C-FLOW", "two steps in a row", seq, {"name": "Ada"}, {"shout": "Hello Ada!"},
             {"n_greet": 1, "n_shout": 1}),
        _routes_case("high", "urgent", "n_high"),
        _routes_case("low", "later", "n_low"),
        _routes_case("", "normal", "n_other"),
        Case("flow/parallel", "C-FLOW", "two branches at once, then wait for all", parallel, {}, {"both": "LR"},
             {"n_left": 1, "n_right": 1, "n_join": 1, "n_both": 1}),
        Case("flow/loop", "C-FLOW", "a loop that stops after three rounds", loop, {"count": "0"}, {"count": "3"},
             {"n_add": 3}),
        Case("flow/output-fields", "C-FLOW", "the output holds the output and input-output fields only", io,
             {"question": "why", "notes": "n"}, {"answer": "You asked why", "notes": "n+"}, {"n_answer": 1},
             exact_output=True),
        Case("agent/model-reply", "C-AGENT", "an AI step's reply lands in its field", reply,
             {"question": "Capital of France?"}, {"answer": "Paris"}, {"n_ask": 1}, mocks={"model": ["Paris"]}),
        Case("agent/model-route", "C-AGENT", "an AI step's reply picks the path", classify,
             {"message": "My order never came"}, {"category": "complaint", "reply": "Sorry about that."},
             {"n_classify": 1, "n_sorry": 1}, mocks={"model": ["complaint"]}),
        _approval_case("approve", {"approved": True}, {"status": "sent", "approved": True}, "n_send"),
        _approval_case("reject", {"approved": False}, {"status": "dropped", "approved": False}, "n_drop"),
        _approval_case("edit", {"approved": True, "value": "Edited."}, {"status": "sent", "draft": "Edited."},
                       "n_send"),
        Case("hitl/question", "C-HITL", "a question re-asked after an invalid answer", question, {},
             {"greeting": "Hi Ada"}, {"n_ask": 1, "n_greet": 1}, answers=["", "Ada"],
             expect_pauses=[("input", "n_ask"), ("input", "n_ask")]),
    ]  # fmt: skip


CASES = _cases()


def applicable(manifest: AdapterManifest, case: Case) -> bool:
    """Whether `case` only uses blocks this framework says it can run."""
    return all(manifest.support(t) != "unsupported" for t in case.types())


# ------------------------------------------------------------------ running ----
async def run_case(adapter: Any, case: Case, resources: Any) -> Result:
    wf = Workflow.model_validate({**case.workflow, "framework": adapter.id})
    thread = new_id("th")
    spec = RunSpec(workflow=wf, thread_id=thread, run_id=new_id("run"), input=dict(case.input), mocks=case.mocks)
    answers, runs = list(case.answers), []
    for _ in range(len(case.answers) + 1):
        events = [ev async for ev in adapter.execute(spec, resources)]
        runs.append(events)
        last = events[-1] if events else {}
        if last.get("type") != "RUN_FINISHED" or last.get("outcome") != "interrupt" or not answers:
            break
        spec = RunSpec(
            workflow=wf, thread_id=thread, run_id=new_id("run"), resume=ResumeSpec(value=answers.pop(0)),
        )  # fmt: skip
    return Result(case, runs, check(case, wf, runs))


def check(case: Case, wf: Workflow, runs: list[list[dict[str, Any]]]) -> list[str]:
    problems = [f"C-EVENTS run {i + 1}: {p}" for i, events in enumerate(runs) for p in check_events(wf, events)]
    if problems:
        return problems
    last = runs[-1][-1]
    if last.get("type") != "RUN_FINISHED" or last.get("outcome") != "success":
        problems.append(f"the last run ended with {last.get('type')} {last.get('outcome') or last.get('message')}")
    output = last.get("output") or {}
    for key, want in case.expect_output.items():
        if output.get(key) != want:
            problems.append(f"output '{key}' is {output.get(key)!r}, expected {want!r}")
    if case.exact_output and sorted(output) != sorted(case.expect_output):
        problems.append(f"the output has fields {sorted(output)}, expected {sorted(case.expect_output)}")
    done = Counter(
        ev["canvas_node_id"] for events in runs for ev in events
        if ev["type"] == "STEP_FINISHED" and ev.get("status") == "ok"
    )  # fmt: skip
    for node_id, times in case.expect_steps.items():
        if done[node_id] != times:
            problems.append(f"step '{node_id}' ran {done[node_id]} times, expected {times}")
    pauses = [
        (i["kind"], i["canvas_node_id"]) for events in runs for i in events[-1].get("interrupts") or []
        if events[-1].get("outcome") == "interrupt"
    ]  # fmt: skip
    if pauses != case.expect_pauses:
        problems.append(f"paused at {pauses}, expected {case.expect_pauses}")
    return problems


def check_events(wf: Workflow, events: list[dict[str, Any]]) -> list[str]:
    """C-EVENTS: the canonical events every adapter must emit (spec doc 12 §C.3)."""
    problems: list[str] = []
    if not events:
        return ["no events"]
    ends = [e for e in events if e["type"] in ("RUN_FINISHED", "RUN_ERROR")]
    if len(ends) != 1 or events[-1] is not ends[0]:
        problems.append("the run must end with exactly one RUN_FINISHED or RUN_ERROR, as its last event")
    end = events[-1]
    if end["type"] == "RUN_ERROR":
        problems.append(f"the run failed: {end.get('message')}")
    elif end["type"] == "RUN_FINISHED":
        missing = {"outcome", "interrupts", "output"} - end.keys()
        if missing:
            problems.append(f"RUN_FINISHED lacks {sorted(missing)}")
        if end.get("outcome") == "interrupt" and end.get("interrupts"):
            told = {e["value"]["id"] for e in events if e["type"] == "CUSTOM" and e.get("name") == "ac.interrupt"}
            if not {i["id"] for i in end["interrupts"]} <= told:
                problems.append("every pause must also be announced as a CUSTOM ac.interrupt event")
    ids = {n.id for n in wf.nodes}
    open_tasks: dict[str, str] = {}
    for e in events:
        if e["type"] in ("STEP_STARTED", "STEP_FINISHED") and e.get("canvas_node_id") not in ids:
            problems.append(f"{e['type']} names '{e.get('canvas_node_id')}', which is not a block of the workflow")
        if e["type"] == "STEP_STARTED":
            open_tasks[e["task_id"]] = e["canvas_node_id"]
        elif e["type"] == "STEP_FINISHED":
            if open_tasks.pop(e.get("task_id", ""), None) is None:
                problems.append(f"STEP_FINISHED for '{e.get('canvas_node_id')}' without a STEP_STARTED")
            if e.get("status") not in ("ok", "error", "interrupted"):
                problems.append(f"STEP_FINISHED status {e.get('status')!r} is not ok, error or interrupted")
    if open_tasks:
        problems.append(f"steps started but never finished: {sorted(set(open_tasks.values()))}")
    return problems
