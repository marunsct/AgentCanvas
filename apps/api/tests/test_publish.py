"""Sprint 8 publish (M6-T4): checklist, pinned versions behind API keys, key rotation, public endpoints."""

from __future__ import annotations

from test_versions import HIGH_CASE, new_triage

P = "/v1/projects/prj_default"


def greeter(wf_id: str, greeting: str) -> dict:
    return {
        "id": wf_id, "name": "Greeter",
        "state": {"channels": [{"name": "who"}, {"name": "out"}]},
        "nodes": [{"id": "n_g", "type": "core.set", "name": "greet",
                   "config": {"values": {"out": greeting + " {{ state.who }}"}}}],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_g"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_g"}, "target": {"node": "END"}},
        ],
    }  # fmt: skip


async def make_greeter(api, greeting: str = "Hello") -> str:
    wf = (await api.post(f"{P}/workflows", json={"name": "Greeter"})).json()
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": greeter(wf["id"], greeting)}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    return wf["id"]


async def set_greeting(api, wf_id: str, greeting: str) -> None:
    cur = (await api.get(f"/v1/workflows/{wf_id}")).json()
    r = await api.put(
        f"/v1/workflows/{wf_id}", json={"ir": greeter(wf_id, greeting)}, headers={"If-Match": str(cur["version"])}
    )
    assert r.status_code == 200, r.text


def bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


async def test_publish_pins_the_version_and_keys_rotate(api_client):
    """M6-T4 AC: editing the draft does not change published behaviour; rotating the key invalidates the old one."""
    wf = await make_greeter(api_client)
    await api_client.post(
        f"/v1/workflows/{wf}/test-cases",
        json={"name": "hello", "input": {"who": "Ada"}, "expect": {"channels": {"out": "Hello Ada"}}},
    )
    pub = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()
    assert [c["name"] for c in pub["checklist"]] == ["validation", "policies", "secrets", "tests", "contract"]
    assert all(c["ok"] for c in pub["checklist"]) and pub["deployment"]["semver"] == "1.0.0"
    dep, key = pub["deployment"]["id"], pub["api_key"]
    assert key.startswith("ac_") and pub["deployment"]["key_prefix"] == key[:10]

    call = f"/v1/deployments/{dep}/runs/wait"
    res = (await api_client.post(call, json={"input": {"who": "Ada"}}, headers=bearer(key))).json()
    assert res["status"] == "success" and res["output"]["out"] == "Hello Ada"
    assert (await api_client.post(call, json={"input": {}})).status_code == 401
    assert (await api_client.post(call, json={"input": {}}, headers=bearer("ac_wrong"))).status_code == 401

    await set_greeting(api_client, wf, "Howdy")  # the draft changes, the endpoint does not
    res = (await api_client.post(call, json={"input": {"who": "Bo"}}, headers=bearer(key))).json()
    assert res["output"]["out"] == "Hello Bo"
    thread = (await api_client.get(f"/v1/threads/{res['thread_id']}")).json()
    assert thread["values"]["out"] == "Hello Bo"  # thread views use the deployment's version too

    # republish (the old expectation no longer holds: drop it): same deployment and key, new version
    cases = (await api_client.get(f"/v1/workflows/{wf}/test-cases")).json()
    await api_client.delete(f"/v1/workflows/{wf}/test-cases/{cases[0]['id']}")
    again = (await api_client.post(f"/v1/workflows/{wf}/publish", json={"message": "howdy"})).json()
    assert again["deployment"]["id"] == dep and again["api_key"] is None and again["deployment"]["semver"] == "1.1.0"
    res = (await api_client.post(call, json={"input": {"who": "Cy"}}, headers=bearer(key))).json()
    assert res["output"]["out"] == "Howdy Cy"

    new_key = (await api_client.post(f"/v1/deployments/{dep}/rotate-key")).json()["api_key"]
    assert (await api_client.post(call, json={"input": {}}, headers=bearer(key))).status_code == 401
    assert (await api_client.post(call, json={"input": {"who": "D"}}, headers=bearer(new_key))).status_code == 200
    listed = (await api_client.get(f"/v1/workflows/{wf}/deployments")).json()
    assert [(d["environment"], d["semver"], d["active"]) for d in listed] == [("prod", "1.1.0", True)]
    await api_client.delete(f"/v1/deployments/{dep}")
    assert (await api_client.post(call, json={"input": {}}, headers=bearer(new_key))).status_code == 401


async def test_publish_refused_by_the_checklist(api_client):
    """FR-DEP-02: a failing test case (or a missing secret) blocks publishing with a clear message."""
    wf = await make_greeter(api_client)
    await api_client.post(
        f"/v1/workflows/{wf}/test-cases",
        json={"name": "polite", "input": {"who": "Ada"}, "expect": {"channels": {"out": "Good day Ada"}}},
    )
    r = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert r.status_code == 422
    body = r.json()
    assert body["detail"] == "publish refused: tests: 0/1 test case(s) pass"
    tests = next(c for c in body["checklist"] if c["name"] == "tests")
    assert tests["items"][0].startswith("polite: channel 'out'")
    assert (await api_client.get(f"/v1/workflows/{wf}/deployments")).json() == []
    assert (await api_client.get(f"/v1/workflows/{wf}/versions")).json() == []  # nothing was snapshotted

    cur = (await api_client.get(f"/v1/workflows/{wf}")).json()
    ir = cur["ir"]
    ir["nodes"][0]["config"]["values"]["out"] = "Good day {{ state.who }}"
    ir["resources"] = [
        {"id": "r_h", "type": "tool.http", "name": "crm",
         "config": {"url": "https://crm.example.com/x", "description": "CRM lookup",
                    "allowed_domains": ["crm.example.com"], "headers": {"Authorization": {"$secret": "CRM_TOKEN"}}}}
    ]  # fmt: skip
    await api_client.put(f"/v1/workflows/{wf}", json={"ir": ir}, headers={"If-Match": str(cur["version"])})
    r = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert r.status_code == 422 and "secrets: missing: CRM_TOKEN" in r.json()["detail"]
    await api_client.put(f"{P}/secrets/CRM_TOKEN", json={"value": "t"})
    ok = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert ok.status_code == 200, ok.text


async def test_published_approval_resumes_through_the_api(app, api_client):
    """A deployment's conversation pauses for approval and is resumed with the key; the draft cannot touch it."""
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    wf = await new_triage(api_client)
    cur = (await api_client.get(f"/v1/workflows/{wf['id']}")).json()
    pub = (await api_client.post(f"/v1/workflows/{wf['id']}/publish", json={})).json()
    dep, key = pub["deployment"]["id"], pub["api_key"]
    res = (
        await api_client.post(
            f"/v1/deployments/{dep}/runs/wait", json={"input": {"ticket": "down"}}, headers=bearer(key)
        )
    ).json()
    assert res["status"] == "interrupted" and res["interrupts"][0]["canvas_node_id"] == "n_ok"
    draft_run = await api_client.post(
        f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {}, "thread_id": res["thread_id"]}
    )
    assert draft_run.status_code == 409  # a published conversation never continues on the draft
    done = (
        await api_client.post(
            f"/v1/deployments/{dep}/threads/{res['thread_id']}/resume", json={"value": {"approved": True}},
            headers=bearer(key),
        )
    ).json()  # fmt: skip
    assert done["status"] == "success" and done["output"]["approved"] is True
    assert cur["id"] == wf["id"]


async def test_api_end_users_live_under_the_deployment():
    """A key holder passing an organisation member's id does not get that member's identity (or memories)."""
    from types import SimpleNamespace

    from agentcanvas.api.v1.deployments import ApiRunIn, _api_context
    from agentcanvas.ir.models import Workflow

    class NoRows:
        async def get(self, *_):
            return None

    uid = {"name": "uid", "type": {"type": "string"}, "source": "user"}
    wf = Workflow.model_validate({"id": "wf_ctx", "name": "Ctx", "context": {"fields": [uid]}, "nodes": []})
    dep = SimpleNamespace(id="dep_1", workflow_id="wf_ctx")
    ctx = await _api_context(NoRows(), dep, wf, ApiRunIn(user_id="usr_alice"))  # type: ignore[arg-type]
    assert ctx == {"uid": "api:dep_1:usr_alice"}
    assert await _api_context(NoRows(), dep, wf, ApiRunIn()) == {"uid": "api:dep_1"}  # type: ignore[arg-type]


async def test_concurrent_first_publishes_share_one_deployment(app, api_client):
    """Both first publishes of an environment found no deployment: the second to insert moves the first's."""
    from sqlalchemy import select

    from agentcanvas.db.models import Deployment, WorkflowRow, WorkflowVersion
    from agentcanvas.services.deployments import DeploymentService

    wf = await make_greeter(api_client)
    first = await api_client.post(f"/v1/workflows/{wf}/publish", json={"environment": "qa"})
    assert first.status_code == 200, first.text
    async with app.state.db.sessionmaker() as s:
        row = await s.get(WorkflowRow, wf)
        assert row is not None
        version = (await s.scalars(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf))).one()
        svc = DeploymentService(s)
        real = s.scalar
        calls = 0

        async def stale(q, *args, **kwargs):  # the first look ran before the other publish committed
            nonlocal calls
            calls += 1
            return None if calls == 1 else await real(q, *args, **kwargs)

        s.scalar = stale  # type: ignore[method-assign]
        dep, key = await svc.publish(row, "qa", version)
    assert key is None and dep.id == first.json()["deployment"]["id"]
    async with app.state.db.sessionmaker() as s:
        rows = (await s.scalars(select(Deployment).where(Deployment.workflow_id == wf))).all()
    assert [(d.environment, d.version_id) for d in rows] == [("qa", version.id)]


async def test_an_organisation_can_limit_where_workflows_are_published(api_client):
    """No list: any environment (the web app offers dev, staging, prod). A list: only its names."""
    settings = (await api_client.get("/v1/org/settings")).json()
    assert settings["environments"] is None and settings["offered_environments"] == ["dev", "staging", "prod"]
    wf = await make_greeter(api_client)
    assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={"environment": "qa"})).status_code == 200
    out = (await api_client.put("/v1/org/settings", json={"environments": ["staging", "prod", "prod"]})).json()
    assert out["environments"] == ["staging", "prod"] and out["offered_environments"] == ["staging", "prod"]
    refused = await api_client.post(f"/v1/workflows/{wf}/publish", json={"environment": "qa"})
    assert refused.status_code == 422 and "Settings > Organisation" in refused.json()["detail"]
    assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={"environment": "staging"})).status_code == 200
    assert (await api_client.put("/v1/org/settings", json={"environments": ["Bad Name"]})).status_code == 422
    assert (await api_client.put("/v1/org/settings", json={"environments": []})).json()["environments"] is None
