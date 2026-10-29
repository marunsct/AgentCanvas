"""AgentCanvas on Matura (TS9a): Matura tokens name the organisation and its permissions; webhooks and the change
feed keep organisations, members and roles current; nothing crosses between organisations."""

import itertools
import json
import pathlib
import uuid

import httpx
import pytest
from sqlalchemy import select

from agentcanvas.db.models import AppUser, Membership, Organization
from agentcanvas.security.auth import Authenticator
from agentcanvas.services import matura
from agentcanvas.services.matura import PERMISSIONS, MaturaFeed, role_for
from agentcanvas.settings import Settings
from agentcanvas.vendor.matura import MaturaAPI
from agentcanvas.vendor.matura.testing import FakeIssuer, signed_event

ISSUER = FakeIssuer("https://matura.test/realms/matura", audience="agentcanvas")
SECRET = "whsec-test-0123456789abcdef"
ACME, GLOBEX = str(uuid.uuid4()), str(uuid.uuid4())
ANA, BEN = str(uuid.uuid4()), str(uuid.uuid4())
ROLE_PERMS = {
    "viewer": ["canvas.read"],
    "reviewer": ["canvas.read", "inbox.review"],
    "builder": ["canvas.read", "inbox.review", "workflow.build"],
    "admin": ["canvas.read", "inbox.review", "workflow.build", "org.admin"],
}
_seq = itertools.count(1)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "auth_mode": "matura", "oidc_issuer": ISSUER.issuer, "oidc_audience": "agentcanvas",
            "oidc_client_id": "agentcanvas", "matura_webhook_secret": SECRET,
            "matura_portal_url": "https://portal.matura.test",
        }
    )  # fmt: skip


@pytest.fixture
async def client(app):
    matura.forget()
    app.state.authenticator = Authenticator(app.state.settings, jwks=ISSUER.jwks)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def as_user(user=ANA, tenant=ACME, role="builder", **over) -> dict[str, str]:
    perms = over.pop("perms", ROLE_PERMS.get(role, []))
    t = ISSUER.token(user, tenant_id=tenant, tenant=over.pop("slug", tenant[:8]), roles=[role], perms=perms, **over)
    return {"Authorization": f"Bearer {t}"}


def event(obj: str, typ: str, payload: dict, version: int | None = None, eid: str | None = None) -> dict:
    v = version if version is not None else next(_seq)
    return {
        "specversion": "1.0", "id": eid or f"evt_{uuid.uuid4().hex}", "source": "matura://installation/test",
        "type": f"matura.{typ}", "data": {"object": obj, "version": v, obj: payload},
    }  # fmt: skip


async def send(client, ev: dict, secret: str = SECRET) -> httpx.Response:
    body, headers = signed_event(secret, ev)
    return await client.post("/v1/matura/events", content=body, headers=headers)


def tenant(tid=ACME, name="Acme Corp", slug="acme", status="active"):
    return {"id": tid, "slug": slug, "name": name, "kind": "company", "status": status, "data_mode": "shared"}


def user(uid, email, name="Someone", status="active"):
    return {"id": uid, "email": email, "email_verified": True, "display_name": name, "status": status}


def member(uid, tid=ACME, tenant_role="member", status="active"):
    return {"tenant_id": tid, "user_id": uid, "tenant_role": tenant_role, "status": status}


def grant(uid, role, tid=ACME, status="active", gid=None):
    return {"id": gid or str(uuid.uuid4()), "tenant_id": tid, "subject_type": "user", "subject_id": uid,
            "role": role, "status": status}  # fmt: skip


async def roles_defined(client):
    for name, perms in ROLE_PERMS.items():
        assert (
            await send(client, event("app_role", "app.role.created", {"name": name, "permissions": perms}))
        ).status_code == 204


def test_roles_follow_the_strongest_permission_and_match_the_manifest():
    assert role_for(["canvas.read"]) == "viewer"
    assert role_for(["canvas.read", "inbox.review", "workflow.build"]) == "builder"
    assert role_for(["*"]) == "admin"
    assert role_for(["canvas.read", "inbox.review", "workflow.build", "org.admin"]) == "admin"
    # a role edited in Matura that skips a rung gets only the rungs below the gap
    assert role_for(["canvas.read", "inbox.review", "org.admin"]) == "reviewer"
    assert role_for(["org.admin"]) is None
    assert role_for(["something.else"]) is None
    manifest = json.loads((pathlib.Path(__file__).parents[3] / "matura/manifest.json").read_text())
    assert {p["name"] for p in manifest["permissions"]} == set(PERMISSIONS.values())
    assert {r["name"]: r["permissions"] for r in manifest["roles"]} == ROLE_PERMS
    assert manifest["owner_role"] == matura.OWNER_ROLE


async def test_auth_config_names_matura_the_portal_and_the_tenant_parameter(client):
    cfg = (await client.get("/v1/auth/config")).json()
    assert cfg["mode"] == "matura" and cfg["client_id"] == "agentcanvas" and cfg["issuer"] == ISSUER.issuer
    assert cfg["portal_url"] == "https://portal.matura.test/" and cfg["tenant_param"] == "matura_tenant"


async def test_token_gives_organisation_and_role_and_data_stays_in_its_organisation(client):
    me = (await client.get("/v1/me", headers=as_user(ANA, ACME, "builder", slug="acme"))).json()
    assert me["id"] == ANA and me["org_id"] == ACME and me["role"] == "builder"
    made = await client.post("/v1/projects", json={"name": "Acme bots"}, headers=as_user(ANA, ACME, "builder"))
    assert made.status_code == 201, made.text
    pid = made.json()["id"]

    # Ben in another organisation sees none of it and cannot reach it by id
    ben = as_user(BEN, GLOBEX, "admin")
    assert "Acme bots" not in [p["name"] for p in (await client.get("/v1/projects", headers=ben)).json()]
    assert (await client.get(f"/v1/projects/{pid}/settings", headers=ben)).status_code == 404

    # Ana as a viewer of Globex: in Globex she reads and cannot build
    ana_globex = as_user(ANA, GLOBEX, "viewer")
    assert (await client.get("/v1/me", headers=ana_globex)).json()["role"] == "viewer"
    assert (await client.post("/v1/projects", json={"name": "x"}, headers=ana_globex)).status_code == 403
    me = (await client.get("/v1/me", headers=ana_globex)).json()
    assert me["memberships"] == {ACME: "builder", GLOBEX: "viewer"}


async def test_personal_tokens_work_in_matura_mode_within_the_token_organisation(client):
    """G-13: a token made by a Matura user acts in the organisation it was made in, with the role mirrored there."""
    ana = as_user(ANA, ACME, "builder")
    made = await client.post("/v1/projects", json={"name": "Acme bots"}, headers=ana)
    assert made.status_code == 201, made.text
    tok = await client.post("/v1/me/tokens", json={"name": "ci", "role": "viewer"}, headers=ana)
    assert tok.status_code == 201, tok.text
    h = {"Authorization": f"Bearer {tok.json()['token']}"}
    me = (await client.get("/v1/me", headers=h)).json()
    assert me["id"] == ANA and me["org_id"] == ACME and me["role"] == "viewer"
    assert [p["name"] for p in (await client.get("/v1/projects", headers=h)).json()] == ["Acme bots"]
    assert (await client.post("/v1/projects", json={"name": "x"}, headers=h)).status_code == 403
    assert (await client.get("/v1/me", headers={**h, "X-Org-Id": GLOBEX})).status_code == 403


async def test_groups_do_not_map_to_roles_in_matura_mode(client):
    """G-13: roles come from Matura; a groups table (an OIDC-mode setting) is refused, and a groups claim is ignored."""
    admin = as_user(ANA, ACME, "admin")
    r = await client.put("/v1/org/settings", json={"sso_groups": {"roles": {"eng": "admin"}}}, headers=admin)
    assert r.status_code == 409 and "OpenID Connect" in r.json()["detail"]
    assert (await client.get("/v1/org/settings", headers=admin)).json()["sso_groups_editable"] is False
    viewer = as_user(BEN, ACME, "viewer", groups=["eng"])
    assert (await client.get("/v1/me", headers=viewer)).json()["role"] == "viewer"


async def test_refusals(client):
    no_tenant = {"Authorization": f"Bearer {ISSUER.token(ANA, perms=['canvas.read'])}"}
    r = await client.get("/v1/me", headers=no_tenant)
    assert r.status_code == 403 and "portal.matura.test" in r.json()["detail"]
    r = await client.get("/v1/me", headers=as_user(role="other", perms=["billing.read"]))
    assert r.status_code == 403 and "no access to AgentCanvas" in r.json()["detail"]
    r = await client.get("/v1/me", headers={**as_user(), "X-Org-Id": GLOBEX})
    assert r.status_code == 403 and "sign in again" in r.json()["detail"]
    assert (await client.get("/v1/me", headers={**as_user(), "X-Org-Id": ACME})).status_code == 200
    wrong_audience = ISSUER.token(ANA, tenant_id=ACME, perms=["*"], audience="another-app")
    assert (await client.get("/v1/me", headers={"Authorization": f"Bearer {wrong_audience}"})).status_code == 401
    assert (await client.get("/v1/me", headers=as_user(perms=["*"]))).json()["role"] == "admin"


async def test_members_and_invitations_are_managed_in_matura(client):
    admin = as_user(ANA, ACME, "admin")
    r = await client.put("/v1/org/members", json={"email": "x@example.com", "role": "viewer"}, headers=admin)
    assert r.status_code == 409 and "managed in Matura" in r.json()["detail"]
    assert (await client.delete(f"/v1/org/members/{BEN}", headers=admin)).status_code == 409
    assert (await client.post(f"/v1/me/invitations/{GLOBEX}/accept", headers=admin)).status_code == 409
    assert (await client.get("/v1/org/members", headers=admin)).status_code == 200


async def test_webhooks_need_a_valid_signature(client):
    ev = event("tenant", "tenant.updated", tenant())
    assert (await send(client, ev, secret="wrong")).status_code == 401
    r = await client.post("/v1/matura/events", content=b"{}", headers={"Matura-Signature": "t=1,v1=00"})
    assert r.status_code == 401
    assert (await send(client, ev)).status_code == 204


async def test_webhooks_mirror_members_and_roles(client, app):
    await roles_defined(client)
    for ev in (
        event("tenant", "tenant.created", tenant(name="Acme Corp")),
        event("user", "user.created", user(ANA, "ana@acme.test", "Ana")),
        event("user", "user.created", user(BEN, "ben@acme.test", "Ben")),
        event("membership", "membership.created", member(ANA, tenant_role="owner")),
        event("membership", "membership.created", member(BEN)),
    ):
        assert (await send(client, ev)).status_code == 204
    admin = as_user(ANA, ACME, "admin")
    members = {m["user_id"]: m for m in (await client.get("/v1/org/members", headers=admin)).json()}
    assert members[ANA]["role"] == "admin"  # owners get the owner role
    assert BEN not in members  # a member without an AgentCanvas role has no access here

    gid = str(uuid.uuid4())
    await send(client, event("grant", "grant.added", grant(BEN, "reviewer", gid=gid)))
    members = {m["user_id"]: m for m in (await client.get("/v1/org/members", headers=admin)).json()}
    assert members[BEN]["role"] == "reviewer" and members[BEN]["email"] == "ben@acme.test"

    # a role's permissions change in Matura: everyone holding it changes with it
    await send(
        client, event("app_role", "app.role.updated", {"name": "reviewer", "permissions": ROLE_PERMS["builder"]})
    )
    members = {m["user_id"]: m for m in (await client.get("/v1/org/members", headers=admin)).json()}
    assert members[BEN]["role"] == "builder"

    # an older delivery of the grant cannot bring back what a newer one revoked
    await send(
        client, event("grant", "grant.revoked", grant(BEN, "reviewer", status="revoked", gid=gid), version=10_000)
    )
    await send(client, event("grant", "grant.added", grant(BEN, "reviewer", gid=gid), version=9_999))
    assert BEN not in {m["user_id"] for m in (await client.get("/v1/org/members", headers=admin)).json()}

    # the organisation's name follows Matura's
    await send(client, event("tenant", "tenant.updated", tenant(name="Acme Inc")))
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(Organization, ACME)).name == "Acme Inc"


async def test_duplicate_events_apply_once(client, app):
    await roles_defined(client)
    ev = event("membership", "membership.created", member(BEN, tenant_role="admin"))
    assert await matura.apply(app.state.db, ev) is True
    assert await matura.apply(app.state.db, ev) is False


async def test_removed_member_and_disabled_app_lose_access(client, app):
    await roles_defined(client)
    for ev in (
        event("tenant", "tenant.created", tenant()),
        event("user", "user.created", user(BEN, "ben@acme.test")),
        event("membership", "membership.created", member(BEN, tenant_role="admin")),
        event("user", "user.created", user(ANA, "ana@acme.test")),
        event("membership", "membership.created", member(ANA, tenant_role="owner")),
    ):
        await send(client, ev)
    admin = as_user(ANA, ACME, "admin")
    assert BEN in {m["user_id"] for m in (await client.get("/v1/org/members", headers=admin)).json()}
    await send(client, event("membership", "membership.removed", member(BEN, tenant_role="admin", status="removed")))
    assert BEN not in {m["user_id"] for m in (await client.get("/v1/org/members", headers=admin)).json()}

    await send(client, event("tenant_app", "tenant_app.disabled", {"tenant_id": ACME, "app": "agentcanvas"}))
    async with app.state.db.sessionmaker() as s:
        assert (await s.scalars(select(Membership).where(Membership.org_id == ACME))).all() == []


async def test_perms_ref_tokens_read_the_roles_from_the_contract_tables(client):
    await roles_defined(client)
    t = ISSUER.token(ANA, tenant_id=ACME, roles=["reviewer"], perms=[], perms_ref="matura_app_role")
    assert (await client.get("/v1/me", headers={"Authorization": f"Bearer {t}"})).json()["role"] == "reviewer"


async def test_change_feed_catches_up_what_webhooks_missed(app):
    pages = {
        None: {"items": [
            event("tenant", "tenant.created", tenant(GLOBEX, "Globex", "globex"), eid="evt_1"),
            event("user", "user.created", user(BEN, "ben@globex.test", "Ben"), eid="evt_2"),
        ], "next_cursor": "2", "has_more": True},
        "2": {"items": [
            event("membership", "membership.created", member(BEN, GLOBEX), eid="evt_3"),
            event("grant", "grant.added", grant(BEN, "builder", GLOBEX), eid="evt_4"),
        ], "next_cursor": "4", "has_more": False},
        "4": {"items": [], "next_cursor": "4", "has_more": False},
    }  # fmt: skip
    roles = [{"name": n, "permissions": p, "version": 1} for n, p in ROLE_PERMS.items()]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Basic ")
        if request.url.path.endswith("/snapshot"):
            return httpx.Response(200, json={"cursor": "", "tenants": [], "roles": roles})
        return httpx.Response(200, json=pages[request.url.params.get("cursor") or None])

    api = MaturaAPI("https://matura.test", "agentcanvas", "agentcanvas", "mts_secret",
                    client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))  # fmt: skip
    feed = MaturaFeed(app.state.db, app.state.settings, api=api)
    assert await feed.tick() == 4
    assert await feed.tick() == 0  # the cursor was stored
    async with app.state.db.sessionmaker() as s:
        m = await s.get(Membership, (GLOBEX, BEN))
        assert m is not None and m.role == "builder"
        u = (await s.scalars(select(AppUser).where(AppUser.id == BEN))).one()
        assert u.email == "ben@globex.test" and u.oidc_subject == f"matura:{BEN}"
    await api.aclose()
