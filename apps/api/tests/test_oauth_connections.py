"""Per-user OAuth connections to app connectors (G-7), against a fake authorization server and a fake MCP server:
discovery (RFC 9728 / 8414), Client ID Metadata Documents and dynamic registration, authorization code with PKCE,
encrypted tokens, refresh with rotation, and the connection of whoever a run acts for."""

import asyncio
import time
from datetime import UTC, datetime, timedelta

import httpx
import httpx2
import pytest
from fake_oauth import AS, MCP, FakeWorld
from sqlalchemy import select
from test_auth import SIGNING_JWK, bearer, settings, token  # noqa: F401 - `settings` turns OIDC sign-in on

from agentcanvas.db.models import OAuthConnection, OAuthFlow
from agentcanvas.errors import InvalidInput, UpstreamError
from agentcanvas.runtime.identity import CURRENT, RunIdentity
from agentcanvas.security.auth import Authenticator
from agentcanvas.services import oauth
from agentcanvas.services.oauth import ConnectionAuth, ConnectionRequired

LONG = int(time.time()) + 86400  # the whole suite may outlast a token's five minutes
ALICE = bearer(token(exp=LONG))  # bootstrap admin of org_default
BOB = bearer(token(sub="bob-sub", email="bob@example.com", name="Bob", exp=LONG))
ORG = "org_default"


@pytest.fixture
def world(monkeypatch):
    w = FakeWorld()
    monkeypatch.setattr(oauth, "client_factory", w.client)
    return w


@pytest.fixture
async def client(app, world):
    app.state.authenticator = Authenticator(app.state.settings, jwks={"keys": [SIGNING_JWK]})
    app.state.settings = app.state.settings.model_copy(update={"public_url": "https://canvas.test"})
    app.state.oauth.public_url = "https://canvas.test"  # where people reach the web app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/v1/me", headers=ALICE)).status_code == 200
        r = await c.put("/v1/org/members", json={"email": "bob@example.com", "role": "builder"}, headers=ALICE)
        assert r.status_code == 200
        assert (await c.get("/v1/me", headers=BOB)).status_code == 200
        yield c


async def authorize(client, headers=ALICE, **body) -> httpx.Response:
    return await client.post("/v1/connections/authorize", json={"server_url": MCP, **body}, headers=headers)


async def connect(client, world, headers=ALICE, who="alice", **body) -> dict:
    r = await authorize(client, headers, **body)
    assert r.status_code == 200, r.text
    back = world.authorize(r.json()["authorization_url"], who)
    done = await client.post("/v1/connections/callback", json=back, headers=headers)
    assert done.status_code == 200, done.text
    return done.json()


async def row_of(app, **where) -> OAuthConnection:
    async with app.state.db.sessionmaker() as s:
        q = select(OAuthConnection)
        for k, v in where.items():
            q = q.where(getattr(OAuthConnection, k) == v)
        return (await s.scalars(q)).one()


async def user_id(app, sub: str) -> str:
    from agentcanvas.db.repositories import UserRepo

    async with app.state.db.sessionmaker() as s:
        u = await UserRepo(s).by_subject(f"oidc:{sub}")
        assert u is not None
        return u.id


# --- the sign-in flow ---------------------------------------------------------------------------------------------


async def test_a_person_connects_with_pkce_and_the_tokens_are_stored_encrypted(app, client, world):
    r = await authorize(client, return_to="/workflows/wf_1")
    assert r.status_code == 200, r.text
    url = r.json()["authorization_url"]
    assert url.startswith(f"{AS}/authorize?") and "code_challenge_method=S256" in url and "scope=read" in url
    assert len(world.registrations) == 1  # no metadata documents here: registered dynamically, once
    assert world.registrations[0]["redirect_uris"] == ["https://canvas.test/oauth/callback"]
    assert world.registrations[0]["token_endpoint_auth_method"] == "none"
    back = world.authorize(url)
    done = await client.post("/v1/connections/callback", json=back, headers=ALICE)
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["return_to"] == "/workflows/wf_1"
    assert body["connection"] == {**body["connection"], "owner": "you", "status": "active", "resource": MCP,
                                  "host": "mcp.test", "scope": "read"}  # fmt: skip
    listed = (await client.get("/v1/connections", headers=ALICE)).json()
    assert [c["id"] for c in listed] == [body["connection"]["id"]]
    row = await row_of(app, id=body["connection"]["id"])
    access = next(iter(world.access))
    assert access.encode() not in row.access_token_enc and row.refresh_token_enc is not None  # not in clear
    async with app.state.db.sessionmaker() as s:
        fernet = await app.state.keyring.fernet(s, ORG)
    assert fernet.decrypt(row.access_token_enc).decode() == access
    assert row.owner == await user_id(app, "alice-sub") and row.client_id == "dcr_1"
    # a second person connecting reuses the organisation's registration
    await connect(client, world, BOB, who="bob")
    assert len(world.registrations) == 1
    # the secrets are in no audit row
    log = str((await client.get("/v1/org/audit", headers=ALICE)).json())
    assert access not in log and back["code"] not in log and back["state"] not in log


async def test_a_server_that_takes_client_id_metadata_documents_is_told_to_fetch_ours(client, world):
    world.cimd = True
    r = await authorize(client)
    assert r.status_code == 200, r.text
    url = r.json()["authorization_url"]
    assert "client_id=https%3A%2F%2Fcanvas.test%2Fapi%2Fv1%2Foauth%2Fclient-metadata.json" in url
    assert world.registrations == []  # no registration needed
    doc = (await client.get("/v1/oauth/client-metadata.json")).json()  # public: the server fetches it unauthenticated
    assert doc["client_id"] == "https://canvas.test/api/v1/oauth/client-metadata.json"
    assert doc["redirect_uris"] == ["https://canvas.test/oauth/callback"]
    assert doc["token_endpoint_auth_method"] == "none" and "authorization_code" in doc["grant_types"]
    back = world.authorize(url)
    assert (await client.post("/v1/connections/callback", json=back, headers=ALICE)).status_code == 200


async def test_the_metadata_document_needs_a_public_https_address(app, client):
    app.state.settings = app.state.settings.model_copy(update={"public_url": "http://localhost:5173"})
    assert (await client.get("/v1/oauth/client-metadata.json")).status_code == 404


async def test_without_documents_or_registration_the_server_is_not_supported(client, world):
    world.dcr = False
    r = await authorize(client)
    assert r.status_code == 422 and "neither client ID metadata documents nor dynamic client registration" in r.text


@pytest.mark.parametrize("what", ["state", "code"])
async def test_a_tampered_or_replayed_callback_is_refused(app, client, world, what):
    url = (await authorize(client)).json()["authorization_url"]
    back = world.authorize(url)
    bad = {**back, what: back[what] + "x"}
    r = await client.post("/v1/connections/callback", json=bad, headers=ALICE)
    assert r.status_code in (422, 502), r.text
    # the state is single-use: even the genuine pair no longer works after a failed attempt on that state
    if what == "code":
        again = await client.post("/v1/connections/callback", json=back, headers=ALICE)
        assert again.status_code == 422 and "unknown, expired" in again.json()["detail"]
    assert (await client.get("/v1/connections", headers=ALICE)).json() == []


async def test_a_state_works_once_and_only_for_the_person_who_started_it(client, world):
    url = (await authorize(client)).json()["authorization_url"]
    back = world.authorize(url)
    stolen = await client.post("/v1/connections/callback", json=back, headers=BOB)  # Bob replays Alice's link
    assert stolen.status_code == 422 and "started by someone else" in stolen.json()["detail"]
    assert (await client.get("/v1/connections", headers=BOB)).json() == []
    # the attempt used up the state: Alice's own return now fails too (start again)
    assert (await client.post("/v1/connections/callback", json=back, headers=ALICE)).status_code == 422
    fresh = await connect(client, world)
    assert (await client.post("/v1/connections/callback", json=back, headers=ALICE)).status_code == 422  # replay
    assert fresh["connection"]["status"] == "active"


async def test_an_expired_attempt_is_refused(app, client, world):
    url = (await authorize(client)).json()["authorization_url"]
    back = world.authorize(url)
    async with app.state.db.sessionmaker() as s:
        flow = (await s.scalars(select(OAuthFlow))).one()
        flow.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()
    r = await client.post("/v1/connections/callback", json=back, headers=ALICE)
    assert r.status_code == 422 and "expired" in r.json()["detail"]


async def test_the_issuer_in_the_response_must_be_the_one_we_started_with(client, world):
    world.iss_param = True
    url = (await authorize(client)).json()["authorization_url"]
    back = world.authorize(url)
    forged = {**back, "iss": "https://evil.test"}
    assert (await client.post("/v1/connections/callback", json=forged, headers=ALICE)).status_code == 422
    url = (await authorize(client)).json()["authorization_url"]
    back = world.authorize(url)
    del back["iss"]  # promised, not sent
    assert (await client.post("/v1/connections/callback", json=back, headers=ALICE)).status_code == 422
    assert (await connect(client, world))["connection"]["status"] == "active"


async def test_return_to_is_a_path_of_this_app_never_an_address(client, world):
    for evil in ("https://evil.test/", "//evil.test", "/\\evil.test", ""):
        done = await connect(client, world, return_to=evil)
        assert done["return_to"] == "/", evil


@pytest.mark.parametrize(
    "tweak, message",
    [
        ("prm_resource", "another resource"),
        ("issuer_in_metadata", "another issuer"),
        ("s256", "S256"),
        ("metadata_http", "https"),
    ],
)
async def test_untrustworthy_server_metadata_is_refused(client, world, tweak, message):
    if tweak == "prm_resource":
        world.prm_resource = "https://other.test/mcp"
    elif tweak == "issuer_in_metadata":
        world.issuer_in_metadata = "https://evil.test"
    elif tweak == "s256":
        world.s256 = False
    else:
        world.metadata_http = True
    r = await authorize(client)
    assert r.status_code in (422, 502) and message in r.json()["detail"], r.text
    assert (await client.get("/v1/connections", headers=ALICE)).json() == []


@pytest.mark.parametrize(
    "url", ["http://mcp.example.com/mcp", "ftp://mcp.test/mcp", "https://u:p@mcp.test/mcp", "nope"]
)
async def test_the_server_address_must_be_https(client, url):
    r = await authorize(client, server_url=url)
    assert r.status_code == 422, r.text


async def test_an_authorization_server_that_refuses_the_code_is_an_error_not_a_connection(client, world):
    url = (await authorize(client)).json()["authorization_url"]
    back = world.authorize(url)
    world.codes.clear()  # the server forgot the code
    r = await client.post("/v1/connections/callback", json=back, headers=ALICE)
    assert r.status_code == 502 and "invalid_grant" in r.json()["detail"]
    assert (await client.get("/v1/connections", headers=ALICE)).json() == []


# --- who may connect what -----------------------------------------------------------------------------------------


async def test_the_service_connection_is_for_admins(client, world):
    r = await authorize(client, BOB, service=True)
    assert r.status_code == 403
    done = await connect(client, world, ALICE, who="service-account", service=True)
    assert done["connection"]["owner"] == "service"
    seen = (await client.get("/v1/connections", headers=BOB)).json()  # everyone in the organisation sees it exists
    assert [c["owner"] for c in seen] == ["service"]
    assert (await client.delete(f"/v1/connections/{done['connection']['id']}", headers=BOB)).status_code == 404
    assert (await client.delete(f"/v1/connections/{done['connection']['id']}", headers=ALICE)).status_code == 204


async def test_connections_are_personal(app, client, world):
    mine = await connect(client, world, ALICE, who="alice")
    bobs = await connect(client, world, BOB, who="bob")
    assert [c["id"] for c in (await client.get("/v1/connections", headers=BOB)).json()] == [bobs["connection"]["id"]]
    admin_sees = {c["id"]: c["owner"] for c in (await client.get("/v1/connections", headers=ALICE)).json()}
    assert admin_sees == {mine["connection"]["id"]: "you", bobs["connection"]["id"]: await user_id(app, "bob-sub")}
    assert (await client.delete(f"/v1/connections/{mine['connection']['id']}", headers=BOB)).status_code == 404
    assert (await client.delete("/v1/connections/oac_missing", headers=BOB)).status_code == 404
    assert (await client.delete(f"/v1/connections/{bobs['connection']['id']}", headers=BOB)).status_code == 204


async def test_a_reviewer_or_viewer_connects_and_disconnects_their_own_connector(app, client, world):
    for sub, role in (("rita-sub", "reviewer"), ("vic-sub", "viewer")):
        email = f"{sub}@example.com"
        r = await client.put("/v1/org/members", json={"email": email, "role": role}, headers=ALICE)
        assert r.status_code == 200
        who = bearer(token(sub=sub, email=email, name=sub, exp=LONG))
        assert (await authorize(client, who, service=True)).status_code == 403  # the service identity stays admin's
        done = await connect(client, world, who, who=sub)
        assert done["connection"]["owner"] == "you"
        assert (await client.delete(f"/v1/connections/{done['connection']['id']}", headers=who)).status_code == 204


async def test_a_connection_of_another_organisation_does_not_exist_here(app, client, world):
    from agentcanvas.db.models import Membership, Organization
    from agentcanvas.db.repositories import UserRepo

    mine = await connect(client, world, ALICE)
    async with app.state.db.sessionmaker() as s:
        s.add(Organization(id="org_b", name="B"))
        await s.flush()
        alice = await UserRepo(s).by_subject("oidc:alice-sub")
        assert alice is not None
        s.add(Membership(org_id="org_b", user_id=alice.id, role="admin"))
        await s.commit()
    there = {**ALICE, "X-Org-Id": "org_b"}
    assert (await client.get("/v1/connections", headers=there)).json() == []
    assert (await client.delete(f"/v1/connections/{mine['connection']['id']}", headers=there)).status_code == 404
    elsewhere = await connect(client, world, there)  # connecting in org_b is a separate connection
    assert elsewhere["connection"]["id"] != mine["connection"]["id"]
    assert len((await client.get("/v1/connections", headers=ALICE)).json()) == 1


async def test_an_api_token_cannot_start_a_sign_in(client):
    made = (await client.post("/v1/me/tokens", json={"name": "ci"}, headers=ALICE)).json()["token"]
    assert (await authorize(client, bearer(made))).status_code == 403


async def test_disconnecting_revokes_at_the_server_and_deletes_the_tokens(app, client, world):
    done = await connect(client, world)
    access, refresh = next(iter(world.access)), next(iter(world.refresh))
    assert (await client.delete(f"/v1/connections/{done['connection']['id']}", headers=ALICE)).status_code == 204
    assert set(world.revoked) == {access, refresh}
    assert (await client.get("/v1/connections", headers=ALICE)).json() == []


# --- tokens at run time -------------------------------------------------------------------------------------------


def tokens_of(app) -> oauth.ConnectionTokens:
    return app.state.oauth_tokens


async def test_a_token_near_expiry_is_refreshed_once_even_for_concurrent_calls(app, client, world):
    await connect(client, world)
    uid = await user_id(app, "alice-sub")
    old = next(iter(world.access))
    async with app.state.db.sessionmaker() as s:
        row = (await s.scalars(select(OAuthConnection))).one()
        row.expires_at = datetime.now(UTC) + timedelta(seconds=5)  # inside the refresh margin
        await s.commit()
    got = await asyncio.gather(*(tokens_of(app).access_token(ORG, uid, MCP) for _ in range(5)))
    assert len(set(got)) == 1 and got[0] != old and world.refreshes == 1  # rotation: a second refresh would fail
    assert got[0] in world.access
    assert await tokens_of(app).access_token(ORG, uid, MCP) == got[0]  # fresh now: no further refresh
    assert world.refreshes == 1


async def test_a_token_the_server_rejects_is_replaced_once(app, client, world):
    await connect(client, world)
    uid = await user_id(app, "alice-sub")
    first = await tokens_of(app).access_token(ORG, uid, MCP)
    world.expire(first)  # revoked at the server before it expired
    second = await tokens_of(app).access_token(ORG, uid, MCP, rejected=first)
    assert second != first and second in world.access
    # a late second caller that also saw the first one rejected gets the new token without another refresh
    assert await tokens_of(app).access_token(ORG, uid, MCP, rejected=first) == second
    assert world.refreshes == 1


async def test_a_revoked_refresh_token_marks_the_connection_for_reconnecting(app, client, world):
    await connect(client, world)
    uid = await user_id(app, "alice-sub")
    world.refresh.clear()  # the person revoked access at the provider
    first = next(iter(world.access))
    with pytest.raises(ConnectionRequired, match="was revoked or expired"):
        await tokens_of(app).access_token(ORG, uid, MCP, rejected=first)
    assert (await client.get("/v1/connections", headers=ALICE)).json()[0]["status"] == "reconnect"
    with pytest.raises(ConnectionRequired, match="connect it again"):
        await tokens_of(app).access_token(ORG, uid, MCP)
    await connect(client, world)  # connecting again repairs it
    assert (await client.get("/v1/connections", headers=ALICE)).json()[0]["status"] == "active"
    assert await tokens_of(app).access_token(ORG, uid, MCP)


async def test_no_connection_means_a_clear_error(app, client):
    with pytest.raises(ConnectionRequired, match="Connect button"):
        await tokens_of(app).access_token(ORG, "usr_nobody", MCP)


async def get_as(app, identity: RunIdentity | None, mode: str = "starter") -> httpx2.Response:
    CURRENT.set(identity)
    auth = ConnectionAuth(tokens_of(app), MCP, mode)
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app.state.fake), auth=auth) as c:
        return await c.get(MCP)


async def test_a_call_carries_the_connection_of_whoever_the_run_acts_for(app, client, world):
    app.state.fake = world
    await connect(client, world, ALICE, who="alice")
    await connect(client, world, BOB, who="bob")
    await connect(client, world, ALICE, who="the-service", service=True)
    alice, bob = await user_id(app, "alice-sub"), await user_id(app, "bob-sub")
    assert (await get_as(app, RunIdentity(ORG, alice))).json()["who"] == "alice"
    assert (await get_as(app, RunIdentity(ORG, bob))).json()["who"] == "bob"
    assert (await get_as(app, RunIdentity(ORG, None), "service")).json()["who"] == "the-service"  # a schedule
    # a person's run uses it only when it is a deployment's (resumed from the inbox) or an admin's
    service_ok = RunIdentity(ORG, bob, service_ok=True)
    assert (await get_as(app, service_ok, "service")).json()["who"] == "the-service"
    with pytest.raises(ConnectionRequired, match="not by a draft run a person starts"):
        await get_as(app, RunIdentity(ORG, bob), "service")  # a builder's draft run cannot borrow the admin's
    # no person started it, and the connector is set to act as them: refused, with the way out
    with pytest.raises(ConnectionRequired, match="service connection"):
        await get_as(app, RunIdentity(ORG, None))
    with pytest.raises(ConnectionRequired):
        await get_as(app, None)
    # another organisation's run finds nothing of ours
    with pytest.raises(ConnectionRequired, match="Connect button"):
        await get_as(app, RunIdentity("org_b", alice))


async def test_a_server_that_refuses_a_fresh_token_gets_one_retry_with_a_renewed_one(app, client, world):
    app.state.fake = world
    await connect(client, world, ALICE, who="alice")
    alice = await user_id(app, "alice-sub")
    world.expire(next(iter(world.access)))  # the stored token stopped working at the server
    r = await get_as(app, RunIdentity(ORG, alice))
    assert r.status_code == 200 and r.json()["who"] == "alice" and world.refreshes == 1


# --- discovery on its own -----------------------------------------------------------------------------------------


async def test_discovery_reads_the_servers_metadata(world):
    meta = await oauth.discover(MCP)
    assert meta.issuer == AS and meta.scope == "read" and meta.registration_endpoint == f"{AS}/register"
    assert meta.token_endpoint == f"{AS}/token" and not meta.cimd
    assert oauth.canonical("HTTPS://MCP.Test:443/mcp?x=1#frag") == "https://mcp.test/mcp?x=1"
    assert oauth.canonical("https://mcp.test.:8443") == "https://mcp.test:8443/"
    assert oauth.canonical("http://localhost:80/m") == "http://localhost/m"


async def test_a_server_without_protected_resource_metadata_is_its_own_authorization_server(world):
    world.prm_missing = True
    with pytest.raises(UpstreamError):  # falls back to the origin: https://mcp.test has no metadata in the fake
        await oauth.discover(MCP)


def test_urls_are_checked():
    assert oauth.secure_url("http://localhost:8080/x", "x")
    assert oauth.secure_url("http://127.0.0.1/x", "x")
    for bad in ("http://mcp.example.com/x", "https://a@b.test/", "https://b.test/#f", "file:///etc/passwd"):
        with pytest.raises(InvalidInput):
            oauth.secure_url(bad, "x")
    verifier, challenge = oauth.pkce()
    assert len(verifier) >= 43 and challenge != verifier


async def test_an_admin_enters_the_client_credentials_of_a_provider_without_registration(app, client, world):
    """N-5: GitHub- and Slack-style providers take neither metadata documents nor dynamic registration."""
    world.dcr = False
    assert (await authorize(client)).status_code == 422
    put = await client.put(
        "/v1/connections/clients",
        json={"server_url": MCP, "client_id": "gh_client_1", "client_secret": "s3cret-value-xyz"}, headers=ALICE,
    )  # fmt: skip
    assert put.status_code == 200, put.text
    assert put.json() | {"created_at": None} == {"issuer": AS, "host": "as.test", "client_id": "gh_client_1",
                                                  "has_secret": True, "created_at": None}  # fmt: skip
    assert "s3cret-value-xyz" not in put.text
    listed = (await client.get("/v1/connections/clients", headers=ALICE)).json()
    assert listed["redirect_uri"] == "https://canvas.test/oauth/callback"  # what the admin registers at the provider
    assert [c["client_id"] for c in listed["clients"]] == ["gh_client_1"] and "s3cret" not in str(listed)
    done = await connect(client, world)  # now the sign-in works, with the entered client id and no registration
    assert done["connection"]["status"] == "active" and world.registrations == []
    row = await row_of(app, id=done["connection"]["id"])
    assert row.client_id == "gh_client_1"
    async with app.state.db.sessionmaker() as s:
        stored = await s.get(oauth.OAuthClient, (ORG, AS, "https://canvas.test/oauth/callback"))
        fernet = await app.state.keyring.fernet(s, ORG)
    assert stored is not None and stored.client_secret_enc is not None
    assert b"s3cret-value-xyz" not in stored.client_secret_enc
    assert fernet.decrypt(stored.client_secret_enc).decode() == "s3cret-value-xyz"
    assert "s3cret-value-xyz" not in str((await client.get("/v1/org/audit", headers=ALICE)).json())


async def test_entered_credentials_win_over_a_metadata_document_and_can_be_removed(client, world):
    world.cimd = True
    await client.put("/v1/connections/clients", json={"server_url": MCP, "client_id": "mine_1"}, headers=ALICE)
    url = (await authorize(client)).json()["authorization_url"]
    assert "client_id=mine_1" in url and "client-metadata.json" not in url
    gone = await client.delete("/v1/connections/clients", params={"issuer": AS}, headers=ALICE)
    assert gone.status_code == 204
    assert (await client.get("/v1/connections/clients", headers=ALICE)).json()["clients"] == []
    assert "client-metadata.json" in (await authorize(client)).json()["authorization_url"]  # back to the document
    again = await client.delete("/v1/connections/clients", params={"issuer": AS}, headers=ALICE)
    assert again.status_code == 404


async def test_only_an_admin_manages_client_credentials(client, world):
    body = {"server_url": MCP, "client_id": "x"}
    assert (await client.put("/v1/connections/clients", json=body, headers=BOB)).status_code == 403
    assert (await client.get("/v1/connections/clients", headers=BOB)).status_code == 403
    assert (await client.delete("/v1/connections/clients", params={"issuer": AS}, headers=BOB)).status_code == 403
    bad = await client.put(
        "/v1/connections/clients", json={**body, "server_url": "http://plain.test/mcp"}, headers=ALICE
    )
    assert bad.status_code == 422  # the server address must be https, as for any sign-in


async def test_without_a_public_address_the_server_starts_and_a_sign_in_says_what_to_set(app, client):
    """The production stack has no CORS origin and may have no public URL: that must not stop the API from starting."""
    app.state.oauth.public_url = ""
    r = await authorize(client)
    assert r.status_code == 422 and "AGENTCANVAS_PUBLIC_URL" in r.text
    put = await client.put("/v1/connections/clients", json={"server_url": MCP, "client_id": "x"}, headers=ALICE)
    assert put.status_code == 422 and "AGENTCANVAS_PUBLIC_URL" in put.text


async def test_the_api_starts_with_no_cors_origin_and_no_public_url(app):
    """The production compose files set neither; start-up read the first CORS origin and crashed on the empty list."""
    from agentcanvas.api.main import create_app

    bare = app.state.settings.model_copy(update={"cors_origins": [], "public_url": ""})
    application = create_app(bare)
    async with application.router.lifespan_context(application):
        assert application.state.oauth.public_url == ""
