"""Server settings an organisation's admin may override (Settings > Organisation): the server's environment variable is
the default and, where security or the operator's capacity is at stake, the ceiling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from test_publish import bearer, make_greeter
from test_ratelimit import limits  # noqa: F401 - fixture
from test_roles import act_as
from test_tools import mcp_wf

from agentcanvas.api.deps import current_user
from agentcanvas.db.models import AuditLog
from agentcanvas.services.org_settings import effective
from agentcanvas.settings import Settings

ORG = "/v1/org/settings"
LOCAL = {"transport": "stdio", "command": "python", "url": None}


async def put(api, **limits_) -> dict:
    r = await api.put(ORG, json={"limits": limits_})
    assert r.status_code == 200, r.text
    return r.json()["limits"]


def test_the_servers_ceilings_apply_every_time_a_value_is_read():
    server = Settings(allow_local_mcp=True, rate_limit_per_project=100, rate_limit_per_deployment=0,
                      audit_retention_days=400, memory_retention_days=0)  # fmt: skip
    own = {"allow_local_mcp": False, "rate_limit_per_project": 50, "rate_limit_per_deployment": 7,
           "audit_retention_days": 30, "memory_retention_days": 14}  # fmt: skip
    out = effective(server, own)
    assert out["allow_local_mcp"] is False and out["rate_limit_per_project"] == 50
    assert out["rate_limit_per_deployment"] == 7  # the server's 0 is unlimited: any limit is lower
    assert out["audit_retention_days"] == 400 and out["memory_retention_days"] == 14  # never a shorter audit log
    # the operator tightens the server later: the organisation's higher value stops applying
    tighter = server.model_copy(update={"rate_limit_per_project": 20, "audit_retention_days": 800})
    now = effective(tighter, {"rate_limit_per_project": 50, "audit_retention_days": 500})
    assert now["rate_limit_per_project"] == 20 and now["audit_retention_days"] == 800
    # an organisation cannot switch local MCP on, and "forever" is always a longer audit log
    assert (
        effective(server.model_copy(update={"allow_local_mcp": False}), {"allow_local_mcp": True})["allow_local_mcp"]
        is False
    )
    assert effective(server, {"audit_retention_days": 0})["audit_retention_days"] == 0


async def test_an_admin_sets_clears_and_is_refused_above_the_ceiling(app, api_client):
    s = app.state.settings
    first = (await api_client.get(ORG)).json()["limits"]
    assert first["own"] == {k: None for k in first["own"]}  # nothing set: the server's values apply
    assert first["effective"] == first["server"] and first["server"]["rate_limit_per_project"] == 600

    got = await put(api_client, rate_limit_per_project=50, memory_retention_days=30)
    assert got["own"]["rate_limit_per_project"] == 50 and got["effective"]["rate_limit_per_project"] == 50
    assert got["effective"]["memory_retention_days"] == 30 and got["server"]["rate_limit_per_project"] == 600
    kept = await put(api_client, rate_limit_per_webhook=5)  # what is not sent is kept
    assert kept["own"]["rate_limit_per_project"] == 50 and kept["own"]["rate_limit_per_webhook"] == 5
    cleared = await put(api_client, rate_limit_per_project=None)  # null returns it to the server's
    assert cleared["own"]["rate_limit_per_project"] is None and cleared["effective"]["rate_limit_per_project"] == 600
    detail = (await api_client.get("/v1/org/audit", params={"action": "put_org_settings"})).json()[0]["detail"]
    assert detail["limits"] == {"rate_limit_per_project": None}

    refused = {
        "rate_limit_per_project": 601,  # above the server's 600
        "allow_local_mcp": True,  # the server forbids local MCP servers
        "audit_retention_days": 30,  # shorter than the server's 400
    }
    assert s.allow_local_mcp is False
    for key, value in refused.items():
        r = await api_client.put(ORG, json={"limits": {key: value}})
        assert r.status_code == 422, (key, r.text)
    assert (await api_client.put(ORG, json={"limits": {"rate_limit_per_project": 0}})).status_code == 422
    longer = await put(api_client, audit_retention_days=0, allow_local_mcp=False)
    assert longer["effective"]["audit_retention_days"] == 0 and longer["effective"]["allow_local_mcp"] is False


async def test_only_admins_change_them(app, api_client):
    try:
        for role in ("viewer", "reviewer", "builder"):
            act_as(app, role)
            r = await api_client.put(ORG, json={"limits": {"rate_limit_per_project": 5}})
            assert r.status_code == 403, role
    finally:
        app.dependency_overrides.pop(current_user, None)
    assert (await api_client.get(ORG)).json()["limits"]["own"]["rate_limit_per_project"] is None


async def test_an_organisation_lowers_its_rate_limits(app, api_client, limits):  # noqa: F811
    limits.rate_limit_per_deployment, limits.rate_limit_per_project = 120, 600
    wf = await make_greeter(api_client)
    pub = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()
    call, key = f"/v1/deployments/{pub['deployment']['id']}/runs/wait", bearer(pub["api_key"])
    await put(api_client, rate_limit_per_deployment=2)
    for _ in range(2):
        assert (await api_client.post(call, json={"input": {"who": "Ada"}}, headers=key)).status_code == 200
    refused = await api_client.post(call, json={"input": {"who": "Ada"}}, headers=key)
    assert refused.status_code == 429 and refused.json()["limit"].startswith("deployment:")
    await put(api_client, rate_limit_per_deployment=None)  # back to the server's 120
    assert (await api_client.post(call, json={"input": {"who": "Ada"}}, headers=key)).status_code == 200


async def test_an_organisation_switches_local_mcp_off_where_the_server_allows_it(app, api_client):
    app.state.settings.allow_local_mcp = True
    try:
        wf = (await api_client.post("/v1/projects/prj_default/workflows", json={"name": "Local"})).json()
        ir = mcp_wf(LOCAL).model_dump(mode="json") | {"id": wf["id"]}

        async def codes() -> set[str]:
            r = await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": "1"})
            assert r.status_code == 200, r.text
            return {d["code"] for d in r.json()["diagnostics"]}

        assert "E042" not in await codes()  # the server allows local MCP servers
        await put(api_client, allow_local_mcp=False)
        r = await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": "2"})
        assert "E042" in {d["code"] for d in r.json()["diagnostics"]}  # this organisation does not
        inspect = await api_client.post(
            "/v1/mcp/inspect?project_id=prj_default", json={"name": "local", "config": {"transport": "stdio"}}
        )
        assert inspect.status_code == 403
    finally:
        app.state.settings.allow_local_mcp = False


async def test_an_organisation_keeps_its_audit_log_longer_and_deletes_memories_sooner(app, api_client):
    job, now = app.state.retention, datetime.now(UTC)
    async with app.state.db.sessionmaker() as s:
        s.add(AuditLog(org_id="org_default", action="old", target="-", created_at=now - timedelta(days=500)))
        s.add(AuditLog(org_id="org_default", action="older", target="-", created_at=now - timedelta(days=900)))
        await s.commit()
    await put(api_client, audit_retention_days=800)  # the server's 400 is the least
    await job.tick(now)
    actions = {e["action"] for e in (await api_client.get("/v1/org/audit")).json()}
    assert "old" in actions and "older" not in actions

    store = app.state.runner.adapter.store
    await store.aput(("org:org_default", "users", "u1"), "likes", {"text": "tea"})
    later = now + timedelta(days=30)
    assert await job.expire_memory(later, ["org_default"]) == 0  # the server keeps memories
    await put(api_client, memory_retention_days=7)
    assert await job.expire_memory(later, ["org_default"]) == 1
