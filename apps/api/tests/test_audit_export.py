"""Security events leave the server in every sign-in mode (ASVS V16): each security-relevant audit entry is a
structured log line, and with AGENTCANVAS_AUDIT_SINK_URL a signed CloudEvent; SCIM and group-driven role changes go
the same way; refused sign-ins and refused actions are logged with what was tried, never the credential itself."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from test_auth import SIGNING_JWK, bearer, settings, token  # noqa: F401 - `settings` turns OIDC sign-in on

from agentcanvas.security.auth import Authenticator
from agentcanvas.services.audit_sink import AuditEvent, WebhookDestination, destinations_for
from agentcanvas.settings import Settings
from agentcanvas.vendor.matura.signatures import verify

LONG = int(time.time()) + 86400
ALICE = bearer(token(exp=LONG))  # bootstrap admin of org_default
SECRET = "audit-sink-secret-0123456789"


class Lines(list):
    """Stands in for a module's logger (loggers are cached once used, so structlog's capture cannot see them)."""

    def __getattr__(self, level: str):
        return lambda event, **kw: self.append({"event": event, "log_level": level, **kw})


@pytest.fixture
async def client(app):
    app.state.authenticator = Authenticator(app.state.settings, jwks={"keys": [SIGNING_JWK]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/v1/me", headers=ALICE)).status_code == 200
        yield c


async def test_an_oidc_installation_sends_security_events_to_its_own_endpoint():
    seen: list[httpx.Request] = []
    on = Settings(auth_mode="oidc", audit_sink_url="https://siem.example.com/ingest", audit_sink_secret=SECRET)
    [hook] = destinations_for(on)
    assert isinstance(hook, WebhookDestination)
    hook.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(202)))
    ev = AuditEvent("org_a", "usr_a", "create_scim_token", "org_id=org_a", {"caller": "token tok_1"}, "10.0.0.1", id=7)
    await hook.send(ev)
    await hook.aclose()
    verify(SECRET, seen[0].headers["agentcanvas-signature"], seen[0].content)
    body = json.loads(seen[0].content)
    assert body["type"] == "agentcanvas.audit.create_scim_token" and body["tenantid"] == "org_a"
    assert body["id"] == "audit-7"  # the entry's: the same on a repeated delivery
    assert destinations_for(Settings(auth_mode="oidc")) == []  # no endpoint: the log line only
    with pytest.raises(ValueError, match="AUDIT_SINK_SECRET"):
        destinations_for(Settings(auth_mode="oidc", audit_sink_url="https://siem.example.com/ingest"))


async def test_scim_and_group_role_changes_reach_the_log_line(app, client, monkeypatch):
    from agentcanvas.services import audit_sink

    monkeypatch.setattr(audit_sink, "audit_log", logs := Lines())
    r = await client.put("/v1/org/settings", json={"sso_groups": {"roles": {"ops": "admin"}}}, headers=ALICE)
    assert r.status_code == 200, r.text
    made = (await client.post("/v1/org/scim/token", headers=ALICE)).json()["token"]
    r = await client.post("/scim/v2/Users", json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                                                   "userName": "carol@example.com"}, headers=bearer(made))  # fmt: skip
    assert r.status_code == 201, r.text
    bob = bearer(token(sub="bob-sub", email="bob@example.com", groups=["ops"], exp=LONG))
    assert (await client.get("/v1/me", headers=bob)).json()["role"] == "admin"
    lines = [e for e in logs if e["event"] == "audit.event"]  # always, with or without a destination
    actions = [e["action"] for e in lines]
    assert {"put_org_settings", "create_scim_token", "scim_create_user", "sso_role_sync"} <= set(actions), actions
    sync = next(e for e in lines if e["action"] == "sso_role_sync")
    assert sync["org_id"] == "org_default" and sync["actor_id"] and sync["id"]


async def test_refused_sign_ins_and_actions_are_logged_without_the_credential(client, monkeypatch):
    from agentcanvas.api import audit

    monkeypatch.setattr(audit, "log", logs := Lines())
    r = await client.get("/v1/me", headers=bearer("act_" + "A" * 43))
    assert r.status_code == 401
    r = await client.post("/scim/v2/Users", json={}, headers=bearer("scim_" + "B" * 40))
    assert r.status_code == 401
    viewer = bearer(token(sub="vic-sub", email="vic@example.com", exp=LONG))
    await client.put("/v1/org/members", json={"email": "vic@example.com", "role": "viewer"}, headers=ALICE)
    r = await client.put("/v1/org/settings", json={"retention_days": 1}, headers=viewer)
    assert r.status_code == 403
    refused = [e for e in logs if e["event"] in ("auth.refused", "auth.forbidden")]
    assert [e["event"] for e in refused] == ["auth.refused", "auth.refused", "auth.forbidden"], refused
    assert all(e["log_level"] == "warning" and e["ip"] for e in refused)
    assert refused[0]["credential"] == "personal token act_AAAAAA" and refused[0]["path"] == "/v1/me"
    assert refused[1]["credential"] == "SCIM token scim_BBBBB"
    assert refused[2]["org_id"] == "org_default" and refused[2]["user_id"] and refused[2]["route"] == "put_org_settings"
    assert "A" * 20 not in json.dumps(refused) and "B" * 20 not in json.dumps(refused)
