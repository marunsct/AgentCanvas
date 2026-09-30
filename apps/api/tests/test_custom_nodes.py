"""Sprint 7 custom widgets from the workspace: tools (M5-T5), custom nodes (M5-T7), catalog scan."""

from __future__ import annotations

import importlib.util
import json
import sys

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.workspace import Workspace
from agentcanvas.workspace.catalog import scan

P = "/v1/projects/prj_default"

NODE_SRC = '''from pydantic import BaseModel, Field

from agentcanvas.sdk import canvas_node


class Cfg(BaseModel):
    weight: float = Field(2.0, description="multiplier")
    label: str = "score"


@canvas_node(name="acme.score", label="Score", category="Acme", config=Cfg, reads=["amount"], writes=["score"])
def score(state: dict, cfg: Cfg) -> dict:
    """Scores an amount."""
    return {"score": f"{cfg.label}={state['amount'] * cfg.weight:g}"}


@canvas_node(name="acme.tag", config={"type": "object", "properties": {"tag": {"type": "string"}}}, writes=["score"])
async def tag(state: dict, cfg: dict, runtime) -> dict:
    return {"score": f"{state.get('score')}#{cfg.get('tag')}"}


@canvas_node(name="Bad Name")
def bad(state, cfg):
    return {}
'''

TOOL_SRC = '''from langchain_core.tools import tool


@tool
def lookup_order(order_id: str) -> str:
    """Look up an order by id."""
    return f"order {order_id}: shipped"


@tool("refund")
def do_refund(order_id: str) -> str:
    """Refund an order."""
    return "refunded"


def helper():
    return 1
'''

EVENTS_SRC = """from agentcanvas.sdk import on


@on("crm.lead.created", filter="event.data.amount > 100", dedup_key="event.data.id")
def qualify(event, act):
    act.start_workflow("Lead qualification", input={"lead": event.data})
"""


def test_catalog_scan_reads_source_without_importing(tmp_path):
    ws = Workspace(tmp_path / "w").ensure()
    ws.write("nodes/acme.py", NODE_SRC)
    ws.write("tools/orders.py", TOOL_SRC)
    ws.write("events/leads.py", EVENTS_SRC)
    ws.write("nodes/broken.py", "def (:\n")
    cat = scan(ws.root)  # no import: Pydantic config schemas stay open
    assert [t["name"] for t in cat.tools] == ["lookup_order", "refund"]
    assert cat.tools[0] == {
        "name": "lookup_order",
        "import_path": "tools.orders:lookup_order",
        "description": "Look up an order by id.",
    }
    assert [m.type for m in cat.nodes] == ["acme.score", "acme.tag"]
    assert cat.nodes[0].impl == "nodes.acme:score" and cat.nodes[0].provenance == "workspace"
    assert cat.nodes[0].writes == ["score"] and cat.nodes[0].reads == ["amount"]
    assert cat.nodes[1].config_schema["properties"]["tag"] == {"type": "string"}
    assert {p["path"] for p in cat.problems} == {"nodes/acme.py", "nodes/broken.py"}  # bad name, syntax error
    assert [s["import_path"] for s in cat.scripts] == ["scripts.example:run"]
    assert cat.handlers == [
        {
            "name": "qualify", "type": "crm.lead.created", "filter": "event.data.amount > 100",
            "dedup_key": "event.data.id", "import_path": "events.leads:qualify", "targets": ["Lead qualification"],
        }
    ]  # fmt: skip
    trusted = scan(ws.root, import_code=True)  # dev: the Pydantic model becomes the config form
    assert trusted.nodes[0].config_schema["properties"]["weight"]["default"] == 2.0


def custom_workflow(name: str) -> dict:
    return {
        "name": name,
        "state": {"channels": [{"name": "amount", "type": {"type": "number"}}, {"name": "score"}]},
        "nodes": [
            {"id": "n_score", "type": "acme.score", "name": "score", "config": {"weight": 3, "label": "s"}},
            {"id": "n_tag", "type": "acme.tag", "name": "tag", "config": {"tag": "vip"}},
        ],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_score"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_score"}, "target": {"node": "n_tag"}},
            {"id": "e3", "kind": "flow", "source": {"node": "n_tag"}, "target": {"node": "END"}},
        ],
    }


async def make_wf(api, ir: dict) -> dict:
    wf = (await api.post(f"{P}/workflows", json={"name": ir["name"]})).json()
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": {**ir, "id": wf["id"]}}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    return r.json()


def events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


@pytest.mark.asyncio
async def test_custom_nodes_appear_validate_run_and_export(app, api_client, tmp_path):
    """M5-T7 AC: a new custom node appears in the palette after saving and runs in both modes."""
    await api_client.put(f"{P}/files/nodes/acme.py", json={"content": NODE_SRC})
    await api_client.put(f"{P}/files/tools/orders.py", json={"content": TOOL_SRC})
    types = {
        m["type"]: m for m in (await api_client.get("/v1/node-types", params={"project_id": "prj_default"})).json()
    }
    assert types["acme.score"]["provenance"] == "workspace" and types["acme.score"]["category"] == "Acme"
    assert types["acme.score"]["config_schema"]["properties"]["weight"]["default"] == 2.0
    assert "acme.score" not in {m["type"] for m in (await api_client.get("/v1/node-types")).json()}
    cat = (await api_client.get(f"{P}/workspace/catalog")).json()
    assert {t["name"] for t in cat["tools"]} == {"lookup_order", "refund"} and "acme.tag" in cat["nodes"]

    wf = await make_wf(api_client, custom_workflow("custom"))
    assert not [d for d in wf["diagnostics"] if d["severity"] == "error"], wf["diagnostics"]
    bad = await make_wf(
        api_client,
        {**custom_workflow("bad"), "nodes": [{**custom_workflow("x")["nodes"][0], "config": {"weight": "heavy"}}]},
    )
    assert any(d["code"] == "E004" for d in bad["diagnostics"])  # config checked against the Pydantic schema

    fin = events((await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"amount": 5}})).text)[
        -1
    ]
    assert fin["output"]["score"] == "s=15#vip"

    code = (await api_client.get(f"/v1/workflows/{wf['id']}/code")).text
    assert 'ac.custom_node(import_path="nodes.acme:score"' in code
    root = app.state.projects.workspace("prj_default").root  # an exported project runs from its root
    sys.path.insert(0, str(root))
    try:
        path = tmp_path / "exported.py"
        path.write_text(code)
        spec = importlib.util.spec_from_file_location("exported_custom", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = await mod.build_graph(Resources(), checkpointer=InMemorySaver()).ainvoke(
            {"amount": 5}, {"configurable": {"thread_id": "t"}}
        )
        assert out["score"] == "s=15#vip"
    finally:
        sys.path.remove(str(root))
        for name in [m for m in sys.modules if m == "nodes" or m.startswith("nodes.")]:
            del sys.modules[name]


@pytest.mark.asyncio
async def test_custom_nodes_in_the_sandbox(app, api_client):
    app.state.settings.script_execution = "subprocess"
    app.state.projects._resources.clear()
    await api_client.put(
        f"{P}/files/nodes/plain.py",
        json={
            "content": "from agentcanvas.sdk import canvas_node\n\n\n"
            '@canvas_node(name="acme.double", config={"type": "object"}, writes=["score"])\n'
            "def double(state, cfg):\n    return {'score': state['amount'] * cfg.get('by', 2)}\n"
        },
    )
    ir = custom_workflow("sandboxed")
    ir["nodes"] = [{"id": "n_d", "type": "acme.double", "name": "double", "config": {"by": 4}}]
    ir["edges"] = [
        {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_d"}},
        {"id": "e2", "kind": "flow", "source": {"node": "n_d"}, "target": {"node": "END"}},
    ]
    ir["state"]["channels"][1] = {"name": "score", "type": {"type": "number"}}
    wf = await make_wf(api_client, ir)
    fin = events((await api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"amount": 5}})).text)[
        -1
    ]
    assert fin["output"]["score"] == 20
