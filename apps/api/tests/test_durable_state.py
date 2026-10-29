"""G-9: paused runs and remote A2A replies live in the database. A run paused on one API instance continues on another
that shares the database (a second replica, or the same server after a restart), whatever its framework."""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from agentcanvas.adapters.crewai import crewai_python
from agentcanvas.db.models import A2AReply, Thread
from agentcanvas.settings import Settings

P = "/v1/projects/prj_default/workflows"


@contextlib.asynccontextmanager
async def instance(
    settings: Settings, resources: Callable[[], Any] | None = None
) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    """One API instance on the shared database, with nothing of another instance in its memory."""
    from agentcanvas.api.main import create_app

    app = create_app(settings)
    if resources is not None:
        made = resources()
        app.state.resources_factory = lambda thread_id, wf: made
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as api:
            yield app, api


def _events(text: str) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


async def _workflow(api: httpx.AsyncClient, ir: dict[str, Any]) -> str:
    wf = (await api.post(P, json={"name": "Durable"})).json()
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": {**ir, "id": wf["id"]}}, headers={"If-Match": "1"})
    assert r.status_code == 200 and not [d for d in r.json()["diagnostics"] if d["severity"] == "error"], r.text
    return wf["id"]


def _ask(framework: str) -> dict[str, Any]:
    return {
        "name": "Ask", "framework": framework, "state": {"channels": [{"name": "name", "default": ""}]},
        "nodes": [{"id": "n_ask", "type": "core.human_input", "name": "ask",
                   "config": {"prompt": "Your name?", "output": "name"}}],
        "edges": [{"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_ask"}},
                  {"id": "e2", "kind": "flow", "source": {"node": "n_ask"}, "target": {"node": "END"}}],
    }  # fmt: skip


@pytest.mark.parametrize("framework", ["openai_agents", "crewai"])
async def test_a_paused_run_continues_on_another_instance(settings, framework):
    """The OpenAI Agents SDK runs in the server, CrewAI in a worker process: both keep a paused run on its thread."""
    if framework == "crewai" and crewai_python() is None:
        pytest.skip("no CrewAI here (make crewai, or AGENTCANVAS_CREWAI_PYTHON)")
    async with instance(settings) as (app, api):
        wf = await _workflow(api, _ask(framework))
        first = _events(
            (await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {}, "thread_id": "th_g9"})).text
        )
        assert first[-1]["type"] == "RUN_FINISHED" and first[-1]["outcome"] == "interrupt", first[-1]
        async with app.state.db.sessionmaker() as s:
            assert await s.scalar(select(Thread.paused_state).where(Thread.id == "th_g9"))
    async with instance(settings) as (_, api):  # another instance: its memory has never seen this run
        state = (await api.get("/v1/threads/th_g9")).json()
        assert state["status"] == "interrupted", state
        done = _events((await api.post("/v1/threads/th_g9/resume", json={"value": "Ada"})).text)
        assert done[-1]["type"] == "RUN_FINISHED" and done[-1]["status"] == "success", done[-1]
        assert done[-1]["output"]["name"] == "Ada"


async def test_a_remote_agents_question_is_answered_on_another_instance(settings):
    """A LangGraph step talking to a remote A2A agent pauses on the agent's question. The agent's replies were kept
    in the API process, so the answer reaching another instance stopped with "AgentCanvas restarted"; they are kept
    in the database now, and the step continues on the same remote task without sending its first message again."""
    from test_interop import a2a_server

    from agentcanvas.adapters.langchain.nodes import Resources

    log: list[dict[str, Any]] = []
    ir = {
        "name": "Billing", "state": {"channels": [{"name": "messages", "type": {"type": "array"},
                                                   "reducer": "add_messages"}]},
        "nodes": [{"id": "n_billing", "type": "agent.a2a", "name": "billing",
                   "config": {"url": "https://agents.example.com/a2a", "output": "messages"}}],
        "edges": [{"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_billing"}},
                  {"id": "e2", "kind": "flow", "source": {"node": "n_billing"}, "target": {"node": "END"}}],
    }  # fmt: skip
    remote = lambda: Resources(http_transport=a2a_server(log))  # noqa: E731
    async with instance(settings, remote) as (app, api):
        wf = await _workflow(api, ir)
        body = {"input": {"messages": [{"role": "user", "content": "refund me"}]}, "thread_id": "th_a2a"}
        first = _events((await api.post(f"/v1/workflows/{wf}/runs/stream", json=body)).text)
        assert first[-1].get("outcome") == "interrupt", first[-1]
        async with app.state.db.sessionmaker() as s:
            row = await s.scalar(select(A2AReply).where(A2AReply.thread_id == "th_a2a"))
            assert row is not None and row.replies[0]["status"]["state"] == "input-required"
    async with instance(settings, remote) as (app, api):
        done = _events((await api.post("/v1/threads/th_a2a/resume", json={"value": "ACME-9"})).text)
        assert done[-1]["type"] == "RUN_FINISHED" and done[-1]["status"] == "success", done[-1]
        assert done[-1]["output"]["messages"][-1]["content"] == "Refunded on account ACME-9"
        async with app.state.db.sessionmaker() as s:  # a finished step forgets its replies
            assert await s.scalar(select(A2AReply).where(A2AReply.thread_id == "th_a2a")) is None
    sent = [(m["parts"][0]["text"], m.get("taskId")) for m in log]
    assert sent == [("refund me", None), ("ACME-9", "task1")]  # the first message was not sent twice
