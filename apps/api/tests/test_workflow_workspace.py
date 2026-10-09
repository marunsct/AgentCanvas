"""S17: a code folder per workflow, exports that bundle exactly the code a workflow uses, Copilot help with code."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import zipfile

from test_copilot import script

from agentcanvas.api.v1.copilot import CodeHelpIn, code_prompt, split_reply
from agentcanvas.workspace import Workspace
from agentcanvas.workspace.bundle import collect, entry_points
from agentcanvas.workspace.catalog import scan

P = "/v1/projects/prj_default"
SHOUT = "from scripts.textutil import shout\n\n\ndef run(state):\n    return {'text': shout(state.get('text') or '')}\n"
TEXTUTIL = "from .marks import BANG\n\n\ndef shout(text):\n    return text.upper() + BANG\n"
MARKS = "BANG = '!'\n"


async def new_flow(api, name: str) -> dict:
    """START -> script step -> END, with a `text` field."""
    wf = (await api.post(f"{P}/workflows", json={"name": name})).json()
    ir = {
        **wf["ir"],
        "state": {"channels": [{"name": "text"}]},
        "nodes": [{"id": "n_s", "type": "core.script", "name": "shout", "config": {"import_path": "scripts.x:run"}}],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_s"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_s"}, "target": {"node": "END"}},
        ],
    }
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(wf["version"])})
    assert r.status_code == 200, r.text
    return r.json()


async def set_import(api, wf: dict, import_path: str) -> dict:
    cur = (await api.get(f"/v1/workflows/{wf['id']}")).json()
    ir = cur["ir"]
    ir["nodes"][0]["config"]["import_path"] = import_path
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(cur["version"])})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------------------- folders ----
async def test_each_workflow_has_its_own_folder(api_client):
    a, b = await new_flow(api_client, "Folder A"), await new_flow(api_client, "Folder B")
    out = (await api_client.get(f"{P}/workflow-files/{a['id']}")).json()
    folder = out["folder"]
    assert folder == f"flows/{a['id'].lower()}"
    paths = {f["path"] for f in out["files"]}
    assert f"{folder}/scripts/__init__.py" in paths and "scripts/example.py" in paths  # own and shared code

    r = await api_client.post(f"{P}/scripts", json={"name": "shout", "workflow_id": a["id"]})
    assert r.status_code == 201, r.text
    assert r.json()["import_path"] == f"flows.{a['id'].lower()}.scripts.shout:run"
    again = await api_client.post(f"{P}/scripts", json={"name": "shout", "workflow_id": a["id"]})
    assert again.status_code == 409 and again.json()["import_path"] == r.json()["import_path"]

    seen_by_b = {f["path"] for f in (await api_client.get(f"{P}/workflow-files/{b['id']}")).json()["files"]}
    assert not any(p.startswith(f"{folder}/") for p in seen_by_b)  # other workflows' code is out of the way
    cat_b = (await api_client.get(f"{P}/workspace/catalog", params={"workflow_id": b["id"]})).json()
    cat_a = (await api_client.get(f"{P}/workspace/catalog", params={"workflow_id": a["id"]})).json()
    own = r.json()["import_path"]
    assert own in {s["import_path"] for s in cat_a["scripts"]} and own not in {
        s["import_path"] for s in cat_b["scripts"]
    }
    assert "scripts.example:run" in {s["import_path"] for s in cat_b["scripts"]}  # shared code for everyone

    other = await api_client.post(f"{P}/workflows", json={"name": "Elsewhere"})
    assert other.status_code == 201
    missing = await api_client.get(f"{P}/workflow-files/wf_NOPE")
    assert missing.status_code == 404


async def test_a_workflow_runs_its_own_code(api_client):
    wf = await new_flow(api_client, "Own code run")
    key = wf["id"].lower()
    await api_client.put(f"{P}/files/flows/{key}/scripts/loud.py", json={"content": SHOUT})  # uses shared code
    await api_client.put(f"{P}/files/scripts/textutil.py", json={"content": TEXTUTIL})
    await api_client.put(f"{P}/files/scripts/marks.py", json={"content": MARKS})
    wf = await set_import(api_client, wf, f"flows.{key}.scripts.loud:run")
    assert not [d for d in wf["diagnostics"] if d["severity"] == "error"], wf["diagnostics"]
    r = await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"text": "x"}})
    last = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")][-1]
    assert last["output"]["text"] == "X!", last


def test_catalog_marks_whose_code_it_is(tmp_path):
    ws = Workspace(tmp_path / "p").ensure()
    ws.ensure_workflow("wf_ABC")
    look_src = (
        'from langchain_core.tools import tool\n\n\n@tool\ndef look(q: str) -> str:\n    """Look."""\n    return q\n'
    )
    ws.write("flows/wf_abc/tools/look.py", look_src)
    cat = scan(ws.root)
    look = next(t for t in cat.tools if t["name"] == "look")
    assert look["workflow"] == "wf_abc" and look["import_path"] == "flows.wf_abc.tools.look:look"
    assert next(s for s in cat.scripts if s["name"] == "example")["workflow"] is None


# ----------------------------------------------------------------------------------------- bundle ----
def test_bundle_follows_imports_and_leaves_the_rest_out(tmp_path):
    ws = Workspace(tmp_path / "p").ensure()
    own = ws.ensure_workflow("wf_ONE")
    ws.ensure_workflow("wf_TWO")
    ws.write(f"{own}/scripts/shout.py", SHOUT)
    ws.write("scripts/textutil.py", TEXTUTIL)
    ws.write("scripts/marks.py", MARKS)
    ws.write(f"{own}/prompts.md", "Be loud.\n")
    ws.write(f"{own}/requirements.txt", "rapidfuzz>=3\n-r other.txt\nhttps://evil.example/pkg.whl\n")
    ws.write("requirements.txt", "rapidfuzz>=3\nslugify==0.0.1\n")
    ws.write("flows/wf_two/scripts/secret.py", "X = 1\n")
    ws.write("tools/unused.py", "Y = 2\n")
    ir = json.dumps({"nodes": [{"config": {"import_path": "flows.wf_one.scripts.shout:run"}}]})
    b = collect(ws.root, "wf_ONE", ir, "safe_import('scripts.example:run')")
    assert b.missing == []
    assert set(b.files) == {
        "flows/__init__.py", f"{own}/__init__.py", f"{own}/scripts/__init__.py", f"{own}/scripts/shout.py",
        f"{own}/prompts.md", f"{own}/requirements.txt", "scripts/__init__.py", "scripts/textutil.py",
        "scripts/marks.py", "scripts/example.py",
    }  # fmt: skip
    assert b.requirements == ["rapidfuzz>=3", "slugify==0.0.1"]  # options and URLs are not carried over
    gone = collect(ws.root, "wf_ONE", '{"import_path": "scripts.nope:run"}')
    assert gone.missing == ["scripts.nope:run"]
    assert entry_points('"os:system" "tools.x:y" "examples.demo:run"') == ["tools.x:y"]


async def test_export_bundles_the_workflow_code_and_its_test_passes(api_client, tmp_path):
    wf = await new_flow(api_client, "Bundle me")
    key = wf["id"].lower()
    await api_client.get(f"{P}/workflow-files/{wf['id']}")  # creates the folder
    await api_client.put(f"{P}/files/flows/{key}/scripts/shout.py", json={"content": SHOUT})
    await api_client.put(f"{P}/files/scripts/textutil.py", json={"content": TEXTUTIL})
    await api_client.put(f"{P}/files/scripts/marks.py", json={"content": MARKS})
    await api_client.put(f"{P}/files/tools/unrelated_s17.py", json={"content": "Z = 3\n"})
    await set_import(api_client, wf, f"flows.{key}.scripts.shout:run")

    resp = await api_client.get(f"/v1/workflows/{wf['id']}/export.zip")
    assert resp.status_code == 200, resp.text
    zipfile.ZipFile(io.BytesIO(resp.content)).extractall(tmp_path)
    root = tmp_path / "bundle-me"
    names = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    assert {f"src/flows/{key}/scripts/shout.py", "src/scripts/textutil.py", "src/scripts/marks.py"} <= names
    assert "src/tools/unrelated_s17.py" not in names and "tests/test_workspace_code.py" in names

    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENTCANVAS_", "PYTEST"))}
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_workspace_code.py"],
        cwd=root, env={**env, "PYTHONPATH": str(root / "src")}, capture_output=True, text=True, timeout=120,
        check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-2000:]

    await set_import(api_client, wf, f"flows.{key}.scripts.not_there:run")
    bad = await api_client.get(f"/v1/workflows/{wf['id']}/export.zip")
    assert bad.status_code == 422 and "not_there" in bad.text


# ------------------------------------------------------------------------------------- code help ----
def test_code_prompt_and_reply_parsing():
    body = CodeHelpIn(
        action="fix", path="flows/wf_x/tools/look.py", content="x = 1", problems=["line 1: F821 undefined"]
    )
    msgs = code_prompt(body)
    assert "a tool the AI can call" in msgs[0]["content"] and "F821" in msgs[1]["content"]
    text, code = split_reply("Fixed it.\n\n```python\nx = 2\n```", "fix")
    assert text == "Fixed it." and code == "x = 2\n"
    assert split_reply("It sets x.", "explain") == ("It sets x.", None)
    assert split_reply("```python\nx = 3\n```", "write")[0] == "Here is the updated file."


async def test_code_help_endpoint(app, api_client):
    await api_client.put(f"{P}/copilot-settings", json={"source": "separate", "model": "anthropic:claude-haiku-4-5"})
    wf = await new_flow(api_client, "Code help")
    url = f"/v1/workflows/{wf['id']}/copilot/code"
    script(app, "Here you go.\n```python\ndef run(state):\n    return {'n': len(state)}\n```")
    r = await api_client.post(url, json={"action": "write", "path": "scripts/count.py", "instruction": "count"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert (
        out["text"] == "Here you go."
        and out["code"].startswith("def run(state):")
        and out["model"].endswith("haiku-4-5")
    )
    files = {f["path"] for f in (await api_client.get(f"{P}/files")).json()}
    assert "scripts/count.py" not in files  # a proposal only: nothing is written

    script(app, "It returns an empty dict.")
    r = await api_client.post(
        url, json={"action": "explain", "path": "scripts/x.py", "content": "def run(s): return {}"}
    )
    assert r.json() == {**r.json(), "text": "It returns an empty dict.", "code": None}
    bad = await api_client.post(url, json={"action": "rewrite", "path": "x.py"})
    assert bad.status_code == 422


async def test_shared_imports_work_in_process_and_in_the_sandbox(tmp_path):
    """Workspace files import each other as they will in an export, in every execution mode."""
    from agentcanvas.sandbox.runners import run_in_sandbox
    from agentcanvas.workspace import load_attr

    ws = Workspace(tmp_path / "imports").ensure()
    own = ws.ensure_workflow("wf_IMP")
    ws.write(f"{own}/scripts/shout.py", SHOUT)
    ws.write("scripts/textutil.py", TEXTUTIL)
    ws.write("scripts/marks.py", MARKS)
    ws.write(f"{own}/scripts/rel.py", "from . import shout\n\n\ndef run(state):\n    return shout.run(state)\n")
    path = "flows.wf_imp.scripts.shout:run"
    assert load_attr(path, ws.root)({"text": "hi"}) == {"text": "HI!"}
    assert load_attr("flows.wf_imp.scripts.rel:run", ws.root)({"text": "yo"}) == {"text": "YO!"}
    out = await run_in_sandbox("subprocess", ws.root, path, {"text": "hey"}, image="unused")
    assert out == {"text": "HEY!"}
