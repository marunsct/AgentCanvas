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
    """Every Copilot test runs on both engines: the loop and the Deep Agents harness (product-plan-2026-10.md §2)."""
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
        if isinstance(reply, AIMessage):  # as given, e.g. with usage
            return reply
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


async def test_secrets_knowledge_and_export_tools_say_what_the_project_has():
    async def check(wf: Workflow):
        return validate(wf, default_registry())

    async def bases():
        return [{"name": "faq", "description": "", "documents": 0, "chunks": 0, "embedding_model": "builtin",
                 "stale": True}]  # fmt: skip

    templates = list_templates(Settings(env="test"))
    qa = Workflow.model_validate({**templates["knowledge_qa"], "id": "wf_qa"})
    ctx = ToolContext(qa, default_registry(), templates, check, secret_names=["SLACK_TOKEN"], knowledge=bases)
    names = ["list_secrets", "list_knowledge_bases", "export_plan"]
    secrets = await run_tool(ctx, "list_secrets", {}, names)
    assert secrets["ok"] and secrets["secrets"] == ["SLACK_TOKEN"]
    [key] = [k for k in secrets["model_keys"] if k["secret"] == "ANTHROPIC_API_KEY"][:1]
    assert key["set"] in (None, "server")  # not a project secret here
    kbs = await run_tool(ctx, "list_knowledge_bases", {}, names)
    assert kbs["knowledge_bases"][0]["stale"] and kbs["used_by_workflow"] == ["handbook"]
    assert kbs["missing"] == ["handbook"]  # the workflow searches a base this project does not have

    plan = await run_tool(ctx, "export_plan", {"target": "kubernetes"}, names)
    assert plan["ok"] and plan["framework"]["id"] == "langchain" and plan["deploy"]["target"] == "kubernetes"
    assert "ANTHROPIC_API_KEY" in [k["name"] for k in plan["deploy"]["keys"]]
    assert plan["knowledge"] == [{"name": "handbook", "shipped": False}]  # left out of the export
    crew = await run_tool(ctx, "export_plan", {"framework": "crewai"}, names)
    assert crew["ok"] and "plain CrewAI project" in crew["deploy"] and any("emulated" in g for g in crew["gaps"])
    assert not (await run_tool(ctx, "export_plan", {"framework": "nope"}, names))["ok"]

    network = Workflow.model_validate({**templates["agent_network"], "id": "wf_net"})
    ctx = ToolContext(network, default_registry(), templates, check, secret_names=["ISSUE_TRACKER_KEY"])
    secrets = await run_tool(ctx, "list_secrets", {}, names)
    assert secrets["used_by_workflow"] == ["ISSUE_TRACKER_KEY", "PLATFORM_AGENT_KEY"]
    assert secrets["missing"] == ["PLATFORM_AGENT_KEY"]
    assert not (await run_tool(ctx, "list_knowledge_bases", {}, names))["ok"]  # no project here (the eval harness)


async def test_ask_mode_reads_secret_names_knowledge_bases_and_the_export_plan(app, api_client):
    from agentcanvas.adapters.langchain.testing import ToolFakeModel
    from agentcanvas.copilot.tools import READ_ONLY

    assert {"list_secrets", "list_knowledge_bases", "export_plan"} <= set(READ_ONLY)  # Ask mode has them
    secret = await api_client.put("/v1/projects/prj_default/secrets/API_TOKEN", json={"value": "s3cret-value"})
    assert secret.status_code == 204
    kb = await api_client.post("/v1/projects/prj_default/knowledge", json={"name": "handbook", "sample": "handbook"})
    assert kb.status_code == 201, kb.text
    wf_id = (await new_workflow(api_client, template="knowledge_qa"))["id"]
    seen: list = []

    class Recording(ToolFakeModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            seen.append([str(m.content) for m in messages if m.type == "tool"])
            return super()._generate(messages, stop, run_manager, **kwargs)

    replies = [
        AIMessage(content="", tool_calls=[{"name": n, "args": {}, "id": f"c_{n}"}
                                          for n in ("list_secrets", "list_knowledge_bases", "export_plan")]),
        AIMessage(content="It needs ANTHROPIC_API_KEY; the handbook is there."),
    ]  # fmt: skip
    app.state.resources_factory = lambda thread_id, wf: DemoResources(
        overrides={"copilot": Recording(messages=iter(replies), disable_streaming=True)}
    )
    events = await ask(api_client, wf_id, "what does exporting this need?", mode="ask")
    tools = {t["name"]: t["ok"] for t in of(events, "tool")}  # Deep Agents runs the three side by side
    assert tools == {"list_secrets": True, "list_knowledge_bases": True, "export_plan": True}
    results = [json.loads(r) for r in seen[-1]]  # what the model read back
    assert next(r for r in results if "secrets" in r)["secrets"] == ["API_TOKEN"]
    bases = next(r for r in results if "knowledge_bases" in r)["knowledge_bases"]
    assert bases[0]["name"] == "handbook" and bases[0]["documents"] > 0 and bases[0]["stale"] is False
    plan = next(r for r in results if "deploy" in r)
    assert plan["knowledge"] == [{"name": "handbook", "shipped": True}]
    assert "s3cret-value" not in json.dumps(seen) and "s3cret-value" not in json.dumps(events)  # names, never values


async def test_proposed_files_are_checked_with_ruff_and_read_back_as_proposed(tmp_path):
    from agentcanvas.workspace.store import Workspace

    async def check(wf: Workflow):
        return validate(wf, default_registry())

    ws = Workspace(tmp_path / "ws").ensure()  # a new folder gets the conventional layout
    ctx = ToolContext(Workflow(id="wf_x", name="x"), default_registry(), {}, check, workspace=ws)
    allowed = ["propose_file", "read_workspace_file", "list_workspace_files"]
    out = await run_tool(ctx, "propose_file", {"path": "scripts/a.py", "content": "import os\n"}, allowed)
    assert out["ok"] and out["new"] and any("F401" in p for p in out["problems"])  # the model is told to fix it
    back = await run_tool(ctx, "read_workspace_file", {"path": "scripts/a.py"}, allowed)
    assert back["content"] == "import os\n" and back["note"] == "as you proposed it"
    listed = await run_tool(ctx, "list_workspace_files", {"prefix": "scripts/"}, allowed)
    assert "scripts/a.py" in listed["files"] and "scripts/example.py" in listed["files"]
    for bad in ({"path": ".git/config", "content": "x"}, {"path": "img.png", "content": "x"}, {"path": "a.py"}):
        assert not (await run_tool(ctx, "propose_file", bad, allowed))["ok"], bad


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

    # the next version credits it: "Copilot (accepted by ...)" in the history (FR-COP-07); the one after does not
    v1 = (await api_client.post(f"/v1/workflows/{wf_id}/versions", json={"message": "greeter"})).json()
    [credit] = v1["copilot_changes"]
    assert credit["summary"] == "Add a greeter; Connect it" and credit["accepted_by"] == "usr_dev"
    assert credit["accepted_by_email"] and credit["changes"] == len(proposal["commands"])
    rename = [{"op": "UpdateMeta", "name": "Greeter, by hand"}]
    r = await api_client.post(f"/v1/workflows/{wf_id}/commands", json={"commands": rename}, headers={"If-Match": "2"})
    assert r.status_code == 200
    v2 = (await api_client.post(f"/v1/workflows/{wf_id}/versions", json={"message": "renamed"})).json()
    assert v2["copilot_changes"] == []
    listed = (await api_client.get(f"/v1/workflows/{wf_id}/versions")).json()
    assert [len(v["copilot_changes"]) for v in listed] == [0, 1]  # newest first

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
    assert convo["model"] == {"model": "anthropic:claude-haiku-5-5", "origin": "workflow model 'fast_model'",
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


async def test_the_stream_ends_with_done_when_the_proposal_cannot_be_checked(app, api_client, monkeypatch):
    """E-10: the proposal's check and diff ran outside the turn's error handling, so a failure there ended the stream
    with neither `error` nor `done` (the editor showed its generic reload notice)."""
    from agentcanvas.copilot import service

    def broken(before, after):
        raise RuntimeError("diff failed")

    monkeypatch.setattr(service, "diff", broken)
    await api_client.put(SETTINGS, json={"source": "separate", "model": "anthropic:claude-haiku-4-5"})
    wf_id = (await new_workflow(api_client))["id"]
    script(app, call("propose_changes", summary="Add a greeter", commands=GREETER), "Done.")
    events = await ask(api_client, wf_id, "build it")
    assert any("diff failed" in e["message"] for e in of(events, "error")) and events[-1]["type"] == "done"
    assert not of(events, "proposal")
    conv = (await api_client.get(f"/v1/workflows/{wf_id}/copilot")).json()
    assert conv["proposals"] == [] and conv["pending"] is None  # nothing stored that the stream did not show


def test_a_large_workflow_loses_whole_blocks_in_the_turn_context():
    """E-10: the workflow JSON was cut at 30,000 characters mid-token, with nothing telling the model so."""
    from agentcanvas.copilot.service import workflow_text
    from agentcanvas.ir.models import Edge, Endpoint, Node, Workflow

    nodes = [Node(id=f"n_{i}", type="core.set", name=f"s{i}", config={"values": {"a": "x" * 200}}) for i in range(60)]
    edges = [
        Edge(id=f"e_{i}", kind="flow", source=Endpoint(node=f"n_{i}"), target=Endpoint(node=f"n_{i + 1}"))
        for i in range(59)
    ]
    wf = Workflow(id="wf_big", name="big", nodes=nodes, edges=edges)
    text = workflow_text(wf, limit=5_000)
    doc = json.loads(text)  # whole JSON, not cut
    assert len(text) <= 5_000 and 0 < len(doc["nodes"]) < 60 and "of 60 blocks" in doc["truncated"]
    kept = {n["id"] for n in doc["nodes"]}
    assert all({e["source"]["node"], e["target"]["node"]} <= kept for e in doc["edges"])
    assert "truncated" not in json.loads(workflow_text(wf))


@pytest.mark.parametrize("steps", [17])
async def test_a_runaway_loop_stops(app, api_client, steps):
    wf_id = (await new_workflow(api_client, template="ticket_triage"))["id"]
    script(app, *[call("get_workflow") for _ in range(steps)])
    events = await ask(api_client, wf_id, "loop")
    assert len(of(events, "tool")) == 16 and "too many steps" in of(events, "error")[0]["message"]
    assert events[-1]["type"] == "done"


async def test_eval_harness_scores_a_case(engine):
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
    from agentcanvas.copilot.evals import engine_class

    result = await run_case(
        case, model_service(), resources, model, default_registry(), Settings(env="test"), engine_class(engine)
    )
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


async def test_another_persons_conversation_is_not_found(app, api_client):
    """conv_id is checked against the workflow and the caller: someone else's conversation answers 404."""
    from test_roles import act_as

    from agentcanvas.api.deps import current_user

    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]
    script(app, "Hi.")
    await ask(api_client, wf, "hi", mode="ask")
    mine = (await api_client.get(f"/v1/workflows/{wf}/copilot")).json()["id"]
    act_as(app, "admin", user_id="usr_other")
    try:
        base = f"/v1/workflows/{wf}/copilot/threads/{mine}"
        for method, url in (("POST", f"{base}/open"), ("PATCH", base), ("DELETE", base)):
            r = await api_client.request(method, url, json={"title": "x"} if method == "PATCH" else None)
            assert r.status_code == 404, (method, r.text)
        assert (await api_client.get(f"/v1/workflows/{wf}/copilot/threads")).json() == []
    finally:
        app.dependency_overrides.pop(current_user, None)


async def test_usage_events_say_what_the_conversation_cost(app, api_client):
    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]  # its model, claude-haiku, has a price
    script(app, "Hi.")
    events = await ask(api_client, wf, "hi", mode="ask")
    done = events[-1]
    assert done["type"] == "done" and "cost_usd" in done  # 0 tokens from the scripted model: a cost of 0
    assert done["cost_usd"] is None or done["cost_usd"] >= 0


async def test_each_answer_keeps_what_it_cost_and_the_conversation_sums_them(app, api_client):
    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]  # its model, claude-haiku, has a price
    spend = {"input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200}
    for text in ("Hi.", "Hello again."):
        script(app, AIMessage(content=text, usage_metadata=spend))  # type: ignore[arg-type]
        done = (await ask(api_client, wf, "hi", mode="ask"))[-1]
    convo = (await api_client.get(f"/v1/workflows/{wf}/copilot")).json()
    answers = [m for m in convo["messages"] if m["role"] == "assistant"]
    assert [a["usage"] for a in answers] == [{"input_tokens": 1000, "output_tokens": 200}] * 2  # each its own turn
    assert answers[0]["cost_usd"] > 0 and answers[0]["model"]
    assert convo["cost_usd"] == pytest.approx(answers[0]["cost_usd"] + answers[1]["cost_usd"])
    assert done["cost_usd"] == pytest.approx(convo["cost_usd"])  # the live figure matches what a reload shows
    assert convo["usage"]["input_tokens"] == 2000


async def test_each_model_call_is_priced_on_its_own(app, api_client):
    """A long-context price applies when one request's prompt passes the threshold (Claude Haiku 5.5: 100K), never
    to the sum of a turn's calls: five calls of 30K tokens each cost five times one call, not the long-context
    price of 150K."""
    from agentcanvas.runtime.runner import price

    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]
    spend = {"input_tokens": 30000, "output_tokens": 1000, "total_tokens": 31000,
             "input_token_details": {"cache_read": 25000}}  # fmt: skip
    look = [{"name": "search_guide", "args": {"query": "secrets"}, "id": f"call_{i}"} for i in range(4)]
    replies = [AIMessage(content="", tool_calls=[c], usage_metadata=spend) for c in look]  # type: ignore[arg-type]
    script(app, *replies, AIMessage(content="Done.", usage_metadata=spend))  # type: ignore[arg-type]
    done = (await ask(api_client, wf, "how are secrets kept?", mode="ask"))[-1]
    convo = (await api_client.get(f"/v1/workflows/{wf}/copilot")).json()
    [answer] = [m for m in convo["messages"] if m["role"] == "assistant" and m.get("cost_usd") is not None]
    one = price(answer["model"], 30000, 1000, cache_read=25000)
    assert answer["model"] == "anthropic:claude-haiku-5-5" and one is not None
    assert answer["cost_usd"] == pytest.approx(5 * one) and done["cost_usd"] == pytest.approx(5 * one)
    assert answer["usage"] == {"input_tokens": 150000, "output_tokens": 5000, "cache_read_tokens": 125000}


async def test_an_answer_from_the_guide_cites_the_sections_it_read(app, api_client):
    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]  # it has a model the Copilot can use
    script(app, call("search_guide", query="run a model locally with Ollama"), "Use Ollama on your machine.")
    await ask(api_client, wf, "how do I use a local model?", mode="ask")
    answer = (await api_client.get(f"/v1/workflows/{wf}/copilot")).json()["messages"][-1]
    assert answer["role"] == "assistant" and 1 <= len(answer["sources"]) <= 2
    assert all(set(s) == {"page", "section"} and not s["page"].endswith(".md") for s in answer["sources"])
    pages = {p["page"] for p in (await api_client.get("/v1/guide")).json()}
    assert {s["page"] for s in answer["sources"]} <= pages  # each one opens in the app's guide

    script(app, "Hello.")  # an answer that read nothing cites nothing
    await ask(api_client, wf, "hi", mode="ask")
    assert "sources" not in (await api_client.get(f"/v1/workflows/{wf}/copilot")).json()["messages"][-1]


async def test_a_conversation_switches_its_model_within_policy(app, api_client):
    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]
    url = f"/v1/workflows/{wf}/copilot/model"
    chosen = await api_client.put(url, json={"model": "anthropic:claude-opus-5-5"})
    assert chosen.status_code == 200 and chosen.json() == {
        "model": "anthropic:claude-opus-5-5", "origin": "chosen for this chat", "problem": None}  # fmt: skip
    convo = (await api_client.get(f"/v1/workflows/{wf}/copilot")).json()
    assert (
        convo["chosen_model"] == "anthropic:claude-opus-5-5" and convo["model"]["model"] == "anthropic:claude-opus-5-5"
    )
    script(app, "Hi.")
    events = await ask(api_client, wf, "hi", mode="ask")
    assert events[0]["model"] == "anthropic:claude-opus-5-5"  # the turn uses it

    # the project's privacy setting still applies: a hosted model is refused when only local ones are allowed
    s = (await api_client.get("/v1/projects/prj_default/copilot-settings")).json()
    await api_client.put("/v1/projects/prj_default/copilot-settings", json={**s, "privacy": "local_only"})
    try:
        assert (await api_client.put(url, json={"model": "openai:gpt-5.5"})).status_code == 422
    finally:
        await api_client.put("/v1/projects/prj_default/copilot-settings", json={**s, "privacy": "standard"})

    assert (await api_client.put(url, json={"model": None})).json()["origin"] != "chosen for this chat"
    await api_client.delete(f"/v1/workflows/{wf}/copilot")  # "New chat" starts on the project's model
    assert (await api_client.get(f"/v1/workflows/{wf}/copilot")).json()["chosen_model"] is None


async def test_the_model_menu_offers_only_models_set_up_to_answer(app, api_client, monkeypatch):
    import httpx

    from agentcanvas.registry import installed_providers

    for p in installed_providers():
        for name in (p.key_env, p.optional_key_env):
            if name:
                monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(app.state.projects.secrets.setdefault("prj_default", {}), "ANTHROPIC_API_KEY", "sk-ant-test")

    def servers(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/tags":  # Ollama runs, with one model pulled
            return httpx.Response(200, json={"models": [{"name": "qwen3:8b"}]})
        raise httpx.ConnectError("connection refused")  # LM Studio, vLLM and llama.cpp are not running

    monkeypatch.setattr(app.state, "model_list_transport", httpx.MockTransport(servers), raising=False)
    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]
    url = f"/v1/workflows/{wf}/copilot/models"
    offered = {m["id"]: m for m in (await api_client.get(url)).json()}
    assert "anthropic:claude-haiku-4-5" in offered  # its key is a project secret
    assert offered["ollama:qwen3:8b"] == {"id": "ollama:qwen3:8b", "label": "qwen3:8b", "provider": "Ollama (local)",
                                          "local": True}  # fmt: skip
    assert not [m for m in offered if m.split(":")[0] in ("openai", "google_genai", "lmstudio", "fake")]

    # the Copilot's own checks apply: local-only leaves the local models
    s = (await api_client.get("/v1/projects/prj_default/copilot-settings")).json()
    await api_client.put("/v1/projects/prj_default/copilot-settings", json={**s, "privacy": "local_only"})
    try:
        assert [m["id"] for m in (await api_client.get(url)).json()] == ["ollama:qwen3:8b"]
    finally:
        await api_client.put("/v1/projects/prj_default/copilot-settings", json={**s, "privacy": "standard"})

    monkeypatch.setattr(app.state, "demo_models", True, raising=False)  # the demo server answers every model
    assert "openai:gpt-5.5" in {m["id"] for m in (await api_client.get(url)).json()}


async def test_a_proposal_can_carry_workspace_code_written_only_when_accepted(app, api_client):
    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]
    ws = app.state.projects.workspace("prj_default")
    path = f"scripts/clean_{wf.lower()}.py"
    sloppy = "import os\n\n\ndef run(state: dict) -> dict:\n    return {}\n"
    tidy = 'def run(state: dict) -> dict:\n    return {"text": str(state.get("text", "")).strip()}\n'
    script(
        app,
        call("list_workspace_files", prefix="scripts/"),
        call("propose_file", path=path, content=sloppy, summary="Clean the text"),
        call("propose_file", path=path, content=tidy),  # fixes what ruff reported: the latest version is kept
        call("propose_file", path="../outside.py", content="x = 1\n"),
        "I wrote a script that trims the text.",
    )
    events = await ask(api_client, wf, "add a step that trims the text")
    results = [e for e in events if e["type"] == "tool"]
    assert [r["ok"] for r in results] == [True, True, True, False]
    proposal = of(events, "proposal")[0]["proposal"]
    assert [f["path"] for f in proposal["files"]] == [path] and proposal["files"][0]["content"] == tidy
    assert proposal["files"][0]["before"] is None and not (ws.root / path).exists()  # nothing written yet

    url = f"/v1/workflows/{wf}/copilot/proposals/{proposal['id']}"
    for wrong in ({"files": [0, 3]}, {"tests": [1]}):  # E-10: out-of-range indices were silently dropped
        r = await api_client.post(url, json={"status": "accepted", **wrong})
        assert r.status_code == 422 and "keep indices" in r.json()["detail"]
    assert (await api_client.post(url, json={"status": "accepted", "files": [0]})).status_code == 204
    assert (ws.root / path).read_text() == tidy
    log = ws._git("log", "-1", "--format=%s").stdout
    assert log.startswith("Copilot: Clean the text") and "accepted by" in log and path in log
    listed = (await api_client.get(f"/v1/workflows/{wf}/copilot")).json()["proposals"][-1]
    assert listed["files"] == [path] and listed["accepted_files"] == [path]  # paths only, no contents


async def test_accepting_files_edited_since_the_proposal_writes_none_of_them(app, api_client):
    """I-8: accepting wrote the proposed files over whatever the person had changed since the Copilot read them."""
    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]
    ws = app.state.projects.workspace("prj_default")
    edited, fresh = f"scripts/e_{wf.lower()}.py", f"scripts/f_{wf.lower()}.py"
    ws.write(edited, "X = 0\n", "start")
    script(
        app, call("propose_file", path=edited, content="X = 1\n"), call("propose_file", path=fresh, content="Y = 1\n"),
        "Done.",
    )  # fmt: skip
    proposal = of(await ask(api_client, wf, "change x"), "proposal")[0]["proposal"]
    ws.write(edited, "X = 0  # my edit\n", "a person's own edit")
    url = f"/v1/workflows/{wf}/copilot/proposals/{proposal['id']}"
    r = await api_client.post(url, json={"status": "accepted"})
    assert r.status_code == 409 and "changed since the proposal" in r.json()["detail"] and edited in r.json()["detail"]
    assert (ws.root / edited).read_text() == "X = 0  # my edit\n" and not (ws.root / fresh).exists()
    listed = (await api_client.get(f"/v1/workflows/{wf}/copilot")).json()["proposals"][-1]
    assert listed["status"] == "pending"  # still open: ask again, or untick the file
    assert (await api_client.post(url, json={"status": "accepted", "files": [1]})).status_code == 204
    assert (ws.root / fresh).read_text() == "Y = 1\n" and (ws.root / edited).read_text() == "X = 0  # my edit\n"


async def test_unticked_or_rejected_files_are_not_written_and_ask_mode_cannot_propose_them(app, api_client):
    wf = (await new_workflow(api_client, template="ticket_triage"))["id"]
    ws = app.state.projects.workspace("prj_default")
    path = f"tools/t_{wf.lower()}.py"
    script(app, call("propose_file", path=path, content="X = 1\n"), "Done.")
    proposal = of(await ask(api_client, wf, "write a tool"), "proposal")[0]["proposal"]
    url = f"/v1/workflows/{wf}/copilot/proposals/{proposal['id']}"
    assert (await api_client.post(url, json={"status": "accepted", "files": []})).status_code == 204
    assert not (ws.root / path).exists()

    script(app, call("propose_file", path=path, content="X = 1\n"), "I can't change things in Ask mode.")
    events = await ask(api_client, wf, "write a tool", mode="ask")
    assert not of(events, "proposal") and not (ws.root / path).exists()
