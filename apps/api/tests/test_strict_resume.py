"""Strict resume: every answer names the pause it is for, so two pauses waiting at once (approvals on parallel
branches) are answered separately, in either order."""

from __future__ import annotations

import json

import pytest
from test_publish import bearer

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
