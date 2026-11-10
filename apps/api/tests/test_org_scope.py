"""Organisations (M7-T2): a user of organisation B sees nothing of organisation A, and probing ids gets 404."""

import asyncio

import pytest
from test_publish import greeter, make_greeter

from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.db.models import AppUser, Membership, Organization, Project, ProjectMember
from agentcanvas.ir.models import Workflow

ORG_B, USER_B, PROJECT_B = "org_b", "usr_b", "prj_b"


@pytest.fixture
async def org_b(app):
    async with app.state.db.sessionmaker() as s:
        s.add(Organization(id=ORG_B, name="Org B"))
        s.add(AppUser(id=USER_B, oidc_subject="b", email="b@example.com"))
        await s.flush()
        s.add(Membership(org_id=ORG_B, user_id=USER_B, role="admin"))
        s.add(Project(id=PROJECT_B, org_id=ORG_B, name="B's project"))
        await s.commit()


def act_as_b(app):
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id=USER_B, org_id=ORG_B, role="admin", memberships={ORG_B: "admin"}
    )


async def _seed_a(app, api_client) -> dict[str, str]:
    """One of each kind of thing in organisation A's default project, as A's dev admin."""
    wf_id = await make_greeter(api_client)
    wf = Workflow.model_validate((await api_client.get(f"/v1/workflows/{wf_id}")).json()["ir"])
    h = await app.state.runner.start(wf, input={"who": "Ada"})
    await asyncio.wait_for(h.done.wait(), 10)
    pub = (await api_client.post(f"/v1/workflows/{wf_id}/publish", json={})).json()
    dep_id = pub["deployment"]["id"]
    key_id = (await api_client.get(f"/v1/deployments/{dep_id}/keys")).json()[0]["id"]
    assert (await api_client.put("/v1/projects/prj_default/secrets/TOKEN", json={"value": "s3cret"})).status_code == 204
    return {"wf": wf_id, "run": h.run_id, "thread": h.thread_id, "dep": dep_id, "key": key_id}


async def _refused_everything(api_client, a: dict[str, str]) -> None:
    """Every read and write naming A's things answers 404, as if they did not exist."""
    wf_id, dep_id, key_id, run_id, thread_id = a["wf"], a["dep"], a["key"], a["run"], a["thread"]
    for path in [
        f"/v1/workflows/{wf_id}",
        f"/v1/workflows/{wf_id}/versions",
        f"/v1/workflows/{wf_id}/code",
        f"/v1/workflows/{wf_id}/deployments",
        f"/v1/runs/{run_id}",
        f"/v1/threads/{thread_id}",
        f"/v1/threads/{thread_id}/history",
        f"/v1/threads/{thread_id}/checkpoints/cp_1",
        "/v1/projects/prj_default/workflows",
        "/v1/projects/prj_default/secrets",
        "/v1/projects/prj_default/files",
        f"/v1/threads?workflow_id={wf_id}",
    ]:
        r = await api_client.get(path)
        assert r.status_code == 404, f"{path}: {r.status_code} {r.text}"
    # writes are refused the same way
    assert (await api_client.delete(f"/v1/workflows/{wf_id}")).status_code == 404
    assert (await api_client.post(f"/v1/deployments/{dep_id}/rotate-key")).status_code == 404
    assert (await api_client.get(f"/v1/deployments/{dep_id}/keys")).status_code == 404
    assert (await api_client.post("/v1/projects/prj_default/workflows", json={"name": "x"})).status_code == 404
    for method, path, body in [  # S17: every kind of write names a resource of A, so every one is a 404
        ("PUT", "/v1/projects/prj_default/secrets/TOKEN", {"value": "mine"}),
        ("DELETE", "/v1/projects/prj_default/secrets/TOKEN", None),
        ("PUT", "/v1/projects/prj_default/files/scripts/x.py", {"content": "x = 1"}),
        ("POST", f"/v1/workflows/{wf_id}/runs/stream", {"input": {}}),
        ("POST", f"/v1/workflows/{wf_id}/triggers", {"kind": "cron", "config": {"schedule": "* * * * *"}}),
        ("POST", f"/v1/runs/{run_id}/cancel", None),
        ("POST", f"/v1/threads/{thread_id}/resume", {"value": True}),
        ("POST", f"/v1/threads/{thread_id}/fork", {}),
        ("DELETE", f"/v1/threads/{thread_id}", None),
        ("DELETE", f"/v1/deployments/{dep_id}", None),
        ("GET", f"/v1/deployments/{dep_id}/share", None),  # S23: share links
        ("PUT", f"/v1/deployments/{dep_id}/share", {"enabled": True}),
        ("POST", f"/v1/deployments/{dep_id}/share/regenerate", None),
        ("POST", f"/v1/deployments/{dep_id}/keys", {"name": "mine"}),  # E-8: deployment keys
        ("DELETE", f"/v1/deployments/{dep_id}/keys/{key_id}", None),
        ("PUT", "/v1/projects/prj_default/spending-caps", {"max_cost_usd_day": 1000}),  # E-9: spending caps
        ("POST", f"/v1/deployments/{dep_id}/callback-secret", None),  # E-2: run callbacks' signing secret
        ("GET", f"/v1/deployments/{dep_id}/oauth", None),  # E-11: OAuth sign-in to its MCP and A2A endpoints
        ("PUT", f"/v1/deployments/{dep_id}/oauth", {"enabled": False}),
        ("GET", f"/v1/deployments/{dep_id}/system-record", None),  # E-13: the AI-system record
        ("PUT", f"/v1/deployments/{dep_id}/system-record", {"purpose": "mine"}),
        ("GET", f"/v1/deployments/{dep_id}/system-record/export", None),
        ("GET", "/v1/projects/prj_default/members", None),  # E-10: who works in the project
        ("PUT", "/v1/projects/prj_default/members", {"email": "b@example.com", "role": "admin"}),
    ]:
        r = await api_client.request(method, path, json=body)
        assert r.status_code == 404, f"{method} {path}: {r.status_code} {r.text}"


async def test_org_b_cannot_read_org_a(app, api_client, org_b):
    """M7-T2 AC: a user of org A cannot read org B's workflow (here: B cannot read A's)."""
    a = await _seed_a(app, api_client)
    act_as_b(app)
    try:
        await _refused_everything(api_client, a)
        # lists show only B's own things
        assert [p["id"] for p in (await api_client.get("/v1/projects")).json()] == [PROJECT_B]
        assert (await api_client.get("/v1/threads")).json()["items"] == []
        assert (await api_client.get("/v1/inbox")).json() == []
        # B works normally inside its own organisation
        mine = await api_client.post(f"/v1/projects/{PROJECT_B}/workflows", json={"name": "B flow"})
        assert mine.status_code == 201, mine.text
    finally:
        app.dependency_overrides.pop(current_user)
    # and A still sees its workflow, not B's
    names = [w["name"] for w in (await api_client.get("/v1/projects/prj_default/workflows")).json()["items"]]
    assert names == ["Greeter"]


OUTSIDER, OTHER_PROJECT = "usr_c", "prj_c"


async def private_with_outsider(app, role: str = "builder") -> None:
    """Organisation A's projects private, and a member of A (with `role`) bound only to another project of A's."""
    async with app.state.db.sessionmaker() as s:
        org = await s.get(Organization, "org_default")
        org.settings = {**(org.settings or {}), "private_projects": True}
        s.add(AppUser(id=OUTSIDER, oidc_subject="c", email="c@example.com"))
        s.add(Project(id=OTHER_PROJECT, org_id="org_default", name="C's project"))
        await s.flush()
        s.add(Membership(org_id="org_default", user_id=OUTSIDER, role=role))
        s.add(ProjectMember(id="pmb_c", project_id=OTHER_PROJECT, user_id=OUTSIDER, role="admin"))
        await s.commit()


def act_as_outsider(app, role: str = "builder"):
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id=OUTSIDER, org_id="org_default", role=role, memberships={"org_default": role}
    )


async def test_same_organisation_other_project_sees_nothing_of_a_private_project(app, api_client):
    """E-10: with projects private, a member of the same organisation who is not in the project is refused exactly
    like another organisation, even as an admin of their own project."""
    a = await _seed_a(app, api_client)
    await private_with_outsider(app)
    act_as_outsider(app)
    try:
        await _refused_everything(api_client, a)
        assert [p["id"] for p in (await api_client.get("/v1/projects")).json()] == [OTHER_PROJECT]
        assert (await api_client.get("/v1/threads")).json()["items"] == []
        assert (await api_client.get("/v1/inbox")).json() == []
        mine = await api_client.post(f"/v1/projects/{OTHER_PROJECT}/workflows", json={"name": "C flow"})
        assert mine.status_code == 201, mine.text
    finally:
        app.dependency_overrides.pop(current_user)


async def test_create_project_in_own_org(api_client):
    r = await api_client.post("/v1/projects", json={"name": "Support bots"})
    assert r.status_code == 201
    assert "Support bots" in [p["name"] for p in (await api_client.get("/v1/projects")).json()]
    assert (await api_client.post("/v1/projects", json={"name": "Support bots"})).status_code == 409


# path parameters that org_scope does not check itself, and what keeps each inside the organisation (S17)
CHILD_PARAMS = {
    "a": "a version of {wf_id}",
    "b": "a version of {wf_id}",
    "ref": "a version of {wf_id}",
    "case_id": "a test case looked up within {wf_id}",
    "ex_id": "an example looked up within {ds_id}",
    "environment": "a release gate looked up within {wf_id}",
    "name": "a secret looked up within {project_id}",
    "path": "a file inside {project_id}'s workspace",
    "policy_id": "PolicyRepo.require filters by the caller's organisation",
    "user_id": "member routes act on the caller's organisation only",
    "connection_id": "/connections: a connection of the caller's own organisation and, but for admins, own owner",
    "group_id": "/scim/v2: ScimService looks groups up within the organisation of the SCIM token",
    "key_id": "DeploymentService.revoke_key looks the key up within {dep_id}",
    "token_id": "TokenService.revoke compares the token's organisation (and, for /me, its owner) with the caller's",
    "org_id": "/me/invitations: the caller's own invitations only",
    "target": "a framework name, not a stored object (S22 bridge export)",
    "node_id": "an Agent block inside {wf_id}'s workflow (S21)",
    "proposal_id": "a Copilot proposal looked up within the caller's conversation about {wf_id}",
    "conv_id": "a Copilot conversation looked up within {wf_id} and the caller's own conversations",
    "doc_id": "a knowledge document looked up within {kb_id}",
    "file_id": "an uploaded file looked up within {project_id}",
    "token": "a share link's or an export link's token: public routes, the token is the capability (S23, E-7)",
    "export_id": "an organisation export looked up within the caller's organisation (E-7)",
    "page": "a page of the user guide shipped with the server, the same for every organisation",
    "session_id": "an editor tab of {wf_id} (G-4): only the caller's own rows of that workflow are removed",
    "checkpoint_id": "a checkpoint read from {thread_id}'s own saved state",
    "member_id": "a project member looked up within {project_id} (E-10)",
}


def test_every_path_parameter_is_scoped(app):
    """A new route with an id that neither org_scope nor a parent check covers fails here (S17)."""
    import re

    from agentcanvas.api.deps import SCOPED_PARAMS

    params = {p for path in app.openapi()["paths"] for p in re.findall(r"\{([^}]+)\}", path)}
    assert params - set(SCOPED_PARAMS) - set(CHILD_PARAMS) == set()


async def test_thread_ids_of_another_organisation_tell_nothing(app, api_client, org_b):
    """A cursor naming another organisation's thread is unknown, like a missing one; a caller-named thread id in use
    elsewhere gets the same answer whether it is another workflow of the caller's or another organisation's."""
    wf_id = await make_greeter(api_client)
    wf = Workflow.model_validate((await api_client.get(f"/v1/workflows/{wf_id}")).json()["ir"])
    h = await app.state.runner.start(wf, thread_id="support-42", input={"who": "Ada"})
    await asyncio.wait_for(h.done.wait(), 10)
    other_wf = await make_greeter(api_client)
    mine = await api_client.post(f"/v1/workflows/{other_wf}/runs/stream", json={"input": {}, "thread_id": "support-42"})

    act_as_b(app)
    try:
        assert (await api_client.get("/v1/threads", params={"cursor": "support-42"})).json() == (
            await api_client.get("/v1/threads", params={"cursor": "th_missing"})
        ).json() | {"detail": "unknown cursor 'support-42'"}
        wf_b = (await api_client.post(f"/v1/projects/{PROJECT_B}/workflows", json={"name": "b"})).json()["id"]
        r = await api_client.put(f"/v1/workflows/{wf_b}", json={"ir": greeter(wf_b, "Hi")}, headers={"If-Match": "1"})
        assert r.status_code == 200, r.text
        theirs = await api_client.post(
            f"/v1/workflows/{wf_b}/runs/stream", json={"input": {}, "thread_id": "support-42"}
        )
        assert theirs.status_code == mine.status_code == 422
        assert theirs.json() == mine.json()
    finally:
        app.dependency_overrides.pop(current_user, None)
