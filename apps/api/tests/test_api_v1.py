"""/v1 workflow API: CRUD, optimistic locking, commands, layout, catalog (Sprint 1)."""

import asyncio

import pytest

from agentcanvas.db.session import run_alembic

P = "/v1/projects/prj_default/workflows"


async def create(api, **body):
    r = await api.post(P, json={"name": "Demo", **body})
    assert r.status_code == 201, r.text
    return r.json()


async def test_health_ready_and_request_id(api_client):
    assert (await api_client.get("/healthz")).json() == {"status": "ok"}
    r = await api_client.get("/readyz", headers={"x-request-id": "req_abc"})
    assert r.json() == {"status": "ready"} and r.headers["x-request-id"] == "req_abc"
    assert (await api_client.get("/healthz")).headers["x-request-id"].startswith("req_")


async def test_not_ready_when_the_run_store_does_not_answer(app, api_client, monkeypatch):
    """S11 M8-T2: load balancers stop sending traffic to a replica whose store is down; liveness stays up."""

    async def down(*_, **__):
        raise ConnectionError("store unreachable")

    monkeypatch.setattr(app.state.runner.adapter.store, "alist_namespaces", down)
    r = await api_client.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "unavailable", "failed": ["store"]}
    assert (await api_client.get("/healthz")).status_code == 200


async def test_default_project_is_seeded(api_client):
    assert [p["id"] for p in (await api_client.get("/v1/projects")).json()] == ["prj_default"]


async def test_create_empty_workflow(api_client):
    wf = await create(api_client)
    assert wf["id"].startswith("wf_") and wf["version"] == 1 and wf["ir"]["ir_version"] == "1.2"
    assert wf["diagnostics"] == []
    r = await api_client.get(f"/v1/workflows/{wf['id']}")
    assert r.headers["ETag"] == '"1"' and r.json()["ir_hash"] == wf["ir_hash"]


async def test_create_from_template(api_client):
    wf = await create(api_client, name="Router", template="classify_and_route")
    assert wf["name"] == "Router" and len(wf["ir"]["nodes"]) == 5 and wf["diagnostics"] == []
    templates = {t["id"] for t in (await api_client.get("/v1/templates")).json()}
    assert {"classify_and_route", "chat_assistant", "support_triage", "research_brief"} <= templates


async def test_unknown_template_and_project_are_404_problems(api_client):
    r = await api_client.post(P, json={"name": "x", "template": "nope"})
    assert r.status_code == 404 and r.headers["content-type"] == "application/problem+json"
    assert r.json()["title"] == "Not found"
    assert (await api_client.post("/v1/projects/prj_missing/workflows", json={"name": "x"})).status_code == 404
    assert (await api_client.get("/v1/workflows/wf_missing")).status_code == 404


async def test_put_requires_if_match_and_detects_conflicts(api_client, triage):
    wf = await create(api_client)
    ir = triage.model_dump(mode="json") | {"id": wf["id"]}
    assert (await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir})).status_code == 428
    r = await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": '"1"'})
    assert r.status_code == 200 and r.json()["version"] == 2 and r.headers["ETag"] == '"2"'
    stale = await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": '"1"'})
    assert stale.status_code == 409 and stale.json()["expected_version"] == 1


async def test_concurrent_writers_one_wins(api_client, triage):
    wf = await create(api_client)
    ir = triage.model_dump(mode="json") | {"id": wf["id"]}
    results = await asyncio.gather(
        *[api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": "1"}) for _ in range(3)]
    )
    assert sorted(r.status_code for r in results) == [200, 409, 409]


async def test_invalid_ir_is_422_with_errors(api_client):
    wf = await create(api_client)
    bad = {"id": wf["id"], "name": "x", "nodes": [{"id": "n1", "type": "core.llm", "name": "Bad Name"}]}
    r = await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": bad}, headers={"If-Match": "1"})
    assert r.status_code == 422 and "snake_case" in r.json()["errors"][0]["msg"]
    other = {"id": "wf_other", "name": "x"}
    assert (
        await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": other}, headers={"If-Match": "1"})
    ).status_code == 422


async def test_commands_apply_return_diagnostics_and_inverse(api_client):
    wf = await create(api_client)
    cmds = [
        {"op": "SetChannels", "channels": [{"name": "greeting"}]},
        {
            "op": "AddNode",
            "node": {
                "id": "n_hi",
                "type": "core.set",
                "name": "hello",
                "config": {"values": {"greeting": "Hi {{ state.who }}"}},
            },
        },
        {
            "op": "Connect",
            "edge": {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_hi"}},
        },
    ]
    r = await api_client.post(f"/v1/workflows/{wf['id']}/commands", json={"commands": cmds}, headers={"If-Match": "1"})
    body = r.json()
    assert r.status_code == 200 and body["version"] == 2
    assert {d["code"] for d in body["diagnostics"]} == {"E010", "E015", "E023"}  # no END, unknown channel 'who'
    # undo with the returned inverse brings back the empty workflow
    r = await api_client.post(
        f"/v1/workflows/{wf['id']}/commands", json={"commands": body["inverse"]}, headers={"If-Match": "2"}
    )
    assert r.json()["ir"]["nodes"] == [] and r.json()["ir_hash"] == wf["ir_hash"]


async def test_bad_command_is_422_and_atomic(api_client):
    wf = await create(api_client)
    r = await api_client.post(
        f"/v1/workflows/{wf['id']}/commands",
        json={"commands": [{"op": "RemoveNode", "id": "x"}]},
        headers={"If-Match": "1"},
    )
    assert r.status_code == 422 and "unknown node" in r.json()["detail"]
    assert (await api_client.get(f"/v1/workflows/{wf['id']}")).json()["version"] == 1
    r = await api_client.post(
        f"/v1/workflows/{wf['id']}/commands", json={"commands": [{"op": "Explode"}]}, headers={"If-Match": "1"}
    )
    assert r.status_code == 422


async def test_layout_does_not_change_ir_hash(api_client):
    wf = await create(api_client, template="classify_and_route")
    r = await api_client.put(
        f"/v1/workflows/{wf['id']}/layout",
        json={"positions": {"n_route": {"x": 10, "y": 20}, "ghost": {"x": 0, "y": 0}}},
    )
    assert r.json()["layout"] == {"n_route": {"x": 10, "y": 20}}  # unknown ids are dropped
    after = (await api_client.get(f"/v1/workflows/{wf['id']}")).json()
    assert after["ir_hash"] == wf["ir_hash"] and after["version"] == 1 and after["layout"]["n_route"]["x"] == 10


async def test_list_is_paginated(api_client):
    ids = [(await create(api_client, name=f"wf {i}"))["id"] for i in range(5)]
    page1 = (await api_client.get(P, params={"limit": 2})).json()
    page2 = (await api_client.get(P, params={"limit": 2, "cursor": page1["next_cursor"]})).json()
    page3 = (await api_client.get(P, params={"limit": 2, "cursor": page2["next_cursor"]})).json()
    got = [w["id"] for w in page1["items"] + page2["items"] + page3["items"]]
    assert got == sorted(ids, reverse=True) and page3["next_cursor"] is None


async def test_delete(api_client):
    wf = await create(api_client)
    assert (await api_client.delete(f"/v1/workflows/{wf['id']}")).status_code == 204
    assert (await api_client.get(f"/v1/workflows/{wf['id']}")).status_code == 404


async def test_code_and_validate(api_client):
    wf = await create(api_client, template="classify_and_route")
    code = (await api_client.get(f"/v1/workflows/{wf['id']}/code")).text
    assert "builder.add_conditional_edges(" in code and '"question": "answer"' in code
    empty = await create(api_client)
    r = await api_client.get(f"/v1/workflows/{empty['id']}/code")
    assert r.status_code == 200  # an empty graph is valid (nothing to run)
    assert (await api_client.post(f"/v1/workflows/{empty['id']}/validate")).json() == []


async def test_catalog_endpoints(api_client):
    types = {t["type"]: t for t in (await api_client.get("/v1/node-types")).json()}
    assert {"core.llm", "core.agent", "core.router", "model.chat", "tool.python"} <= set(types)
    assert types["core.llm"]["inputs"][0] == {
        "name": "model",
        "type": "ChatModel",
        "required": True,
        "multi": False,
        "ordered": False,
        "description": "The AI model that writes the reply. Plug in an \u201cAI model\u201d block.",
    }
    assert any(m["id"] == "anthropic:claude-sonnet-5-5" for m in (await api_client.get("/v1/models")).json())
    schema = (await api_client.get("/v1/ir-schema")).json()
    assert schema["title"] == "Workflow" and "Channel" in schema["$defs"]


@pytest.mark.parametrize(
    ("kind", "source", "ok"),
    [
        ("cel", 'state.urgency == "high"', True),
        ("cel", "state.urgency ==", False),
        ("template", "Hi {{ state.name }}", True),
        ("template", "{{ oops", False),
    ],
)
async def test_expression_check(api_client, kind, source, ok):
    r = (await api_client.post("/v1/expressions/check", json={"kind": kind, "source": source})).json()
    assert r["ok"] is ok and (r["error"] is None) is ok


def test_migrations_upgrade_and_downgrade(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'm.db'}"
    run_alembic(url, "upgrade", "head")
    run_alembic(url, "downgrade", "base")
    run_alembic(url, "upgrade", "head")
