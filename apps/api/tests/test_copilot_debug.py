"""Copilot II (S15): problems with explanations, past runs, test runs and reproductions of the working copy,
proposed tests and notes saved on accept, the guide search, and split/privacy model settings."""

from __future__ import annotations

import json

import pytest
from test_copilot import SETTINGS, ask, call, of, script
from test_publish import make_greeter

from agentcanvas.copilot import docs


@pytest.fixture(autouse=True, params=["loop", "deepagents"])
def engine(request, settings):
    """Every Copilot test runs on both engines: the loop and the Deep Agents harness (PLAN.md section 2)."""
    settings.copilot_engine = request.param
    return request.param


FIX_GREETING = [{"op": "UpdateNode", "id": "n_g", "patch": {"config": {"values": {"out": "Hi {{ state.who }}"}}}}]


async def separate_model(api_client) -> None:
    r = await api_client.put(SETTINGS, json={"source": "separate", "model": "anthropic:claude-haiku-4-5"})
    assert r.status_code == 200, r.text


async def a_run(api_client, wf: str, who: str = "Ada") -> str:
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": who}})
    assert r.status_code == 200, r.text
    events = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert events[-1]["type"] == "RUN_FINISHED", events[-1]
    return next(e["runId"] for e in events if e.get("runId"))


async def test_debug_explains_a_run_and_checks_a_fix(app, api_client, monkeypatch):
    await separate_model(api_client)
    wf = await make_greeter(api_client)
    run_id = await a_run(api_client, wf)

    # explain: list runs, read one; nothing changes
    script(app, call("list_runs"), call("get_run", run_id=run_id), call("get_problems"), "It greeted Ada.")
    events = await ask(api_client, wf, "what happened in the last run?", mode="ask")
    tools = of(events, "tool")
    assert [t["name"] for t in tools] == ["list_runs", "get_run", "get_problems"] and all(t["ok"] for t in tools)
    assert not of(events, "proposal")

    # fix and check: propose the change, reproduce the old run on it, test it with an expectation
    results = record_tool_results(monkeypatch)
    script(
        app,
        call("propose_changes", summary="Say Hi", commands=FIX_GREETING),
        call("reproduce_run", run_id=run_id),
        call("run_test", input={"who": "Bo"}, expect={"channels": {"out": "Hi Bo"}}),
        call("propose_tests", summary="Add a test", tests=[
            {"name": "greets Bo", "input": {"who": "Bo"}, "expect": {"channels": {"out": "Hi Bo"}}}]),
        call("save_note", note="Greetings start with Hi."),
        "Fixed: it now says Hi, and the test passes.",
    )  # fmt: skip
    events = await ask(api_client, wf, "make it say Hi instead and check it")
    reproduced, tested = results[1], results[2]
    assert (
        reproduced["ok"] and reproduced["output"]["out"] == "Hi Ada" and reproduced["original"]["status"] == "success"
    )
    assert tested["passed"] is True and tested["status"] == "success"
    assert (await api_client.get(f"/v1/workflows/{wf}")).json()["ir"]["nodes"][0]["config"]["values"]["out"] == (
        "Hello {{ state.who }}"
    )  # test runs used the working copy; the draft is unchanged until accepted

    proposal = of(events, "proposal")[0]["proposal"]
    assert len(proposal["tests"]) == 1 and proposal["notes"] == ["Greetings start with Hi."]
    r = await api_client.post(
        f"/v1/workflows/{wf}/commands", json={"commands": proposal["commands"]}, headers={"If-Match": "2"}
    )
    assert r.status_code == 200, r.text
    r = await api_client.post(f"/v1/workflows/{wf}/copilot/proposals/{proposal['id']}", json={"status": "accepted"})
    assert r.status_code == 204
    cases = (await api_client.get(f"/v1/workflows/{wf}/test-cases")).json()
    assert [c["name"] for c in cases] == ["greets Bo"]
    assert "Greetings start with Hi." in (await api_client.get(SETTINGS)).json()["notes"]
    ran = (await api_client.post(f"/v1/workflows/{wf}/test-cases/run", json={})).json()
    assert ran["passed"] == 1


async def test_problems_are_explained_and_tests_wait_for_a_valid_workflow(app, api_client, monkeypatch):
    await separate_model(api_client)
    wf = await make_greeter(api_client)
    broken = [{"op": "Disconnect", "id": "e2"}]
    script(app, call("propose_changes", summary="x", commands=broken), call("get_problems"),
           call("run_test", input={"who": "x"}), "It no longer reaches the end.")  # fmt: skip
    results = record_tool_results(monkeypatch)
    await ask(api_client, wf, "remove the last arrow")
    problems = results[1]["problems"]
    assert problems and all("what" in p and "fix" in p for p in problems if p["code"].startswith("E"))
    assert not results[2]["ok"] and "fix the" in results[2]["error"]


def test_guide_search_finds_sections():
    found = docs.search("how do I add an API key secret")
    assert found and any(f["page"] in ("models.md", "workspace.md") for f in found)
    assert docs.search("") == []


async def test_split_and_privacy_settings(app, api_client):
    wf = await make_greeter(api_client)
    r = await api_client.put(SETTINGS, json={"source": "separate", "model": "anthropic:claude-haiku-4-5",
                                             "ask_model": "ollama:llama3.3"})  # fmt: skip
    assert r.status_code == 200
    script(app, "answer")
    events = await ask(api_client, wf, "what is this?", mode="ask")
    assert events[0]["model"] == "ollama:llama3.3"
    script(app, "ok")
    assert (await ask(api_client, wf, "build"))[0]["model"] == "anthropic:claude-haiku-4-5"

    r = await api_client.put(SETTINGS, json={"source": "separate", "model": "anthropic:claude-haiku-4-5",
                                             "privacy": "local_only"})  # fmt: skip
    assert r.status_code == 422 and "local models only" in r.json()["detail"]  # refused when saved
    bad_ask = {"source": "separate", "model": "ollama:llama3.3", "privacy": "local_only",
               "ask_model": "anthropic:claude-haiku-4-5"}  # fmt: skip
    r = await api_client.put(SETTINGS, json=bad_ask)  # refused when saved, not on every later question
    assert r.status_code == 422 and "local models only" in r.json()["detail"]
    await api_client.put(SETTINGS, json={"source": "separate", "model": "ollama:llama3.3", "privacy": "local_only"})
    script(app, "ok")
    assert (await ask(api_client, wf, "hi"))[0]["model"] == "ollama:llama3.3"


async def test_debug_suite_plants_bugs_and_scores_a_fix():
    from langchain_core.messages import AIMessage

    from agentcanvas.adapters import model_service
    from agentcanvas.adapters.langchain.testing import DemoResources, scripted
    from agentcanvas.copilot.evals import debug_cases, run_case
    from agentcanvas.copilot.settings import ResolvedModel
    from agentcanvas.registry import default_registry
    from agentcanvas.settings import Settings

    settings, registry = Settings(env="prod"), default_registry()
    cases = debug_cases(settings, registry)
    assert len(cases) >= 25
    case = next(c for c in cases if c["id"] == "ticket_triage/no_end")
    fix = {"op": "Connect", "edge": {"id": "e5", "kind": "flow", "source": {"node": "n_ok"}, "target": {"node": "END"}}}
    replies = [
        AIMessage(content="", tool_calls=[{"name": "get_problems", "args": {}, "id": "c0"}]),
        AIMessage(content="", tool_calls=[{"name": "propose_changes", "id": "c1",
                                           "args": {"summary": "reconnect", "commands": [fix]}}]),
        AIMessage(content="The approval step did not lead to Finish; I reconnected it."),
    ]  # fmt: skip
    resources = DemoResources(overrides={"copilot": scripted(*replies)})
    model = ResolvedModel(model="anthropic:claude-haiku-4-5", origin="test")
    result = await run_case(case, model_service(), resources, model, registry, settings)
    assert result.passed and result.turns == 1, result


def test_test_runs_refuse_unaccepted_code_and_secrets_sent_elsewhere():
    """Review fix: a dry run stubs risky tools but not code; new code and re-pointed secrets wait for an accept."""
    from agentcanvas.copilot.settings import CopilotSettings, endpoint_for
    from agentcanvas.copilot.tools import unaccepted_risks
    from agentcanvas.ir.models import Resource, Workflow
    from agentcanvas.registry import default_registry

    base = Workflow.model_validate(
        {"id": "wf_x", "name": "x", "resources": [{"id": "r_m", "type": "model.chat", "name": "m",
         "config": {"model": "openai:gpt-5", "api_key": {"$secret": "OPENAI_API_KEY"}}}]}
    )  # fmt: skip
    reg = default_registry()
    assert unaccepted_risks(base, base, reg) == []
    moved = base.model_copy(deep=True)
    moved.resources[0].config["base_url"] = "https://elsewhere.example"
    assert "sends a secret" in unaccepted_risks(base, moved, reg)[0]
    renamed = base.model_copy(deep=True)
    renamed.resources[0].config["temperature"] = 0.1  # same address: fine
    assert unaccepted_risks(base, renamed, reg) == []
    script_step = base.model_copy(deep=True)
    script_step.nodes.append(_script_node())
    assert "code step 's'" in unaccepted_risks(base, script_step, reg)[0]
    local = base.model_copy(deep=True)  # a local app connector starts a program when the graph is built
    stdio = {"transport": "stdio", "command": "npx", "args": ["-y", "pkg"]}
    local.resources.append(Resource(id="r_x", type="mcp.server", name="x", config=stdio))
    assert "starts a program" in unaccepted_risks(base, local, reg)[0]

    # Ask mode on a self-hosted server keeps the separate model's address and key
    cs = CopilotSettings(source="separate", model="vllm:big", base_url="http://gpu:8000/v1", ask_model="vllm:small")
    assert endpoint_for("vllm:small", cs, None) == {"base_url": "http://gpu:8000/v1"}
    assert endpoint_for("anthropic:claude-haiku-4-5", cs, None) == {}


def _script_node():
    from agentcanvas.ir.models import Node

    return Node(id="n_s", type="core.script", name="s")


def test_a_turn_keeps_decisions_taken_meanwhile():
    from agentcanvas.api.v1.copilot import merge_proposals

    stored = [{"id": "p1", "status": "accepted"}, {"id": "p0", "status": "rejected"}]
    turn = [{"id": "p1", "status": "superseded"}, {"id": "p2", "status": "pending"}]
    merged = merge_proposals(stored, turn)
    assert {p["id"]: p["status"] for p in merged} == {"p0": "rejected", "p1": "accepted", "p2": "pending"}


def test_the_open_proposal_carries_into_the_next_turn():
    """Review fix: a follow-up turn tests the open proposal's fix and keeps its changes in the new proposal."""
    from agentcanvas.copilot.tools import ToolContext, carry_pending
    from agentcanvas.ir.models import Node, Workflow
    from agentcanvas.registry import default_registry

    async def validate(_wf):
        return []

    wf = Workflow(id="wf_x", name="x", nodes=[Node(id="n_a", type="core.set", name="a")])
    fix = [{"op": "UpdateNode", "id": "n_a", "patch": {"name": "renamed"}}]
    open_one = {"id": "p1", "status": "pending", "summary": "Rename", "commands": fix, "tests": [{"name": "t"}]}
    stored = [{"id": "p0", "status": "rejected", "commands": []}, open_one]
    ctx = ToolContext(workflow=wf, registry=default_registry(), templates={}, validate=validate)
    carry_pending(ctx, stored)
    assert ctx.workflow.nodes[0].name == "renamed" and ctx.proposal.commands == fix
    assert ctx.proposal.tests == [{"name": "t"}] and ctx.proposal.summary == ["Rename"]
    stale = [{"id": "p1", "status": "pending", "commands": [{"op": "UpdateNode", "id": "n_gone", "patch": {}}]}]
    ctx = ToolContext(workflow=wf, registry=default_registry(), templates={}, validate=validate)
    carry_pending(ctx, stale)  # no longer applies: left out
    assert ctx.workflow is wf and not ctx.proposal


async def test_a_reproduction_replays_risky_tools_instead_of_stubbing_them():
    """Review fix: in a dry-run reproduction, recorded tool output is replayed, not replaced by the dry-run stub."""
    from agentcanvas.adapters.langchain.nodes import Resources

    rec = [{"tool": "send_email", "args": {"to": "a@b.c"}, "output": "sent #42"}]
    res = Resources().with_mocks({"mail": rec})
    res.dry_run = {"send_email"}
    tools = res.tool("mail", {"transport": "http", "url": "https://mail.example/mcp"}, "mcp.server")
    assert await tools[0].ainvoke({"to": "a@b.c"}) == "sent #42"


def record_tool_results(monkeypatch) -> list[dict]:
    """Every tool result of the turn, in order, whichever engine runs it (both call the tools in `TOOLS`)."""
    import dataclasses

    from agentcanvas.copilot import tools as copilot_tools

    results: list[dict] = []
    for name, tool in list(copilot_tools.TOOLS.items()):

        async def run(ctx, args, _run=tool.run):
            out = await _run(ctx, args)
            results.append(out)
            return out

        monkeypatch.setitem(copilot_tools.TOOLS, name, dataclasses.replace(tool, run=run))
    return results


async def test_a_proposal_is_accepted_in_part(app, api_client):
    """FR-COP-01: the person keeps some changes, some tests and not the notes; the record says what was kept."""
    await separate_model(api_client)
    wf = await make_greeter(api_client)
    tests = [{"name": f"case {i}", "input": {"who": "Bo"}, "expect": {"channels": {"out": "Hi Bo"}}} for i in (1, 2)]
    script(
        app,
        call("propose_changes", summary="Say Hi", commands=FIX_GREETING),
        call("propose_tests", summary="Add tests", tests=tests),
        call("save_note", note="Greetings start with Hi."),
        "Proposed.",
    )  # fmt: skip
    events = await ask(api_client, wf, "make it say Hi, with tests")
    proposal = of(events, "proposal")[0]["proposal"]
    url = f"/v1/workflows/{wf}/copilot/proposals/{proposal['id']}"
    total = len(proposal["commands"])
    bad = await api_client.post(url, json={"status": "accepted", "commands": [total]})
    assert bad.status_code == 422
    r = await api_client.post(url, json={"status": "accepted", "commands": [0], "tests": [1], "notes": False})
    assert r.status_code == 204, r.text
    assert [c["name"] for c in (await api_client.get(f"/v1/workflows/{wf}/test-cases")).json()] == ["case 2"]
    assert "Greetings start with Hi." not in (await api_client.get(SETTINGS)).json()["notes"]
    kept = (await api_client.get(f"/v1/workflows/{wf}/copilot")).json()["proposals"][-1]
    assert kept["status"] == "accepted" and kept["accepted_commands"] == [0]


async def test_get_problems_adds_best_practice_suggestions():
    """FR-COP-04: judgement calls, with what, why and fix, apart from the validator's problems."""
    from agentcanvas.copilot.rules import suggestions
    from agentcanvas.ir.models import Workflow
    from agentcanvas.services.workflows import list_templates
    from agentcanvas.settings import Settings

    triage = Workflow.model_validate(list_templates(Settings())["ticket_triage"])
    found = {s["code"]: s for s in suggestions(triage)}
    assert "S001" not in found  # its router has a default
    assert found["S002"]["what"] == "This model has no backup model." and found["S002"]["fix"]
    bare = triage.model_copy(deep=True)
    router = next(n for n in bare.nodes if n.type == "core.router")
    router.config.pop("default")
    llm = next(n for n in bare.nodes if n.type == "core.llm")
    llm.config["prompt"] = "Summarise."
    codes = [s["code"] for s in suggestions(bare)]
    assert "S001" in codes and "S003" in codes


async def test_real_model_runs_need_the_persons_permission(app, api_client):
    """run_test on a workflow with models: refused (and the panel asked) until the person allows real runs; with
    scripted replies for every model it needs no permission."""
    from test_copilot import new_workflow

    await separate_model(api_client)
    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]
    test = call("run_test", input={"ticket": "Invoice?"})
    script(app, test, "Done.")
    events = await ask(api_client, wf, "try it")
    assert any(e["type"] == "permission" and e["kind"] == "real_runs" for e in events)
    tool = next(e for e in events if e["type"] == "tool" and e["name"] == "run_test")
    assert tool["ok"] is False and "not allowed test runs that call real models" in tool["error"]

    script(app, test, "Done.")
    r = await api_client.post(
        f"/v1/workflows/{wf}/copilot/messages", json={"text": "try it", "mode": "build", "allow_real_runs": True}
    )
    events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
    assert not any(e["type"] == "permission" for e in events)

    mocked = call("run_test", input={"ticket": "Invoice?"}, mocks={"fast_model": ["low"], "smart_model": ["Thanks"]})
    script(app, mocked, "Done.")
    events = await ask(api_client, wf, "try it with scripted replies")
    assert not any(e["type"] == "permission" for e in events)
