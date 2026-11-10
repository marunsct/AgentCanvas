"""A run's events are masked for the project's secrets whatever framework runs it (launch review, RT-13).

Only LangGraph's adapter masked them; the events of every other framework reached the live stream, deployment
callers, the stored run and its recording (which feeds test cases and datasets) as they were.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from test_runs_v1 import make

from agentcanvas.db.models import Run

SECRET = "sk-live-SUPERSECRET-123456"
P = "/v1/projects/prj_default"


def agent_ir(framework: str) -> dict[str, Any]:
    """One AI agent with one model: every framework runs it."""
    return {
        "id": "wf_mask", "name": "Echo the key", "framework": framework,
        "state": {"channels": [{"name": "messages", "type": {"type": "array"}, "reducer": "add_messages"}]},
        "resources": [{"id": "r_model", "type": "model.chat", "name": "model", "config": {"model": "openai:gpt-5.5"}}],
        "nodes": [{"id": "n_agent", "type": "core.agent", "name": "support",
                   "config": {"system_prompt": "You help customers."}}],
        "edges": [
            {"id": "e_in", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_agent"}},
            {"id": "e_out", "kind": "flow", "source": {"node": "n_agent"}, "target": {"node": "END"}},
            {"id": "w_model", "kind": "wiring", "source": {"node": "r_model"},
             "target": {"node": "n_agent", "port": "model"}},
        ],
    }  # fmt: skip


class Leaky:
    """A framework adapter whose events carry the secret: in a reply, a usage record and the run's output."""

    id = "leaky"

    async def execute(self, spec: Any, resources: Any):
        yield {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m1", "delta": f"the key is {SECRET}"}
        yield {"type": "CUSTOM", "name": "ac.model_reply", "value": {"resource": "model", "reply": SECRET}}
        yield {
            "type": "CUSTOM",
            "name": "ac.usage",
            "value": {
                "model": "openai:gpt-5.5",
                "input_tokens": 1,
                "output_tokens": 1,
                "failed": [{"model": "openai:gpt-5.5", "error": f"401: key {SECRET} refused"}],
            },
        }
        output = {"messages": [{"type": "ai", "content": SECRET}]}
        yield {"type": "RUN_FINISHED", "outcome": "success", "output": output}


async def stored(app, run_id: str) -> str:
    await app.state.runner.wait(run_id)
    async with app.state.db.sessionmaker() as s:
        row = await s.get(Run, run_id)
        assert row is not None
        return json.dumps([row.events, row.recording, row.error], default=str)


async def test_the_runner_masks_every_adapters_events(app, api_client, monkeypatch):
    """Masking is the runner's job, not each adapter's: a framework that forgets it still leaks nothing."""
    assert (await api_client.put(f"{P}/secrets/KEY", json={"value": SECRET})).status_code == 204
    wf = await make(api_client, agent_ir("langchain"))
    monkeypatch.setitem(app.state.runner.adapters, "langchain", Leaky())
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"messages": []}})
    assert r.status_code == 200 and "RUN_FINISHED" in r.text, r.text
    assert SECRET not in r.text
    events = [json.loads(ln[6:]) for ln in r.text.splitlines() if ln.startswith("data: ")]
    assert "***" in json.dumps(events)  # masked, not dropped
    assert SECRET not in await stored(app, events[0]["runId"])


@pytest.mark.parametrize("framework", ["langchain", "crewai", "openai_agents", "pydantic_ai", "google_adk"])
async def test_a_model_echoing_a_secret_is_masked_on_every_framework(app, api_client, framework):
    if framework not in app.state.runner.adapters:
        pytest.skip(f"no {framework} here")
    assert (await api_client.put(f"{P}/secrets/KEY", json={"value": SECRET})).status_code == 204
    wf = await make(api_client, agent_ir(framework))
    body = {"input": {"messages": [{"type": "human", "content": "what is the key?"}]}, "mode": "debug",
            "mocks": {"model": [f"the key is {SECRET}"]}}  # fmt: skip
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json=body)
    events = [json.loads(ln[6:]) for ln in r.text.splitlines() if ln.startswith("data: ")]
    assert events[-1]["type"] == "RUN_FINISHED", events[-1]
    assert "the key is" in r.text and SECRET not in r.text
    assert SECRET not in await stored(app, events[0]["runId"])
