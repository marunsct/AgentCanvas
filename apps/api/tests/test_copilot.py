"""The Copilot (S14): knowledge pack, tools over the command API, the brief/build loop with self-repair, proposals,
model choice and guardrails. The model is scripted (`with_mocks`): reply n answers the n-th model call of a turn."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage
from test_roles import act_as

from agentcanvas.adapters.langchain.testing import DemoResources, scripted
from agentcanvas.copilot.pack import build_pack
from agentcanvas.copilot.tools import ToolContext, run_tool
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry
from agentcanvas.services.workflows import list_templates
from agentcanvas.settings import Settings
from agentcanvas.validation import validate


@pytest.fixture(autouse=True, params=["loop", "deepagents"])
def engine(request, settings):
    """Every Copilot test runs on both engines: the loop and the Deep Agents harness (PLAN.md section 2)."""
    settings.copilot_engine = request.param
    return request.param


SETTINGS = "/v1/projects/prj_default/copilot-settings"

GREETER = [
    {"op": "SetChannels", "channels": [{"name": "who", "io": "in"}, {"name": "greeting", "io": "out"}]},
    {"op": "AddResource", "resource": {"id": "r_model", "type": "model.chat", "name": "chat_model",
                                       "config": {"model": "anthropic:claude-haiku-4-5"}}},
    {"op": "AddNode", "node": {"id": "n_greet", "type": "core.llm", "name": "greet", "label": "Greet",
                               "config": {"prompt": "Greet {{ state.who }}", "output": "greeting"}}},
    {"op": "Connect", "edge": {"id": "e_start", "kind": "flow", "source": {"node": "START"},
                               "target": {"node": "n_greet"}}},
]  # fmt: skip
FIX = [
    {"op": "Connect", "edge": {"id": "e_end", "kind": "flow", "source": {"node": "n_greet"},
                               "target": {"node": "END"}}},
    {"op": "Connect", "edge": {"id": "w_model", "kind": "wiring", "source": {"node": "r_model"},
                               "target": {"node": "n_greet", "port": "model"}}},
]  # fmt: skip


def call(name: str, **args) -> dict:
    return {"content": "", "tool_calls": [{"name": name, "args": args}]}


def script(app, *replies) -> None:
    """The model's replies for the next turn, in order (a fresh script per request)."""

    def message(i: int, reply) -> AIMessage:
        if isinstance(reply, str):
            return AIMessage(content=reply)
        calls = [
            {"name": c["name"], "args": c["args"], "id": f"call_{i}_{j}"} for j, c in enumerate(reply["tool_calls"])
        ]
        return AIMessage(content=reply["content"], tool_calls=calls)

    app.state.resources_factory = lambda thread_id, wf: DemoResources(
        overrides={"copilot": scripted(*[message(i, r) for i, r in enumerate(replies)])}
    )


async def new_workflow(api_client, **kw) -> dict:
    r = await api_client.post("/v1/projects/prj_default/workflows", json={"name": "helper", **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def ask(api_client, wf_id: str, text: str, mode: str = "build") -> list[dict]:
    r = await api_client.post(f"/v1/workflows/{wf_id}/copilot/messages", json={"text": text, "mode": mode})
    assert r.status_code == 200, r.text
    return [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]


def of(events: list[dict], kind: str) -> list[dict]:
    return [e for e in events if e["type"] == kind]


def test_pack_is_generated_from_the_product():
    registry = default_registry()
    pack = build_pack(registry, list_templates(Settings(env="test")))
    for m in registry.all():
        assert f"`{m.type}`" in pack.text  # every block, from its manifest
    assert "ticket_triage" in pack.text and "propose_changes" in pack.text
    assert 4_000 < pack.tokens < 40_000  # small enough to send whole, cached


async def test_tools_apply_to_a_working_copy_and_report_errors():
    async def check(wf: Workflow):
        return validate(wf, default_registry())

    ctx = ToolContext(Workflow(id="wf_x", name="x"), default_registry(), {}, check)
    out = await run_tool(ctx, "propose_changes", {"summary": "greeter", "commands": GREETER}, ["propose_changes"])
    assert out["ok"] and out["errors"]  # not wired to END, no model: the model is told what to fix
    out = await run_tool(ctx, "propose_changes", {"summary": "wire it", "commands": FIX}, ["propose_changes"])
    assert out["ok"] and out["errors"] == [], out
    assert len(ctx.proposal.commands) == 6 and ctx.proposal.summary == ["greeter", "wire it"]

    bad = await run_tool(
        ctx, "propose_changes", {"commands": [{"op": "RemoveNode", "id": "n_nope"}]}, ["propose_changes"]
    )
    assert not bad["ok"] and "n_nope" in bad["error"] and len(ctx.proposal.commands) == 6  # atomic
    leak = [{"op": "UpdateResource", "id": "r_model", "patch": {"config": {"api_key": "sk-live-123"}}}]
    out = await run_tool(ctx, "propose_changes", {"commands": leak}, ["propose_changes"])
    assert not out["ok"] and "$secret" in out["error"]
    denied = await run_tool(ctx, "propose_changes", {"commands": FIX}, ["get_workflow"])
    assert not denied["ok"] and "not available" in denied["error"]
    found = await run_tool(ctx, "find_blocks", {"query": "approval"}, ["find_blocks"])
    assert "core.approval" in [b["type"] for b in found["blocks"]]


async def test_brief_then_build_with_self_repair_and_accept(app, api_client):
    await api_client.put(SETTINGS, json={"source": "separate", "model": "anthropic:claude-haiku-4-5"})
    wf_id = (await new_workflow(api_client))["id"]
    script(
        app,
        call("set_brief", goal="Greet a person by name", inputs=["who"], outputs=["greeting"],
             open_questions=["Formal or casual?"]),
        "Here is the brief. Formal or casual?",
    )  # fmt: skip
    events = await ask(api_client, wf_id, "make a workflow that greets people")
    assert of(events, "brief")[0]["brief"]["goal"] == "Greet a person by name"
    assert of(events, "text")[-1]["text"].endswith("casual?") and not of(events, "proposal")
    assert events[0]["type"] == "start" and events[-1]["type"] == "done"

    script(
        app,
        call("propose_changes", summary="Add a greeter", commands=GREETER),
        call("propose_changes", summary="Connect it", commands=FIX),  # repairs the errors the first call reported
        "I added a Greet step that writes a casual greeting.",
    )
    events = await ask(api_client, wf_id, "casual, go ahead")
    tools = of(events, "tool")
    assert [t["name"] for t in tools] == ["propose_changes", "propose_changes"] and all(t["ok"] for t in tools)
    proposal = of(events, "proposal")[0]["proposal"]
    assert proposal["errors"] == 0 and proposal["base_version"] == 1
    assert {"n_greet", "r_model", "e_end"} <= set(proposal["diff"]["added"])
    assert [n["id"] for n in proposal["preview"]["nodes"]] == ["n_greet"]
    assert (await api_client.get(f"/v1/workflows/{wf_id}")).json()["version"] == 1  # nothing saved yet

    convo = (await api_client.get(f"/v1/workflows/{wf_id}/copilot")).json()
    assert convo["brief"]["goal"] == "Greet a person by name" and convo["pending"]["id"] == proposal["id"]
    assert [m["role"] for m in convo["messages"]] == ["user", "assistant", "user", "assistant"]

    # the editor applies the proposal through the normal edit endpoint, then reports it
    r = await api_client.post(
        f"/v1/workflows/{wf_id}/commands", json={"commands": proposal["commands"]}, headers={"If-Match": "1"}
    )
    assert r.status_code == 200 and not [d for d in r.json()["diagnostics"] if d["severity"] == "error"]
    url = f"/v1/workflows/{wf_id}/copilot/proposals/{proposal['id']}"
    assert (await api_client.post(url, json={"status": "accepted"})).status_code == 204
    assert (await api_client.post(url, json={"status": "rejected"})).status_code == 409
    convo = (await api_client.get(f"/v1/workflows/{wf_id}/copilot")).json()
    assert convo["pending"] is None and convo["proposals"][0]["status"] == "accepted"

    # the next turn tells the model what happened to its proposal
    seen: list = []
    from agentcanvas.copilot.service import CopilotTurn

    original = CopilotTurn._context

    def spy(self, mode):  # either engine builds its per-turn context here
        seen.append(original(self, mode))
        return seen[-1]

    CopilotTurn._context = spy  # type: ignore[method-assign]
    try:
        script(app, "It greets whoever you pass as `who`.")
        await ask(api_client, wf_id, "what does it do?", mode="ask")
    finally:
        CopilotTurn._context = original  # type: ignore[method-assign]
    assert "Add a greeter; Connect it: accepted" in seen[0] and '"n_greet"' in seen[0]

    assert (await api_client.delete(f"/v1/workflows/{wf_id}/copilot")).status_code == 204
    assert (await api_client.get(f"/v1/workflows/{wf_id}/copilot")).json()["messages"] == []


async def test_ask_mode_cannot_change_and_viewers_only_ask(app, api_client):
    wf_id = (await new_workflow(api_client, template="ticket_triage"))["id"]
    script(app, call("propose_changes", summary="x", commands=FIX), "I can't change things in Ask mode.")
    events = await ask(api_client, wf_id, "add a step", mode="ask")
    assert of(events, "tool")[0]["ok"] is False and not of(events, "proposal")
    act_as(app, "viewer")
    try:
        r = await api_client.post(f"/v1/workflows/{wf_id}/copilot/messages", json={"text": "build", "mode": "build"})
        assert r.status_code == 403 and "Ask mode" in r.json()["detail"]
        script(app, "It triages tickets.")
        assert of(await ask(api_client, wf_id, "what is this?", mode="ask"), "text")
    finally:
        app.dependency_overrides.clear()


async def test_model_choice_policy_and_token_cap(app, api_client):
    wf_id = (await new_workflow(api_client))["id"]  # empty workflow, no project default: no model yet
    convo = (await api_client.get(f"/v1/workflows/{wf_id}/copilot")).json()
    assert "needs a model" in convo["model"]["problem"]
    r = await api_client.post(f"/v1/workflows/{wf_id}/copilot/messages", json={"text": "hi"})
    assert r.status_code == 422

    tri = (await new_workflow(api_client, template="ticket_triage"))["id"]
    convo = (await api_client.get(f"/v1/workflows/{tri}/copilot")).json()
    assert convo["model"] == {"model": "anthropic:claude-haiku-4-5", "origin": "workflow model 'fast_model'",
                              "problem": None}  # fmt: skip

    r = await api_client.put(SETTINGS, json={"source": "separate", "model": "openai:gpt-5.5", "token_cap": 1000})
    assert r.status_code == 200 and r.json()["resolved"]["model"] == "openai:gpt-5.5"
    assert (await api_client.get(f"/v1/workflows/{tri}/copilot")).json()["model"]["origin"] == "Copilot settings"
    assert (await api_client.put(SETTINGS, json={"source": "separate", "model": None})).status_code == 422  # merged

    policy = {"name": "Only Anthropic", "scope": "resource", "match": 'resource.type == "model.chat"',
              "require": 'resource.config.model.startsWith("anthropic:")'}  # fmt: skip
    assert (await api_client.post("/v1/org/policies", json=policy)).status_code == 201
    r = await api_client.put(SETTINGS, json={"source": "separate", "model": "openai:gpt-5.5"})
    assert r.status_code == 422 and "policies" in r.json()["detail"]

    # the cap: a conversation that used its tokens refuses more
    await api_client.put(SETTINGS, json={"source": "workflow", "token_cap": 1000})
    script(app, "ok")
    await ask(api_client, tri, "hello")
    from sqlalchemy import update

    from agentcanvas.db.models import CopilotConversation

    async with app.state.db.sessionmaker() as s:
        await s.execute(update(CopilotConversation).values(usage={"input_tokens": 900, "output_tokens": 200}))
        await s.commit()
    r = await api_client.post(f"/v1/workflows/{tri}/copilot/messages", json={"text": "more"})
    assert r.status_code == 429

    await api_client.put(SETTINGS, json={"enabled": False})
    r = await api_client.post(f"/v1/workflows/{tri}/copilot/messages", json={"text": "more"})
    assert r.status_code == 403


@pytest.mark.parametrize("steps", [17])
async def test_a_runaway_loop_stops(app, api_client, steps):
    wf_id = (await new_workflow(api_client, template="ticket_triage"))["id"]
    script(app, *[call("get_workflow") for _ in range(steps)])
    events = await ask(api_client, wf_id, "loop")
    assert len(of(events, "tool")) == 16 and "too many steps" in of(events, "error")[0]["message"]
    assert events[-1]["type"] == "done"


async def test_eval_harness_scores_a_case():
    from agentcanvas.adapters import model_service
    from agentcanvas.copilot.evals import CASES, run_case
    from agentcanvas.copilot.settings import ResolvedModel

    case = next(c for c in CASES if c["id"] == "greeter")
    replies = [
        AIMessage(content="", tool_calls=[{"name": "set_brief", "args": {"goal": "greet"}, "id": "c0"}]),
        AIMessage(content="Confirm?"),
        AIMessage(content="", tool_calls=[{"name": "propose_changes", "id": "c1",
                                           "args": {"summary": "s", "commands": GREETER + FIX}}]),
        AIMessage(content="Done."),
    ]  # fmt: skip
    resources = DemoResources(overrides={"copilot": scripted(*replies)})
    model = ResolvedModel(model="anthropic:claude-haiku-4-5", origin="test")
    result = await run_case(case, model_service(), resources, model, default_registry(), Settings(env="test"))
    assert result.passed and result.turns == 2 and result.applied, result


def test_the_copilot_knows_the_target_framework():
    from agentcanvas.copilot.service import framework_note

    assert "LangGraph" in framework_note("langchain")
    assert "native CrewAI flow" in framework_note("crewai")  # S27
    assert "native Google ADK 2 graph workflow" in framework_note("google_adk")  # S29
    assert "Pydantic AI Agents" in framework_note("pydantic_ai") and "Agno Agents" in framework_note("agno")  # S32
    assert "Agent Framework Agents" in framework_note("microsoft_agent_framework")  # S33-S34
    assert "Strands Agents" in framework_note("strands") and "FunctionAgents" in framework_note("llamaindex")  # S35
    assert "TypeScript Mastra flow" in framework_note("mastra")  # S36
    assert "no adapter" in framework_note("semantic_kernel")
    assert "CrewAI (`crewai`)" in framework_note("langchain")  # S21: agents designed for other frameworks


def test_a_turn_keeps_decisions_taken_meanwhile():
    from agentcanvas.api.v1.copilot import merge_proposals

    stored = [{"id": "p1", "status": "accepted"}, {"id": "p0", "status": "rejected"}]
    turn = [{"id": "p1", "status": "superseded"}, {"id": "p2", "status": "pending"}]
    merged = merge_proposals(stored, turn)
    assert {p["id"]: p["status"] for p in merged} == {"p0": "rejected", "p1": "accepted", "p2": "pending"}


def test_overlapping_turns_leave_one_open_proposal_and_models_follow_their_config():
    from agentcanvas.adapters.langchain.nodes import Resources
    from agentcanvas.api.v1.copilot import merge_proposals

    stored = [{"id": "p1", "status": "pending"}]  # saved by a turn on another API process
    merged = merge_proposals(stored, [{"id": "p2", "status": "pending"}])
    assert [p["status"] for p in merged] == ["superseded", "pending"]

    r = Resources()
    a = r.model("copilot", {"model": "openai:gpt-4o-mini", "api_key": "k", "base_url": "http://a.example/v1"})
    b = r.model("copilot", {"model": "openai:gpt-4o-mini", "api_key": "k", "base_url": "http://b.example/v1"})
    assert a is not b  # a changed address (or model, or key) is a new client, not the first one cached
    assert r.model("copilot", {"model": "openai:gpt-4o-mini", "api_key": "k", "base_url": "http://b.example/v1"}) is b


async def test_saving_some_copilot_settings_keeps_the_others(api_client):
    """A form that does not know a field (an older page, a script) must not erase it: PUT merges, null clears."""
    full = {"source": "separate", "model": "openai:gpt-5-mini", "base_url": "https://llm.internal/v1",
            "api_key": {"$secret": "LLM_KEY"}, "notes": "Use approvals.", "token_cap": 50_000}  # fmt: skip
    assert (await api_client.put(SETTINGS, json=full)).status_code == 200
    r = await api_client.put(SETTINGS, json={"enabled": True, "notes": "Use approvals everywhere."})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["base_url"] == "https://llm.internal/v1" and out["api_key"] == {"$secret": "LLM_KEY"}
    assert (
        out["model"] == "openai:gpt-5-mini"
        and out["token_cap"] == 50_000
        and out["notes"] == "Use approvals everywhere."
    )
    cleared = (await api_client.put(SETTINGS, json={"base_url": None})).json()
    assert cleared["base_url"] is None and cleared["model"] == "openai:gpt-5-mini"
    switched = (await api_client.put(SETTINGS, json={"source": "workflow"})).json()
    assert switched["source"] == "workflow"


async def test_conversations_are_kept_listed_reopened_renamed_and_deleted(app, api_client):
    """New chat archives the conversation instead of deleting it; the panel lists them and reopens one."""
    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]
    base = f"/v1/workflows/{wf}/copilot"
    script(app, "It triages tickets.")
    await ask(api_client, wf, "What does   this do?", mode="ask")
    first = (await api_client.get(base)).json()["id"]
    assert (await api_client.delete(base)).status_code == 204  # New chat
    assert (await api_client.get(base)).json()["messages"] == []
    script(app, "Three queues.")
    await ask(api_client, wf, "How many queues?", mode="ask")

    threads = (await api_client.get(f"{base}/threads")).json()
    assert [(t["title"], t["open"], t["messages"]) for t in threads] == [
        ("How many queues?", True, 2), ("What does this do?", False, 2)]  # fmt: skip
    assert (await api_client.post(f"{base}/threads/{first}/open")).status_code == 204
    assert (await api_client.get(base)).json()["messages"][0]["content"] == "What does   this do?"
    assert [t["open"] for t in (await api_client.get(f"{base}/threads")).json()].count(True) == 1

    renamed = await api_client.patch(f"{base}/threads/{first}", json={"title": "Overview"})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Overview"
    other = next(t["id"] for t in threads if t["id"] != first)
    assert (await api_client.delete(f"{base}/threads/{other}")).status_code == 204
    assert [t["id"] for t in (await api_client.get(f"{base}/threads")).json()] == [first]
    assert (await api_client.post(f"{base}/threads/cpc_nope/open")).status_code == 404


async def test_one_turn_at_a_time_on_any_replica(app, api_client):
    """The lease is in the database, so another API process sees it; an expired one (a crashed server) is taken over."""
    from datetime import UTC, datetime, timedelta

    from agentcanvas.db.models import CopilotConversation

    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]
    script(app, "Hello.")
    await ask(api_client, wf, "hi", mode="ask")
    conv_id = (await api_client.get(f"/v1/workflows/{wf}/copilot")).json()["id"]
    for until, expect in ((timedelta(minutes=5), 409), (timedelta(minutes=-1), 200)):
        async with app.state.db.sessionmaker() as s:
            row = await s.get(CopilotConversation, conv_id)
            row.busy_until = datetime.now(UTC) + until  # type: ignore[union-attr]
            await s.commit()
        script(app, "Again.")
        r = await api_client.post(f"/v1/workflows/{wf}/copilot/messages", json={"text": "again", "mode": "ask"})
        assert r.status_code == expect, (until, r.text)
    async with app.state.db.sessionmaker() as s:  # the finished turn gave the lease back
        assert (await s.get(CopilotConversation, conv_id)).busy_until is None  # type: ignore[union-attr]
