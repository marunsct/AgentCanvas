import json

import httpx
import pytest

from agentcanvas.api import app as api
from agentcanvas.runtime.nodes import Resources
from agentcanvas.testing import scripted, tool_call


def parse_sse(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


@pytest.fixture
async def client(triage):
    per_thread: dict[str, Resources] = {}

    def factory(thread_id: str) -> Resources:
        if thread_id not in per_thread:  # scripted fakes are stateful: one script per thread
            per_thread[thread_id] = Resources(
                overrides={
                    "fast_model": scripted("low"),
                    "smart_model": scripted(tool_call("send_reply", {"text": "Hi"}), "Reply sent."),
                }
            )
        return per_thread[thread_id]

    api.S.resources_factory = factory
    async with api.lifespan(api.app):
        transport = httpx.ASGITransport(app=api.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/workflows", json=triage.model_dump())
            assert r.status_code == 200 and r.json()["diagnostics"] == []
            yield c


async def test_run_pause_resume(client):
    r = await client.post(
        "/workflows/wf_support_triage/runs/stream", json={"input": {"ticket": "Invoice?"}, "thread_id": "t1"}
    )
    events = parse_sse(r.text)
    types = [e["type"] for e in events]
    assert types[0] == "RUN_STARTED" and types[-1] == "RUN_FINISHED"
    assert events[-1]["outcome"] == "interrupt"
    started = [e["canvas_node_id"] for e in events if e["type"] == "STEP_STARTED"]
    assert started == ["n_cls", "n_rt", "n_ag"]  # canvas lights up in this order
    assert any(e.get("name") == "ac.interrupt" for e in events)

    state = (await client.get("/threads/t1/state")).json()
    assert state["next"] == ["reply_drafter"] and state["interrupts"]

    r = await client.post("/threads/t1/resume", json={"value": {"decisions": [{"type": "approve"}]}})
    assert parse_sse(r.text)[-1]["outcome"] == "success"
    assert len((await client.get("/threads/t1/history")).json()) >= 4


async def test_code_endpoint(client):
    code = (await client.get("/workflows/wf_support_triage/code")).text
    assert "def build_graph" in code and '@canvas-node n_ag "Reply drafter"' in code


async def test_two_runs_get_fresh_models(client):
    """Each run builds fresh resources, so scripted fakes do not run out."""
    for thread in ("a", "b"):
        r = await client.post(
            "/workflows/wf_support_triage/runs/stream", json={"input": {"ticket": "x"}, "thread_id": thread}
        )
        assert parse_sse(r.text)[-1]["outcome"] == "interrupt"


async def test_invalid_workflow_is_rejected(client, triage):
    bad = triage.model_copy(deep=True)
    bad.id = "wf_bad"
    bad.edges = [e for e in bad.edges if e.id != "w1"]  # classify has no model
    await client.post("/workflows", json=bad.model_dump())
    r = await client.post("/workflows/wf_bad/runs/stream", json={"input": {}})
    assert r.status_code == 422 and r.json()["detail"][0]["code"] == "E001"
