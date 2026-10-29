"""OIDC sign-in (M7-T1): bearer JWTs checked against the issuer's keys; dev mode keeps the seeded admin."""

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from agentcanvas.security.auth import Authenticator, check_auth_settings
from agentcanvas.settings import Settings

ISSUER, AUDIENCE = "https://idp.test/realms/acme", "agentcanvas-api"


def _keypair(kid: str):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid=kid, use="sig", alg="RS256")
    return key, jwk


SIGNING_KEY, SIGNING_JWK = _keypair("k1")
OTHER_KEY, _ = _keypair("k1")  # same kid, different key: a forged token


def token(sub="alice-sub", email="alice@example.com", key=SIGNING_KEY, **over) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": AUDIENCE, "sub": sub, "email": email, "email_verified": True,
        "name": "Alice", "iat": now, "exp": now + 300,
    }  # fmt: skip
    claims.update(over)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "auth_mode": "oidc", "oidc_issuer": ISSUER, "oidc_audience": AUDIENCE, "oidc_client_id": "spa",
            "bootstrap_admin_emails": ["alice@example.com"],
        }
    )  # fmt: skip


@pytest.fixture
async def client(app):
    app.state.authenticator = Authenticator(app.state.settings, jwks={"keys": [SIGNING_JWK]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def bearer(t: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {t}"}


async def test_missing_token_is_401_problem_json(client):
    r = await client.get("/v1/projects")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    assert "Bearer" in r.json()["detail"]


@pytest.mark.parametrize(
    "bad",
    [
        token(exp=int(time.time()) - 120),  # expired beyond the leeway
        token(aud="someone-else"),
        token(iss="https://evil.test"),
        token(key=OTHER_KEY),  # bad signature
        "not-a-jwt",
    ],
)
async def test_invalid_tokens_are_401(client, bad):
    r = await client.get("/v1/projects", headers=bearer(bad))
    assert r.status_code == 401, r.text


async def test_first_sign_in_provisions_the_user_and_bootstraps_an_admin(client):
    r = await client.get("/v1/me", headers=bearer(token()))
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["email"] == "alice@example.com" and me["role"] == "admin" and me["org_id"] == "org_default"
    again = (await client.get("/v1/me", headers=bearer(token()))).json()
    assert again["id"] == me["id"]  # same user on the next request
    assert (await client.get("/v1/projects", headers=bearer(token()))).status_code == 200


async def test_user_without_membership_is_403(client):
    r = await client.get("/v1/me", headers=bearer(token(sub="bob-sub", email="bob@example.com")))
    assert r.status_code == 403
    assert "not a member" in r.json()["detail"]


async def test_unverified_bootstrap_email_is_not_trusted(client):
    r = await client.get("/v1/me", headers=bearer(token(sub="mallory", email_verified=False)))
    assert r.status_code == 403


async def test_org_header_must_name_a_membership(client):
    r = await client.get("/v1/me", headers={**bearer(token()), "X-Org-Id": "org_other"})
    assert r.status_code == 403


async def test_two_clients_in_different_organisations_do_not_write_on_every_request(app, client):
    """F-4: a request naming another organisation than the default made it the default, a write and a commit on
    every request of two clients (or tabs) that alternate."""
    from test_efficiency import statements

    from agentcanvas.db.models import Membership, Organization
    from agentcanvas.db.repositories import UserRepo

    alice = bearer(token())
    assert (await client.get("/v1/me", headers=alice)).status_code == 200
    async with app.state.db.sessionmaker() as s:
        alice_id = (await UserRepo(s).by_subject("oidc:alice-sub")).id  # type: ignore[union-attr]
        s.add(Organization(id="org_b", name="Org B"))
        await s.flush()
        s.add(Membership(org_id="org_b", user_id=alice_id, role="admin"))
        await s.commit()
    default = (await client.get("/v1/me", headers=alice)).json()["org_id"]
    other = "org_b" if default == "org_default" else "org_default"
    assert (await client.get("/v1/me", headers={**alice, "X-Org-Id": other})).json()["org_id"] == other
    assert (await client.get("/v1/me", headers=alice)).json()["org_id"] == other  # a switch is remembered
    with statements(app) as seen:
        for _ in range(5):
            for org in (default, other):
                r = await client.get("/v1/me", headers={**alice, "X-Org-Id": org})
                assert r.json()["org_id"] == org
    writes = [x for x in seen if x.startswith("UPDATE membership")]
    assert len(writes) == 1, writes  # the first switch back; then at most once a minute per organisation


async def test_public_routes_need_no_sign_in(client):
    cfg = (await client.get("/v1/auth/config")).json()
    assert cfg == {"mode": "oidc", "issuer": ISSUER, "client_id": "spa",
                   "scopes": "openid profile email offline_access", "audience": AUDIENCE,
                   "portal_url": None, "tenant_param": None}  # fmt: skip
    # deployment and webhook endpoints keep their own credentials: no sign-in, but still refused without them
    assert (await client.post("/v1/deployments/dep_x/runs/wait", json={})).status_code in (401, 404)
    assert (await client.post("/v1/hooks/trg_x", content=b"{}")).status_code in (401, 404)
    assert (await client.get("/healthz")).status_code == 200


def test_dev_mode_is_refused_in_production():
    with pytest.raises(RuntimeError, match="AUTH_MODE"):
        check_auth_settings(Settings(env="prod", auth_mode="dev"))
    with pytest.raises(RuntimeError, match="OIDC_ISSUER"):
        check_auth_settings(Settings(env="prod", auth_mode="oidc"))
    oidc = {"auth_mode": "oidc", "oidc_issuer": ISSUER, "oidc_audience": AUDIENCE}
    with pytest.raises(RuntimeError, match="SECRET_KEY"):  # S11: the development key never reaches production
        check_auth_settings(Settings(env="prod", **oidc))
    strong = "k" * 64
    with pytest.raises(RuntimeError, match="SCRIPT_EXECUTION=inprocess"):  # S-8: code as the API's own user
        check_auth_settings(Settings(env="prod", secret_key=strong, **oidc))
    with pytest.raises(RuntimeError, match="SCRIPT_EXECUTION=subprocess"):
        check_auth_settings(Settings(env="staging", secret_key=strong, script_execution="subprocess", **oidc))
    check_auth_settings(Settings(env="prod", secret_key=strong, script_execution="remote", sandbox_url="http://s",
                                 sandbox_token="t" * 32, **oidc))  # fmt: skip
    check_auth_settings(Settings(env="prod", secret_key=strong, allow_unisolated_scripts=True, **oidc))
    with pytest.raises(RuntimeError, match="SCRIPT_EXECUTION"):  # S2: hosted runs scripts in the sandbox only
        check_auth_settings(Settings(edition="hosted", secret_key=strong, **oidc))
    check_auth_settings(Settings(edition="hosted", secret_key=strong, script_execution="docker", **oidc))
    check_auth_settings(Settings(env="dev"))


async def test_email_trust_override_only_covers_a_missing_claim(settings):
    """`oidc_trust_email` accepts an email the provider sends without `email_verified`, never an explicit false."""
    trusting = Authenticator(settings.model_copy(update={"oidc_trust_email": True}), jwks={"keys": [SIGNING_JWK]})
    strict = Authenticator(settings, jwks={"keys": [SIGNING_JWK]})
    assert (await trusting.verify(token(email_verified=None))).email_verified is True
    assert (await trusting.verify(token(email_verified=False))).email_verified is False
    assert (await trusting.verify(token(email_verified="true"))).email_verified is False
    assert (await strict.verify(token(email_verified=None))).email_verified is False


async def test_seeded_dev_user_has_no_rights_with_sign_in_on(client):
    """The dev admin is retired at startup: it is not a member, so it cannot count as the organisation's admin."""
    me = (await client.get("/v1/me", headers=bearer(token()))).json()
    members = (await client.get("/v1/org/members", headers=bearer(token()))).json()
    assert [m["user_id"] for m in members] == [me["id"]]
    # a token whose subject is the dev user's is just another stranger
    assert (await client.get("/v1/me", headers=bearer(token(sub="dev", email="x@example.com")))).status_code == 403


# routes reachable without signing in (S15); deployment and webhook routes check their own credentials
PUBLIC = {
    ("GET", "/v1/auth/config"), ("GET", "/healthz"), ("GET", "/readyz"),
    # S15: A2A clients read the card before they authenticate; the MCP GET only says "POST" (405)
    ("GET", "/v1/deployments/{dep_id}/a2a/.well-known/agent-card.json"),
    ("GET", "/v1/deployments/{dep_id}/a2a/.well-known/agent.json"),
    ("GET", "/v1/deployments/{dep_id}/mcp"),
    # S23: share links are public; the token in the path is the capability (an unknown one answers 404)
    ("GET", "/v1/share/{token}"),
    ("POST", "/v1/share/{token}/runs/stream"),
    ("POST", "/v1/share/{token}/threads/{thread_id}/resume"),
    ("POST", "/v1/share/{token}/runs/{run_id}/feedback"),
}  # fmt: skip
OWN_CREDENTIALS = (
    "/v1/deployments/{dep_id}/runs/", "/v1/deployments/{dep_id}/threads/", "/v1/hooks/",
    "/v1/deployments/{dep_id}/mcp", "/v1/deployments/{dep_id}/a2a",
)  # fmt: skip


async def test_every_other_route_needs_sign_in(app, client):
    """S15 route scan: every operation in the API answers 401 without a token, except the public allowlist."""
    import re

    open_routes = []
    for path, ops in app.openapi()["paths"].items():
        url = re.sub(r"\{[^}]+\}", "x", path)
        for method in ops:
            r = await client.request(method.upper(), url, json={})
            public = (method.upper(), path) in PUBLIC
            if public or path.startswith(OWN_CREDENTIALS):
                assert public or r.status_code == 401, (method, path, r.status_code)
            elif r.status_code != 401:
                open_routes.append((method, path, r.status_code))
    assert open_routes == []
    assert (await client.get("/openapi.json")).status_code == 200  # docs stay on outside production


async def test_api_docs_are_off_in_production(settings):
    from agentcanvas.api.main import create_app

    prod = create_app(
        settings.model_copy(update={"env": "prod", "secret_key": "k" * 64, "allow_unisolated_scripts": True})
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=prod), base_url="http://test") as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await c.get(path)).status_code == 404, path


async def test_cors_refuses_other_origins(client):
    """S21: a browser on another origin gets no CORS grant (allowed origins come from AGENTCANVAS_CORS_ORIGINS)."""
    pre = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}
    evil = await client.options("/v1/projects", headers={"Origin": "https://evil.test", **pre})
    assert "access-control-allow-origin" not in evil.headers
    ok = await client.options("/v1/projects", headers={"Origin": "http://localhost:5173", **pre})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_joining_another_organisation_needs_acceptance(app, client):
    """S11: an invitation to someone who already belongs elsewhere waits for them; the org used last is the default."""
    from agentcanvas.db.models import Membership, Organization
    from agentcanvas.db.repositories import UserRepo

    alice, bob = bearer(token()), bearer(token(sub="bob-sub", email="bob@example.com", name="Bob"))
    assert (await client.get("/v1/me", headers=alice)).json()["org_id"] == "org_default"
    assert (await client.get("/v1/me", headers=bob)).status_code == 403
    async with app.state.db.sessionmaker() as s:
        bob_id = (await UserRepo(s).by_subject("oidc:bob-sub")).id  # type: ignore[union-attr]
        s.add(Organization(id="org_b", name="Org B"))
        await s.flush()
        s.add(Membership(org_id="org_b", user_id=bob_id, role="admin"))
        await s.commit()

    invited = await client.put("/v1/org/members", headers=bob, json={"email": "alice@example.com", "role": "builder"})
    assert invited.status_code == 200 and invited.json()["accepted"] is False
    [row] = [m for m in (await client.get("/v1/org/members", headers=bob)).json() if m["email"] == "alice@example.com"]
    assert row["accepted"] is False and row["display_name"] is None  # no profile details before she joins
    me = (await client.get("/v1/me", headers=alice)).json()
    assert me["org_id"] == "org_default" and set(me["memberships"]) == {"org_default"}
    assert (await client.get("/v1/me", headers={**alice, "X-Org-Id": "org_b"})).status_code == 403
    assert (await client.get("/v1/me/invitations", headers=alice)).json() == [
        {"org_id": "org_b", "org_name": "Org B", "role": "builder"}
    ]

    assert (await client.post("/v1/me/invitations/org_b/accept", headers=alice)).status_code == 204
    me = (await client.get("/v1/me", headers=alice)).json()
    assert me["org_id"] == "org_b" and me["role"] == "builder"  # the organisation just joined is the default
    [row] = [m for m in (await client.get("/v1/org/members", headers=bob)).json() if m["email"] == "alice@example.com"]
    assert row["accepted"] is True and row["display_name"] == "Alice"
    await client.get("/v1/projects", headers={**alice, "X-Org-Id": "org_default"})  # switching back...
    assert (await client.get("/v1/me", headers=alice)).json()["org_id"] == "org_default"  # ...sticks

    await client.put("/v1/org/members", headers=alice, json={"email": "bob@example.com", "role": "viewer"})
    assert (await client.delete("/v1/me/invitations/org_default", headers=bob)).status_code == 204  # declined
    assert (await client.get("/v1/me/invitations", headers=bob)).json() == []
    assert set((await client.get("/v1/me", headers=bob)).json()["memberships"]) == {"org_b"}
    assert (await client.post("/v1/me/invitations/org_default/accept", headers=bob)).status_code == 404


async def test_unknown_key_ids_do_not_make_the_server_fetch_keys_for_every_request(settings):
    """Tokens are checked before anyone is signed in, so a stranger can send as many as they like. A key id the issuer
    does not list must not turn each of them into a fetch of the issuer's key set (PyJWKClient's refresh cooldown)."""
    import http.server
    import threading

    hits: list[str] = []
    body = json.dumps({"keys": [SIGNING_JWK]}).encode()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        auth = Authenticator(
            settings.model_copy(update={"oidc_jwks_url": f"http://127.0.0.1:{server.server_address[1]}/keys"})
        )
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "iat": now, "exp": now + 300}
        for i in range(60):
            forged = jwt.encode(claims, OTHER_KEY, algorithm="RS256", headers={"kid": f"random-{i}"})
            with pytest.raises(Exception, match="signing key"):
                await auth.verify(forged)
        assert len(hits) <= 2, f"{len(hits)} fetches of the issuer's keys for 60 tokens with unknown key ids"
        assert (await auth.verify(token())).subject == "alice-sub"  # the real key still works
    finally:
        server.shutdown()


def _b64(obj) -> str:
    import base64

    raw = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _forged_tokens() -> dict[str, str]:
    import hashlib
    import hmac

    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    now = int(time.time())
    good = {"iss": ISSUER, "aud": AUDIENCE, "sub": "mallory", "email": "alice@example.com", "email_verified": True,
            "iat": now, "exp": now + 300}  # fmt: skip
    header = {"kid": "k1"}

    def rs256(claims: dict, key=SIGNING_KEY) -> str:
        return jwt.encode(claims, key, "RS256", header)

    def without(name: str) -> dict:
        return {k: v for k, v in good.items() if k != name}

    # the classic algorithm confusion: HS256, "signed" with the issuer's public key as the secret (PyJWT will not
    # create one, so it is made by hand)
    public_pem = SIGNING_KEY.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    signing_input = f"{_b64({'alg': 'HS256', 'typ': 'JWT', 'kid': 'k1'})}.{_b64(good)}".encode()
    mac = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    return {
        "alg none": f"{_b64({'alg': 'none', 'typ': 'JWT', 'kid': 'k1'})}.{_b64(good)}.",
        "HS256 with the public key": f"{signing_input.decode()}.{_b64(mac)}",
        "no expiry": rs256(without("exp")),
        "not yet valid": rs256({**good, "nbf": now + 3600}),
        "issued in the future": rs256({**good, "iat": now + 3600}),
        "no audience": rs256(without("aud")),
        "audience list without ours": rs256({**good, "aud": ["a", "b"]}),
        "no subject": rs256(without("sub")),
        "empty subject": rs256({**good, "sub": ""}),
        "blank subject": rs256({**good, "sub": "  "}),
        "huge token": rs256({**good, "pad": "A" * 200_000}, OTHER_KEY),
        "garbage header": "!!!.e30.sig",
        "five parts": "a.b.c.d.e",
        "empty parts": "..",
    }


@pytest.mark.parametrize("what", list(_forged_tokens()))
async def test_forged_and_malformed_tokens_are_401(client, what):
    r = await client.get("/v1/projects", headers=bearer(_forged_tokens()[what]))
    assert r.status_code == 401, (what, r.status_code, r.text[:200])
