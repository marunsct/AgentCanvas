"""Sprint 8 tracing (M6-T6): trace ids, run names = IR names, metadata; "Open trace" redirect."""

from __future__ import annotations

import asyncio

from langchain_core.tracers.context import run_collector_var
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler
from test_publish import make_greeter

from agentcanvas.ir.models import Workflow
from agentcanvas.runtime import tracing


async def test_trace_tree_uses_ir_names_and_metadata(app, api_client):
    wf_id = await make_greeter(api_client)
    wf = Workflow.model_validate((await api_client.get(f"/v1/workflows/{wf_id}")).json()["ir"])
    cb = RunCollectorCallbackHandler()
    token = run_collector_var.set(cb)  # the run task inherits the collector (context variables)
    try:
        h = await app.state.runner.start(wf, input={"who": "Ada"}, origin="test")
    finally:
        run_collector_var.reset(token)
    await asyncio.wait_for(h.done.wait(), 10)
    root = next(r for r in cb.traced_runs if r.parent_run_id is None)
    assert root.id == tracing.trace_id(h.run_id) and root.name == "Greeter"
    meta = root.extra["metadata"]
    assert meta["workflow_id"] == wf_id and meta["run_id"] == h.run_id and meta["origin"] == "test"
    assert "agentcanvas" in root.tags and f"workflow:{wf_id}" in root.tags
    # graph nodes (and so the trace's child runs) are named after the canvas steps
    assert [e["stepName"] for e in h.events if e["type"] == "STEP_STARTED"] == ["greet"]

    run = (await api_client.get(f"/v1/runs/{h.run_id}")).json()
    assert run["traced"] is False  # tracing is off here
    assert (await api_client.get(f"/v1/runs/{h.run_id}/trace", follow_redirects=False)).status_code == 404


async def test_open_trace_redirects(app, api_client, monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test")  # the tracer posts in the background; failures are logged
    monkeypatch.setattr(tracing, "_project_url", lambda: "https://smith.example/o/org/projects/p/prj")
    wf_id = await make_greeter(api_client)
    wf = Workflow.model_validate((await api_client.get(f"/v1/workflows/{wf_id}")).json()["ir"])
    h = await app.state.runner.start(wf, input={"who": "Ada"})
    await asyncio.wait_for(h.done.wait(), 10)
    assert h.events[-1]["traced"] is True
    r = await api_client.get(f"/v1/runs/{h.run_id}/trace", follow_redirects=False)
    assert r.status_code == 307
    assert (
        r.headers["location"] == f"https://smith.example/o/org/projects/p/prj/r/{tracing.trace_id(h.run_id)}?poll=true"
    )
    j = await api_client.get(f"/v1/runs/{h.run_id}/trace", params={"format": "json"})  # the web app's form
    assert j.status_code == 200 and j.json() == {"url": r.headers["location"]}
