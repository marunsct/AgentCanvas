"""OIDC groups claim -> roles (G-13): the identity provider's groups decide a person's role at sign-in, in the
organisations that set a table; forged tokens carry no groups; the last admin is never removed by a token."""

import time

import httpx
import pytest
from test_auth import OTHER_KEY, SIGNING_JWK, bearer, settings, token  # noqa: F401 - `settings` turns OIDC sign-in on

from agentcanvas.db.models import AppUser, Membership, Organization
from agentcanvas.security.auth import Authenticator
from agentcanvas.services.sso_groups import GroupMapping, groups_in, role_for

LONG = int(time.time()) + 86400  # the whole suite may outlast a token's five minutes
ALICE = bearer(token(exp=LONG))  # bootstrap admin of org_default


def tok(groups, sub="bob-sub", email="bob@example.com", claim="groups", **over) -> dict[str, str]:
    return bearer(token(sub=sub, email=email, **{claim: groups}, **over))


@pytest.fixture
async def client(app):
    app.state.authenticator = Authenticator(app.state.settings, jwks={"keys": [SIGNING_JWK]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/v1/me", headers=ALICE)).status_code == 200
        yield c


async def _map(client, roles=None, **extra) -> httpx.Response:
    body = {"sso_groups": {"roles": roles or {"eng": "builder", "ops": "admin", "readers": "viewer"}, **extra}}
    r = await client.put("/v1/org/settings", json=body, headers=ALICE)
    assert r.status_code == 200, r.text
    return r


def test_the_claim_is_read_from_lists_strings_and_nested_paths():
    assert groups_in({"groups": ["a", "b", 3, ""]}, "groups") == {"a", "b"}
    assert groups_in({"groups": "a, b  c"}, "groups") == {"a", "b", "c"}
    assert groups_in({"realm_access": {"roles": ["x"]}}, "realm_access.roles") == {"x"}
    assert groups_in({"https://idp/groups": ["x"]}, "https://idp/groups") == {"x"}
    assert groups_in({"groups": {"a": 1}}, "groups") == set() and groups_in({}, "groups") == set()
    m = GroupMapping(roles={"a": "viewer", "b": "admin", "c": "builder"})
    assert role_for(m, {"a", "c", "zzz"}) == "builder" and role_for(m, {"a", "b"}) == "admin"
    assert role_for(m, {"zzz"}) is None


async def test_without_a_table_groups_mean_nothing(client):
    r = await client.get("/v1/me", headers=tok(["ops"]))
    assert r.status_code == 403 and "not a member" in r.json()["detail"]


async def test_a_group_gives_a_role_and_the_strongest_wins(client):
    await _map(client)
    me = (await client.get("/v1/me", headers=tok(["eng"]))).json()
    assert me["org_id"] == "org_default" and me["role"] == "builder"  # joined without an invitation
    assert (await client.get("/v1/me", headers=tok(["eng", "readers"]))).json()["role"] == "builder"
    assert (await client.get("/v1/me", headers=tok(["readers", "ops", "eng"]))).json()["role"] == "admin"


async def test_the_role_follows_the_groups_at_every_sign_in(client):
    await _map(client)
    assert (await client.get("/v1/me", headers=tok(["ops"]))).json()["role"] == "admin"
    assert (await client.get("/v1/me", headers=tok(["readers"]))).json()["role"] == "viewer"  # demoted
    r = await client.post("/v1/projects", json={"name": "nope"}, headers=tok(["readers"]))
    assert r.status_code == 403
    log = (await client.get("/v1/org/audit", params={"action": "sso_role_sync"}, headers=ALICE)).json()
    assert [(e["detail"]["from"], e["detail"]["to"]) for e in log] == [("admin", "viewer"), (None, "admin")]
    assert log[0]["detail"]["groups"] == ["readers"] and log[0]["actor_email"] == "bob@example.com"
    await client.get("/v1/me", headers=tok(["readers"]))  # nothing changed: nothing logged
    again = (await client.get("/v1/org/audit", params={"action": "sso_role_sync"}, headers=ALICE)).json()
    assert len(again) == len(log)


async def test_unmatched_tokens_keep_the_role_or_remove_the_person(client):
    await _map(client)
    assert (await client.get("/v1/me", headers=tok(["eng"]))).json()["role"] == "builder"
    assert (await client.get("/v1/me", headers=tok(["someone-else"]))).json()["role"] == "builder"  # keep (default)
    assert (await client.get("/v1/me", headers=tok([]))).json()["role"] == "builder"
    await _map(client, unmatched="remove")
    r = await client.get("/v1/me", headers=tok(["someone-else"]))
    assert r.status_code == 403  # no longer a member of anything
    members = (await client.get("/v1/org/members", headers=ALICE)).json()
    assert "bob@example.com" not in {m["email"] for m in members}
    removed = (await client.get("/v1/org/audit", params={"action": "sso_member_removed"}, headers=ALICE)).json()
    assert removed[0]["detail"]["from"] == "builder"
    assert (await client.get("/v1/me", headers=tok(["eng"]))).json()["role"] == "builder"  # and back with the group


async def test_a_token_cannot_remove_or_demote_the_last_admin(client):
    await _map(client, {"readers": "viewer"}, unmatched="remove")
    assert (await client.get("/v1/me", headers=tok(["readers"], sub="alice-sub", email="alice@example.com"))).json()[
        "role"
    ] == "admin"
    assert (await client.get("/v1/me", headers=tok(["nobody"], sub="alice-sub", email="alice@example.com"))).json()[
        "role"
    ] == "admin"
    assert (await client.get("/v1/org/members", headers=ALICE)).status_code == 200


async def test_forged_and_wrong_tokens_grant_nothing(client):
    await _map(client)
    forged = bearer(token(sub="mallory", email="m@example.com", groups=["ops"], key=OTHER_KEY))
    assert (await client.get("/v1/me", headers=forged)).status_code == 401
    assert (await client.get("/v1/me", headers=tok(["ops"], sub="m2", aud="someone-else"))).status_code == 401
    members = (await client.get("/v1/org/members", headers=ALICE)).json()
    assert {m["email"] for m in members} == {"alice@example.com"}
    # a lookalike claim the table does not name, and groups that only look like a mapped one
    assert (await client.get("/v1/me", headers=tok(["ops"], claim="roles", sub="m3"))).status_code == 403
    assert (await client.get("/v1/me", headers=tok(["OPS", "ops ", "op", "ops2"], sub="m4"))).status_code == 403


async def test_another_claim_name_and_nested_paths(client):
    await _map(client, {"admins": "admin"}, claim="realm_access.roles")
    t = bearer(token(sub="kc", email="kc@example.com", realm_access={"roles": ["admins"]}))
    assert (await client.get("/v1/me", headers=t)).json()["role"] == "admin"
    assert (await client.get("/v1/me", headers=tok(["admins"], sub="kc2"))).status_code == 403  # `groups` is not read


async def test_a_table_applies_to_its_own_organisation_only(app, client):
    async with app.state.db.sessionmaker() as s:
        s.add(Organization(id="org_b", name="Org B"))
        await s.commit()
    # with more than one organisation, a table adds people only when the operator allows it (CP-2)
    app.state.settings = app.state.settings.model_copy(update={"sso_groups_add_members": True})
    await _map(client)  # org_default's table
    me = (await client.get("/v1/me", headers=tok(["ops"]))).json()
    assert me["memberships"] == {"org_default": "admin"}
    async with app.state.db.sessionmaker() as s:
        assert await s.get(Membership, ("org_b", me["id"])) is None


async def test_only_admins_set_the_table_and_it_is_validated(client):
    await _map(client)
    bob = tok(["eng"])
    assert (await client.get("/v1/me", headers=bob)).status_code == 200
    r = await client.put("/v1/org/settings", json={"sso_groups": {"roles": {"x": "admin"}}}, headers=bob)
    assert r.status_code == 403
    for bad in ({"roles": {"x": "superuser"}}, {"claim": "bad claim!", "roles": {}}, {"roles": {"": "viewer"}}):
        r = await client.put("/v1/org/settings", json={"sso_groups": bad}, headers=ALICE)
        assert r.status_code == 422, bad
    got = (await client.get("/v1/org/settings", headers=ALICE)).json()
    assert got["sso_groups"]["roles"]["ops"] == "admin" and got["sso_groups_editable"] is True
    cleared = await client.put("/v1/org/settings", json={"sso_groups": None}, headers=ALICE)
    assert cleared.json()["sso_groups"] is None
    assert (await client.get("/v1/me", headers=tok(["readers"], sub="late"))).status_code == 403
    audit = (await client.get("/v1/org/audit", params={"action": "put_org_settings"}, headers=ALICE)).json()
    assert audit[0]["detail"]["sso_groups"] is None and audit[1]["detail"]["sso_groups"]["roles"]["ops"] == "admin"


async def test_another_organisations_table_cannot_enrol_everyone(app, client):
    """CP-2: org_b's admin maps a claim every token carries (the issuer). That used to add every person who signed in
    to org_b, and its member list then showed everyone's name and address."""
    from test_auth import ISSUER

    async with app.state.db.sessionmaker() as s:
        s.add(Organization(id="org_b", name="Org B", sso_groups={"claim": "iss", "roles": {ISSUER: "viewer"}}))
        s.add(AppUser(id="usr_evil", oidc_subject="evil-sub", email="evil@evil.example"))
        await s.flush()
        s.add(Membership(org_id="org_b", user_id="usr_evil", role="admin", accepted=True))  # org_b's own admin
        await s.commit()
    me = (await client.get("/v1/me", headers=ALICE)).json()
    assert me["memberships"] == {"org_default": "admin"}
    newcomer = await client.get("/v1/me", headers=tok(["eng"], sub="newcomer", email="new@example.com"))
    assert newcomer.status_code == 403  # not enrolled anywhere by a table that is not their organisation's
    async with app.state.db.sessionmaker() as s:
        assert await s.get(Membership, ("org_b", me["id"])) is None
    # with the operator's permission, the same table does enrol
    app.state.settings = app.state.settings.model_copy(update={"sso_groups_add_members": True})
    later = (await client.get("/v1/me", headers=tok(["eng"], sub="later", email="later@example.com"))).json()
    assert later["memberships"] == {"org_b": "viewer"}
