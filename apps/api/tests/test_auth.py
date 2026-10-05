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


async def test_public_routes_need_no_sign_in(client):
    cfg = (await client.get("/v1/auth/config")).json()
    assert cfg == {"mode": "oidc", "issuer": ISSUER, "client_id": "spa", "scopes": "openid profile email",
                   "audience": AUDIENCE}  # fmt: skip
    # deployment and webhook endpoints keep their own credentials: no sign-in, but still refused without them
    assert (await client.post("/v1/deployments/dep_x/runs/wait", json={})).status_code in (401, 404)
    assert (await client.post("/v1/hooks/trg_x", content=b"{}")).status_code in (401, 404)
    assert (await client.get("/healthz")).status_code == 200


def test_dev_mode_is_refused_in_production():
    with pytest.raises(RuntimeError, match="AUTH_MODE"):
        check_auth_settings(Settings(env="prod", auth_mode="dev"))
    with pytest.raises(RuntimeError, match="OIDC_ISSUER"):
        check_auth_settings(Settings(env="prod", auth_mode="oidc"))
    check_auth_settings(Settings(env="prod", auth_mode="oidc", oidc_issuer=ISSUER, oidc_audience=AUDIENCE))
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
