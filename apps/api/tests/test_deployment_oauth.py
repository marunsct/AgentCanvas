"""E-11: people sign in to a deployment's MCP and A2A endpoints with OAuth access tokens (the MCP authorization spec,
RFC 9728 protected resource metadata), beside the deployment's keys; each person is their own end user."""

from __future__ import annotations

import json
import re
import time
from typing import Any

import httpx
import jwt
import pytest
from sqlalchemy import select
from test_auth import SIGNING_JWK, SIGNING_KEY, _keypair
from test_protocols import rpc
from test_publish import bearer, make_greeter

from agentcanvas.db.models import AuditLog, Thread
from agentcanvas.services import deployment_oauth
from agentcanvas.services.deployment_oauth import DEFAULT_SCOPE, AccessTokens

ISSUER = "https://idp.test/realms/acme"
OTHER_ISSUER = "https://other-idp.test"
OTHER_KEY, OTHER_JWK = _keypair("o1")


def access_token(aud: str | list[str] | None, sub: str = "alice", key=SIGNING_KEY, kid: str = "k1", **over: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {"iss": ISSUER, "aud": aud, "sub": sub, "iat": now, "exp": now + 300,
                              "scope": f"openid {DEFAULT_SCOPE}"}  # fmt: skip
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid, "typ": "at+jwt"})


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"oidc_issuer": ISSUER})


@pytest.fixture
def tokens(app) -> AccessTokens:
    app.state.access_tokens = AccessTokens(
        app.state.settings, jwks={ISSUER: {"keys": [SIGNING_JWK]}, OTHER_ISSUER: {"keys": [OTHER_JWK]}}
    )
    return app.state.access_tokens


async def _published(api) -> tuple[str, str]:
    pub = (await api.post(f"/v1/workflows/{await make_greeter(api)}/publish", json={})).json()
    return pub["deployment"]["id"], pub["api_key"]


def mcp_url(dep: str) -> str:
    return f"/v1/deployments/{dep}/mcp"


def resource(dep: str, kind: str = "mcp") -> str:
    return f"http://test/v1/deployments/{dep}/{kind}"


CALL = rpc("tools/call", {"name": "greeter", "arguments": {"who": "Ada"}})


def challenge_params(header: str) -> dict[str, str]:
    assert header.startswith("Bearer "), header
    return dict(re.findall(r'(\w+)="([^"]*)"', header))


async def test_sign_in_is_off_until_a_builder_turns_it_on(api_client, tokens):
    dep, _ = await _published(api_client)
    got = (await api_client.get(f"/v1/deployments/{dep}/oauth")).json()
    assert got["enabled"] is False and got["authorization_server"] == ISSUER and got["scopes"] == [DEFAULT_SCOPE]
    assert got["mcp_resource"] == resource(dep)
    no_token = await api_client.post(mcp_url(dep), json=CALL)
    assert no_token.status_code == 401 and "www-authenticate" not in no_token.headers  # keys only, as before
    signed = bearer(access_token(resource(dep)))
    assert (await api_client.post(mcp_url(dep), json=CALL, headers=signed)).status_code == 401
    well_known = f"/.well-known/oauth-protected-resource/v1/deployments/{dep}/mcp"
    assert (await api_client.get(well_known)).status_code == 404
    card = (await api_client.get(f"/v1/deployments/{dep}/a2a/.well-known/agent-card.json")).json()
    assert list(card["securitySchemes"]) == ["deploymentKey"]
    deps = (await api_client.get(f"/v1/workflows/{await _wf_of(api_client, dep)}/deployments")).json()
    assert deps[0]["oauth"] is False


async def _wf_of(api, dep: str) -> str:
    for wf in (await api.get("/v1/projects/prj_default/workflows")).json()["items"]:
        if any(d["id"] == dep for d in (await api.get(f"/v1/workflows/{wf['id']}/deployments")).json()):
            return wf["id"]
    raise AssertionError(dep)


async def _turn_on(api, dep: str, **body: Any) -> dict:
    r = await api.put(f"/v1/deployments/{dep}/oauth", json={"enabled": True, **body})
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_client_finds_where_to_sign_in_from_the_401(api_client, tokens):
    """The MCP authorization spec's discovery: 401 with resource_metadata, then the metadata names the issuer."""
    dep, _ = await _published(api_client)
    out = await _turn_on(api_client, dep)
    meta_url = f"http://test/.well-known/oauth-protected-resource/v1/deployments/{dep}/mcp"
    assert out["enabled"] is True and out["mcp_metadata"] == meta_url

    refused = await api_client.post(mcp_url(dep), json=CALL)
    assert refused.status_code == 401
    params = challenge_params(refused.headers["www-authenticate"])
    assert params == {"scope": DEFAULT_SCOPE, "resource_metadata": meta_url}
    meta = await api_client.get(meta_url.removeprefix("http://test"))
    assert meta.status_code == 200 and meta.headers["access-control-allow-origin"] == "*"
    assert meta.json() == {
        "resource": resource(dep), "authorization_servers": [ISSUER], "bearer_methods_supported": ["header"],
        "scopes_supported": [DEFAULT_SCOPE], "resource_name": "Greeter",
    }  # fmt: skip
    a2a = (await api_client.get(f"/.well-known/oauth-protected-resource/v1/deployments/{dep}/a2a")).json()
    assert a2a["resource"] == resource(dep, "a2a")
    a2a_refused = await api_client.post(f"/v1/deployments/{dep}/a2a", json=rpc("tasks/get", {"id": "x"}))
    assert a2a_refused.status_code == 401 and "/a2a" in a2a_refused.headers["www-authenticate"]

    # behind nginx the endpoint's address is under /api, and so is the path after the well-known prefix
    behind = {"x-forwarded-prefix": "/api"}
    at_root = f"/.well-known/oauth-protected-resource/api/v1/deployments/{dep}/mcp"
    proxied = await api_client.get(at_root, headers=behind)
    assert proxied.json()["resource"] == f"http://test/api/v1/deployments/{dep}/mcp"
    wrong = await api_client.get(f"/.well-known/oauth-protected-resource/v1/deployments/{dep}/mcp", headers=behind)
    assert wrong.status_code == 404  # an address callers do not use has no metadata (RFC 9728 section 3.3)
    for missing in ("v1/deployments/dep_nope/mcp", f"v1/deployments/{dep}/runs", f"x/v1/deployments/{dep}/mcp/more"):
        assert (await api_client.get(f"/.well-known/oauth-protected-resource/{missing}")).status_code == 404


async def test_a_signed_in_person_runs_the_deployment_as_their_own_end_user(app, api_client, tokens):
    dep, key = await _published(api_client)
    await _turn_on(api_client, dep)
    res = (await api_client.post(mcp_url(dep), json=CALL, headers=bearer(access_token(resource(dep))))).json()
    out = res["result"]["structuredContent"]
    assert out["status"] == "success" and out["output"]["out"] == "Hello Ada"
    async with app.state.db.sessionmaker() as s:
        thread = await s.get(Thread, out["thread_id"])
        assert thread is not None and thread.end_user == f"api:{dep}:oidc:{ISSUER}:alice"
        row = (await s.scalars(select(AuditLog).where(AuditLog.action == "mcp_endpoint"))).all()[-1]
        assert row.detail["caller"] == "oauth" and row.detail["end_user"] == f"oidc:{ISSUER}:alice"
    # the keys keep working beside sign-in
    assert (await api_client.post(mcp_url(dep), json=CALL, headers=bearer(key))).json()["result"]["isError"] is False
    a2a = f"/v1/deployments/{dep}/a2a"
    msg = {"message": {"role": "user", "messageId": "m1", "parts": [{"kind": "data", "data": {"who": "Grace"}}]}}
    task = await api_client.post(a2a, json=rpc("message/send", msg), headers=bearer(access_token(resource(dep, "a2a"))))
    assert task.json()["result"]["artifacts"][0]["parts"][0]["data"]["out"] == "Hello Grace"
    # the end user's data can be erased by that id, with a key, like any other end user's
    erased = await api_client.post(f"/v1/deployments/{dep}/end-users/erase", json={"user_id": f"oidc:{ISSUER}:alice"},
                                   headers=bearer(key))  # fmt: skip
    assert erased.status_code == 200 and erased.json()["conversations"] == 2, erased.text


@pytest.mark.parametrize(
    ("why", "make", "status", "error"),
    [
        ("expired", lambda dep: access_token(resource(dep), exp=int(time.time()) - 120), 401, "invalid_token"),
        ("another server's token", lambda dep: access_token("https://elsewhere.test/mcp"), 401, "invalid_token"),
        ("the A2A endpoint's token", lambda dep: access_token(resource(dep, "a2a")), 401, "invalid_token"),
        ("no audience", lambda dep: access_token(None), 401, "invalid_token"),
        ("another issuer", lambda dep: access_token(resource(dep), iss=OTHER_ISSUER, key=OTHER_KEY, kid="o1"), 401,
         "invalid_token"),
        ("forged", lambda dep: access_token(resource(dep), key=OTHER_KEY), 401, "invalid_token"),
        ("unknown key", lambda dep: access_token(resource(dep), kid="k9"), 401, "invalid_token"),
        ("blank subject", lambda dep: access_token(resource(dep), sub=" "), 401, "invalid_token"),
        ("not a JWT", lambda dep: "opaque-token", 401, "invalid_token"),
        ("no scope", lambda dep: access_token(resource(dep), scope="openid"), 403, "insufficient_scope"),
    ],
)  # fmt: skip
async def test_tokens_that_are_not_for_this_endpoint_are_refused(api_client, tokens, why, make, status, error):
    dep, _ = await _published(api_client)
    await _turn_on(api_client, dep)
    r = await api_client.post(mcp_url(dep), json=CALL, headers=bearer(make(dep)))
    assert r.status_code == status, (why, r.text)
    params = challenge_params(r.headers["www-authenticate"])
    assert params["error"] == error and params["resource_metadata"].endswith(f"/{dep}/mcp"), why
    assert params["error_description"] and '"' not in params["error_description"]


async def test_a_configured_audience_scopes_and_issuer_are_honoured(api_client, tokens):
    """Entra and Okta issue tokens for a fixed audience (api://…) with `scp`; a deployment may name another issuer."""
    dep, _ = await _published(api_client)
    out = await _turn_on(api_client, dep, audience="api://agentcanvas", scopes=["Deployments.Run", "agents"])
    assert out["audience"] == "api://agentcanvas" and out["scopes"] == ["Deployments.Run", "agents"]
    entra = access_token("api://agentcanvas", scope=None, scp="Deployments.Run agents")
    assert (await api_client.post(mcp_url(dep), json=CALL, headers=bearer(entra))).status_code == 200
    shouted = f"HTTP://TEST/v1/deployments/{dep}/mcp/"  # scheme and host in capitals, a trailing slash: the same
    listed = access_token(shouted, scope=None, scp=["agents", "Deployments.Run"])
    assert (await api_client.post(mcp_url(dep), json=CALL, headers=bearer(listed))).status_code == 200
    partly = access_token("api://agentcanvas", scope="agents")
    r = await api_client.post(mcp_url(dep), json=CALL, headers=bearer(partly))
    assert r.status_code == 403 and challenge_params(r.headers["www-authenticate"])["scope"] == "Deployments.Run agents"

    await _turn_on(api_client, dep, issuer=OTHER_ISSUER, scopes=[])
    meta = (await api_client.get(f"/.well-known/oauth-protected-resource/v1/deployments/{dep}/mcp")).json()
    assert meta["authorization_servers"] == [OTHER_ISSUER] and "scopes_supported" not in meta
    theirs = access_token(resource(dep), iss=OTHER_ISSUER, key=OTHER_KEY, kid="o1", scope=None)
    assert (await api_client.post(mcp_url(dep), json=CALL, headers=bearer(theirs))).status_code == 200
    ours = bearer(access_token(resource(dep)))
    assert (await api_client.post(mcp_url(dep), json=CALL, headers=ours)).status_code == 401


async def test_people_continue_only_their_own_conversations(api_client, tokens):
    dep, key = await _published(api_client)
    await _turn_on(api_client, dep)
    alice, bob = bearer(access_token(resource(dep))), bearer(access_token(resource(dep), sub="bob"))
    first = (await api_client.post(mcp_url(dep), json=CALL, headers=alice)).json()["result"]["structuredContent"]
    again = rpc("tools/call", {"name": "greeter", "arguments": {"who": "Lin", "thread_id": first["thread_id"]}})
    theirs = (await api_client.post(mcp_url(dep), json=again, headers=bob)).json()["result"]
    assert theirs["isError"] is True and "not found" in theirs["content"][0]["text"]
    mine = (await api_client.post(mcp_url(dep), json=again, headers=alice)).json()["result"]
    assert mine["isError"] is False and mine["structuredContent"]["thread_id"] == first["thread_id"]
    # the deployment's key acts for its owner, as before: it continues anyone's conversation
    assert (await api_client.post(mcp_url(dep), json=again, headers=bearer(key))).json()["result"]["isError"] is False

    a2a = f"/v1/deployments/{dep}/a2a"
    a_alice = bearer(access_token(resource(dep, "a2a")))
    a_bob = bearer(access_token(resource(dep, "a2a"), sub="bob"))
    msg = {"message": {"role": "user", "messageId": "m1", "parts": [{"kind": "data", "data": {"who": "Grace"}}]}}
    task = (await api_client.post(a2a, json=rpc("message/send", msg), headers=a_alice)).json()["result"]
    assert (await api_client.post(a2a, json=rpc("tasks/get", {"id": task["id"]}), headers=a_alice)).json()["result"]
    hidden = (await api_client.post(a2a, json=rpc("tasks/get", {"id": task["id"]}), headers=a_bob)).json()
    assert hidden["error"]["code"] == -32001  # the same "not found" as an unknown task
    follow = {"message": {**msg["message"], "messageId": "m2", "contextId": task["contextId"]}}
    refused = (await api_client.post(a2a, json=rpc("message/send", follow), headers=a_bob)).json()
    assert refused["error"]["code"] == -32001


async def test_the_agent_card_offers_openid_connect_beside_the_key(api_client, tokens):
    dep, _ = await _published(api_client)
    await _turn_on(api_client, dep)
    url = f"/v1/deployments/{dep}/a2a/.well-known/agent-card.json"
    card = (await api_client.get(url)).json()
    oidc = card["securitySchemes"]["oauth"]
    assert oidc["type"] == "openIdConnect"
    assert oidc["openIdConnectUrl"] == f"{ISSUER}/.well-known/openid-configuration"
    assert resource(dep, "a2a") in oidc["description"]
    assert card["security"] == [{"deploymentKey": []}, {"oauth": [DEFAULT_SCOPE]}]  # either one
    v1 = (await api_client.get(url, headers={"A2A-Version": "1.0"})).json()
    scheme = v1["securitySchemes"]["oauth"]["openIdConnectSecurityScheme"]
    assert scheme["openIdConnectUrl"] == oidc["openIdConnectUrl"]
    assert v1["securityRequirements"][1] == {"schemes": {"oauth": {"list": [DEFAULT_SCOPE]}}}
    server_card = (await api_client.get(f"/v1/deployments/{dep}/mcp/server-card")).json()
    assert "OAuth access token" in server_card["remotes"][0]["headers"][0]["description"]


def test_the_reference_sdk_reads_the_card_with_openid_connect():
    resolver = pytest.importorskip("a2a.client.card_resolver")
    from test_frameworks import GREETER

    from agentcanvas.api.v1.protocols import CardSignIn, agent_card
    from agentcanvas.ir.models import Workflow

    wf = Workflow.model_validate({"id": "wf_c", "name": "Greeter", **GREETER})
    sign_in = CardSignIn(issuer=ISSUER, scopes=[DEFAULT_SCOPE], audience="https://x.example/a2a")
    for version in ("1.0", None):
        card = resolver.parse_agent_card(agent_card(wf, "1.0.0", "https://x.example/a2a", version, sign_in))
        assert card.name == "Greeter"
        assert card.security_schemes["oauth"].open_id_connect_security_scheme.open_id_connect_url.startswith(ISSUER)


async def test_turning_sign_in_on_is_checked_audited_and_undone_by_unpublishing(app, api_client, tokens):
    dep, key = await _published(api_client)
    put = f"/v1/deployments/{dep}/oauth"
    for body, status, said in [
        ({"enabled": True, "issuer": "http://idp.example.com"}, 422, "https"),
        ({"enabled": True, "issuer": "https://idp.example.com/#x"}, 422, "not a usable address"),
        ({"enabled": True, "issuer": "https://unknown-idp.test"}, 502, "signing keys"),
        ({"enabled": True, "scopes": ["offline_access"]}, 422, "offline_access"),
        ({"enabled": True, "scopes": ['bad"scope']}, 422, ""),
    ]:
        r = await api_client.put(put, json=body)
        assert r.status_code == status and said in r.text, (body, r.text)
    assert (await api_client.get(put)).json()["enabled"] is False

    app.state.settings = app.state.settings.model_copy(update={"oidc_issuer": None})
    r = await api_client.put(put, json={"enabled": True})
    assert r.status_code == 422 and "AGENTCANVAS_OIDC_ISSUER" in r.text
    app.state.settings = app.state.settings.model_copy(update={"oidc_issuer": ISSUER, "env": "prod"})
    r = await api_client.put(put, json={"enabled": True})
    assert r.status_code == 409 and "AGENTCANVAS_PUBLIC_URL" in r.text
    app.state.settings = app.state.settings.model_copy(update={"public_url": "https://canvas.example.com"})
    on = await _turn_on(api_client, dep)
    assert on["mcp_resource"] == f"https://canvas.example.com/api/v1/deployments/{dep}/mcp"  # from the setting
    assert (await api_client.post(mcp_url(dep), json=CALL, headers=bearer(access_token(on["mcp_resource"])))).is_success
    # a token another server was given, replayed with that server's name as Host, is not for this one
    evil = access_token(f"https://evil.test/api/v1/deployments/{dep}/mcp")
    spoofed = {**bearer(evil), "host": "evil.test", "x-forwarded-prefix": "/api"}
    assert (await api_client.post(mcp_url(dep), json=CALL, headers=spoofed)).status_code == 401
    app.state.settings = app.state.settings.model_copy(update={"public_url": None})  # unset again afterwards
    assert (await api_client.post(mcp_url(dep), json=CALL, headers=bearer(key))).is_success  # keys still work
    lost = await api_client.post(mcp_url(dep), json=CALL, headers=bearer(access_token(on["mcp_resource"])))
    assert lost.status_code == 401 and "AGENTCANVAS_PUBLIC_URL" in lost.text
    app.state.settings = app.state.settings.model_copy(update={"env": "test"})

    async with app.state.db.sessionmaker() as s:
        rows = (await s.scalars(select(AuditLog).where(AuditLog.action == "put_deployment_oauth"))).all()
    assert [r.detail["enabled"] for r in rows] == [True]
    assert rows[0].detail["issuer"] == ISSUER and rows[0].detail["scopes"] == [DEFAULT_SCOPE]
    assert (await api_client.put(put, json={"enabled": False})).json()["enabled"] is False
    await _turn_on(api_client, dep)
    assert (await api_client.delete(f"/v1/deployments/{dep}")).status_code == 204
    assert (await api_client.get(put)).json()["enabled"] is False  # publishing again does not bring it back
    r = await api_client.put(put, json={"enabled": True})
    assert r.status_code == 409 and "unpublished" in r.text


async def test_the_issuer_is_found_from_its_discovery_document_and_key_rotation_is_followed(app, monkeypatch):
    """Without test keys, the issuer's metadata (RFC 8414 or OpenID Connect discovery) names its keys; a token signed
    with a key not seen yet loads them again."""
    published: list[dict] = [SIGNING_JWK]
    fetched: list[str] = []

    def idp(request: httpx.Request) -> httpx.Response:
        fetched.append(request.url.path)
        if request.url.path == "/.well-known/openid-configuration/realms/acme":
            return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": "https://idp.test/keys"})
        if request.url.path == "/keys":
            return httpx.Response(200, json={"keys": list(published)})
        return httpx.Response(404, json={"error": "not_found"})

    monkeypatch.setattr(deployment_oauth, "client_factory", lambda public_only: httpx.AsyncClient(
        transport=httpx.MockTransport(idp)))  # fmt: skip
    monkeypatch.setattr(deployment_oauth, "REFRESH_UNKNOWN_KID_S", 0.0)
    tokens = AccessTokens(app.state.settings)
    aud = "https://canvas.example.com/api/v1/deployments/dep_x/mcp"
    who = await tokens.verify(access_token(aud), issuer=ISSUER, audiences=[aud], scopes=[DEFAULT_SCOPE])
    assert who.user_id == f"oidc:{ISSUER}:alice" and fetched.count("/keys") == 1
    await tokens.verify(access_token(aud, sub="bob"), issuer=ISSUER, audiences=[aud], scopes=[])
    assert fetched.count("/keys") == 1  # cached

    new_key, new_jwk = _keypair("k2")
    published.append(new_jwk)
    rotated = access_token(aud, key=new_key, kid="k2")
    assert (await tokens.verify(rotated, issuer=ISSUER, audiences=[aud], scopes=[])).subject == "alice"
    assert fetched.count("/keys") == 2

    async def down(*_: Any) -> None:
        raise deployment_oauth.oauth.UpstreamError("idp.test answered 503")

    other = AccessTokens(app.state.settings)
    monkeypatch.setattr(other, "_fetch", down)
    with pytest.raises(deployment_oauth.InvalidToken, match="503"):
        await other.verify(access_token(aud), issuer=ISSUER, audiences=[aud], scopes=[])
    with pytest.raises(deployment_oauth.oauth.UpstreamError):
        await other.check(ISSUER)


def test_a_challenge_never_carries_a_quote_or_a_control_character():
    header = deployment_oauth.challenge("https://x.test/m", ["a"], "invalid_token", 'bad "token"\r\nInjected: 1')
    assert "\r" not in header and "\n" not in header
    assert json.loads('"' + challenge_params(header)["error_description"] + '"') == "bad tokenInjected: 1"


def test_the_web_server_passes_the_metadata_path_to_the_api_whole():
    from test_deploy_files import ROOT

    template = (ROOT / "infra" / "nginx.conf").read_text()
    block = template.split("location ^~ /.well-known/oauth-protected-resource/ {", 1)[1].split("\n  }", 1)[0]
    assert "proxy_pass ${API_UPSTREAM};" in block  # no URI: nginx keeps the whole path
    assert "proxy_set_header X-Forwarded-Prefix /api;" in block
    assert "proxy_set_header X-Forwarded-Proto $forwarded_proto;" in block


async def test_a_form_asked_of_a_signed_in_person_is_theirs_to_answer_even_with_a_new_token(app, api_client, tokens):
    """F-1's request state is bound to the caller: for OAuth, the person (tokens are refreshed while they answer)."""
    from test_mcp_input import FORMS, modern
    from test_protocols import _publish
    from test_versions import HIGH_CASE, new_triage

    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    dep, _ = await _publish(api_client, (await new_triage(api_client))["id"])
    await _turn_on(api_client, dep)
    alice = bearer(access_token(resource(dep)))
    body, headers = modern("tools/list", caps=FORMS)
    tool = (await api_client.post(mcp_url(dep), json=body, headers={**alice, **headers})).json()["result"]["tools"]
    call = {"name": tool[0]["name"], "arguments": {"ticket": "down"}}
    body, headers = modern("tools/call", call, FORMS)
    asked = (await api_client.post(mcp_url(dep), json=body, headers={**alice, **headers})).json()["result"]
    [iid] = asked["inputRequests"]
    answer = {
        **call,
        "requestState": asked["requestState"],
        "inputResponses": {iid: {"action": "accept", "content": {"approve": True}}},
    }
    body, headers = modern("tools/call", answer, FORMS, id_=2)
    bob = bearer(access_token(resource(dep), sub="bob"))
    err = (await api_client.post(mcp_url(dep), json=body, headers={**bob, **headers})).json()["error"]
    assert "another caller" in err["message"]
    refreshed = bearer(access_token(resource(dep), jti="second"))  # alice again, with a new token
    done = (await api_client.post(mcp_url(dep), json=body, headers={**refreshed, **headers})).json()["result"]
    assert done["structuredContent"]["status"] == "success"
