"""AgentCanvas's full adoption of Matura (TS26): workspaces, the portal link, data modes and audit forwarding.
Row-level security is in test_matura_rls.py."""

import json
import uuid

import httpx
import pytest
from test_matura import ACME, ANA, BEN, GLOBEX, ISSUER, SECRET, as_user, event, send, tenant

from agentcanvas.security.auth import Authenticator
from agentcanvas.services import matura
from agentcanvas.services.audit_sink import AuditEvent, MaturaWebhookSink, NoopSink, sink_for
from agentcanvas.settings import Settings
from agentcanvas.vendor.matura import verify

MAIN, RESEARCH, OLD, ELSEWHERE = (str(uuid.uuid4()) for _ in range(4))


def matura_settings(settings: Settings, **over) -> Settings:
    return settings.model_copy(
        update={
            "auth_mode": "matura", "oidc_issuer": ISSUER.issuer, "oidc_audience": "agentcanvas",
            "oidc_client_id": "agentcanvas", "matura_webhook_secret": SECRET,
            "matura_public_url": "https://id.matura.test", **over,
        }
    )  # fmt: skip


@pytest.fixture
async def client(app):
    matura.forget()
    app.state.authenticator = Authenticator(app.state.settings, jwks=ISSUER.jwks)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


class Recorder:
    def __init__(self):
        self.events: list[AuditEvent] = []

    async def emit(self, event: AuditEvent) -> None:
        self.events.append(event)


def workspace(wid, name, tid=ACME, status="active", default=False):
    return {"id": wid, "tenant_id": tid, "name": name, "status": status, "is_default": default}


class TestInMatura:
    @pytest.fixture
    def settings(self, settings: Settings) -> Settings:
        return matura_settings(settings)

    async def test_projects_can_belong_to_a_workspace_and_be_listed_by_it(self, client):
        for ev in (
            event("tenant", "tenant.created", tenant()),
            event("tenant", "tenant.created", tenant(GLOBEX, "Globex", "globex")),
            event("workspace", "workspace.created", workspace(MAIN, "Main", default=True)),
            event("workspace", "workspace.created", workspace(RESEARCH, "Research")),
            event("workspace", "workspace.archived", workspace(OLD, "Old", status="archived")),
            event("workspace", "workspace.created", workspace(ELSEWHERE, "Globex team", tid=GLOBEX)),
        ):
            assert (await send(client, ev)).status_code == 204
        ana = as_user(ANA, ACME, "builder")
        listed = (await client.get("/v1/workspaces", headers=ana)).json()
        assert listed == [
            {"id": MAIN, "name": "Main", "is_default": True}, {"id": RESEARCH, "name": "Research", "is_default": False}
        ]  # fmt: skip

        r = await client.post("/v1/projects", json={"name": "Lab", "workspace_id": RESEARCH}, headers=ana)
        assert r.status_code == 201 and r.json()["workspace_id"] == RESEARCH
        lab = r.json()["id"]
        plain = (await client.post("/v1/projects", json={"name": "Plain"}, headers=ana)).json()
        assert plain["workspace_id"] is None
        for wid in (OLD, ELSEWHERE, "not-a-uuid"):  # archived, another organisation's, nonsense
            r = await client.post("/v1/projects", json={"name": f"x {wid}", "workspace_id": wid}, headers=ana)
            assert r.status_code == 422, r.text

        names = lambda r: [p["name"] for p in r.json()]  # noqa: E731
        assert names(await client.get("/v1/projects", params={"workspace_id": RESEARCH}, headers=ana)) == ["Lab"]
        assert names(await client.get("/v1/projects", params={"workspace_id": MAIN}, headers=ana)) == []
        assert {"Lab", "Plain"} <= set(names(await client.get("/v1/projects", headers=ana)))

        # moved into the default workspace, then out again; viewers and other organisations cannot move it
        r = await client.put(f"/v1/projects/{lab}/workspace", json={"workspace_id": MAIN}, headers=ana)
        assert r.status_code == 200 and r.json()["workspace_id"] == MAIN
        assert names(await client.get("/v1/projects", params={"workspace_id": MAIN}, headers=ana)) == ["Lab"]
        assert (await client.put(f"/v1/projects/{lab}/workspace", json={"workspace_id": None}, headers=ana)).json()[
            "workspace_id"
        ] is None
        viewer = as_user(BEN, ACME, "viewer")
        r = await client.put(f"/v1/projects/{lab}/workspace", json={"workspace_id": MAIN}, headers=viewer)
        assert r.status_code == 403
        ben = as_user(BEN, GLOBEX, "admin")
        r = await client.put(f"/v1/projects/{lab}/workspace", json={"workspace_id": ELSEWHERE}, headers=ben)
        assert r.status_code == 404
        assert (await client.get("/v1/workspaces", headers=ben)).json()[0]["id"] == ELSEWHERE

    async def test_me_links_to_the_organisation_in_the_portal(self, client):
        assert (await client.get("/v1/auth/config")).json()["portal_url"] == "https://id.matura.test/portal/"
        # before Matura has sent the organisation, the link uses its id; afterwards its slug
        me = (await client.get("/v1/me", headers=as_user(ANA, ACME, "admin", slug="acme"))).json()
        assert me["portal_url"] in (f"https://id.matura.test/portal/{ACME}/", "https://id.matura.test/portal/acme/")
        await send(client, event("tenant", "tenant.updated", tenant(slug="acme-corp"), version=1_000))
        me = (await client.get("/v1/me", headers=as_user(ANA, ACME, "admin"))).json()
        assert me["portal_url"] == "https://id.matura.test/portal/acme-corp/"

    async def test_only_the_shared_data_mode_is_supported(self, client):
        r = await client.get("/v1/me", headers=as_user(ANA, ACME, "builder", pmode="schema"))
        assert r.status_code == 503 and "supports only 'shared'" in r.json()["detail"]
        assert (await client.get("/v1/me", headers=as_user(ANA, ACME, "builder", pmode="shared"))).status_code == 200


def test_portal_address_and_sink_follow_the_settings():
    s = Settings(auth_mode="matura")
    assert matura.portal_base(s) is None
    assert matura.portal_base(s.model_copy(update={"matura_public_url": "https://id.example/"})) == (
        "https://id.example/portal/"
    )
    assert matura.portal_base(
        s.model_copy(update={"matura_public_url": "https://id.example", "matura_portal_url": "https://admin.example"})
    ) == "https://admin.example/"  # fmt: skip
    assert isinstance(sink_for(s), NoopSink)
    on = s.model_copy(update={"matura_audit_url": "https://id.example/v1/audit", "matura_webhook_secret": SECRET})
    assert isinstance(sink_for(on), MaturaWebhookSink)
    assert isinstance(sink_for(on.model_copy(update={"auth_mode": "oidc"})), NoopSink)


async def test_security_actions_reach_the_audit_sink(app, api_client):
    assert isinstance(app.state.audit_sink, NoopSink)  # the default: nothing leaves AgentCanvas
    rec = app.state.audit_sink = Recorder()
    r = await api_client.put("/v1/projects/prj_default/secrets/API_TOKEN", json={"value": "s3cret"})
    assert r.status_code == 204
    r = await api_client.post("/v1/projects/prj_default/workflows", json={"name": "not a security action"})
    assert r.status_code == 201
    assert [e.action for e in rec.events] == ["put_secret"]
    e = rec.events[0]
    assert e.org_id and e.target == "name=API_TOKEN" and "s3cret" not in json.dumps(e.detail)


async def test_the_webhook_sink_signs_cloudevents_and_never_fails_the_caller():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(500 if len(seen) > 1 else 202)

    sink = MaturaWebhookSink(
        "https://id.matura.test/v1/audit", SECRET, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    ev = AuditEvent(ACME, ANA, "publish", "wf_id=wf_1", {"environment": "prod"}, "10.0.0.1")
    await sink.emit(ev)
    await sink.emit(ev)  # Matura answers 500: logged, not raised
    await sink.aclose()
    assert len(seen) == 2
    verify(SECRET, seen[0].headers["matura-signature"], seen[0].content)
    body = json.loads(seen[0].content)
    assert body["type"] == "agentcanvas.audit.publish" and body["tenantid"] == ACME
    assert body["data"]["actor_id"] == ANA and body["data"]["detail"] == {"environment": "prod"}


async def test_workspaces_are_matura_only(api_client):
    assert (await api_client.get("/v1/workspaces")).json() == []
    r = await api_client.post("/v1/projects", json={"name": "W", "workspace_id": str(uuid.uuid4())})
    assert r.status_code == 422 and "Matura" in r.json()["detail"]
    assert (await api_client.get("/v1/projects")).json()[0]["workspace_id"] is None
