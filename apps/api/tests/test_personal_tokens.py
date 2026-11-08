"""Personal API tokens (G-13): `Authorization: Bearer act_…` acts as the person who made it, in its organisation only,
with at most the role chosen then; revoked, expired, unknown and orphaned tokens get 401."""

import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from test_auth import SIGNING_JWK, bearer, settings, token  # noqa: F401 - `settings` turns OIDC sign-in on

from agentcanvas.db.models import Membership, Organization, PersonalToken, Project
from agentcanvas.db.repositories import UserRepo
from agentcanvas.security.auth import Authenticator
from agentcanvas.services.tokens import hash_token

LONG = int(time.time()) + 86400  # the whole suite may outlast a token's five minutes
ALICE = bearer(token(exp=LONG))  # bootstrap admin of org_default
BOB = bearer(token(sub="bob-sub", email="bob@example.com", name="Bob", exp=LONG))


@pytest.fixture
async def client(app):
    app.state.authenticator = Authenticator(app.state.settings, jwks={"keys": [SIGNING_JWK]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/v1/me", headers=ALICE)).status_code == 200  # Alice exists and is admin
        yield c


async def _make(client, headers=ALICE, **body) -> dict:
    r = await client.post("/v1/me/tokens", json={"name": "CI", **body}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _member(client, email: str, role: str) -> None:
    r = await client.put("/v1/org/members", json={"email": email, "role": role}, headers=ALICE)
    assert r.status_code == 200, r.text


async def test_a_token_acts_as_its_owner_and_is_shown_once(app, client):
    made = await _make(client)
    assert made["token"].startswith("act_") and made["prefix"] == made["token"][:10]
    assert made["role"] == "admin" and made["expires_at"] is not None  # 90 days unless said otherwise
    me = await client.get("/v1/me", headers=bearer(made["token"]))
    assert me.status_code == 200, me.text
    assert me.json()["email"] == "alice@example.com" and me.json()["role"] == "admin"
    assert (await client.get("/v1/projects", headers=bearer(made["token"]))).status_code == 200
    listed = (await client.get("/v1/me/tokens", headers=ALICE)).json()
    assert [t["id"] for t in listed] == [made["id"]] and "token" not in listed[0]
    assert listed[0]["last_used_at"] is not None
    async with app.state.db.sessionmaker() as s:  # only the hash is stored
        row = await s.get(PersonalToken, made["id"])
        assert (
            row is not None and row.token_hash == hash_token(made["token"]) and made["token"] not in str(row.__dict__)
        )


@pytest.mark.parametrize("bad", ["act_", "act_not-a-token", "act_" + "A" * 43])
async def test_unknown_tokens_are_401(client, bad):
    made = await _make(client)
    for t in (bad, made["token"] + "x", made["token"][:-1]):
        r = await client.get("/v1/me", headers=bearer(t))
        assert r.status_code == 401, (t, r.text)


async def test_a_revoked_token_stops_working_at_once(client):
    made = await _make(client)
    h = bearer(made["token"])
    assert (await client.get("/v1/me", headers=h)).status_code == 200
    assert (await client.delete(f"/v1/me/tokens/{made['id']}", headers=ALICE)).status_code == 204
    r = await client.get("/v1/me", headers=h)
    assert r.status_code == 401 and "revoked" in r.json()["detail"]
    assert (await client.get("/v1/me", headers=h)).status_code == 401  # replayed later: still refused
    [listed] = (await client.get("/v1/me/tokens", headers=ALICE)).json()
    assert listed["revoked_at"] is not None


async def test_an_expired_token_is_401(app, client):
    made = await _make(client, expires_in_days=1)
    async with app.state.db.sessionmaker() as s:
        row = await s.get(PersonalToken, made["id"])
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()
    r = await client.get("/v1/me", headers=bearer(made["token"]))
    assert r.status_code == 401 and "expired" in r.json()["detail"]


async def test_a_token_stays_in_its_organisation(app, client):
    made = await _make(client)
    async with app.state.db.sessionmaker() as s:  # Alice also belongs to org_b, which has a project
        alice = await UserRepo(s).by_subject("oidc:alice-sub")
        assert alice is not None
        s.add(Organization(id="org_b", name="Org B"))
        await s.flush()
        s.add(Membership(org_id="org_b", user_id=alice.id, role="admin"))
        s.add(Project(id="prj_b", org_id="org_b", name="B's"))
        await s.commit()
    h = bearer(made["token"])
    r = await client.get("/v1/me", headers={**h, "X-Org-Id": "org_b"})
    assert r.status_code == 403 and "org_default" in r.json()["detail"]
    assert (await client.get("/v1/projects/prj_b/workflows", headers=h)).status_code == 404
    assert "prj_b" not in {p["id"] for p in (await client.get("/v1/projects", headers=h)).json()}
    # Alice's sign-in reaches org_b; the token made in org_default does not
    assert (await client.get("/v1/projects/prj_b/workflows", headers={**ALICE, "X-Org-Id": "org_b"})).status_code == 200
    # and a token of org_b cannot be revoked from org_default, nor seen there
    b_token = await _make(client, headers={**ALICE, "X-Org-Id": "org_b"})
    here = {**ALICE, "X-Org-Id": "org_default"}
    assert b_token["id"] not in {t["id"] for t in (await client.get("/v1/me/tokens", headers=here)).json()}
    assert (await client.delete(f"/v1/me/tokens/{b_token['id']}", headers=here)).status_code == 404
    assert (await client.delete(f"/v1/org/tokens/{b_token['id']}", headers=here)).status_code == 404
    assert (await client.get("/v1/me", headers=bearer(b_token["token"]))).json()["org_id"] == "org_b"


async def test_a_token_has_at_most_its_role_and_its_owners_role_now(client):
    viewer_token = await _make(client, role="viewer")
    h = bearer(viewer_token["token"])
    assert (await client.get("/v1/me", headers=h)).json()["role"] == "viewer"
    assert (await client.post("/v1/projects", json={"name": "nope"}, headers=h)).status_code == 403
    await _member(client, "bob@example.com", "builder")
    assert (await client.get("/v1/me", headers=BOB)).status_code == 200
    r = await client.post("/v1/me/tokens", json={"name": "too much", "role": "admin"}, headers=BOB)
    assert r.status_code == 403  # no more than the holder's own role
    bobs = await _make(client, headers=BOB)
    assert bobs["role"] == "builder"
    await _member(client, "bob@example.com", "viewer")  # a demotion applies to the token at once
    r = await client.post("/v1/projects", json={"name": "after demotion"}, headers=bearer(bobs["token"]))
    assert r.status_code == 403
    assert (await client.get("/v1/me", headers=bearer(bobs["token"]))).json()["role"] == "viewer"


async def test_a_token_of_someone_who_left_is_401(app, client):
    await _member(client, "bob@example.com", "builder")
    assert (await client.get("/v1/me", headers=BOB)).status_code == 200
    bobs = await _make(client, headers=BOB)
    async with app.state.db.sessionmaker() as s:
        bob = await UserRepo(s).by_subject("oidc:bob-sub")
        assert bob is not None
        bob_id = bob.id
    assert (await client.delete(f"/v1/org/members/{bob_id}", headers=ALICE)).status_code == 204
    r = await client.get("/v1/me", headers=bearer(bobs["token"]))
    assert r.status_code == 401  # leaving deletes the person's tokens in the organisation


async def test_a_token_cannot_make_another_token(client):
    made = await _make(client)
    r = await client.post("/v1/me/tokens", json={"name": "child"}, headers=bearer(made["token"]))
    assert r.status_code == 403 and "sign in" in r.json()["detail"]
    # it may revoke itself (a script cleaning up after itself)
    assert (await client.delete(f"/v1/me/tokens/{made['id']}", headers=bearer(made["token"]))).status_code == 204


async def test_tokens_are_audited_and_admins_see_and_revoke_everyones(client):
    await _member(client, "bob@example.com", "builder")
    assert (await client.get("/v1/me", headers=BOB)).status_code == 200
    bobs = await _make(client, headers=BOB)
    # Bob uses it to change something: the audit row names the token
    r = await client.post("/v1/projects", json={"name": "made by a script"}, headers=bearer(bobs["token"]))
    assert r.status_code == 201, r.text
    log = (await client.get("/v1/org/audit", headers=ALICE)).json()
    made = next(e for e in log if e["action"] == "create_token")
    assert made["target"] == f"token_id={bobs['id']}" and made["detail"]["role"] == "builder"
    assert bobs["token"] not in str(log)
    used = next(e for e in log if e["action"] == "create_project")
    assert used["actor_email"] == "bob@example.com" and used["detail"]["caller"] == f"personal token {bobs['id']}"
    # Bob, a builder, cannot list everyone's tokens; Alice, an admin, can and revokes his
    assert (await client.get("/v1/org/tokens", headers=BOB)).status_code == 403
    assert (await client.delete(f"/v1/org/tokens/{bobs['id']}", headers=BOB)).status_code == 403
    assert bobs["id"] in {t["id"] for t in (await client.get("/v1/org/tokens", headers=ALICE)).json()}
    assert (await client.delete(f"/v1/me/tokens/{bobs['id']}", headers=ALICE)).status_code == 404  # not hers
    assert (await client.delete(f"/v1/org/tokens/{bobs['id']}", headers=ALICE)).status_code == 204
    assert (await client.get("/v1/me", headers=bearer(bobs["token"]))).status_code == 401
    log = (await client.get("/v1/org/audit", headers=ALICE)).json()
    revoked = next(e for e in log if e["action"] == "revoke_org_token")
    assert revoked["detail"]["owner"] == bobs["user_id"]
