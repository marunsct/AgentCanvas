"""Milestone review after S20-S22: exports and the Copilot refuse input that would run as code or leave the network."""

from __future__ import annotations

import pytest

from agentcanvas.adapters.langchain import deploykit
from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.adapters.langchain.packager import package_name
from agentcanvas.copilot.settings import CopilotSettings, check_model
from agentcanvas.designs import AgentDesign
from agentcanvas.designs.export import agent_tools
from agentcanvas.errors import InvalidInput
from agentcanvas.registry import default_registry


def test_a_workflow_name_stays_a_comment_in_the_exported_makefile(triage):
    triage.name = "Bot\n$(shell touch /tmp/pwned)\\"
    f = deploykit.facts(triage, "bot", "1.0.0")
    assert "\n" not in f.name and not f.name.endswith("\\")


def test_secret_names_that_are_not_variables_are_refused(triage):
    triage.resource("r_fast").config["api_key"] = {"$secret": "K$(touch /tmp/p2)"}  # type: ignore[union-attr]
    with pytest.raises(InvalidInput, match="not a variable name"):
        deploykit.facts(triage, "bot", "1.0.0")


def test_tool_import_paths_are_checked_before_they_become_code():
    bad = "tools.x import y\nimport os; os.system('id')\nfrom tools.x:y"
    design = AgentDesign(name="a", workflow="w", instructions="", model=None, tools=[_tool(bad)])
    with pytest.raises(InvalidInput, match="import path"):
        agent_tools(design)


def _tool(path):
    from types import SimpleNamespace

    return SimpleNamespace(type="tool.python", resource="t", config={"import_path": path})


def test_package_names_never_hide_modules_the_export_imports():
    assert package_name("LangGraph") == "wf_langgraph" and package_name("Email") == "wf_email"
    assert package_name("Support triage v2!") == "support_triage_v2"


def test_local_only_copilot_refuses_a_remote_address():
    reg = default_registry()
    local = CopilotSettings(privacy="local_only")
    with pytest.raises(InvalidInput, match="not on this machine"):
        check_model("ollama:llama3.1", local, [], reg, "https://models.example.com")
    check_model("ollama:llama3.1", local, [], reg, "http://ollama:11434")
    check_model("ollama:llama3.1", local, [], reg, "http://192.168.1.20:11434")


async def test_hosted_mcp_servers_must_be_public():
    from langchain_core.tools import ToolException

    res = Resources()
    res.public_only = True
    with pytest.raises(ToolException, match="private or internal"):
        await res.load_mcp("m", {"url": "http://169.254.169.254/mcp"})
