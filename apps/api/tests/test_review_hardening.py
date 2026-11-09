"""Fixes from the 2026-10-18 deep review: workspace code stays in the sandbox, unvalidated drafts do not run from
triggers or events, interrupt ids are unique per run, and hand-over never restarts a run it cannot continue."""

from __future__ import annotations

import asyncio
import builtins
from pathlib import Path

import pytest

from agentcanvas.adapters.langchain.nodes import Resources, script_node
from agentcanvas.sandbox.runners import SandboxRunner
from agentcanvas.settings import Settings
from agentcanvas.workspace.catalog import tool_spec

TOOL = '''from langchain_core.tools import tool
import builtins

builtins.AC_TOOL_MODULE_IMPORTED = True  # must never happen in the API process


@tool("adder")
def add(a: int, b: int, note: str | None = None) -> int:
    """Add two numbers."""
    return a + b
'''


@pytest.fixture
def tool_ws(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "calc.py").write_text(TOOL)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "__init__.py").write_text("")
    return tmp_path


def test_tool_spec_reads_the_source_without_importing(tool_ws):
    spec = tool_spec(tool_ws, "tools.calc:add")
    assert spec["name"] == "adder" and spec["description"] == "Add two numbers."
    assert spec["schema"] == {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}, "note": {"type": "string"}},
        "required": ["a", "b"],
    }
    assert not hasattr(builtins, "AC_TOOL_MODULE_IMPORTED")
    with pytest.raises(ValueError):
        tool_spec(tool_ws, "os:system")
    with pytest.raises(LookupError):
        tool_spec(tool_ws, "tools.calc:missing")


async def test_python_tools_run_in_the_sandbox_not_in_the_api(tool_ws):
    """C1: with a sandbox configured, a Python tool's module is never imported into the API process."""
    runner = SandboxRunner(Settings(script_execution="subprocess"), tool_ws, {})
    res = Resources(workspace=tool_ws, script_runner=runner)
    found = res.tool("calc", {"import_path": "tools.calc:add"}, "tool.python")
    assert found.name == "adder"
    assert await found.ainvoke({"a": 2, "b": 3}) in (5, "5")
    assert not hasattr(builtins, "AC_TOOL_MODULE_IMPORTED")


def test_script_steps_from_any_workspace_folder_go_to_the_sandbox():
    """H1: a Script step pointing at `tools.` or `nodes.` used to run in-process."""
    calls: list[str] = []
    runner = lambda path: calls.append(path) or (lambda state: state)  # noqa: E731
    res = Resources(script_runner=runner)
    for path in ("scripts.a:run", "tools.evil:run", "nodes.evil:run", "events.evil:run"):
        script_node(import_path=path, resources=res)
    assert calls == ["scripts.a:run", "tools.evil:run", "nodes.evil:run", "events.evil:run"]


async def test_every_run_passes_the_security_rules_whatever_started_it():
    """H3: triggers, event handlers and hand-over ran drafts as stored, so a cron trigger could launch a local
    (stdio) MCP server's command although E042 only lets an admin allow that. The gate is a runner prepare hook."""
    from test_tools import mcp_wf

    from agentcanvas.errors import WorkflowHasErrors
    from agentcanvas.services.run_gate import RunGate

    local = mcp_wf({"transport": "stdio", "command": "touch", "args": ["/tmp/pwned"], "url": None})
    with pytest.raises(WorkflowHasErrors, match="E042"):
        await RunGate(Settings(allow_local_mcp=False))(local)
    await RunGate(Settings(allow_local_mcp=True))(local)  # an admin allowed local MCP servers
    await RunGate(Settings())(mcp_wf())  # a remote MCP server is fine


async def test_a_trigger_cannot_run_a_draft_that_breaks_a_security_rule(app, api_client):
    """The gate sits in the runner, so the cron/webhook path refuses before anything starts."""
    from test_tools import mcp_wf

    from agentcanvas.errors import WorkflowHasErrors

    wf = mcp_wf({"transport": "stdio", "command": "touch", "args": ["/tmp/pwned"], "url": None})
    with pytest.raises(WorkflowHasErrors):
        await app.state.runner.start(wf, input={"messages": []}, origin="trigger")
    assert not Path("/tmp/pwned").exists()


def test_interrupt_ids_are_unique_per_run():
    """H5: the workers name a pause `<thread>:<block>`, so a second pause at one block of a conversation reused the
    first one's id and could not be answered. The server re-keys every pause by its run."""
    from agentcanvas.adapters.crewai import run_scoped_interrupts

    info = {"id": "th_1:n_ok", "kind": "approval", "canvas_node_id": "n_ok", "payload": {}}
    done = {"type": "RUN_FINISHED", "outcome": "interrupt", "interrupts": [info]}
    first, second = run_scoped_interrupts(done, "run_a"), run_scoped_interrupts(done, "run_b")
    assert first["interrupts"][0]["id"] == "run_a:n_ok" and second["interrupts"][0]["id"] == "run_b:n_ok"
    custom = run_scoped_interrupts({"type": "CUSTOM", "name": "ac.interrupt", "value": info}, "run_a")
    assert custom["value"]["id"] == "run_a:n_ok" and info["id"] == "th_1:n_ok"  # the worker's event is not changed
    assert run_scoped_interrupts({"type": "TEXT_MESSAGE_CONTENT", "delta": "x"}, "run_a") == {
        "type": "TEXT_MESSAGE_CONTENT",
        "delta": "x",
    }


@pytest.fixture
async def with_echo(monkeypatch, settings):
    """An app offering LangGraph and the Echo demo engine, which keeps no saved state."""
    import httpx

    monkeypatch.setenv("AGENTCANVAS_ADAPTERS", "langchain,echo")
    from agentcanvas.api.main import create_app

    application = create_app(settings)
    async with application.router.lifespan_context(application):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as c:
            yield application, c


async def test_hand_over_never_restarts_a_run_it_cannot_continue(with_echo):
    """H6: hand-over restarted every run from its checkpoint, but frameworks without saved state started again from
    the beginning with no input, repeating side effects. Those runs now end with an error instead."""
    from sqlalchemy import update
    from test_frameworks import events, greeter

    from agentcanvas.db.models import Run, Thread
    from agentcanvas.services.redeploy import NO_STATE

    app, api = with_echo
    wf = await greeter(api, "echo")
    r = await api.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"who": "Ada"}, "thread_id": "th_ho"})
    run_id = events(r.text)[0]["runId"]
    async with app.state.db.sessionmaker() as s:  # as if its server had been stopped mid-run
        await s.execute(update(Run).where(Run.id == run_id).values(status="interrupted", handover=True))
        await s.commit()
    assert await app.state.handover.tick() == []
    async with app.state.db.sessionmaker() as s:
        row, thread = await s.get(Run, run_id), await s.get(Thread, "th_ho")
        assert row is not None and thread is not None
        assert row.status == "error" and row.error == NO_STATE and thread.status == "error"


def test_the_api_role_never_bypasses_row_level_security():
    """H4: the compose stacks connected as the image's superuser, which skips every policy."""
    from agentcanvas.db.app_role import statements

    sql = statements("agentcanvas_app")
    assert "NOSUPERUSER NOBYPASSRLS" in sql[0] and all("{role}" in q for q in sql)
    with pytest.raises(ValueError):
        statements('x"; DROP TABLE project; --')


async def test_the_next_message_is_accepted_the_moment_a_run_shows_its_last_event(app, api_client, monkeypatch):
    """A chat client sends its next message as soon as it reads RUN_FINISHED. The thread must already be free then:
    the run used to publish that event first and release the thread (a database write) after it, so a quick client got
    'already has an active run'. Here the write is made slow, so the old order fails every time."""
    from test_frameworks import greeter

    from agentcanvas.ir.models import Workflow

    runner = app.state.runner
    wf = Workflow.model_validate((await greeter(api_client))["ir"])
    real_finish = runner._finish

    async def slow_finish(*args, **kwargs):
        await asyncio.sleep(0.3)
        await real_finish(*args, **kwargs)

    monkeypatch.setattr(runner, "_finish", slow_finish)
    first = await runner.start(wf, thread_id="th_quick", input={"who": "Ada"})
    for _ in range(200):  # what a streaming client does: read until the terminal event
        if any(e["type"] in ("RUN_FINISHED", "RUN_ERROR") for e in first.events):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("the run never published a terminal event")
    second = await runner.start(wf, thread_id="th_quick", input={"who": "Bo"})  # raised Conflict before the fix
    for h in (first, second):
        await asyncio.wait_for(h.done.wait(), 10)
    assert [r["status"] for r in (await api_client.get("/v1/threads/th_quick/runs")).json()] == ["success", "success"]


async def test_a_chat_can_go_on_after_its_first_message(app, api_client):
    """The chat template with message input: the engine turns the input's message dicts into message objects in place,
    which the run's recording then could not store, so finishing the run failed and its thread stayed 'running':
    every later message got 'already has an active run'. Two turns on one thread must both finish."""
    from agentcanvas.adapters.langchain.testing import DemoResources

    app.state.resources_factory = lambda _thread, _wf: DemoResources()
    wf = (
        await api_client.post("/v1/projects/prj_default/workflows", json={"name": "Chat", "template": "chat_assistant"})
    ).json()
    for text in ("Hello there", "And again"):
        r = await api_client.post(
            f"/v1/workflows/{wf['id']}/runs/stream",
            json={"input": {"messages": [{"role": "user", "content": text}]}, "thread_id": "th_chat"},
        )
        assert r.status_code == 200, r.text
        assert '"RUN_FINISHED"' in r.text
    thread = (await api_client.get("/v1/threads/th_chat")).json()
    assert thread["status"] == "idle"
    runs = (await api_client.get("/v1/threads/th_chat/runs")).json()
    assert [x["status"] for x in runs] == ["success", "success"]


async def test_a_run_whose_recording_cannot_be_saved_still_frees_its_thread(app, api_client, monkeypatch):
    """Whatever optional detail fails to save, the run ends with its status and the thread is free for the next one."""
    from test_frameworks import greeter

    from agentcanvas.runtime import runner as runner_module

    def broken(*_args, **_kwargs):
        return {"input": object()}  # not storable as JSON

    monkeypatch.setattr(runner_module, "_recording", broken)
    wf = await greeter(api_client)
    r = await api_client.post(
        f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"who": "Ada"}, "thread_id": "th_bare"}
    )
    assert '"RUN_FINISHED"' in r.text
    assert (await api_client.get("/v1/threads/th_bare")).json()["status"] == "idle"
    assert [x["status"] for x in (await api_client.get("/v1/threads/th_bare/runs")).json()] == ["success"]
    monkeypatch.undo()
    again = await api_client.post(
        f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"who": "Bo"}, "thread_id": "th_bare"}
    )
    assert again.status_code == 200


async def test_unexpected_errors_and_values_the_database_refuses_answer_as_problems(app):
    """A bug is a problem document with the request id (not a text page); a value Postgres cannot store (a NUL
    character in an id or name, a number out of range) is the client's 422, not a 500."""
    import httpx
    from sqlalchemy.exc import DataError, IntegrityError

    async def boom():
        raise RuntimeError("secret internals")

    async def refused():
        raise DataError("INSERT INTO t VALUES (:x)", {"x": "\x00"}, Exception("NUL"))

    async def raced():
        raise IntegrityError("INSERT INTO t VALUES (:x)", {"x": 1}, Exception("UNIQUE constraint failed: t.x"))

    app.add_api_route("/v1/_test/boom", boom)
    app.add_api_route("/v1/_test/refused", refused)
    app.add_api_route("/v1/_test/raced", raced)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/_test/boom", headers={"x-request-id": "req_quote_me"})
        assert r.status_code == 500 and r.headers["content-type"] == "application/problem+json"
        assert r.json()["title"] == "Internal error" and r.json()["request_id"] == "req_quote_me"
        assert "secret internals" not in r.text
        r = await client.get("/v1/_test/refused")
        assert r.status_code == 422 and "INSERT" not in r.text and r.json()["title"] == "Invalid input"
        r = await client.get("/v1/_test/raced")  # a uniqueness met by a race the code did not expect: retry, not 500
        assert r.status_code == 409 and "UNIQUE" not in r.text and r.json()["title"] == "Conflict"


def test_worker_folders_of_dead_servers_are_swept_and_live_ones_kept(tmp_path, monkeypatch):
    """A killed server leaves its framework workers' temp folders behind; the next one removes the ones whose server
    process is gone, never a live server's (or this one's)."""
    import os
    import subprocess
    import sys
    import tempfile

    from agentcanvas.adapters.crewai import sweep_stale_worker_dirs

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    dead = tmp_path / f"agentcanvas-strands-{gone.pid}-abc123"
    mine = tmp_path / f"agentcanvas-strands-{os.getpid()}-abc123"
    live = tmp_path / f"agentcanvas-google_adk-{os.getppid()}-abc123"
    old_format = tmp_path / "agentcanvas-crewai-abc123"  # written before the process id was in the name
    unrelated = tmp_path / "agentcanvas-notes-and-other"
    for d in (dead, mine, live, old_format, unrelated):
        d.mkdir()
        (d / "x").write_text("x")
    assert sweep_stale_worker_dirs() == 1
    assert not dead.exists()
    assert all(d.exists() for d in (mine, live, old_format, unrelated))


async def test_finished_runs_are_forgotten_by_the_size_of_their_events_not_only_their_number(app, api_client):
    """500 finished runs with big outputs must not fill the server's memory: the oldest go once their events add up
    to more than the limit, and the newest stay."""
    from test_frameworks import greeter

    from agentcanvas.ir.models import Workflow

    runner = app.state.runner
    wf = Workflow.model_validate((await greeter(api_client))["ir"])
    runner._keep_bytes = 3000  # a few runs' worth
    handles = []
    for i in range(8):
        h = await runner.start(wf, input={"who": f"Ada {i}"})
        await asyncio.wait_for(h.done.wait(), 10)
        handles.append(h)
    kept = [h for h in handles if h.run_id in runner.handles]
    assert 1 <= len(kept) < len(handles), (len(kept), [h.size for h in handles])
    assert handles[-1].run_id in runner.handles and handles[0].run_id not in runner.handles
    assert sum(h.size for h in kept) <= 3000 + max(h.size for h in kept)
