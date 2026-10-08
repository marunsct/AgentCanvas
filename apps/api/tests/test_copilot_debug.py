"""Copilot II (S15): problems with explanations, past runs, test runs and reproductions of the working copy,
proposed tests and notes saved on accept, the guide search, and split/privacy model settings."""

from __future__ import annotations

import json

from test_copilot import SETTINGS, ask, call, of, script
from test_publish import make_greeter

from agentcanvas.copilot import docs

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


async def test_debug_explains_a_run_and_checks_a_fix(app, api_client):
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
    seen: list = []
    original = app.state.projects.adapter.chat

    async def spy(resources, model, messages, tools, usage=None, config=None):
        seen.append(messages)
        return await original(resources, model, messages, tools, usage=usage, config=config)

    app.state.projects.adapter.chat = spy
    try:
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
    finally:
        app.state.projects.adapter.chat = original
    results = [m for m in seen[-1] if m["role"] == "tool"]
    reproduced, tested = json.loads(results[1]["content"]), json.loads(results[2]["content"])
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


async def test_problems_are_explained_and_tests_wait_for_a_valid_workflow(app, api_client):
    await separate_model(api_client)
    wf = await make_greeter(api_client)
    broken = [{"op": "Disconnect", "id": "e2"}]
    script(app, call("propose_changes", summary="x", commands=broken), call("get_problems"),
           call("run_test", input={"who": "x"}), "It no longer reaches the end.")  # fmt: skip
    seen: list = []
    original = app.state.projects.adapter.chat

    async def spy(resources, model, messages, tools, usage=None, config=None):
        seen.append(messages)
        return await original(resources, model, messages, tools, usage=usage, config=config)

    app.state.projects.adapter.chat = spy
    try:
        await ask(api_client, wf, "remove the last arrow")
    finally:
        app.state.projects.adapter.chat = original
    results = [json.loads(m["content"]) for m in seen[-1] if m["role"] == "tool"]
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
    assert r.status_code == 200
    r = await api_client.post(f"/v1/workflows/{wf}/copilot/messages", json={"text": "hi"})
    assert r.status_code == 422 and "local models only" in r.json()["detail"]
    await api_client.put(SETTINGS, json={"source": "separate", "model": "ollama:llama3.3", "privacy": "local_only"})
    script(app, "ok")
    assert (await ask(api_client, wf, "hi"))[0]["model"] == "ollama:llama3.3"


async def test_debug_suite_plants_bugs_and_scores_a_fix():
    from langchain_core.messages import AIMessage

    from agentcanvas.adapters import get_adapter
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
    result = await run_case(case, get_adapter(), resources, model, registry, settings)
    assert result.passed and result.turns == 1, result


def test_test_runs_refuse_unaccepted_code_and_secrets_sent_elsewhere():
    """Review fix: a dry run stubs risky tools but not code; new code and re-pointed secrets wait for an accept."""
    from agentcanvas.copilot.settings import CopilotSettings, _endpoint_for
    from agentcanvas.copilot.tools import unaccepted_risks
    from agentcanvas.ir.models import Workflow
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

    # Ask mode on a self-hosted server keeps the separate model's address and key
    cs = CopilotSettings(source="separate", model="vllm:big", base_url="http://gpu:8000/v1", ask_model="vllm:small")
    assert _endpoint_for("vllm:small", cs, None) == {"base_url": "http://gpu:8000/v1"}
    assert _endpoint_for("anthropic:claude-haiku-4-5", cs, None) == {}


def _script_node():
    from agentcanvas.ir.models import Node

    return Node(id="n_s", type="core.script", name="s")


def test_a_turn_keeps_decisions_taken_meanwhile():
    from agentcanvas.api.v1.copilot import merge_proposals

    stored = [{"id": "p1", "status": "accepted"}, {"id": "p0", "status": "rejected"}]
    turn = [{"id": "p1", "status": "superseded"}, {"id": "p2", "status": "pending"}]
    merged = merge_proposals(stored, turn)
    assert {p["id"]: p["status"] for p in merged} == {"p0": "rejected", "p1": "accepted", "p2": "pending"}
