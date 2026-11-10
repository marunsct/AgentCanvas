"""Strict resume: every answer names the pause it is for, so two pauses waiting at once (approvals on parallel
branches) are answered separately, in either order, from every place a run can be answered."""

from __future__ import annotations

import json

import pytest
from test_protocols import rpc
from test_publish import bearer
from test_share import ADA, say, share_on

P = "/v1/projects/prj_default/workflows"
SIGNERS = {"n_a": "ok_a", "n_b": "ok_b"}


def events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def parallel(kind: str = "core.approval") -> dict:
    """Two steps after START, side by side: both pause in the same superstep."""
    nodes = []
    for nid, out in SIGNERS.items():
        config = {"show": "who", "output": out} if kind == "core.approval" else {"prompt": f"{nid}?", "output": out}
        nodes.append({"id": nid, "type": kind, "name": f"sign_{nid[-1]}", "config": config})
    edges = []
    for nid in SIGNERS:
        edges.append({"id": f"in_{nid}", "kind": "flow", "source": {"node": "START"}, "target": {"node": nid}})
        edges.append({"id": f"out_{nid}", "kind": "flow", "source": {"node": nid}, "target": {"node": "END"}})
    channels = [{"name": "who"}, *({"name": out} for out in SIGNERS.values())]
    return {"name": "Two signers", "state": {"channels": channels}, "nodes": nodes, "edges": edges}


async def make(api, ir: dict) -> str:
    wf = (await api.post(P, json={"name": ir["name"]})).json()
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": {**ir, "id": wf["id"]}}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    return wf["id"]


async def paused(api, wf: str, thread: str) -> dict[str, str]:
    """Start the run; the canvas step of each pause -> its interrupt id."""
    r = await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "x"}, "thread_id": thread})
    fin = events(r.text)[-1]
    assert fin["status"] == "interrupted", fin
    asked = {i["canvas_node_id"]: i["id"] for i in fin["interrupts"]}
    assert set(asked) == set(SIGNERS) and len(set(asked.values())) == 2
    return asked


async def waiting(api, thread: str) -> set[str]:
    return {i["canvas_node_id"] for i in (await api.get("/v1/inbox")).json() if i["thread_id"] == thread}


ORDERS = pytest.mark.parametrize("order", [["n_a", "n_b"], ["n_b", "n_a"]])


@ORDERS
async def test_two_parallel_approvals_are_decided_separately_in_the_inbox(api_client, order):
    api = api_client
    wf = await make(api, parallel())
    asked = await paused(api, wf, "th_inbox")
    first, second = order
    r = await api.post(f"/v1/inbox/{asked[first]}/decision", params={"stream": "true"}, json={"value": False})
    fin = events(r.text)[-1]
    assert fin["status"] == "interrupted" and [i["id"] for i in fin["interrupts"]] == [asked[second]]
    assert fin["output"][SIGNERS[first]] is False and SIGNERS[second] not in fin["output"]
    assert await waiting(api, "th_inbox") == {second}  # the other one still waits, under the same id
    r = await api.post(f"/v1/inbox/{asked[second]}/decision", params={"stream": "true"}, json={"value": True})
    fin = events(r.text)[-1]
    assert fin["status"] == "success" and fin["output"] == {"who": "x", SIGNERS[first]: False, SIGNERS[second]: True}


@ORDERS
async def test_the_thread_resume_needs_the_interrupt_id_while_two_wait(api_client, order):
    api = api_client
    wf = await make(api, parallel())
    asked = await paused(api, wf, "th_two")
    r = await api.post("/v1/threads/th_two/resume", json={"value": True})
    assert r.status_code == 409, r.text  # refused before anything runs: no guess at which step it approves
    assert "waits for 2 answers" in r.json()["detail"] and sorted(r.json()["pending"]) == sorted(asked.values())
    assert await waiting(api, "th_two") == set(SIGNERS)
    assert (await api.get("/v1/threads/th_two")).json()["status"] == "interrupted"
    assert [x["status"] for x in (await api.get("/v1/threads/th_two/runs")).json()] == ["interrupted"]

    first, second = order
    r = await api.post("/v1/threads/th_two/resume", json={"value": True, "interrupt_id": asked[first]})
    assert events(r.text)[-1]["status"] == "interrupted"
    assert await waiting(api, "th_two") == {second}
    r = await api.post("/v1/threads/th_two/resume", json={"value": False})  # the only one left: no id needed
    fin = events(r.text)[-1]
    assert fin["status"] == "success" and fin["output"] == {"who": "x", SIGNERS[first]: True, SIGNERS[second]: False}
    decided = (await api.get("/v1/inbox", params={"status": "decided"})).json()
    assert {i["canvas_node_id"]: i["decision"] for i in decided} == {first: {"value": True}, second: {"value": False}}


async def test_an_answer_for_a_pause_that_is_not_waiting_is_refused(api_client):
    api = api_client
    wf = await make(api, parallel())
    asked = await paused(api, wf, "th_stale")
    unknown = await api.post("/v1/threads/th_stale/resume", json={"value": True, "interrupt_id": "0" * 32})
    assert unknown.status_code == 404, unknown.text  # it used to run and silently ask both again
    await api.post("/v1/threads/th_stale/resume", json={"value": True, "interrupt_id": asked["n_a"]})
    again = await api.post("/v1/threads/th_stale/resume", json={"value": False, "interrupt_id": asked["n_a"]})
    assert again.status_code == 409 and "already decided" in again.json()["detail"]
    assert await waiting(api, "th_stale") == {"n_b"}


async def test_langgraph_refuses_an_answer_for_a_pause_it_moved_past(app, api_client):
    """The engine's own check (strict resume): an answer that reaches it for a pause no longer waiting fails before
    anything is written, rather than being dropped while the run goes on."""
    from agentcanvas.ir.models import Workflow
    from agentcanvas.runtime.types import ResumeSpec, RunSpec

    api = api_client
    wf_id = await make(api, parallel())
    asked = await paused(api, wf_id, "th_engine")
    await api.post(f"/v1/inbox/{asked['n_a']}/decision", params={"stream": "true"}, json={"value": True})
    runner = app.state.runner
    wf = Workflow.model_validate((await api.get(f"/v1/workflows/{wf_id}")).json()["ir"])
    adapter = runner.adapter_for(wf)
    spec = RunSpec(workflow=wf, thread_id="th_engine", run_id="run_stale", resume=ResumeSpec(False, asked["n_a"]))
    evs = [e async for e in adapter.execute(spec, runner.resources("th_engine", wf))]
    assert evs[-1]["type"] == "RUN_ERROR" and evs[-1]["code"] == "unknown_interrupt", evs[-1]
    assert asked["n_b"] in evs[-1]["message"]
    state = await adapter.get_state(wf, "th_engine", runner.resources("th_engine", wf))
    assert state.values["ok_a"] is True and [i.id for i in state.interrupts] == [asked["n_b"]]  # nothing changed


# ------------------------------------------------------------------------- published workflows ----
async def published(api, ir: dict) -> tuple[str, str]:
    wf = await make(api, ir)
    pub = (await api.post(f"/v1/workflows/{wf}/publish", json={})).json()
    return pub["deployment"]["id"], pub["api_key"]


async def api_paused(api, dep: str, key: str) -> tuple[str, dict[str, str]]:
    r = await api.post(f"/v1/deployments/{dep}/runs/wait", json={"input": {"who": "x"}}, headers=bearer(key))
    res = r.json()
    assert res["status"] == "interrupted", res
    return res["thread_id"], {i["canvas_node_id"]: i["id"] for i in res["interrupts"]}


@ORDERS
async def test_the_deployment_api_answers_parallel_pauses_by_id(api_client, order):
    api = api_client
    dep, key = await published(api, parallel())
    thread, asked = await api_paused(api, dep, key)
    url = f"/v1/deployments/{dep}/threads/{thread}/resume"
    r = await api.post(url, json={"value": True}, headers=bearer(key))
    assert r.status_code == 409 and "interrupt_id" in r.json()["detail"], r.text
    first, second = order
    res = (await api.post(url, json={"value": True, "interrupt_id": asked[first]}, headers=bearer(key))).json()
    assert res["status"] == "interrupted" and [i["id"] for i in res["interrupts"]] == [asked[second]]
    res = (await api.post(url, json={"value": False, "interrupt_id": asked[second]}, headers=bearer(key))).json()
    assert res["status"] == "success" and res["output"][SIGNERS[first]] is True
    assert res["output"][SIGNERS[second]] is False


@ORDERS
async def test_the_mcp_answer_tool_names_the_pause(api_client, order):
    api = api_client
    dep, key = await published(api, parallel())
    url, auth = f"/v1/deployments/{dep}/mcp", bearer(key)
    tools = (await api.post(url, json=rpc("tools/list"), headers=auth)).json()["result"]["tools"]
    call = rpc("tools/call", {"name": tools[0]["name"], "arguments": {"who": "x"}})
    res = (await api.post(url, json=call, headers=auth)).json()["result"]
    assert "Call `answer` once for each, with its interrupt_id." in res["content"][0]["text"]
    thread = res["structuredContent"]["thread_id"]
    asked = {i["canvas_node_id"]: i["id"] for i in res["structuredContent"]["interrupts"]}

    def answer(**args):
        return rpc("tools/call", {"name": "answer", "arguments": {"thread_id": thread, **args}})

    vague = (await api.post(url, json=answer(approve=True), headers=auth)).json()["result"]
    assert vague["isError"] is True and "name the one you answer" in vague["content"][0]["text"]
    first, second = order
    res = (await api.post(url, json=answer(approve=False, interrupt_id=asked[first]), headers=auth)).json()["result"]
    assert res["structuredContent"]["status"] == "interrupted"
    assert [i["id"] for i in res["structuredContent"]["interrupts"]] == [asked[second]]
    res = (await api.post(url, json=answer(approve=True, interrupt_id=asked[second]), headers=auth)).json()["result"]
    out = res["structuredContent"]["output"]
    assert res["structuredContent"]["status"] == "success"
    assert out[SIGNERS[first]] is False and out[SIGNERS[second]] is True


async def test_the_mcp_answer_tool_says_nothing_of_another_deployments_pauses(api_client):
    api = api_client
    dep, key = await published(api, parallel())
    other, other_key = await published(api, parallel())
    thread, theirs = await api_paused(api, other, other_key)  # the other deployment's conversation waits for two
    call = rpc("tools/call", {"name": "answer", "arguments": {"thread_id": thread, "approve": True}})
    res = (await api.post(f"/v1/deployments/{dep}/mcp", json=call, headers=bearer(key))).json()["result"]
    text = res["content"][0]["text"]
    assert res["isError"] is True and "not found" in text and not any(i in text for i in theirs.values())


@ORDERS
async def test_a2a_replies_name_the_pause_they_answer(api_client, order):
    api = api_client
    dep, key = await published(api, parallel())
    url, auth = f"/v1/deployments/{dep}/a2a", bearer(key)
    msg = {"message": {"role": "user", "messageId": "m1", "parts": [{"kind": "data", "data": {"who": "x"}}]}}
    task = (await api.post(url, json=rpc("message/send", msg), headers=auth)).json()["result"]
    assert task["status"]["state"] == "input-required"
    assert '{"interrupt_id": "..."}' in task["status"]["message"]["parts"][0]["text"]
    asked = {i["canvas_node_id"]: i["id"] for i in task["status"]["message"]["parts"][1]["data"]["interrupts"]}

    def reply(n: int, text: str, **data):
        parts: list[dict] = [{"kind": "text", "text": text}]
        if data:
            parts.append({"kind": "data", "data": data})
        message = {"role": "user", "messageId": f"m{n}", "taskId": task["id"], "contextId": task["contextId"],
                   "parts": parts}  # fmt: skip
        return rpc("message/send", {"message": message})

    vague = (await api.post(url, json=reply(2, "yes"), headers=auth)).json()
    assert vague["error"]["code"] == -32602 and "name the one you answer" in vague["error"]["message"]
    first, second = order
    res = (await api.post(url, json=reply(3, "no", interrupt_id=asked[first]), headers=auth)).json()["result"]
    assert res["status"]["state"] == "input-required"
    assert "Reply to this task to continue." in res["status"]["message"]["parts"][0]["text"]  # one left
    res = (await api.post(url, json=reply(4, "yes"), headers=auth)).json()["result"]
    assert res["status"]["state"] == "completed"
    out = res["artifacts"][0]["parts"][0]["data"]
    assert out[SIGNERS[first]] is False and out[SIGNERS[second]] is True


async def test_mcp_forms_ask_each_parallel_pause_in_turn(api_client):
    """MRTR: one form per pause; the answer to the first asks the second."""
    from test_mcp_input import FORMS, modern

    api = api_client
    dep, key = await published(api, parallel())
    url, auth = f"/v1/deployments/{dep}/mcp", bearer(key)
    body, headers = modern("tools/list", {}, FORMS)
    tool = (await api.post(url, json=body, headers={**auth, **headers})).json()["result"]["tools"][0]["name"]
    call = {"name": tool, "arguments": {"who": "x"}}
    seen, res = [], {}
    params = dict(call)
    for _ in range(3):
        body, headers = modern("tools/call", params, FORMS)
        res = (await api.post(url, json=body, headers={**auth, **headers})).json()["result"]
        if res.get("resultType") != "input_required":
            break
        (iid,) = res["inputRequests"]
        seen.append(iid)
        params = {**call, "requestState": res["requestState"],
                  "inputResponses": {iid: {"action": "accept", "content": {"approve": True}}}}  # fmt: skip
    assert len(set(seen)) == 2, seen
    assert res["structuredContent"]["status"] == "success" and res["structuredContent"]["output"]["ok_a"] is True
    assert res["structuredContent"]["output"]["ok_b"] is True


async def test_a_share_visitor_answers_two_questions_in_either_order(api_client):
    api = api_client
    dep, _ = await published(api, parallel("core.human_input"))
    token = (await share_on(api, dep, input_field="who"))["token"]
    evs = events((await say(api, token, "x")).text)
    done = evs[-1]
    assert done["status"] == "interrupted", done
    asked = {q["question"]: q["id"] for q in done["interrupts"]}
    assert set(asked) == {"n_a?", "n_b?"}
    resume = f"/v1/share/{token}/threads/{evs[0]['threadId']}/resume"
    vague = await api.post(resume, json={"visitor": ADA, "text": "Bea"})
    assert vague.status_code == 409 and "name the one you answer" in vague.json()["detail"]
    after = events((await api.post(resume, json={"visitor": ADA, "text": "Bea", "interrupt_id": asked["n_b?"]})).text)
    assert after[-1]["status"] == "interrupted" and [q["id"] for q in after[-1]["interrupts"]] == [asked["n_a?"]]
    after = events((await api.post(resume, json={"visitor": ADA, "text": "Al", "interrupt_id": asked["n_a?"]})).text)
    assert after[-1]["status"] == "success", after[-1]


async def test_an_event_handler_resumes_the_pause_it_names(app, api_client):
    from test_events import delivered, finished

    src = (
        "from agentcanvas.sdk import on\n\n\n"
        '@on("contract.signed")\n'
        "def signed(event, act):\n"
        '    act.resume(event.data["thread_id"], True, interrupt_id=event.data.get("interrupt_id"))\n'
    )
    await api_client.put("/v1/projects/prj_default/files/events/sign.py", json={"content": src})
    wf = await make(api_client, parallel())
    asked = await paused(api_client, wf, "th_event")
    await delivered(app)
    url = "/v1/projects/prj_default/events"
    vague = (await api_client.post(url, json={"type": "contract.signed", "data": {"thread_id": "th_event"}})).json()
    assert vague["handlers"][0]["error"] and "name the one you answer" in vague["handlers"][0]["error"]
    named = {"thread_id": "th_event", "interrupt_id": asked["n_b"]}
    res = (await api_client.post(url, json={"type": "contract.signed", "data": named})).json()
    await finished(app, res["handlers"][0]["runs"])
    assert await waiting(api_client, "th_event") == {"n_a"}
    assert (await api_client.get("/v1/threads/th_event")).json()["values"]["ok_b"] is True
