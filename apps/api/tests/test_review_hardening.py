"""Fixes from the 2026-10-18 deep review: workspace code stays in the sandbox, unvalidated drafts do not run from
triggers or events, interrupt ids are unique per run, and hand-over never restarts a run it cannot continue."""

from __future__ import annotations

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
