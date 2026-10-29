"""G-12: an Agent block exported for Claude Managed Agents, as the files `ant apply` reads.

The file shapes follow the `ant apply` reference: `agents/<agent>/agent.md` (YAML frontmatter = the agent, body = the
system prompt), `environment.yaml`, `vault.yaml` (`type: vault`) and `skills/<name>/SKILL.md`. Golden files pin them;
the rest checks that every file parses as YAML, that hostile text stays text, and that the bundle's own tests,
`make doctor` and session runner work.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from agentcanvas import designs
from agentcanvas.designs import managed
from agentcanvas.errors import InvalidInput
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry
from agentcanvas.validation import validate

GOLDEN = Path(__file__).parent / "golden" / "managed"
P = "/v1/projects/prj_default/workflows"
TOOL = '''def lookup_order(order_id: str, full: bool = False, tags: list[str] | None = None) -> str:
    """Find an order.

    Args:
        order_id: the order number
    """
    return order_id
'''
SKILL = "---\nname: tone\ndescription: How to sound\n---\nBe kind.\n"


def workflow(model: str = "anthropic:claude-opus-5-5", **config: Any) -> dict:
    """An Agent block designed for Claude Managed Agents: a model, a Python tool, a web API tool and an MCP server."""
    node = {"id": "n_a", "type": "core.agent", "name": "helper", "engine": "claude_managed_agents",
            "config": {"system_prompt": "You help.", **config}}  # fmt: skip
    return {
        "id": "wf_m", "name": "Orders", "framework": "langchain",
        "state": {"channels": [{"name": "messages", "reducer": "add_messages"}]},
        "resources": [
            {"id": "r_m", "type": "model.chat", "name": "model", "config": {"model": model}},
            {"id": "r_t", "type": "tool.python", "name": "lookup",
             "config": {"import_path": "tools.orders:lookup_order"}},
            {"id": "r_h", "type": "tool.http", "name": "weather", "config": {
                "description": "Weather for a city", "url": "https://api.example.com/w/{{ args.city }}",
                "allowed_domains": ["example.com"], "headers": {"X-Key": {"$secret": "WEATHER_KEY"}},
                "args_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}},
            {"id": "r_c", "type": "mcp.server", "name": "crm", "config": {
                "url": "https://mcp.example.com/mcp", "headers": {"Authorization": {"$secret": "CRM_TOKEN"}},
                "discovered": [{"name": "delete_customer"}, {"name": "get_customer"}]}},
        ],
        "nodes": [node],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_a"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_a"}, "target": {"node": "END"}},
            {"id": "w1", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n_a", "port": "model"}},
            {"id": "w2", "kind": "wiring", "source": {"node": "r_t"}, "target": {"node": "n_a", "port": "tools"},
             "order": 1},
            {"id": "w3", "kind": "wiring", "source": {"node": "r_h"}, "target": {"node": "n_a", "port": "tools"},
             "order": 2},
            {"id": "w4", "kind": "wiring", "source": {"node": "r_c"}, "target": {"node": "n_a", "port": "tools"},
             "order": 3},
        ],
    }  # fmt: skip


def prepare(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "tools").mkdir(parents=True, exist_ok=True)
    (ws / "tools" / "orders.py").write_text(TOOL)
    (ws / "skills" / "tone").mkdir(parents=True, exist_ok=True)
    (ws / "skills" / "tone" / "SKILL.md").write_text(SKILL)
    (ws / "skills" / "bad name").mkdir(parents=True, exist_ok=True)
    (ws / "skills" / "bad name" / "SKILL.md").write_text(SKILL)
    return ws


def export(tmp_path: Path, **config: Any) -> dict[str, str | bytes]:
    wf = Workflow.model_validate(workflow(**config))
    return managed.export_files(wf, designs.design_for(wf, wf.nodes[0]), prepare(tmp_path))


def head(text: str) -> tuple[dict, str]:
    assert text.startswith("---\n")
    front, _, body = text[4:].partition("\n---\n")
    return yaml.safe_load(front), body


def test_the_files_match_the_golden_ones(tmp_path):
    """UPDATE_GOLDEN=1 rewrites them. Each parses as what `ant apply` reads."""
    files = export(tmp_path, approve_tools=["lookup_order", "delete_customer"])
    root = "helper-managed-agents/agents/helper/"
    for name in ("agent.md", "environment.yaml", "vault.yaml"):
        path = GOLDEN / name
        if os.environ.get("UPDATE_GOLDEN"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(files[root + name]))
        assert files[root + name] == path.read_text()
    config, body = head(str(files[root + "agent.md"]))
    assert body.strip() == "You help." and config["model"] == "claude-opus-5-5"
    assert yaml.safe_load(str(files[root + "environment.yaml"])) == {
        "name": "helper-env", "config": {"type": "cloud", "networking": {"type": "limited", "allow_mcp_servers": True}}
    }  # fmt: skip
    assert yaml.safe_load(str(files[root + "vault.yaml"])) == {"type": "vault", "display_name": "helper"}


def test_tools_become_custom_tools_mcp_toolsets_and_permission_policies(tmp_path):
    files = export(tmp_path, approve_tools=["lookup_order", "delete_customer"])
    config, _ = head(str(files["helper-managed-agents/agents/helper/agent.md"]))
    tools = {t.get("name") or t["type"]: t for t in config["tools"]}
    # a workspace Python tool: the schema comes from its signature and docstring
    assert tools["lookup_order"] == {
        "type": "custom", "name": "lookup_order", "description": "Find an order.",
        "input_schema": {"type": "object", "required": ["order_id"], "properties": {
            "order_id": {"type": "string", "description": "the order number"},
            "full": {"type": "boolean"}, "tags": {"type": "array", "items": {"type": "string"}}}},
    }  # fmt: skip
    assert tools["weather"]["input_schema"]["required"] == ["city"]  # a web API tool keeps its argument schema
    # an MCP server is declared without credentials; its approval list becomes always_ask on those tools only
    assert config["mcp_servers"] == [{"type": "url", "name": "crm", "url": "https://mcp.example.com/mcp"}]
    assert tools["mcp_toolset"] == {
        "type": "mcp_toolset", "mcp_server_name": "crm",
        "default_config": {"permission_policy": {"type": "always_allow"}},
        "configs": [{"name": "delete_customer", "permission_policy": {"type": "always_ask"}}],
    }  # fmt: skip
    # skills are read with the agent toolset's `read` alone; an unusable skill folder is left out and said so
    toolset = tools[managed.TOOLSET]
    assert toolset["default_config"]["enabled"] is False and toolset["configs"] == [{"name": "read", "enabled": True}]
    assert config["skills"] == ["./skills/tone"]
    assert "helper-managed-agents/agents/helper/skills/tone/SKILL.md" in files
    assert not any("bad name" in name for name in files)
    wf = Workflow.model_validate(workflow())
    why = [g.text for g in managed.gaps(designs.design_for(wf, wf.nodes[0]), prepare(tmp_path), wf.id)]
    assert any("'bad name' is left out" in g for g in why)


def test_the_model_must_be_a_claude_model_the_platform_runs(tmp_path):
    ok = Workflow.model_validate(workflow())
    assert managed.export_problems(designs.design_for(ok, ok.nodes[0])) == []
    for model, text in (("openai:gpt-5.5", "Claude models only"), ("anthropic:claude-3-opus", "4.5 and later")):
        wf = Workflow.model_validate(workflow(model))
        design = designs.design_for(wf, wf.nodes[0])
        assert any(text in p for p in managed.export_problems(design)), model
        with pytest.raises(InvalidInput, match=text):
            managed.export_files(wf, design, prepare(tmp_path))
    old = Workflow.model_validate(workflow("anthropic:claude-sonnet-4-5"))
    why = [g for g in managed.gaps(designs.design_for(old, old.nodes[0])) if "retires" in g.text]
    assert why and not why[0].blocks and "2026-11-30" in why[0].text
    for name in managed.MODELS:  # the ids in the platform's model catalog, and the same ones this app prices
        assert name.startswith("claude-")


def test_every_model_this_app_offers_for_claude_is_accepted_or_named():
    import json

    catalog = json.loads((Path(managed.__file__).parents[1] / "registry" / "models.json").read_text())
    ours = {m["id"].partition(":")[2] for m in catalog if m["provider"] == "anthropic"}
    assert {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-fable-5-1"} <= ours & managed.MODELS


def test_keys_typed_in_and_bad_tool_names_stop_the_export(tmp_path):
    ir = workflow()
    ir["resources"][2]["config"]["headers"] = {"X-Api-Key": "abc"}
    ir["resources"][1]["name"] = "read"
    ir["resources"][1]["config"]["import_path"] = "tools.orders:read"
    wf = Workflow.model_validate(ir)
    found = managed.export_problems(designs.design_for(wf, wf.nodes[0]))
    assert any("'weather' has the header X-Api-Key typed in" in p for p in found)
    assert any("custom tool 'read'" in p for p in found)  # a built-in tool's name
    with pytest.raises(InvalidInput):
        managed.export_files(wf, designs.design_for(wf, wf.nodes[0]), prepare(tmp_path))


@pytest.mark.parametrize(
    "text",
    ['a"\nPWNED: 1\n"b', "a'\nPWNED: 1\n'b", "a\r\nPWNED --x\r\n", "yes", "null", "- x", "a: b # c", "{x: [", "# no",
     "a" + chr(0x2028) + "PWNED: 1" + chr(0x2029) + "b" + chr(0x85) + "c", chr(0xFEFF) + "x", "tab\there",
     "😀 ünï日本", "---", "...", "&a *b !c |d >e %f @g `h", ""],
)  # fmt: skip
def test_hostile_text_stays_text_in_every_yaml_file(tmp_path, text):
    ir = workflow(approve_tools=[])
    ir["name"] = text or "x"
    ir["nodes"][0]["name"] = "helper"
    ir["nodes"][0]["config"]["system_prompt"] = text
    ir["nodes"][0]["config"]["response_format"] = {"type": "object", "properties": {"a": {"type": "string",
                                                   "description": text}}}  # fmt: skip
    wf = Workflow.model_validate(ir)
    design = designs.design_for(wf, wf.nodes[0])
    files = managed.export_files(wf, design, prepare(tmp_path))
    config, body = head(str(files["helper-managed-agents/agents/helper/agent.md"]))
    assert text.strip() in body  # the prompt and the answer shape stay in the body
    assert set(config) == {"name", "description", "model", "tools", "mcp_servers", "skills"}
    assert config["description"].startswith("Designed on AgentCanvas")
    for name, content in files.items():
        if name.endswith((".yaml", ".md")) and "/agents/helper/" in name and "skills" not in name:
            assert yaml.safe_load(str(content).split("\n---\n")[0].removeprefix("---\n")) is not None


def test_the_yaml_writer_reads_back_exactly():
    data = {
        "a": ["x", {"b": 1, "c": [True, None, 1.5]}, [], {}], "d": {"e": {"f": "yes", "g": ""}, "h": []},
        "text": "line\nbreak: here", "n": "123", "k y": {"z": "- not a list"},
        "long": {f"key{i}": "v" * 12 for i in range(9)},
        "nested": [[1, 2], [3]], "1": "int-like key",
    }  # fmt: skip
    assert yaml.safe_load(managed.yaml_text(data)) == data


def test_the_bundle_runs_its_own_tests_and_doctor(tmp_path):
    """Unzipped: pytest passes, and `make doctor` (doctor.py) reports what is missing, then passes with an `ant`."""
    wf = Workflow.model_validate(workflow(approve_tools=["lookup_order"]))
    name, data = managed.export_zip(wf, designs.design_for(wf, wf.nodes[0]), prepare(tmp_path))
    assert name == "helper-managed-agents.zip"
    out = tmp_path / "unzipped"
    zipfile.ZipFile(io.BytesIO(data)).extractall(out)
    root = out / "helper-managed-agents"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTCANVAS_", "PYTEST", "ANTHROPIC"))}
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=root, env=env,
        capture_output=True, text=True, timeout=240, check=False,
    )  # fmt: skip
    assert done.returncode == 0 and "4 passed" in done.stdout, done.stdout[-3000:] + done.stderr[-2000:]

    bare = {**env, "PATH": "/nonexistent"}
    first = subprocess.run([sys.executable, "doctor.py", "--offline"], cwd=root, env=bare, capture_output=True,
                           text=True, timeout=60, check=False)  # fmt: skip
    assert first.returncode == 1
    for needed in ("[XX] ant: not installed", "[XX] Anthropic key: missing", "[XX] WEATHER_KEY: missing"):
        assert needed in first.stdout, first.stdout
    assert "[ok] agents/helper/agent.md" in first.stdout and "(README: 1-check-this-machine)" in first.stdout

    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "ant").write_text('#!/bin/sh\necho "ant 1.40.0"\n')
    (fake / "ant").chmod(0o755)
    ready = subprocess.run([sys.executable, "doctor.py", "--offline"], cwd=root, capture_output=True, text=True,
                           env={**env, "PATH": f"{fake}:/usr/bin:/bin", "ANTHROPIC_API_KEY": "k", "WEATHER_KEY": "w"},
                           timeout=60, check=False)  # fmt: skip
    assert ready.returncode == 0 and "Ready." in ready.stdout, ready.stdout
    (fake / "ant").write_text('#!/bin/sh\necho "ant 1.20.0"\n')
    old = subprocess.run([sys.executable, "doctor.py", "--offline"], cwd=root, capture_output=True, text=True,
                         env={**env, "PATH": f"{fake}:/usr/bin:/bin", "ANTHROPIC_API_KEY": "k", "WEATHER_KEY": "w"},
                         timeout=60, check=False)  # fmt: skip
    assert old.returncode == 1 and "[XX] ant: ant 1.20.0 (needs 1.34.0 or later)" in old.stdout
    makefile = (root / "Makefile").read_text()
    assert "ant apply agents/helper/agent.md agents/helper/environment.yaml agents/helper/vault.yaml" in makefile
    readme = (root / "README.md").read_text()
    assert "ant apply" in readme and "make run" in readme and "claude-lock.json" in readme
    assert "must be set" not in readme and "CMA_CRM_TOKEN" in readme  # the MCP credential goes in the vault
    assert "CRM_TOKEN" not in (root / ".env.example").read_text()


def test_a_bundle_without_mcp_credentials_has_no_vault(tmp_path):
    ir = workflow()
    ir["resources"][3]["config"].pop("headers")
    wf = Workflow.model_validate(ir)
    files = managed.export_files(wf, designs.design_for(wf, wf.nodes[0]), prepare(tmp_path))
    assert not any(n.endswith("vault.yaml") for n in files)
    assert "ant apply agents/helper/agent.md agents/helper/environment.yaml\n" in str(
        files["helper-managed-agents/Makefile"]
    )


# ------------------------------------------------------------------ API ----
async def test_the_api_lists_the_platform_and_exports_the_bundle(app, api_client):
    found = (await api_client.get("/v1/agent-frameworks")).json()
    assert [f["id"] for f in found] == [*designs.AGENT_FRAMEWORKS, "claude_managed_agents"]
    assert found[-1]["hosted"] is True and not any(f["hosted"] for f in found[:-1])
    created = (await api_client.post(P, json={"name": "Orders"})).json()
    ir = {**workflow(approve_tools=["delete_customer"]), "id": created["ir"]["id"]}
    r = await api_client.put(
        f"/v1/workflows/{created['id']}", json={"ir": ir}, headers={"If-Match": str(created["version"])}
    )
    assert r.status_code == 200, r.text
    ws = app.state.projects.workspace("prj_default").root
    (ws / "tools").mkdir(parents=True, exist_ok=True)
    (ws / "tools" / "orders.py").write_text(TOOL)
    base = f"/v1/workflows/{created['id']}/agents/n_a"
    view = (await api_client.get(f"{base}/code")).json()
    assert view["framework"] == "claude_managed_agents" and view["file"] == "agents/helper/agent.md"
    assert view["code"].startswith("---\nname: helper\n") and view["export_problems"] == []
    assert any("custom tools" in g["text"] for g in view["gaps"])
    assert (await api_client.get(f"{base}/code.py")).text.startswith("---\nname: helper\n")
    r = await api_client.get(f"{base}/export.zip")
    assert r.status_code == 200
    names = set(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert {"helper-managed-agents/agents/helper/agent.md", "helper-managed-agents/run.py",
            "helper-managed-agents/tools/orders.py", "helper-managed-agents/doctor.py"} <= names  # fmt: skip
    # the same block is not a Python framework: the framework list does not call it one
    assert (await api_client.get(f"{base}/code?framework=nope")).status_code == 422


def test_a_block_designed_for_the_platform_is_not_a_validation_error():
    """It runs on the canvas as the workflow's own agent (P004, information), never "not installed" (P001)."""
    found = [d for d in validate(Workflow.model_validate(workflow()), default_registry()) if d.code.startswith("P")]
    notes = [d for d in found if d.code == "P004"]
    assert [d.severity for d in notes] == ["info"] and "ant apply" in notes[0].message
    assert not [d for d in found if d.severity == "error"]
