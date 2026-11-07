"""SCIM 2.0 (G-13; RFC 7643 / 7644): the Users and Groups operations identity providers use, with an organisation's
own bearer token; resources map to members and roles; nothing of another organisation is reachable."""

import time

import httpx
import pytest
from test_auth import SIGNING_JWK, bearer, settings, token  # noqa: F401 - `settings` turns OIDC sign-in on

from agentcanvas.db.models import AppUser, Membership, Organization
from agentcanvas.security.auth import Authenticator
from agentcanvas.services.tokens import hash_token

LONG = int(time.time()) + 86400  # the whole suite may outlast a token's five minutes
ALICE = bearer(token(exp=LONG))  # bootstrap admin of org_default
USER, GROUP = "urn:ietf:params:scim:schemas:core:2.0:User", "urn:ietf:params:scim:schemas:core:2.0:Group"
PATCH = "urn:ietf:params:scim:api:messages:2.0:PatchOp"
ERROR = "urn:ietf:params:scim:api:messages:2.0:Error"
S = "/scim/v2"


@pytest.fixture
async def client(app):
    app.state.authenticator = Authenticator(app.state.settings, jwks={"keys": [SIGNING_JWK]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/v1/me", headers=ALICE)).status_code == 200
        yield c


@pytest.fixture
async def scim(client):
    """The organisation's SCIM token, and Alice's mapping of the groups `eng` and `ops` to roles."""
    r = await client.put(
        "/v1/org/settings",
        json={"sso_groups": {"roles": {"eng": "builder", "ops": "admin"}}},
        headers=ALICE,
    )
    assert r.status_code == 200, r.text
    r = await client.post("/v1/org/scim/token", headers=ALICE)
    assert r.status_code == 201, r.text
    client.scim = bearer(r.json()["token"])
    return client


def user(email, **extra) -> dict:
    return {"schemas": [USER], "userName": email, **extra}


def ops(*operations) -> dict:
    return {"schemas": [PATCH], "Operations": list(operations)}


async def post_user(c, email, **extra) -> dict:
    r = await c.post(f"{S}/Users", json=user(email, **extra), headers=c.scim)
    assert r.status_code == 201, r.text
    return r.json()


async def post_group(c, name, members=(), **extra) -> dict:
    body = {"schemas": [GROUP], "displayName": name, "members": [{"value": m} for m in members], **extra}
    r = await c.post(f"{S}/Groups", json=body, headers=c.scim)
    assert r.status_code == 201, r.text
    return r.json()


async def role_of(c, email) -> str | None:
    members = {m["email"]: m for m in (await c.get("/v1/org/members", headers=ALICE)).json()}
    return members[email]["role"] if email in members else None


def assert_error(r, status, scim_type=None):
    assert r.status_code == status, r.text
    assert r.headers["content-type"].startswith("application/scim+json")
    body = r.json()
    assert body["schemas"] == [ERROR] and body["status"] == str(status) and body["detail"]
    assert body.get("scimType") == scim_type


# --- discovery and authentication ---------------------------------------------------------------------------------


async def test_discovery_documents(scim):
    h = scim.scim
    cfg = await scim.get(f"{S}/ServiceProviderConfig", headers=h)
    assert cfg.status_code == 200 and cfg.headers["content-type"].startswith("application/scim+json")
    body = cfg.json()
    assert body["patch"]["supported"] is True and body["bulk"]["supported"] is False
    assert body["filter"]["supported"] is True and body["authenticationSchemes"][0]["type"] == "oauthbearertoken"
    types = (await scim.get(f"{S}/ResourceTypes", headers=h)).json()
    assert {r["name"]: r["endpoint"] for r in types["Resources"]} == {"User": "/Users", "Group": "/Groups"}
    schemas = (await scim.get(f"{S}/Schemas", headers=h)).json()
    assert {r["id"] for r in schemas["Resources"]} == {USER, GROUP}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer "},
        {"Authorization": "Basic eDp5"},
        {"Authorization": "Bearer scim_nope"},
        {"Authorization": "Bearer act_" + "x" * 43},
        {"Authorization": "Bearer nope"},
    ],
)
async def test_strangers_get_a_scim_401(scim, headers):
    for method, path in (("GET", "/Users"), ("POST", "/Users"), ("GET", "/Groups"), ("DELETE", "/Groups/x")):
        r = await scim.request(method, f"{S}{path}", headers=headers, json={} if method == "POST" else None)
        assert_error(r, 401)
        assert r.headers["www-authenticate"] == "Bearer"


async def test_other_credentials_are_not_scim_tokens(scim):
    assert_error(await scim.get(f"{S}/Users", headers=ALICE), 401)  # a person's sign-in
    made = (await scim.post("/v1/me/tokens", json={"name": "ci"}, headers=ALICE)).json()["token"]
    assert_error(await scim.get(f"{S}/Users", headers=bearer(made)), 401)  # a personal API token
    # and the SCIM token is no sign-in or API token
    assert (await scim.get("/v1/me", headers=scim.scim)).status_code == 401
    assert (await scim.get("/v1/projects", headers=scim.scim)).status_code == 401


async def test_token_is_shown_once_stored_hashed_rotated_and_revoked(app, scim):
    old = scim.scim
    status = (await scim.get("/v1/org/scim", headers=ALICE)).json()
    assert status["enabled"] is True and status["available"] is True and status["prefix"].startswith("scim_")
    assert "token" not in status
    async with app.state.db.sessionmaker() as s:
        org = await s.get(Organization, "org_default")
        assert org is not None and org.scim_token_hash == hash_token(old["Authorization"].split()[1])
        assert old["Authorization"].split()[1] not in str(org.__dict__)
    assert (await scim.get(f"{S}/Users", headers=old)).status_code == 200
    new = (await scim.post("/v1/org/scim/token", headers=ALICE)).json()["token"]
    assert_error(await scim.get(f"{S}/Users", headers=old), 401)  # replayed after rotation
    assert (await scim.get(f"{S}/Users", headers=bearer(new))).status_code == 200
    assert (await scim.delete("/v1/org/scim/token", headers=ALICE)).status_code == 204
    assert_error(await scim.get(f"{S}/Users", headers=bearer(new)), 401)
    assert (await scim.get("/v1/org/scim", headers=ALICE)).json()["enabled"] is False
    log = (await scim.get("/v1/org/audit", params={"action": "create_scim_token"}, headers=ALICE)).json()
    assert log and new not in str(log)


async def test_only_admins_manage_the_token(scim):
    await scim.put("/v1/org/members", json={"email": "bob@example.com", "role": "builder"}, headers=ALICE)
    bob = bearer(token(sub="bob-sub", email="bob@example.com"))
    assert (await scim.get("/v1/me", headers=bob)).status_code == 200
    for method, path in (("GET", "/v1/org/scim"), ("POST", "/v1/org/scim/token"), ("DELETE", "/v1/org/scim/token")):
        assert (await scim.request(method, path, headers=bob)).status_code == 403


async def test_a_token_reaches_only_its_own_organisation(app, scim):
    mine = await post_user(scim, "mine@example.com")
    group = await post_group(scim, "eng", [mine["id"]])
    async with app.state.db.sessionmaker() as s:
        s.add(Organization(id="org_b", name="B", scim_token_hash=hash_token("scim_b-token")))
        await s.commit()
    b = {"Authorization": "Bearer scim_b-token"}
    assert (await scim.get(f"{S}/Users", headers=b)).json()["totalResults"] == 0
    assert_error(await scim.get(f"{S}/Users/{mine['id']}", headers=b), 404)
    assert_error(await scim.delete(f"{S}/Users/{mine['id']}", headers=b), 404)
    assert_error(
        await scim.patch(
            f"{S}/Users/{mine['id']}", json=ops({"op": "replace", "path": "active", "value": False}), headers=b
        ),
        404,
    )
    assert_error(await scim.get(f"{S}/Groups/{group['id']}", headers=b), 404)
    assert_error(await scim.delete(f"{S}/Groups/{group['id']}", headers=b), 404)
    assert (await scim.get(f"{S}/Groups", headers=b)).json()["totalResults"] == 0
    # b's group cannot take in a's user, and the original group and user are untouched
    r = await scim.post(
        f"{S}/Groups", json={"schemas": [GROUP], "displayName": "x", "members": [{"value": mine["id"]}]}, headers=b
    )
    assert_error(r, 400, "invalidValue")
    assert (await scim.get(f"{S}/Users/{mine['id']}", headers=scim.scim)).json()["active"] is True
    assert len((await scim.get(f"{S}/Groups/{group['id']}", headers=scim.scim)).json()["members"]) == 1


# --- users --------------------------------------------------------------------------------------------------------


async def test_create_get_and_find_a_user(scim):
    r = await scim.post(
        f"{S}/Users",
        json=user("Ada.Lovelace@Example.com", externalId="okta-1", displayName="Ada Lovelace", active=True),
        headers=scim.scim,
    )
    assert r.status_code == 201 and r.headers["content-type"].startswith("application/scim+json")
    made = r.json()
    assert made["schemas"] == [USER] and made["userName"] == "ada.lovelace@example.com"
    assert made["externalId"] == "okta-1" and made["active"] is True and made["meta"]["resourceType"] == "User"
    assert made["emails"] == [{"value": "ada.lovelace@example.com", "primary": True, "type": "work"}]
    assert r.headers["location"].endswith(f"/scim/v2/Users/{made['id']}")
    assert (await scim.get(f"{S}/Users/{made['id']}", headers=scim.scim)).json() == made
    assert await role_of(scim, "ada.lovelace@example.com") == "viewer"  # the default until a group says more
    found = (
        await scim.get(f"{S}/Users", params={"filter": 'userName eq "ADA.lovelace@example.com"'}, headers=scim.scim)
    ).json()
    assert found["totalResults"] == 1 and found["Resources"][0]["id"] == made["id"]
    ext = (await scim.get(f"{S}/Users", params={"filter": 'externalId eq "okta-1"'}, headers=scim.scim)).json()
    assert ext["totalResults"] == 1
    none = (await scim.get(f"{S}/Users", params={"filter": 'userName eq "who@example.com"'}, headers=scim.scim)).json()
    assert (
        none["totalResults"] == 0
        and none["Resources"] == []
        and none["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:ListResponse"]
    )


async def test_user_errors(scim):
    h = scim.scim
    await post_user(scim, "dup@example.com")
    assert_error(await scim.post(f"{S}/Users", json=user("DUP@example.com"), headers=h), 409, "uniqueness")
    assert_error(await scim.post(f"{S}/Users", json=user("not-an-email"), headers=h), 400, "invalidValue")
    assert_error(await scim.post(f"{S}/Users", json={"schemas": [USER]}, headers=h), 400, "invalidValue")
    assert_error(await scim.post(f"{S}/Users", content=b"{nope", headers=h), 400, "invalidSyntax")
    assert_error(await scim.post(f"{S}/Users", json=["a"], headers=h), 400, "invalidSyntax")
    assert_error(await scim.get(f"{S}/Users/usr_missing", headers=h), 404)
    assert_error(await scim.get(f"{S}/Users", params={"filter": 'userName co "x"'}, headers=h), 400, "invalidFilter")
    assert_error(
        await scim.get(f"{S}/Users", params={"filter": 'name.familyName eq "x"'}, headers=h), 400, "invalidFilter"
    )
    assert_error(await scim.get(f"{S}/Users", params={"startIndex": "x"}, headers=h), 400, "invalidSyntax")
    bad = await scim.patch(f"{S}/Users/usr_x", json={"Operations": []}, headers=h)
    assert_error(bad, 400, "invalidSyntax")  # no PatchOp schema (checked before the user is looked up)


async def test_listing_pages_by_start_index_and_count(scim):
    for i in range(5):
        await post_user(scim, f"u{i}@example.com")
    first = (await scim.get(f"{S}/Users", params={"count": 2, "startIndex": 1}, headers=scim.scim)).json()
    second = (await scim.get(f"{S}/Users", params={"count": 2, "startIndex": 3}, headers=scim.scim)).json()
    assert first["totalResults"] == 6 and first["itemsPerPage"] == 2 and first["startIndex"] == 1  # + Alice
    assert second["startIndex"] == 3 and not {r["id"] for r in first["Resources"]} & {
        r["id"] for r in second["Resources"]
    }
    zero = (await scim.get(f"{S}/Users", params={"count": 0}, headers=scim.scim)).json()
    assert zero["totalResults"] == 6 and zero["Resources"] == [] and zero["itemsPerPage"] == 0
    capped = (await scim.get(f"{S}/Users", params={"count": 100000}, headers=scim.scim)).json()
    assert capped["itemsPerPage"] == 6


@pytest.mark.parametrize(
    "operation",
    [
        {"op": "replace", "path": "active", "value": False},  # RFC 7644 form
        {"op": "Replace", "path": "active", "value": "False"},  # Entra ID: capitalised op, a string
        {"op": "replace", "value": {"active": False}},  # Okta: no path, an object
        {"op": "replace", "path": "urn:ietf:params:scim:schemas:core:2.0:User:active", "value": False},
    ],
)
async def test_deactivating_suspends_and_reactivating_restores(scim, operation):
    h = scim.scim
    made = await post_user(scim, "bob@example.com")
    await scim.put("/v1/org/members", json={"email": "bob@example.com", "role": "builder"}, headers=ALICE)
    bob = bearer(token(sub="bob-sub", email="bob@example.com"))
    assert (await scim.get("/v1/me", headers=bob)).json()["role"] == "builder"
    pat = await scim.patch(f"{S}/Users/{made['id']}", json=ops(operation), headers=h)
    assert pat.status_code == 200 and pat.json()["active"] is False
    refused = await scim.get("/v1/me", headers=bob)  # nothing is deleted, but there is no access
    assert refused.status_code == 403
    assert (await scim.get("/v1/projects", headers=bob)).status_code == 403
    assert await role_of(scim, "bob@example.com") == "builder"
    member = next(
        m for m in (await scim.get("/v1/org/members", headers=ALICE)).json() if m["email"] == "bob@example.com"
    )
    assert member["deactivated"] is True
    back = await scim.patch(
        f"{S}/Users/{made['id']}", json=ops({"op": "replace", "path": "active", "value": True}), headers=h
    )
    assert back.json()["active"] is True
    assert (await scim.get("/v1/me", headers=bob)).json()["role"] == "builder"


async def test_a_suspended_person_loses_their_api_tokens_too(scim):
    made = await post_user(scim, "bob@example.com")
    await scim.put("/v1/org/members", json={"email": "bob@example.com", "role": "builder"}, headers=ALICE)
    bob = bearer(token(sub="bob-sub", email="bob@example.com"))
    pat = (await scim.post("/v1/me/tokens", json={"name": "ci"}, headers=bob)).json()["token"]
    assert (await scim.get("/v1/me", headers=bearer(pat))).status_code == 200
    await scim.patch(
        f"{S}/Users/{made['id']}", json=ops({"op": "replace", "path": "active", "value": False}), headers=scim.scim
    )
    assert (await scim.get("/v1/me", headers=bearer(pat))).status_code == 401


async def test_put_replaces_and_patch_sets_the_external_id(scim):
    made = await post_user(scim, "carol@example.com", externalId="a")
    r = await scim.put(
        f"{S}/Users/{made['id']}", json=user("carol@example.com", externalId="b", active=False), headers=scim.scim
    )
    assert r.status_code == 200 and r.json()["externalId"] == "b" and r.json()["active"] is False
    r = await scim.put(f"{S}/Users/{made['id']}", json=user("carol@example.com"), headers=scim.scim)
    assert r.json()["active"] is True and "externalId" not in r.json()  # replaced: what is absent is unset
    r = await scim.patch(
        f"{S}/Users/{made['id']}", json=ops({"op": "add", "path": "externalId", "value": "c"}), headers=scim.scim
    )
    assert r.json()["externalId"] == "c"


async def test_a_signed_in_persons_name_and_email_follow_their_token_not_the_directory(scim):
    made = await post_user(scim, "dave@example.com", displayName="Dave from the directory")
    assert made["displayName"] == "Dave from the directory"  # until they sign in
    await scim.put("/v1/org/members", json={"email": "dave@example.com", "role": "viewer"}, headers=ALICE)
    assert (
        await scim.get("/v1/me", headers=bearer(token(sub="dave-sub", email="dave@example.com", name="Dave")))
    ).status_code == 200
    r = await scim.patch(
        f"{S}/Users/{made['id']}",
        json=ops({"op": "replace", "value": {"displayName": "Mallory", "userName": "mallory@example.com"}}),
        headers=scim.scim,
    )
    assert r.json()["displayName"] == "Dave" and r.json()["userName"] == "dave@example.com"


async def test_delete_removes_the_membership_not_the_account(app, scim):
    made = await post_user(scim, "erin@example.com")
    async with app.state.db.sessionmaker() as s:  # Erin also belongs to org_b
        s.add(Organization(id="org_b", name="B"))
        await s.flush()
        s.add(Membership(org_id="org_b", user_id=made["id"], role="viewer"))
        await s.commit()
    g = await post_group(scim, "eng", [made["id"]])
    assert (await scim.delete(f"{S}/Users/{made['id']}", headers=scim.scim)).status_code == 204
    assert_error(await scim.get(f"{S}/Users/{made['id']}", headers=scim.scim), 404)
    assert_error(await scim.delete(f"{S}/Users/{made['id']}", headers=scim.scim), 404)  # replayed
    assert (await scim.get(f"{S}/Groups/{g['id']}", headers=scim.scim)).json()["members"] == []
    async with app.state.db.sessionmaker() as s:
        assert await s.get(AppUser, made["id"]) is not None
        assert await s.get(Membership, ("org_b", made["id"])) is not None
        assert await s.get(Membership, ("org_default", made["id"])) is None


async def test_the_last_admin_cannot_be_suspended_or_deleted_by_the_directory(scim):
    alice = (
        await scim.get(f"{S}/Users", params={"filter": 'userName eq "alice@example.com"'}, headers=scim.scim)
    ).json()["Resources"][0]
    r = await scim.patch(
        f"{S}/Users/{alice['id']}", json=ops({"op": "replace", "path": "active", "value": False}), headers=scim.scim
    )
    assert_error(r, 409, "uniqueness")
    assert_error(await scim.delete(f"{S}/Users/{alice['id']}", headers=scim.scim), 409, "uniqueness")
    assert (await scim.get("/v1/me", headers=ALICE)).json()["role"] == "admin"
    assert (await scim.get(f"{S}/Users/{alice['id']}", headers=scim.scim)).json()["active"] is True


async def test_a_person_in_another_organisation_must_accept_before_they_are_moved(app, scim):
    async with app.state.db.sessionmaker() as s:
        s.add(Organization(id="org_b", name="B"))
        await s.commit()
    dave = bearer(token(sub="dave-sub", email="dave@example.com"))
    async with app.state.db.sessionmaker() as s:
        from agentcanvas.db.repositories import UserRepo

        u, _ = await UserRepo(s).provision("dave-sub", "dave@example.com", "Dave", True)
        s.add(Membership(org_id="org_b", user_id=u.id, role="viewer"))
        await s.commit()
    made = await post_user(scim, "dave@example.com")
    me = (await scim.get("/v1/me", headers=dave)).json()
    assert me["memberships"] == {"org_b": "viewer"}  # not moved: the invitation waits for them
    assert (await scim.get("/v1/me/invitations", headers=dave)).json()[0]["org_id"] == "org_default"
    assert made["active"] is True


# --- groups -------------------------------------------------------------------------------------------------------


async def test_a_group_gives_its_members_the_role_of_its_name(scim):
    h = scim.scim
    ada = await post_user(scim, "ada@example.com")
    bo = await post_user(scim, "bo@example.com")
    g = await post_group(scim, "eng", [ada["id"]], externalId="g-1")
    assert g["schemas"] == [GROUP] and g["displayName"] == "eng" and g["externalId"] == "g-1"
    assert g["members"] == [{"value": ada["id"], "display": "ada@example.com", "type": "User"}]
    assert await role_of(scim, "ada@example.com") == "builder" and await role_of(scim, "bo@example.com") == "viewer"
    assert (await scim.get(f"{S}/Users/{ada['id']}", headers=h)).json()["groups"] == [
        {"value": g["id"], "display": "eng", "type": "direct"}
    ]
    ops_group = await post_group(scim, "ops", [ada["id"], bo["id"]])
    assert (
        await role_of(scim, "ada@example.com") == "admin" and await role_of(scim, "bo@example.com") == "admin"
    )  # strongest
    await post_group(scim, "unmapped", [bo["id"]])
    assert await role_of(scim, "bo@example.com") == "admin"
    assert (await scim.get(f"{S}/Groups/{g['id']}", headers=h)).json() == g
    found = (await scim.get(f"{S}/Groups", params={"filter": 'displayName eq "ops"'}, headers=h)).json()
    assert found["totalResults"] == 1 and found["Resources"][0]["id"] == ops_group["id"]
    slim = (await scim.get(f"{S}/Groups", params={"excludedAttributes": "members"}, headers=h)).json()
    assert slim["totalResults"] == 3 and all("members" not in r for r in slim["Resources"])


async def test_group_patch_operations_add_remove_replace_and_rename(scim):
    h = scim.scim
    ada = await post_user(scim, "ada@example.com")
    bo = await post_user(scim, "bo@example.com")
    g = await post_group(scim, "eng")
    url = f"{S}/Groups/{g['id']}"
    add = await scim.patch(
        url, json=ops({"op": "add", "path": "members", "value": [{"value": ada["id"]}, {"value": bo["id"]}]}), headers=h
    )
    assert {m["value"] for m in add.json()["members"]} == {ada["id"], bo["id"]}
    assert await role_of(scim, "ada@example.com") == await role_of(scim, "bo@example.com") == "builder"
    rm = await scim.patch(url, json=ops({"op": "remove", "path": f'members[value eq "{ada["id"]}"]'}), headers=h)
    assert [m["value"] for m in rm.json()["members"]] == [bo["id"]]
    assert await role_of(scim, "ada@example.com") == "viewer"  # out of the only group giving a role
    await scim.patch(url, json=ops({"op": "Add", "path": "members", "value": [{"value": ada["id"]}]}), headers=h)
    rm2 = await scim.patch(
        url, json=ops({"op": "remove", "path": "members", "value": [{"value": ada["id"]}]}), headers=h
    )  # Okta
    assert [m["value"] for m in rm2.json()["members"]] == [bo["id"]]
    rep = await scim.patch(
        url, json=ops({"op": "replace", "path": "members", "value": [{"value": ada["id"]}]}), headers=h
    )
    assert [m["value"] for m in rep.json()["members"]] == [ada["id"]]
    assert await role_of(scim, "bo@example.com") == "viewer" and await role_of(scim, "ada@example.com") == "builder"
    renamed = await scim.patch(url, json=ops({"op": "replace", "path": "displayName", "value": "ops"}), headers=h)
    assert (
        renamed.json()["displayName"] == "ops" and await role_of(scim, "ada@example.com") == "admin"
    )  # role of the new name
    nopath = await scim.patch(
        url, json=ops({"op": "replace", "value": {"displayName": "eng", "externalId": "x"}}), headers=h
    )
    assert nopath.json()["externalId"] == "x" and await role_of(scim, "ada@example.com") == "builder"
    cleared = await scim.patch(url, json=ops({"op": "remove", "path": "members"}), headers=h)
    assert cleared.json()["members"] == [] and await role_of(scim, "ada@example.com") == "viewer"


async def test_put_group_replaces_its_members_and_delete_takes_the_role_away(scim):
    h = scim.scim
    ada = await post_user(scim, "ada@example.com")
    bo = await post_user(scim, "bo@example.com")
    g = await post_group(scim, "eng", [ada["id"]])
    put = await scim.put(
        f"{S}/Groups/{g['id']}",
        json={"schemas": [GROUP], "displayName": "eng", "members": [{"value": bo["id"]}]},
        headers=h,
    )
    assert [m["value"] for m in put.json()["members"]] == [bo["id"]]
    assert await role_of(scim, "bo@example.com") == "builder" and await role_of(scim, "ada@example.com") == "viewer"
    assert (await scim.delete(f"{S}/Groups/{g['id']}", headers=h)).status_code == 204
    assert await role_of(scim, "bo@example.com") == "viewer"
    assert_error(await scim.get(f"{S}/Groups/{g['id']}", headers=h), 404)
    assert_error(await scim.delete(f"{S}/Groups/{g['id']}", headers=h), 404)


async def test_group_errors(scim):
    h = scim.scim
    ada = await post_user(scim, "ada@example.com")
    g = await post_group(scim, "eng")
    assert_error(
        await scim.post(f"{S}/Groups", json={"schemas": [GROUP], "displayName": "eng"}, headers=h), 409, "uniqueness"
    )
    assert_error(await scim.post(f"{S}/Groups", json={"schemas": [GROUP]}, headers=h), 400, "invalidValue")
    r = await scim.post(
        f"{S}/Groups", json={"schemas": [GROUP], "displayName": "x", "members": [{"value": "usr_nobody"}]}, headers=h
    )
    assert_error(r, 400, "invalidValue")
    r = await scim.post(f"{S}/Groups", json={"schemas": [GROUP], "displayName": "x", "members": "ada"}, headers=h)
    assert_error(r, 400, "invalidValue")
    assert_error(
        await scim.patch(f"{S}/Groups/{g['id']}", json=ops({"op": "replace", "path": "nope", "value": 1}), headers=h),
        400,
        "invalidPath",
    )
    assert_error(
        await scim.patch(
            f"{S}/Groups/{g['id']}",
            json=ops({"op": "add", "path": "members", "value": [{"value": "usr_nobody"}]}),
            headers=h,
        ),
        400,
        "invalidValue",
    )
    assert_error(await scim.patch(f"{S}/Groups/{g['id']}", json=ops({"op": "move"}), headers=h), 400, "invalidSyntax")
    await post_group(scim, "ops")
    assert_error(
        await scim.patch(
            f"{S}/Groups/{g['id']}", json=ops({"op": "replace", "path": "displayName", "value": "ops"}), headers=h
        ),
        409,
        "uniqueness",
    )
    assert_error(
        await scim.get(f"{S}/Groups", params={"filter": 'members.value eq "x"'}, headers=h), 400, "invalidFilter"
    )
    assert (await scim.get(f"{S}/Users/{ada['id']}", headers=h)).json()["groups"] == []


async def test_people_nobody_pushes_keep_their_role_and_the_last_admin_stays(scim):
    """Role changes follow group changes only for the people a request touches; the directory cannot demote the
    organisation's only admin who has signed in (a pending invitation does not count as one)."""
    h = scim.scim
    found = await scim.get(f"{S}/Users", params={"filter": 'userName eq "alice@example.com"'}, headers=h)
    alice = found.json()["Resources"][0]
    ada = await post_user(scim, "ada@example.com")
    await post_group(scim, "ops", [ada["id"]])  # Ada becomes admin, but has never signed in
    assert await role_of(scim, "alice@example.com") == "admin"  # in no group, not touched
    assert await role_of(scim, "ada@example.com") == "admin"
    g = await post_group(scim, "eng", [alice["id"]])  # a builder group would demote Alice, the only signed-in admin
    assert await role_of(scim, "alice@example.com") == "admin"  # skipped, not applied
    members = (await scim.get(f"{S}/Groups/{g['id']}", headers=h)).json()["members"]
    assert [m["value"] for m in members] == [alice["id"]]
    # once someone else who signs in is admin, the same kind of push goes through
    await scim.put("/v1/org/members", json={"email": "bob@example.com", "role": "admin"}, headers=ALICE)
    assert (await scim.get("/v1/me", headers=bearer(token(sub="bob-sub", email="bob@example.com")))).status_code == 200
    await scim.delete(f"{S}/Groups/{g['id']}", headers=h)
    await post_group(scim, "eng2", [alice["id"]])  # not in the roles table: Alice falls to the default
    assert await role_of(scim, "alice@example.com") == "viewer"


async def test_scim_changes_are_audited_without_a_person(scim):
    ada = await post_user(scim, "ada@example.com")
    g = await post_group(scim, "eng", [ada["id"]])
    await scim.patch(
        f"{S}/Users/{ada['id']}", json=ops({"op": "replace", "path": "active", "value": False}), headers=scim.scim
    )
    await scim.delete(f"{S}/Groups/{g['id']}", headers=scim.scim)
    log = (await scim.get("/v1/org/audit", params={"limit": 100}, headers=ALICE)).json()
    scim_rows = [e for e in log if e["action"].startswith("scim_")]
    assert {"scim_create_user", "scim_create_group", "scim_role_sync", "scim_set_active", "scim_delete_group"} <= {
        e["action"] for e in scim_rows
    }
    assert all(e["actor_id"] is None and e["detail"]["caller"] == "scim" for e in scim_rows)
    assert scim.scim["Authorization"].split()[1] not in str(log)


async def test_another_organisations_directory_cannot_re_address_a_shared_invitation(app, scim):
    """CP-1: a person invited but not signed in yet is one row for every organisation that invited them. Organisation
    B's SCIM token used to rename that row's address, so whoever owned the new address joined A with A's role."""
    r = await scim.put("/v1/org/members", json={"email": "victim@corp.com", "role": "admin"}, headers=ALICE)
    assert r.status_code == 200, r.text
    async with app.state.db.sessionmaker() as s:
        s.add(Organization(id="org_b", name="B", scim_token_hash=hash_token("scim_b-token")))
        await s.commit()
    b = {"Authorization": "Bearer scim_b-token"}
    taken = await scim.post(f"{S}/Users", json=user("victim@corp.com", displayName="Evil name"), headers=b)
    assert taken.status_code == 201, taken.text
    vid = taken.json()["id"]
    moved = await scim.patch(
        f"{S}/Users/{vid}", json=ops({"op": "replace", "path": "userName", "value": "attacker@evil.com"}), headers=b
    )
    assert_error(moved, 409, "mutability")
    replaced = await scim.put(f"{S}/Users/{vid}", json=user("attacker@evil.com"), headers=b)
    assert_error(replaced, 409, "mutability")
    async with app.state.db.sessionmaker() as s:
        row = await s.get(AppUser, vid)
    assert row is not None and row.email == "victim@corp.com" and row.oidc_subject == "pending:victim@corp.com"
    assert row.display_name != "Evil name"  # B's directory does not name A's invitation either
    # a user only this organisation invited can still be re-addressed by its directory
    mine = await post_user(scim, "old@example.com")
    ok = await scim.patch(
        f"{S}/Users/{mine['id']}", json=ops({"op": "replace", "path": "userName", "value": "new@example.com"}),
        headers=scim.scim,
    )  # fmt: skip
    assert ok.status_code == 200 and ok.json()["userName"] == "new@example.com"
