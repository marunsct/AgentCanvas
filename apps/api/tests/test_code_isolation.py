"""Workspace code stays out of the API process on a server that sandboxes it (launch review, isolation group)."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_interop import agent_wf

from agentcanvas.adapters.langchain.interpreter import build_graph
from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.errors import WorkflowHasErrors
from agentcanvas.services.run_gate import RunGate
from agentcanvas.settings import Settings
from agentcanvas.validation import validate

LEAKY = """import pathlib

pathlib.Path(__file__).with_name("imported").write_text("yes")


def run(i, m):
    return 1
"""


@pytest.fixture
def leaky(tmp_path):
    """A workspace whose function leaves a mark when its module is imported."""
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "__init__.py").write_text("")
    (tmp_path / "tools" / "leak.py").write_text(LEAKY)
    return tmp_path


def framework_step():
    return agent_wf({"framework": "callable", "import_path": "tools.leak:run"})


def test_a_sandboxing_server_refuses_an_agent_from_another_framework():
    """The block imports workspace code into the API process, where the database URL, the secret key and every
    organisation's cached secrets are in reach: a server that keeps workspace code in a sandbox refuses it, whatever
    its edition."""
    wf = framework_step()
    assert "E041" not in {d.code for d in validate(wf)}  # the local edition runs workspace code in-process anyway
    (found,) = [d for d in validate(wf, isolated=True) if d.code == "E041"]
    assert "sandbox" in found.message


@pytest.mark.parametrize(
    "settings, refused",
    [
        (Settings(), False),
        (Settings(script_execution="remote"), True),
        (Settings(script_execution="docker"), True),
        (Settings(script_execution="subprocess"), True),
        (Settings(env="prod"), True),  # cannot start like this, but a refusal is the safe answer
        (Settings(env="prod", allow_unisolated_scripts=True), False),  # the operator trusts every builder
    ],
)
async def test_every_run_on_a_sandboxing_server_is_refused(settings, refused):
    gate = RunGate(settings)
    if refused:
        with pytest.raises(WorkflowHasErrors, match="E041"):
            await gate(framework_step())
    else:
        await gate(framework_step())


def test_the_step_is_not_built_where_scripts_run_in_a_sandbox(leaky):
    """Defence in depth: an old version or an import that skipped validation still does not run the code."""

    def sandbox(import_path, config=None):  # stands in for a SandboxRunner
        raise AssertionError("not reached")

    with pytest.raises(RuntimeError, match="sandbox"):
        build_graph(framework_step(), Resources(workspace=leaky, script_runner=sandbox), checkpointer=InMemorySaver())
    assert not (leaky / "tools" / "imported").exists()


async def test_a_sandboxing_server_flags_the_step_and_hides_its_templates(app, api_client, monkeypatch):
    monkeypatch.setattr(app.state.settings, "script_execution", "remote")
    listed = {t["id"] for t in (await api_client.get("/v1/templates")).json()}
    assert "agent_network" not in listed and "faq_bot" in listed
    made = await api_client.post(
        "/v1/projects/prj_default/workflows", json={"name": "Net", "template": "agent_network"}
    )
    assert "E041" in {d["code"] for d in made.json()["diagnostics"]}  # the editor shows why it cannot run
