"""Roles and members (M7-T3): viewer reads, reviewer answers the inbox, builder builds, admin manages members."""

import pytest
from test_auth import SIGNING_JWK, bearer, token
from test_auth import settings as oidc_settings  # noqa: F401 - fixture: the API in oidc mode
from test_publish import make_greeter

from agentcanvas.api.deps import CurrentUser, current_user, required_role
from agentcanvas.db.models import AppUser, Membership
from agentcanvas.security.auth import Authenticator


def act_as(app, role: str, user_id: str = "usr_dev"):
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id=user_id, org_id="org_default", role=role, memberships={"org_default": role}
    )


@pytest.fixture
def as_role(app):
    yield lambda role: act_as(app, role)
    app.dependency_overrides.pop(current_user, None)


async def test_viewer_cannot_run_or_edit(app, api_client, as_role):
    """M7-T3 AC: a viewer cannot run or edit (403)."""
    wf = await make_greeter(api_client)
    as_role("viewer")
    assert (await api_client.get(f"/v1/workflows/{wf}")).status_code == 200
    assert (await api_client.post(f"/v1/workflows/{wf}/validate")).status_code == 200  # read-only check
    run = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "Ada"}})
    assert run.status_code == 403
    assert "viewer" in run.json()["detail"]
    edit = await api_client.put(f"/v1/workflows/{wf}/layout", json={})
    assert edit.status_code == 403
    assert (await api_client.delete(f"/v1/workflows/{wf}")).status_code == 403
    assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).status_code == 403


async def test_builder_builds_but_cannot_manage_members(api_client, app, as_role):
    as_role("builder")
    wf = await make_greeter(api_client)
    assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).status_code == 200
    assert (await api_client.get("/v1/org/members")).status_code == 200
    r = await api_client.put("/v1/org/members", json={"email": "x@example.com", "role": "viewer"})
    assert r.status_code == 403


def test_policy_table():
    assert required_role("GET", "/v1/workflows/{wf_id}") == "viewer"
    assert required_role("POST", "/v1/inbox/{interrupt_id}/decision") == "reviewer"
    assert required_role("POST", "/v1/threads/{thread_id}/resume") == "reviewer"
    assert required_role("POST", "/v1/threads/{thread_id}/fork") == "builder"
    assert required_role("PUT", "/v1/org/members") == "admin"


async def test_members_invite_change_and_keep_an_admin(api_client):
    members = (await api_client.get("/v1/org/members")).json()
    assert [(m["email"], m["role"]) for m in members] == [("dev@localhost", "admin")]
    r = await api_client.put("/v1/org/members", json={"email": "Carol@Example.com", "role": "reviewer"})
    assert r.status_code == 200
    carol = r.json()
    assert carol["email"] == "carol@example.com" and carol["invited"] is True and carol["role"] == "reviewer"
    r = await api_client.put("/v1/org/members", json={"email": "carol@example.com", "role": "builder"})
    assert r.json()["user_id"] == carol["user_id"] and r.json()["role"] == "builder"
    # the last admin can be neither demoted nor removed
    assert (
        await api_client.put("/v1/org/members", json={"email": "dev@localhost", "role": "viewer"})
    ).status_code == 409
    assert (await api_client.delete("/v1/org/members/usr_dev")).status_code == 409
    assert (await api_client.delete(f"/v1/org/members/{carol['user_id']}")).status_code == 204
    assert (await api_client.delete(f"/v1/org/members/{carol['user_id']}")).status_code == 404


class TestInvitationInOidcMode:
    @pytest.fixture
    def settings(self, oidc_settings):  # noqa: F811
        return oidc_settings

    async def test_first_verified_sign_in_claims_the_invitation(self, app):
        import httpx

        app.state.authenticator = Authenticator(app.state.settings, jwks={"keys": [SIGNING_JWK]})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            admin = bearer(token())  # alice: bootstrap admin
            assert (await c.get("/v1/me", headers=admin)).status_code == 200
            r = await c.put("/v1/org/members", json={"email": "dave@example.com", "role": "viewer"}, headers=admin)
            assert r.status_code == 200
            # an unverified email does not claim it
            sneaky = bearer(token(sub="mallory", email="dave@example.com", email_verified=False))
            assert (await c.get("/v1/me", headers=sneaky)).status_code == 403
            dave = bearer(token(sub="dave-sub", email="dave@example.com"))
            me = (await c.get("/v1/me", headers=dave)).json()
            assert me["id"] == r.json()["user_id"] and me["role"] == "viewer"
            assert (await c.post("/v1/projects", json={"name": "nope"}, headers=dave)).status_code == 403
        async with app.state.db.sessionmaker() as s:
            user = await s.get(AppUser, me["id"])
            assert user is not None and user.oidc_subject == "oidc:dave-sub"
            assert await s.get(Membership, ("org_default", me["id"])) is not None


class TestSignInEdgeCasesInOidcMode:
    @pytest.fixture
    def settings(self, oidc_settings):  # noqa: F811
        return oidc_settings

    @pytest.fixture
    async def c(self, app):
        import httpx

        app.state.authenticator = Authenticator(app.state.settings, jwks={"keys": [SIGNING_JWK]})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client

    async def test_a_removed_bootstrap_admin_stays_removed(self, c):
        alice = bearer(token())
        alice_id = (await c.get("/v1/me", headers=alice)).json()["id"]
        await c.put("/v1/org/members", json={"email": "bob@example.com", "role": "admin"}, headers=alice)
        bob = bearer(token(sub="bob-sub", email="bob@example.com"))
        assert (await c.get("/v1/me", headers=bob)).json()["role"] == "admin"
        assert (await c.delete(f"/v1/org/members/{alice_id}", headers=bob)).status_code == 204
        assert (await c.get("/v1/me", headers=alice)).status_code == 403  # not re-bootstrapped

    async def test_a_token_cannot_take_over_an_invitation_by_subject(self, c, app):
        alice = bearer(token())
        await c.get("/v1/me", headers=alice)
        await c.put("/v1/org/members", json={"email": "dave@example.com", "role": "viewer"}, headers=alice)
        forged = bearer(token(sub="pending:dave@example.com", email="eve@example.com"))
        assert (await c.get("/v1/me", headers=forged)).status_code == 403
        async with app.state.db.sessionmaker() as s:
            from agentcanvas.db.repositories import UserRepo

            assert await UserRepo(s).by_subject("pending:dave@example.com") is not None

    async def test_a_user_who_signed_in_before_being_invited_gets_the_invitation(self, c, app):
        from agentcanvas.db.repositories import UserRepo

        alice = bearer(token())
        await c.get("/v1/me", headers=alice)
        bob = bearer(token(sub="bob-sub", email="bob@example.com"))
        assert (await c.get("/v1/me", headers=bob)).status_code == 403  # not a member yet, but now a user
        await c.put("/v1/org/members", json={"email": "bob@example.com", "role": "reviewer"}, headers=alice)
        me = (await c.get("/v1/me", headers=bob)).json()
        assert me["role"] == "reviewer"
        async with app.state.db.sessionmaker() as s:
            repo = UserRepo(s)
            existing = await repo.by_subject("oidc:bob-sub")
            assert existing is not None and existing.id == me["id"]
            assert await repo.by_subject("pending:bob@example.com") is None
