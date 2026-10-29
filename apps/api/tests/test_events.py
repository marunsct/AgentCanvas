"""Sprint 7 events as code v0 (FR-X-22): handlers, filters, dedup, schemas, dry runs, lifecycle and emitted events."""

from __future__ import annotations

import asyncio
import json

P = "/v1/projects/prj_default"

LEADS = """from agentcanvas.sdk import on


@on("crm.lead.created", filter="event.data.amount > 100", dedup_key="event.data.id")
def qualify(event, act):
    act.start_workflow("Leads", input={"name": event.data["name"]}, thread_id="lead-" + event.data["id"])


@on("run.failed", filter="event.data.workflow_name == 'Fragile'")
def alert(event, act):
    act.start_workflow("Leads", input={"name": "ALERT " + event.data["error"][:20]})


@on("invoice.flagged")
def flagged(event, act):
    act.emit("audit.recorded", {"invoice": event.data["invoice"]})


@on("audit.recorded")
def audited(event, act):
    act.start_workflow("Leads", input={"name": "audit " + event.data["invoice"]})


@on("contract.signed")
def signed(event, act):
    act.resume(event.data["thread_id"], {"approved": True})


@on("loop.ping")
def loop(event, act):
    act.emit("loop.ping", {})
"""


def linear(name: str, nodes: list[dict], channels: list[str]) -> dict:
    edges, prev = [], "START"
    for i, n in enumerate(nodes):
        edges.append({"id": f"e{i}", "kind": "flow", "source": {"node": prev}, "target": {"node": n["id"]}})
        prev = n["id"]
    edges.append({"id": "e_end", "kind": "flow", "source": {"node": prev}, "target": {"node": "END"}})
    return {"name": name, "state": {"channels": [{"name": c} for c in channels]}, "nodes": nodes, "edges": edges}


async def make_wf(api, ir: dict) -> str:
    wf = (await api.post(f"{P}/workflows", json={"name": ir["name"]})).json()
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": {**ir, "id": wf["id"]}}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    return wf["id"]


async def setup(api) -> str:
    await api.put(f"{P}/files/events/leads.py", json={"content": LEADS})
    greet = {"id": "n_g", "type": "core.set", "name": "greet", "config": {"values": {"out": "Hello {{ state.name }}"}}}
    return await make_wf(api, linear("Leads", [greet], ["name", "out"]))


async def finished(app, run_ids: list[str]) -> None:
    for rid in run_ids:
        await asyncio.wait_for(app.state.runner.handles[rid].done.wait(), 10)


async def delivered(app) -> None:
    """A run's events reach handlers after it is done (they may start its thread's next run): wait for them."""
    tasks = [h.task for h in list(app.state.runner.handles.values()) if h.task]
    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)


def events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


async def test_handler_filter_dedup_and_dry_run(app, api_client):
    await setup(api_client)
    lead = {"type": "crm.lead.created", "data": {"id": "L1", "name": "Ada", "amount": 500}}
    dry = (await api_client.post(f"{P}/events/dry-run", json=lead)).json()
    assert dry["status"] == "dry_run" and dry["event_id"] is None
    assert dry["handlers"][0]["actions"] == [
        {"action": "start_workflow", "workflow": "Leads", "input": {"name": "Ada"}, "thread_id": "lead-L1"}
    ]
    assert (await api_client.get(f"{P}/events")).json() == []  # dry runs are not logged, nothing started

    first = (await api_client.post(f"{P}/events", json=lead)).json()
    assert first["status"] == "handled"
    runs = first["handlers"][0]["runs"]
    await finished(app, runs)
    thread = (await api_client.get("/v1/threads/lead-L1")).json()
    assert thread["values"]["out"] == "Hello Ada"

    again = (await api_client.post(f"{P}/events", json=lead)).json()  # redelivered: dedup key already seen
    assert again["handlers"][0]["skipped"] == "duplicate" and again["status"] == "ignored"
    small = (
        await api_client.post(f"{P}/events", json={**lead, "data": {**lead["data"], "id": "L2", "amount": 5}})
    ).json()
    assert small["handlers"][0]["skipped"] == "filter" and small["status"] == "ignored"
    log = (await api_client.get(f"{P}/events")).json()
    assert [e["status"] for e in log] == ["ignored", "ignored", "handled"]


async def test_event_schema_and_e081(api_client):
    await setup(api_client)
    schema = {
        "type": "object",
        "required": ["id", "amount"],
        "properties": {"id": {"type": "string"}, "amount": {"type": "number"}},
    }
    await api_client.put(f"{P}/files/events/schemas/crm.lead.created.json", json={"content": json.dumps(schema)})
    bad = await api_client.post(
        f"{P}/events", json={"type": "crm.lead.created", "data": {"id": "L9", "amount": "lots"}}
    )
    assert bad.status_code == 422
    cat = (await api_client.get(f"{P}/workspace/catalog")).json()
    assert cat["schemas"] == ["crm.lead.created"]
    assert [p["code"] for p in cat["problems"] if p.get("code")] == []  # amount and id are declared
    schema["properties"].pop("amount")
    await api_client.put(f"{P}/files/events/schemas/crm.lead.created.json", json={"content": json.dumps(schema)})
    cat = (await api_client.get(f"{P}/workspace/catalog")).json()
    assert any(p.get("code") == "E081" and "'amount'" in p["message"] for p in cat["problems"])


async def test_lifecycle_emit_and_resume(app, api_client):
    await setup(api_client)
    # run.failed of a workflow -> the alert handler starts another workflow
    await api_client.put(
        f"{P}/files/scripts/boom.py", json={"content": "def run(state):\n    raise ValueError('disk full')\n"}
    )
    boom = {"id": "n_b", "type": "core.script", "name": "boom", "config": {"import_path": "scripts.boom:run"}}
    fragile = await make_wf(api_client, linear("Fragile", [boom], ["out"]))
    fin = events((await api_client.post(f"/v1/workflows/{fragile}/runs/stream", json={})).text)[-1]
    assert fin["type"] == "RUN_ERROR"
    await delivered(app)
    log = (await api_client.get(f"{P}/events")).json()
    failed = next(e for e in log if e["type"] == "run.failed")
    assert failed["status"] == "handled"
    assert failed["data"]["workflow_name"] == "Fragile"
    await finished(app, failed["results"][0]["runs"])

    # sdk.emit inside a run -> invoice.flagged -> emit audit.recorded -> start a workflow
    flag_src = (
        "from agentcanvas.sdk import emit\n\n\n"
        "def run(state):\n    emit('invoice.flagged', {'invoice': 'INV-7'})\n    return {'out': 'flagged'}\n"
    )
    await api_client.put(f"{P}/files/scripts/flag.py", json={"content": flag_src})
    flag = {"id": "n_f", "type": "core.script", "name": "flag", "config": {"import_path": "scripts.flag:run"}}
    flagger = await make_wf(api_client, linear("Flagger", [flag], ["out"]))
    await api_client.post(f"/v1/workflows/{flagger}/runs/stream", json={})
    await delivered(app)
    log = (await api_client.get(f"{P}/events")).json()
    audit = next(e for e in log if e["type"] == "audit.recorded")
    assert audit["source"] == "emit" and audit["status"] == "handled"
    assert next(e for e in log if e["type"] == "invoice.flagged")["source"] == "run"

    # an external event resumes a run waiting for approval
    ask = {"id": "n_a", "type": "core.approval", "name": "sign", "config": {"show": "out", "output": "ok"}}
    signer = await make_wf(api_client, linear("Contract", [ask], ["out", "ok"]))
    fin = events(
        (await api_client.post(f"/v1/workflows/{signer}/runs/stream", json={"thread_id": "th_contract"})).text
    )[-1]
    assert fin["status"] == "interrupted"
    await delivered(app)
    log = (await api_client.get(f"{P}/events")).json()
    assert (
        any(e["type"] == "interrupt.raised" and e["status"] == "ignored" for e in log) is False
    )  # no subscriber: not logged
    res = (
        await api_client.post(f"{P}/events", json={"type": "contract.signed", "data": {"thread_id": "th_contract"}})
    ).json()
    await finished(app, res["handlers"][0]["runs"])
    assert (await api_client.get("/v1/threads/th_contract")).json()["values"]["ok"] is True

    # emit loops stop: the innermost delivery records the error
    await api_client.post(f"{P}/events", json={"type": "loop.ping", "data": {}})
    pings = [e for e in (await api_client.get(f"{P}/events")).json() if e["type"] == "loop.ping"]
    assert len(pings) == 6  # the original event + 5 levels of re-emission
    assert any(e["status"] == "error" and "levels deep" in (e["results"][0]["error"] or "") for e in pings)


async def test_a_handler_continues_the_thread_of_the_run_that_raised_its_event(app, api_client):
    """Handlers run once the run is over and its thread free: one may resume that very thread."""
    src = (
        "from agentcanvas.sdk import on\n\n\n"
        '@on("interrupt.raised", filter="event.data.workflow_name == \'AutoSign\'")\n'
        'def approve(event, act):\n    act.resume(event.data["thread_id"], {"approved": True})\n'
    )
    await api_client.put(f"{P}/files/events/auto.py", json={"content": src})
    ask = {"id": "n_a", "type": "core.approval", "name": "sign", "config": {"show": "out", "output": "ok"}}
    wf = await make_wf(api_client, linear("AutoSign", [ask], ["out", "ok"]))
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"thread_id": "th_auto"})
    assert events(r.text)[-1]["status"] == "interrupted", r.text  # the stream does not wait for the handler
    await delivered(app)
    raised = next(e for e in (await api_client.get(f"{P}/events")).json() if e["type"] == "interrupt.raised")
    assert raised["status"] == "handled", raised
    await finished(app, raised["results"][0]["runs"])
    assert (await api_client.get("/v1/threads/th_auto")).json()["values"]["ok"] is True


async def test_sandboxed_handler(app, api_client):
    app.state.settings.script_execution = "subprocess"
    app.state.projects._resources.clear()
    await setup(api_client)
    lead = {"type": "crm.lead.created", "data": {"id": "S1", "name": "Sandboxed", "amount": 900}}
    res = (await api_client.post(f"{P}/events", json=lead)).json()
    assert res["status"] == "handled", res
    await finished(app, res["handlers"][0]["runs"])
    assert (await api_client.get("/v1/threads/lead-S1")).json()["values"]["out"] == "Hello Sandboxed"


async def test_resume_only_reaches_threads_of_the_handlers_project(app, api_client):
    """A handler's `act.resume` naming a missing thread, or another project's, fails instead of resuming it."""
    await setup(api_client)
    other = (await api_client.post("/v1/projects", json={"name": "Elsewhere"})).json()["id"]
    ask = {"id": "n_a", "type": "core.approval", "name": "sign", "config": {"show": "out", "output": "ok"}}
    ir = linear("Theirs", [ask], ["out", "ok"])
    wf = (await api_client.post(f"/v1/projects/{other}/workflows", json={"name": "Theirs"})).json()
    r = await api_client.put(
        f"/v1/workflows/{wf['id']}", json={"ir": {**ir, "id": wf["id"]}}, headers={"If-Match": "1"}
    )
    assert r.status_code == 200, r.text
    await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"thread_id": "th_theirs"})
    for thread in ("th_theirs", "th_nowhere"):
        res = (
            await api_client.post(f"{P}/events", json={"type": "contract.signed", "data": {"thread_id": thread}})
        ).json()
        assert res["status"] == "error", res
        assert "not found" in res["handlers"][0]["error"]
    assert (await api_client.get("/v1/threads/th_theirs")).json()["values"].get("ok") is None


async def test_resume_continues_a_published_conversation_on_its_version(app, api_client):
    """`act.resume` on a deployment's thread runs the deployment's pinned version, not the draft."""
    from agentcanvas.services.projects import PROJECT_OF

    await setup(api_client)
    ask = {"id": "n_a", "type": "core.approval", "name": "sign", "config": {"show": "out", "output": "ok"}}
    signer = await make_wf(api_client, linear("Contract", [ask], ["out", "ok"]))
    pub = (await api_client.post(f"/v1/workflows/{signer}/publish", json={})).json()
    dep, key = pub["deployment"]["id"], pub["api_key"]
    started = await api_client.post(
        f"/v1/deployments/{dep}/runs/wait", json={"input": {}}, headers={"Authorization": f"Bearer {key}"}
    )
    thread = started.json()["thread_id"]
    assert started.json()["status"] == "interrupted"
    PROJECT_OF.pop(signer, None)  # a fresh server: nothing registered the workflow's project yet
    res = (await api_client.post(f"{P}/events", json={"type": "contract.signed", "data": {"thread_id": thread}})).json()
    assert res["status"] == "handled", res
    await finished(app, res["handlers"][0]["runs"])
    h = app.state.runner.handles[res["handlers"][0]["runs"][0]]
    assert h.status == "success" and h.deployment_id == dep and h.version_id
    assert PROJECT_OF[signer] == "prj_default"
    assert (await api_client.get(f"/v1/threads/{thread}")).json()["values"]["ok"] is True


async def test_the_event_log_pages_back_through_older_events_and_filters_by_type(app, api_client):
    await setup(api_client)
    for i in range(5):
        kind = "crm.lead.created" if i % 2 else "crm.lead.lost"
        assert (await api_client.post(f"{P}/events", json={"type": kind, "data": {"id": f"P{i}"}})).status_code == 200
    everything = [e["id"] for e in (await api_client.get(f"{P}/events")).json()]
    walked, before = [], None
    for _ in range(5):
        page = (
            await api_client.get(f"{P}/events", params={"limit": 2, **({"before": before} if before else {})})
        ).json()
        walked += [e["id"] for e in page]
        if len(page) < 2:
            break
        before = page[-1]["id"]
    assert walked == everything and len(everything) == 5
    lost = (await api_client.get(f"{P}/events", params={"type": "crm.lead.lost"})).json()
    assert len(lost) == 3 and {e["type"] for e in lost} == {"crm.lead.lost"}
    assert (await api_client.get(f"{P}/events", params={"before": "evt_nope"})).status_code == 422
    # C-11: another project's event is unknown here too, not a page that tells it exists
    other = (await api_client.post("/v1/projects", json={"name": "Elsewhere"})).json()["id"]
    foreign = await api_client.get(f"/v1/projects/{other}/events", params={"before": everything[0]})
    assert foreign.status_code == 422, foreign.text


async def test_an_event_emitted_by_code_in_a_sandbox_reaches_the_run_and_the_handlers(app, api_client):
    """`sdk.emit` in sandboxed code has no run to write to: the child records the events, the host emits them into
    the run (they used to fail the step, so scripts that emitted broke under the default sandbox)."""
    app.state.settings.script_execution = "subprocess"
    app.state.projects._resources.clear()
    await setup(api_client)
    flag_src = (
        "from agentcanvas.sdk import emit\n\n\n"
        "def run(state):\n    emit('invoice.flagged', {'invoice': 'INV-9'})\n    return {'out': 'flagged'}\n"
    )
    await api_client.put(f"{P}/files/scripts/flag_boxed.py", json={"content": flag_src})
    flag = {"id": "n_f", "type": "core.script", "name": "flag", "config": {"import_path": "scripts.flag_boxed:run"}}
    flagger = await make_wf(api_client, linear("Boxed flagger", [flag], ["out"]))
    stream = events((await api_client.post(f"/v1/workflows/{flagger}/runs/stream", json={})).text)
    assert stream[-1]["type"] == "RUN_FINISHED", stream[-1]
    assert any(e["type"] == "CUSTOM" and (e.get("value") or {}).get("ac_event") == "invoice.flagged" for e in stream)
    await delivered(app)
    log = (await api_client.get(f"{P}/events")).json()
    flagged = next(e for e in log if e["type"] == "invoice.flagged")
    assert flagged["source"] == "run" and flagged["data"] == {"invoice": "INV-9"}
