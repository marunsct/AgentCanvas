"""/v1 runner: durable runs, inbox, time travel, breakpoints, cancel, policies (Sprint 2)."""

import asyncio
import json

import httpx
import pytest

from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.testing import scripted, tool_call
from examples.scripts import CALLS

P = "/v1/projects/prj_default/workflows"


def events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def triage_resources():
    per_thread: dict[str, Resources] = {}

    def factory(thread_id: str, wf) -> Resources:
        if thread_id not in per_thread:  # scripted fakes are stateful: one script per thread (FAQ P-03)
            per_thread[thread_id] = Resources(
                overrides={
                    "fast_model": scripted("high" if "high" in thread_id else "low"),
                    "smart_model": scripted(tool_call("send_reply", {"text": "Hi"}), "Reply sent."),
                }
            )
        return per_thread[thread_id]

    return factory


@pytest.fixture
async def triage_api(app, api_client):
    app.state.resources_factory = triage_resources()
    r = await api_client.post(P, json={"name": "Support triage", "template": "support_triage"})
    assert r.status_code == 201
    api_client.wf_id = r.json()["id"]  # type: ignore[attr-defined]
    return api_client


async def make(api, ir: dict) -> str:
    wf = (await api.post(P, json={"name": ir.get("name", "wf")})).json()
    ir = {**ir, "id": wf["id"]}
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    return wf["id"]


def linear(*nodes: dict, channels=("out",), settings=None, context=None) -> dict:
    edges, prev = [], "START"
    for i, n in enumerate(nodes):
        edges.append({"id": f"e{i}", "kind": "flow", "source": {"node": prev}, "target": {"node": n["id"]}})
        prev = n["id"]
    edges.append({"id": "e_end", "kind": "flow", "source": {"node": prev}, "target": {"node": "END"}})
    ir = {"name": "t", "state": {"channels": [{"name": c} for c in channels]}, "nodes": list(nodes), "edges": edges}
    if settings:
        ir["settings"] = settings
    if context:
        ir["context"] = {"fields": context}
    return ir


def script(nid: str, path: str, **extra) -> dict:
    return {"id": nid, "type": "core.script", "name": nid.removeprefix("n_"), "config": {"import_path": path}, **extra}


# ----------------------------------------------------------------- happy path ----
async def test_run_pauses_for_tool_approval_and_inbox_resumes(triage_api):
    api, wf = triage_api, triage_api.wf_id
    r = await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": "Invoice?"}, "thread_id": "th_low"})
    evs = events(r.text)
    assert r.headers["x-thread-id"] == "th_low" and evs[0]["type"] == "RUN_STARTED"
    assert [e["canvas_node_id"] for e in evs if e["type"] == "STEP_STARTED"] == ["n_cls", "n_rt", "n_ag"]
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1))
    fin = evs[-1]
    assert fin["type"] == "RUN_FINISHED" and fin["outcome"] == "interrupt" and fin["status"] == "interrupted"
    assert fin["interrupts"][0]["kind"] == "tool_approval" and fin["interrupts"][0]["canvas_node_id"] == "n_ag"

    inbox = (await api.get("/v1/inbox", params={"workflow_id": wf})).json()
    assert len(inbox) == 1 and inbox[0]["payload"]["action_requests"][0]["name"] == "send_reply"
    assert inbox[0]["workflow_id"] == wf and inbox[0]["workflow_name"] == "Support triage"
    assert (await api.get("/v1/threads/th_low")).json()["status"] == "interrupted"

    r = await api.post(
        f"/v1/inbox/{inbox[0]['id']}/decision",
        params={"stream": "true"},
        json={"value": {"decisions": [{"type": "approve"}]}},
    )
    assert events(r.text)[-1]["outcome"] == "success"
    assert (await api.get("/v1/inbox")).json() == []
    decided = (await api.get("/v1/inbox", params={"status": "decided"})).json()
    assert decided[0]["decision"] == {"decisions": [{"type": "approve"}]}
    thread = (await api.get("/v1/threads/th_low")).json()
    assert thread["status"] == "idle" and thread["values"]["messages"][-1]["content"] == "Reply sent."
    runs = (await api.get("/v1/threads/th_low/runs")).json()
    assert [x["status"] for x in runs] == ["interrupted", "success"]


async def test_approval_node_resume_via_thread_endpoint(triage_api):
    api, wf = triage_api, triage_api.wf_id
    r = await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": "Down!"}, "thread_id": "th_high"})
    fin = events(r.text)[-1]
    assert fin["interrupts"][0] | {"id": None} == {
        "id": None,
        "kind": "approval",
        "canvas_node_id": "n_ok",
        "payload": {"kind": "approval", "canvas_node_id": "n_ok", "value": "ESCALATED: Down!"},
    }
    r = await api.post("/v1/threads/th_high/resume", json={"value": {"approved": True}})
    fin = events(r.text)[-1]
    assert fin["status"] == "success" and fin["output"]["approved"] is True


async def test_resume_survives_api_restart(settings, triage):
    """M1-T6 AC: restart the API during a paused run; the interrupt is still there and resume works."""
    from agentcanvas.api.main import create_app

    factory = triage_resources()

    async def boot():
        app = create_app(settings)
        app.state.resources_factory = factory
        return app

    app1 = await boot()
    async with app1.router.lifespan_context(app1):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app1), base_url="http://t") as api:
            wf = (await api.post(P, json={"name": "t", "template": "support_triage"})).json()["id"]
            r = await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": "x"}, "thread_id": "th_r"})
            assert events(r.text)[-1]["outcome"] == "interrupt"
    app2 = await boot()  # new process-equivalent: fresh runner, adapter persistence reopened
    async with app2.router.lifespan_context(app2):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://t") as api:
            state = (await api.get("/v1/threads/th_r")).json()
            assert state["status"] == "interrupted" and state["next"] == ["n_ag"] and state["interrupts"]
            r = await api.post("/v1/threads/th_r/resume", json={"value": {"decisions": [{"type": "approve"}]}})
            assert events(r.text)[-1]["status"] == "success"


# ---------------------------------------------------------------- time travel ----
async def test_history_state_edit_and_fork_take_other_branch(triage_api):
    api, wf = triage_api, triage_api.wf_id
    await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": "Where?"}, "thread_id": "th_tt"})
    history = (await api.get("/v1/threads/th_tt/history")).json()
    assert len(history) >= 4 and history[0]["next"] == ["n_ag"]
    after_classify = next(c for c in history if c["next"] == ["n_rt"])
    assert (
        after_classify["writes"] == {"n_rt": {}}
        or after_classify["writes"] is None
        or "n_rt" in after_classify["writes"]
    )

    r = await api.post(
        "/v1/threads/th_tt/fork",
        json={"checkpoint_id": after_classify["checkpoint_id"], "values": {"urgency": "high"}, "as_node": "n_cls"},
    )
    evs = events(r.text)
    assert [e["canvas_node_id"] for e in evs if e["type"] == "STEP_STARTED"] == ["n_rt", "n_esc", "n_ok"]
    assert evs[-1]["interrupts"][0]["canvas_node_id"] == "n_ok"
    assert (await api.get("/v1/threads/th_tt")).json()["values"]["summary"] == "ESCALATED: Where?"


async def test_fork_copy_leaves_original_thread_untouched(triage_api):
    api, wf = triage_api, triage_api.wf_id
    await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": "Q"}, "thread_id": "th_orig"})
    cp = next(c for c in (await api.get("/v1/threads/th_orig/history")).json() if c["next"] == ["n_rt"])
    r = await api.post(
        "/v1/threads/th_orig/fork",
        json={"checkpoint_id": cp["checkpoint_id"], "mode": "copy", "values": {"urgency": "high"}, "as_node": "n_cls"},
    )
    new_thread = r.headers["x-thread-id"]
    assert new_thread != "th_orig" and events(r.text)[-1]["interrupts"][0]["canvas_node_id"] == "n_ok"
    assert (await api.get("/v1/threads/th_orig")).json()["next"] == ["n_ag"]  # original still waits for the tool
    assert (await api.get(f"/v1/threads/{new_thread}")).json()["next"] == ["n_ok"]


async def test_state_edit_creates_checkpoint(triage_api):
    api, wf = triage_api, triage_api.wf_id
    await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": "Q"}, "thread_id": "th_edit"})
    before = len((await api.get("/v1/threads/th_edit/history")).json())
    r = await api.post("/v1/threads/th_edit/state", json={"values": {"summary": "edited"}})
    assert r.json()["checkpoint_id"]
    assert len((await api.get("/v1/threads/th_edit/history")).json()) == before + 1
    assert (await api.get("/v1/threads/th_edit")).json()["values"]["summary"] == "edited"


# ---------------------------------------------------------------- breakpoints ----
async def test_breakpoint_pauses_before_node_and_continue_proceeds(triage_api):
    api, wf = triage_api, triage_api.wf_id
    body = {"input": {"ticket": "Q"}, "thread_id": "th_bp", "mode": "debug", "breakpoints": {"before": ["n_rt"]}}
    fin = events((await api.post(f"/v1/workflows/{wf}/runs/stream", json=body)).text)[-1]
    assert fin["breakpoint"] is True and fin["next"] == ["n_rt"] and fin["interrupts"] == []
    assert (await api.get("/v1/inbox")).json() == []  # breakpoints are not inbox items
    fin = events((await api.post("/v1/threads/th_bp/continue")).text)[-1]
    assert fin["breakpoint"] is False and fin["interrupts"][0]["canvas_node_id"] == "n_ag"


async def test_breakpoints_need_debug_mode(triage_api):
    r = await triage_api.post(
        f"/v1/workflows/{triage_api.wf_id}/runs/stream", json={"input": {}, "breakpoints": {"before": ["n_rt"]}}
    )
    assert r.status_code == 422


# ------------------------------------------------------ cancel, attach, 409 ----
async def test_cancel_reattach_and_double_texting(api_client):
    wf = await make(api_client, linear(script("n_slow", "examples.scripts:slow"), channels=("out", "seconds")))
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"seconds": 30}, "stream": False})
    assert r.status_code == 202
    run_id, thread_id = r.json()["run_id"], r.json()["thread_id"]
    busy = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"thread_id": thread_id, "stream": False})
    assert busy.status_code == 409 and busy.json()["run_id"] == run_id
    await asyncio.sleep(0.2)
    assert (await api_client.post(f"/v1/runs/{run_id}/cancel")).json() == {"cancelled": True}
    evs = events((await api_client.get(f"/v1/runs/{run_id}/events")).text)  # re-attach replays the buffer
    assert evs[0]["type"] == "RUN_STARTED" and evs[-1] == evs[-1] | {"type": "RUN_ERROR", "code": "cancelled"}
    assert (await api_client.get(f"/v1/runs/{run_id}")).json()["status"] == "cancelled"
    assert (await api_client.get(f"/v1/threads/{thread_id}")).json()["status"] == "idle"
    tail = events((await api_client.get(f"/v1/runs/{run_id}/events", params={"after": len(evs) - 1})).text)
    assert tail == evs[-1:]


# ------------------------------------------------------------ policies & limits ----
async def test_recursion_limit_is_a_readable_error(api_client):
    ir = linear(script("n_a", "examples.scripts:bump"), channels=("n", "m"), settings={"recursion_limit": 5})
    ir["edges"] = [e for e in ir["edges"] if e["id"] != "e_end"] + [
        {"id": "loop", "kind": "flow", "source": {"node": "n_a"}, "target": {"node": "n_a"}}
    ]
    ir["nodes"].append({"id": "n_exit", "type": "core.set", "name": "never", "config": {"values": {"m": "0"}}})
    ir["edges"].append({"id": "x", "kind": "flow", "source": {"node": "n_exit"}, "target": {"node": "END"}})
    ir["edges"].append({"id": "y", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_exit"}})
    wf = await make(api_client, ir)
    fin = events((await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={})).text)[-1]
    assert fin["type"] == "RUN_ERROR" and fin["code"] == "recursion_limit" and "5 steps" in fin["message"]


async def test_retry_policy_recovers_flaky_node(api_client):
    CALLS["flaky"] = 0
    node = script("n_f", "examples.scripts:flaky", policies={"retry": {"max_attempts": 3, "initial_interval": 0.01}})
    wf = await make(api_client, linear(node))
    fin = events((await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={})).text)[-1]
    assert fin["status"] == "success" and fin["output"]["out"] == "finally" and CALLS["flaky"] == 3


async def test_timeout_routes_to_error_handler(api_client):
    slow = script(
        "n_slow", "examples.scripts:slow", policies={"timeout": {"run_timeout": 0.2}, "error_route": "n_recover"}
    )
    recover = {"id": "n_recover", "type": "core.set", "name": "recover", "config": {"values": {"out": "recovered"}}}
    ir = linear(slow, channels=("out", "seconds", "last_error"))
    ir["nodes"].append(recover)
    ir["edges"].append({"id": "r_end", "kind": "flow", "source": {"node": "n_recover"}, "target": {"node": "END"}})
    wf = await make(api_client, ir)
    evs = events((await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"seconds": 5}})).text)
    fin = evs[-1]
    assert any(e.get("name") == "ac.warning" for e in evs)
    assert fin["status"] == "success" and fin["output"]["out"] == "recovered"
    assert "NodeTimeoutError" in fin["output"]["last_error"] and fin["output"]["last_error"].startswith("slow:")


async def test_cache_policy_skips_second_execution(api_client):
    CALLS["counted"] = 0
    wf = await make(api_client, linear(script("n_c", "examples.scripts:counted", policies={"cache": {"ttl": 60}})))
    for _ in range(2):
        fin = events((await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={})).text)[-1]
        assert fin["output"]["out"] == "call 1"
    assert CALLS["counted"] == 1


async def test_output_channels_limit_run_output(api_client):
    nodes = [{"id": "n_s", "type": "core.set", "name": "s", "config": {"values": {"a": "1", "b": "2"}}}]
    wf = await make(api_client, linear(*nodes, channels=("a", "b"), settings={"output_channels": ["b"]}))
    fin = events((await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={})).text)[-1]
    assert fin["output"] == {"b": "2"}


async def test_context_from_authenticated_user(api_client):
    ctx = [{"name": "user_id", "source": "user"}, {"name": "locale", "required": True}]
    wf = await make(api_client, linear(script("n_w", "examples.scripts:whoami"), context=ctx))
    assert (await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={})).status_code == 422  # locale missing
    body = {"context": {"locale": "en", "user_id": "attacker"}}  # client-supplied user_id is ignored
    fin = events((await api_client.post(f"/v1/workflows/{wf}/runs/stream", json=body)).text)[-1]
    assert fin["output"]["out"] == "user=usr_dev"


async def test_resume_keeps_the_original_run_context(app, api_client):
    """A reviewer resuming someone else's run must not become the run's user (per-user memory namespaces)."""
    from agentcanvas.api.deps import CurrentUser, current_user

    ask = {
        "id": "n_ask",
        "type": "core.human_input",
        "name": "ask",
        "config": {"prompt": "Proceed?", "output": "answer"},
    }
    ctx = [{"name": "user_id", "source": "user"}]
    wf = await make(
        api_client, linear(ask, script("n_w", "examples.scripts:whoami"), channels=("out", "answer"), context=ctx)
    )
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"thread_id": "th_ctx"})
    fin = events(r.text)[-1]
    assert fin["status"] == "interrupted" and fin["interrupts"][0]["kind"] == "input"
    from agentcanvas.db.models import AppUser

    async with app.state.db.sessionmaker() as s:
        s.add(AppUser(id="usr_reviewer", oidc_subject="reviewer", email="reviewer@example.com"))
        await s.commit()
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_reviewer", org_id="org_default", role="admin")
    try:
        r = await api_client.post("/v1/threads/th_ctx/resume", json={"value": "yes"})
    finally:
        app.dependency_overrides.pop(current_user)
    fin = events(r.text)[-1]
    assert fin["status"] == "success" and fin["output"] == {"out": "user=usr_dev", "answer": "yes"}


async def test_invalid_workflow_cannot_run(api_client):
    wf = (await api_client.post(P, json={"name": "bad", "template": "classify_and_route"})).json()
    ir = wf["ir"] | {"edges": [e for e in wf["ir"]["edges"] if e["id"] != "w1"]}
    await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": "1"})
    r = await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={})
    assert r.status_code == 422 and r.json()["errors"][0]["code"] == "E001"


async def test_thread_list_and_delete(triage_api):
    api, wf = triage_api, triage_api.wf_id
    for t in ("th_a", "th_b"):
        await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": t}, "thread_id": t})
    page = (await api.get("/v1/threads", params={"workflow_id": wf, "status": "interrupted"})).json()
    assert {t["id"] for t in page["items"]} == {"th_a", "th_b"} and page["items"][0]["title"] in ("th_a", "th_b")
    assert (await api.delete("/v1/threads/th_a")).status_code == 204
    assert (await api.get("/v1/threads/th_a")).status_code == 404
    assert [t["id"] for t in (await api.get("/v1/threads", params={"workflow_id": wf})).json()["items"]] == ["th_b"]


async def test_durability_override_is_accepted(triage_api):
    r = await triage_api.post(
        f"/v1/workflows/{triage_api.wf_id}/runs/stream", json={"input": {"ticket": "x"}, "durability": "sync"}
    )
    assert events(r.text)[-1]["outcome"] == "interrupt"


async def test_explain_mode_narrates_steps_and_router_decisions(triage_api):
    api, wf = triage_api, triage_api.wf_id
    await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": "Down!"}, "thread_id": "th_high_x"})
    steps = (await api.get("/v1/threads/th_high_x/explain")).json()
    assert [s["ran"][0]["node_id"] for s in steps] == ["n_cls", "n_rt", "n_esc", "n_ok"]
    assert steps[0]["ran"][0]["wrote"] == {"urgency": "high"}
    decision = steps[1]["ran"][0]["decision"]
    assert decision == {
        "route": "high",
        "condition": 'state.urgency == "high"',
        "reason": '`state.urgency == "high"` is true',
        "target": "n_esc",
        "target_name": "escalate",
    }
    assert "chose route 'high' → escalate" in steps[1]["text"]
    assert steps[-1]["ran"][0]["completed"] is False and "waiting" in steps[-1]["text"]
    hist = (await api.get("/v1/threads/th_high_x/history", params={"include_values": True})).json()
    assert hist[0]["values"]["summary"] == "ESCALATED: Down!"
    assert "values" not in (await api.get("/v1/threads/th_high_x/history")).json()[0]
