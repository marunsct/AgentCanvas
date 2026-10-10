"""Organisations (M7-T2): a user of organisation B sees nothing of organisation A, and probing ids gets 404."""

import asyncio

import pytest
from test_publish import make_greeter

from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.db.models import AppUser, Membership, Organization, Project
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


async def test_org_b_cannot_read_org_a(app, api_client, org_b):
    """M7-T2 AC: a user of org A cannot read org B's workflow (here: B cannot read A's)."""
    wf_id = await make_greeter(api_client)  # as the dev admin of org_default
    wf = Workflow.model_validate((await api_client.get(f"/v1/workflows/{wf_id}")).json()["ir"])
    h = await app.state.runner.start(wf, input={"who": "Ada"})
    await asyncio.wait_for(h.done.wait(), 10)
    pub = (await api_client.post(f"/v1/workflows/{wf_id}/publish", json={})).json()
    dep_id = pub["deployment"]["id"]
    assert (await api_client.put("/v1/projects/prj_default/secrets/TOKEN", json={"value": "s3cret"})).status_code == 204

    act_as_b(app)
    try:
        for path in [
            f"/v1/workflows/{wf_id}",
            f"/v1/workflows/{wf_id}/versions",
            f"/v1/workflows/{wf_id}/code",
            f"/v1/workflows/{wf_id}/deployments",
            f"/v1/runs/{h.run_id}",
            f"/v1/threads/{h.thread_id}",
            f"/v1/threads/{h.thread_id}/history",
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
        assert (await api_client.post("/v1/projects/prj_default/workflows", json={"name": "x"})).status_code == 404
        for method, path, body in [  # S17: every kind of write names a resource of A, so every one is a 404
            ("PUT", "/v1/projects/prj_default/secrets/TOKEN", {"value": "mine"}),
            ("DELETE", "/v1/projects/prj_default/secrets/TOKEN", None),
            ("PUT", "/v1/projects/prj_default/files/scripts/x.py", {"content": "x = 1"}),
            ("POST", f"/v1/workflows/{wf_id}/runs/stream", {"input": {}}),
            ("POST", f"/v1/workflows/{wf_id}/triggers", {"kind": "cron", "config": {"schedule": "* * * * *"}}),
            ("POST", f"/v1/runs/{h.run_id}/cancel", None),
            ("POST", f"/v1/threads/{h.thread_id}/resume", {"value": True}),
            ("POST", f"/v1/threads/{h.thread_id}/fork", {}),
            ("DELETE", f"/v1/threads/{h.thread_id}", None),
            ("DELETE", f"/v1/deployments/{dep_id}", None),
            ("GET", f"/v1/deployments/{dep_id}/share", None),  # S23: share links
            ("PUT", f"/v1/deployments/{dep_id}/share", {"enabled": True}),
            ("POST", f"/v1/deployments/{dep_id}/share/regenerate", None),
        ]:
            r = await api_client.request(method, path, json=body)
            assert r.status_code == 404, f"{method} {path}: {r.status_code} {r.text}"
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
    "org_id": "/me/invitations: the caller's own invitations only",
    "target": "a framework name, not a stored object (S22 bridge export)",
    "node_id": "an Agent block inside {wf_id}'s workflow (S21)",
    "proposal_id": "a Copilot proposal looked up within the caller's conversation about {wf_id}",
    "token": "a share link's token: public visitor routes, the token is the capability (S23)",
}


def test_every_path_parameter_is_scoped(app):
    """A new route with an id that neither org_scope nor a parent check covers fails here (S17)."""
    import re

    from agentcanvas.api.deps import SCOPED_PARAMS

    params = {p for path in app.openapi()["paths"] for p in re.findall(r"\{([^}]+)\}", path)}
    assert params - set(SCOPED_PARAMS) - set(CHILD_PARAMS) == set()
