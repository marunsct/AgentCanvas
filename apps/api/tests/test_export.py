"""Sprint 8 export (M6-T2/T3): the zip is a standalone project whose own tests pass."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile

from test_versions import HIGH_CASE, new_triage

from agentcanvas.adapters.langchain.packager import _env_example, dependencies, package_name
from agentcanvas.ir.models import Workflow

P = "/v1/projects/prj_default"
TOOL = '''from langchain_core.tools import tool


@tool
def send_reply(text: str) -> str:
    """Send the reply to the customer."""
    return "sent"
'''


def test_package_metadata(triage):
    assert package_name("Support triage v2!") == "support_triage_v2" and package_name("42 things") == "wf_42_things"
    deps = dependencies(triage)
    assert any(d.startswith("langgraph==") for d in deps) and any(d.startswith("langchain[mcp]==") for d in deps)
    assert any(d.startswith("langchain-openai") for d in deps) and any(
        d.startswith("langchain-anthropic") for d in deps
    )
    wf = Workflow.model_validate(
        {**triage.model_dump(mode="json"), "resources": [
            *triage.model_dump(mode="json")["resources"],
            {"id": "r_h", "type": "tool.http", "name": "crm",
             "config": {"url": "https://x.example.com", "headers": {"Authorization": {"$secret": "CRM_TOKEN"}}}},
        ]}
    )  # fmt: skip
    env = _env_example(wf)
    assert "OPENAI_API_KEY=" in env and "ANTHROPIC_API_KEY=" in env and "CRM_TOKEN=" in env


def test_names_and_labels_cannot_inject_code(triage):
    """Free text lands in comments and docstrings of generated code: a line break must not end them."""
    import ast

    from agentcanvas.adapters.langchain.codegen import generate
    from agentcanvas.adapters.langchain.packager import package

    evil = "x\nimport os; os.system('id')\r\n#"
    rid = triage.resources[0].id
    ir = json.loads(triage.model_dump_json().replace(json.dumps(rid), json.dumps(f"r_{evil}")))  # and its wiring
    ir["name"], ir["id"] = f'Triage """{evil}', f"wf_{evil}"
    ir["nodes"][0]["label"] = evil
    wf = Workflow.model_validate(ir)
    code = generate(wf, format_code=False)
    tree = ast.parse(code)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Import) and n.names[0].name == "os"]
    assert "x import os; os.system('id')  #" in code  # kept, on one comment line
    _, data = package(wf, code)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        init = next(n for n in z.namelist() if n.endswith("/__init__.py") and "/agentcanvas/" not in n)
        doc = ast.get_docstring(ast.parse(z.read(init).decode()))
        readme = z.read(next(n for n in z.namelist() if n.endswith("README.md"))).decode()
    assert doc == 'Triage """x import os; os.system(\'id\')  # (exported from AgentCanvas).'
    assert readme.splitlines()[0] == '# Triage """x import os; os.system(\'id\')  #'


async def test_export_zip_runs_its_own_tests(app, api_client, tmp_path):
    """M6-T2 AC: unzip → pytest passes (with this environment's packages instead of `uv sync`)."""
    await api_client.put(f"{P}/files/tools/support.py", json={"content": TOOL})
    wf = await new_triage(api_client)
    cur = (await api_client.get(f"/v1/workflows/{wf['id']}")).json()
    ir = cur["ir"]
    next(r for r in ir["resources"] if r["id"] == "r_send")["config"]["import_path"] = "tools.support:send_reply"
    r = await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(cur["version"])})
    assert r.status_code == 200, r.text
    await api_client.post(f"/v1/workflows/{wf['id']}/test-cases", json=HIGH_CASE)
    await api_client.post(
        f"/v1/workflows/{wf['id']}/test-cases", json={**HIGH_CASE, "name": "pinned", "pins": {"n_cls": {}}}
    )
    await api_client.post(f"/v1/workflows/{wf['id']}/versions", json={"message": "export me"})

    resp = await api_client.get(f"/v1/workflows/{wf['id']}/export.zip", params={"version": "1.0.0"})
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/zip"
    assert 'filename="triage.zip"' in resp.headers["content-disposition"]
    zipfile.ZipFile(io.BytesIO(resp.content)).extractall(tmp_path)
    root = tmp_path / "triage"
    names = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    expected = {
        "pyproject.toml", "Dockerfile", "langgraph.json", ".env.example", "src/triage/graph.py",
        "src/tools/support.py", "src/agentcanvas/adapters/langchain/nodes.py", "tests/test_smoke.py",
    }  # fmt: skip
    assert expected <= names
    assert 'version = "1.0.0"' in (root / "pyproject.toml").read_text()
    cases = json.loads((root / "tests/test_cases.json").read_text())
    assert [c["name"] for c in cases] == [HIGH_CASE["name"]]  # the pinned case cannot run outside AgentCanvas
    assert json.loads((root / "langgraph.json").read_text())["graphs"] == {
        "triage": "./src/triage/server.py:make_graph"
    }

    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTCANVAS_", "PYTEST"))}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=root, env={**env, "PYTHONPATH": str(root / "src")},
        capture_output=True, text=True, timeout=240, check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]
    assert "3 passed" in proc.stdout  # vendored library, builds, the saved case
