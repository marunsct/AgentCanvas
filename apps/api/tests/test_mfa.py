"""E-8: required multi-factor sign-in, for the whole server (`acr`/`amr` it must show) or for one organisation; a
token that lacks it gets a 401 asking for a stronger sign-in (RFC 9470), never a lock-out of the admin saving it."""

from __future__ import annotations

import httpx
import pytest
from test_auth import SIGNING_JWK, bearer, settings, token  # noqa: F401 - `settings` turns OIDC sign-in on

from agentcanvas.db.models import AppUser, Membership, Organization
from agentcanvas.security.auth import Authenticator
from agentcanvas.services import mfa

ALICE_MFA = token(amr=["pwd", "mfa"])
ALICE_PWD = token(amr=["pwd"])


@pytest.fixture
async def client(app):
    app.state.authenticator = Authenticator(app.state.settings, jwks={"keys": [SIGNING_JWK]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def test_a_requirement_is_met_by_either_acr_or_amr():
    assert mfa.meets({}, [], [])  # nothing required
    assert mfa.meets({"acr": "gold"}, ["gold", "platinum"], ["mfa"])
    assert mfa.meets({"acr": "1", "amr": ["pwd", "hwk"]}, ["2"], ["mfa", "hwk"])
    assert mfa.meets({"amr": "mfa"}, [], ["mfa"])  # a provider that sends one string
    assert not mfa.meets({"acr": "1", "amr": ["pwd"]}, ["2"], ["mfa"])
    assert not mfa.meets({}, [], ["mfa"])


def _refused_for_mfa(r, acr: str | None = None) -> None:
    assert r.status_code == 401, r.text
    body = r.json()
    assert body["type"] == "https://agentcanvas.dev/errors/mfa-required" and body["code"] == "mfa_required"
    assert body["acr_values"] == acr and "challenge" not in body
    challenge = r.headers["www-authenticate"]
    assert challenge.startswith('Bearer error="insufficient_user_authentication"')
    assert (f'acr_values="{acr}"' in challenge) == bool(acr)


async def test_the_server_can_require_a_level_for_every_sign_in(app, client):
    app.state.settings = app.state.settings.model_copy(update={"oidc_required_acr": ["urn:mfa", "gold"]})
    _refused_for_mfa(await client.get("/v1/me", headers=bearer(token(acr="1"))), "urn:mfa gold")
    _refused_for_mfa(await client.get("/v1/me", headers=bearer(ALICE_PWD)), "urn:mfa gold")
    assert (await client.get("/v1/me", headers=bearer(token(acr="gold")))).status_code == 200
    app.state.settings = app.state.settings.model_copy(update={"oidc_required_acr": [], "oidc_required_amr": ["mfa"]})
    _refused_for_mfa(await client.get("/v1/me", headers=bearer(ALICE_PWD)))
    assert (await client.get("/v1/me", headers=bearer(ALICE_MFA))).status_code == 200


async def test_an_organisation_requires_mfa_of_its_members_only(app, client):
    assert (await client.get("/v1/me", headers=bearer(ALICE_PWD))).status_code == 200  # Alice: bootstrap admin
    # turning it on needs a sign-in that is multi-factor itself, so the admin cannot lock the organisation out
    r = await client.put("/v1/org/settings", json={"security": {"require_mfa": True}}, headers=bearer(ALICE_PWD))
    assert r.status_code == 422 and "second factor" in r.json()["detail"]
    r = await client.put("/v1/org/settings", json={"security": {"require_mfa": True}}, headers=bearer(ALICE_MFA))
    assert r.status_code == 200, r.text
    assert r.json()["security"]["require_mfa"] is True and r.json()["security"]["your_sign_in_is_mfa"] is True
    _refused_for_mfa(await client.get("/v1/projects", headers=bearer(ALICE_PWD)))
    assert (await client.get("/v1/projects", headers=bearer(ALICE_MFA))).status_code == 200

    # Bob belongs to another organisation too: there, a password is enough
    async with app.state.db.sessionmaker() as s:
        s.add(Organization(id="org_other", name="Other"))
        s.add(AppUser(id="usr_bob", oidc_subject="oidc:bob-sub", email="bob@example.com"))
        await s.flush()
        s.add(Membership(org_id="org_default", user_id="usr_bob", role="viewer"))
        s.add(Membership(org_id="org_other", user_id="usr_bob", role="admin"))
        await s.commit()
    bob = bearer(token(sub="bob-sub", email="bob@example.com", amr=["pwd"]))
    other = await client.get("/v1/projects", headers={**bob, "X-Org-Id": "org_other"})
    assert other.status_code == 200, other.text
    _refused_for_mfa(await client.get("/v1/projects", headers={**bob, "X-Org-Id": "org_default"}))

    # an API token is not a sign-in: it cannot switch the requirement, and is not asked for a second factor
    made = await client.post("/v1/me/tokens", json={"name": "ci", "role": "admin"}, headers=bearer(ALICE_MFA))
    tok = bearer(made.json()["token"])
    assert (await client.get("/v1/projects", headers=tok)).status_code == 200
    r = await client.put("/v1/org/settings", json={"security": {"require_mfa": False}}, headers=tok)
    assert r.status_code == 403
    r = await client.put("/v1/org/settings", json={"security": {"require_mfa": False}}, headers=bearer(ALICE_MFA))
    assert r.status_code == 200 and r.json()["security"]["require_mfa"] is False
    assert (await client.get("/v1/projects", headers=bearer(ALICE_PWD))).status_code == 200


async def test_what_counts_as_multi_factor_is_the_operators_choice(app, client):
    app.state.settings = app.state.settings.model_copy(update={"oidc_mfa_acr": ["2"], "oidc_mfa_amr": []})
    loa2 = bearer(token(acr="2"))
    assert (
        await client.put("/v1/org/settings", json={"security": {"require_mfa": True}}, headers=loa2)
    ).status_code == 200
    _refused_for_mfa(await client.get("/v1/projects", headers=bearer(ALICE_MFA)), "2")  # `amr` no longer counts
    assert (await client.get("/v1/projects", headers=loa2)).status_code == 200


async def test_without_an_identity_provider_mfa_cannot_be_required(app, client):
    app.state.settings = app.state.settings.model_copy(update={"auth_mode": "dev"})
    r = await client.put("/v1/org/settings", json={"security": {"require_mfa": True}})
    assert r.status_code == 409 and "identity provider" in r.json()["detail"]
