"""E-10: access per project. A project's members (people and SCIM groups) have a role there; with the organisation's
projects private, nobody else but its admins sees the project or anything in it."""

from __future__ import annotations

import pytest
from test_approvers import APPROVE, _member, _waiting
from test_publish import make_greeter
from test_runs_v1 import events, triage_api  # noqa: F401 - fixture

from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.db.models import AppUser, AuditLog, Membership, ProjectMember, ScimGroup, ScimGroupMember
from agentcanvas.db.repositories import MemberRepo, UserRepo, pending_subject

ORG, MAIN = "org_default", "prj_default"


def act(app, user_id: str, role: str, **extra) -> None:
    member = extra.pop("member_role", role)
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id=user_id, org_id=ORG, role=role, memberships={ORG: member}, member_role=member, **extra
    )


@pytest.fixture
def as_user(app):
    yield lambda user_id, role, **extra: act(app, user_id, role, **extra)
    app.dependency_overrides.pop(current_user, None)


async def private(api, on: bool = True) -> None:
    r = await api.put("/v1/org/settings", json={"security": {"private_projects": on}})
    assert r.status_code == 200 and r.json()["security"]["private_projects"] is on, r.text


async def other_project(api) -> str:
    r = await api.post("/v1/projects", json={"name": "Team B"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def bind(api, project_id: str, role: str, *, email: str | None = None, group: str | None = None) -> dict:
    body = {"role": role, **({"email": email} if email else {}), **({"group": group} if group else {})}
    r = await api.put(f"/v1/projects/{project_id}/members", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def test_shared_projects_by_default_and_a_binding_only_raises_a_role(app, api_client, as_user):
    """Off by default: every member sees every project with their organisation role, as before. A binding gives an
    organisation viewer more in one project, nowhere else."""
    wf = await make_greeter(api_client)
    team = await other_project(api_client)
    await _member(app, "usr_vi", "vi@example.com", "viewer")
    assert (await api_client.get("/v1/org/settings")).json()["security"]["private_projects"] is False
    await bind(api_client, team, "builder", email="Vi@Example.com")
    as_user("usr_vi", "viewer")
    projects = {p["id"]: p["role"] for p in (await api_client.get("/v1/projects")).json()}
    assert projects == {MAIN: "viewer", team: "builder"}
    assert (await api_client.get(f"/v1/workflows/{wf}")).status_code == 200
    assert (await api_client.post(f"/v1/projects/{MAIN}/workflows", json={"name": "x"})).status_code == 403
    r = await api_client.post(f"/v1/projects/{team}/workflows", json={"name": "Theirs"})
    assert r.status_code == 201, r.text
    r = await api_client.put("/v1/org/members", json={"email": "z@example.com", "role": "viewer"})
    assert r.status_code == 403  # the organisation's own pages keep the organisation role


async def test_private_projects_hide_everything_of_a_project_nobody_bound_you_to(app, api_client, as_user):
    wf = await make_greeter(api_client)
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "Ada"}, "stream": False})
    assert r.status_code == 202, r.text
    run = r.json()
    await app.state.runner.wait(run["run_id"])
    assert (await api_client.put(f"/v1/projects/{MAIN}/secrets/TOKEN", json={"value": "s3cret"})).status_code == 204
    team = await other_project(api_client)
    await _member(app, "usr_bo", "bo@example.com", "builder")
    await bind(api_client, team, "viewer", email="bo@example.com")
    await private(api_client)

    as_user("usr_bo", "builder")
    assert [(p["id"], p["role"]) for p in (await api_client.get("/v1/projects")).json()] == [(team, "viewer")]
    for path in [
        f"/v1/projects/{MAIN}/workflows",
        f"/v1/projects/{MAIN}/secrets",
        f"/v1/workflows/{wf}",
        f"/v1/runs/{run['run_id']}",
        f"/v1/threads/{run['thread_id']}",
        f"/v1/threads?project_id={MAIN}",
    ]:
        r = await api_client.get(path)
        assert r.status_code == 404, f"{path}: {r.status_code} {r.text}"
    assert (await api_client.get("/v1/threads")).json()["items"] == []
    r = await api_client.get("/v1/threads", params={"cursor": run["thread_id"]})
    assert r.status_code == 422  # a cursor in a hidden project is as unknown as a missing one
    assert (await api_client.delete(f"/v1/workflows/{wf}")).status_code == 404
    # their own project with their role there: viewer, whatever their organisation role
    assert (await api_client.get(f"/v1/projects/{team}/workflows")).status_code == 200
    r = await api_client.post(f"/v1/projects/{team}/workflows", json={"name": "x"})
    assert r.status_code == 403 and "in this project" in r.json()["detail"], r.text
    app.dependency_overrides.pop(current_user)
    # organisation admins see every project as admin
    assert {p["id"]: p["role"] for p in (await api_client.get("/v1/projects")).json()} == {MAIN: "admin", team: "admin"}
    assert len((await api_client.get("/v1/threads")).json()["items"]) == 1


async def test_a_scim_group_binding_and_the_highest_binding_wins(app, api_client, as_user):
    team = await other_project(api_client)
    await _member(app, "usr_gr", "gr@example.com", "viewer", groups=("Support",))
    await private(api_client)
    as_user("usr_gr", "viewer")
    assert (await api_client.get("/v1/projects")).json() == []
    app.dependency_overrides.pop(current_user)
    group = await bind(api_client, team, "builder", group="support")  # names match ignoring case
    assert group["kind"] == "group" and group["group"] == "Support"
    await bind(api_client, team, "reviewer", email="gr@example.com")
    as_user("usr_gr", "viewer")
    assert [(p["id"], p["role"]) for p in (await api_client.get("/v1/projects")).json()] == [(team, "builder")]
    assert (await api_client.post(f"/v1/projects/{team}/workflows", json={"name": "x"})).status_code == 201
    app.dependency_overrides.pop(current_user)
    async with app.state.db.sessionmaker() as s:  # leaving the group (SCIM) takes what it gave
        await s.delete(await s.get(ScimGroupMember, ("grp_Support", "usr_gr")))
        await s.commit()
    as_user("usr_gr", "viewer")
    assert [(p["id"], p["role"]) for p in (await api_client.get("/v1/projects")).json()] == [(team, "reviewer")]


async def test_project_admins_manage_members_and_caps_but_not_the_project_itself(app, api_client, as_user):
    team = await other_project(api_client)
    await _member(app, "usr_pa", "pa@example.com", "builder")
    await _member(app, "usr_x", "x@example.com", "viewer")
    await bind(api_client, team, "admin", email="pa@example.com")
    await private(api_client)
    as_user("usr_pa", "builder")
    added = await bind(api_client, team, "reviewer", email="x@example.com")
    assert added["role"] == "reviewer" and added["email"] == "x@example.com"
    assert [m["email"] for m in (await api_client.get(f"/v1/projects/{team}/members")).json()] == [
        "pa@example.com",
        "x@example.com",
    ]
    r = await api_client.put(f"/v1/projects/{team}/spending-caps", json={"max_cost_usd_day": 5})
    assert r.status_code == 200, r.text
    assert (await api_client.patch(f"/v1/projects/{team}", json={"name": "Mine now"})).status_code == 403
    assert (await api_client.delete(f"/v1/projects/{team}")).status_code == 403
    r = await api_client.put(f"/v1/projects/{team}/trace-export", json={"kind": "langsmith", "api_key": "k"})
    assert r.status_code == 403
    assert (await api_client.get(f"/v1/projects/{MAIN}/members")).status_code == 404  # not theirs
    r = await api_client.put(f"/v1/projects/{team}/members", json={"email": "nobody@example.com", "role": "viewer"})
    assert r.status_code == 422 and "not a member" in r.json()["detail"]
    r = await api_client.put(f"/v1/projects/{team}/members", json={"group": "Ghosts", "role": "viewer"})
    assert r.status_code == 422
    r = await api_client.put(
        f"/v1/projects/{team}/members", json={"email": "x@example.com", "group": "G", "role": "viewer"}
    )
    assert r.status_code == 422
    r = await api_client.delete(f"/v1/projects/{team}/members/{added['id']}")
    assert r.status_code == 204
    assert (await api_client.delete(f"/v1/projects/{team}/members/{added['id']}")).status_code == 404
    app.dependency_overrides.pop(current_user)
    as_user("usr_x", "viewer")  # a reviewer there before, nothing now
    assert (await api_client.get(f"/v1/projects/{team}/members")).status_code == 404
    app.dependency_overrides.pop(current_user)
    async with app.state.db.sessionmaker() as s:  # both changes are in the audit log, forwarded as security events
        actions = (await s.scalars(AuditLog.__table__.select().with_only_columns(AuditLog.action))).all()
    assert {"put_project_member", "delete_project_member"} <= set(actions)


async def test_a_builder_of_one_project_only(app, api_client, as_user):
    """The reverse: a builder of the organisation can be read-only in one project, and still builds in the others."""
    wf = await make_greeter(api_client)
    team = await other_project(api_client)
    await _member(app, "usr_ro", "ro@example.com", "builder")
    await bind(api_client, team, "builder", email="ro@example.com")
    await bind(api_client, MAIN, "viewer", email="ro@example.com")
    await private(api_client)
    as_user("usr_ro", "builder")
    assert (await api_client.get(f"/v1/workflows/{wf}")).status_code == 200
    assert (await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {}})).status_code == 403
    assert (await api_client.post(f"/v1/projects/{team}/workflows", json={"name": "x"})).status_code == 201


async def test_an_api_token_never_does_more_than_its_own_role(app, api_client, as_user):
    team = await other_project(api_client)
    await _member(app, "usr_tk", "tk@example.com", "viewer")
    await bind(api_client, team, "builder", email="tk@example.com")
    await private(api_client)
    as_user("usr_tk", "viewer", token_id="tok_1", token_role="viewer")
    assert [p["role"] for p in (await api_client.get("/v1/projects")).json()] == ["viewer"]
    assert (await api_client.post(f"/v1/projects/{team}/workflows", json={"name": "x"})).status_code == 403
    # an admin's token made with the builder role sees every project, as a builder
    as_user("usr_dev", "builder", token_id="tok_2", token_role="builder", member_role="admin")
    assert {p["role"] for p in (await api_client.get("/v1/projects")).json()} == {"builder"}
    r = await api_client.put(f"/v1/projects/{team}/members", json={"email": "tk@example.com", "role": "admin"})
    assert r.status_code == 403
    as_user("usr_dev", "admin", token_id="tok_3", token_role="admin")
    r = await api_client.put(f"/v1/projects/{team}/members", json={"email": "tk@example.com", "role": "admin"})
    assert r.status_code == 403 and "API token" in r.json()["detail"]  # roles are handed out from a sign-in
    r = await api_client.put("/v1/org/settings", json={"security": {"private_projects": False}})
    assert r.status_code == 403  # and opening every project to everyone too


async def test_creating_a_private_project_makes_you_its_admin(app, api_client, as_user):
    await _member(app, "usr_mk", "mk@example.com", "builder")
    await private(api_client)
    as_user("usr_mk", "builder")
    r = await api_client.post("/v1/projects", json={"name": "Mine"})
    assert r.status_code == 201 and r.json()["role"] == "admin", r.text
    assert [p["name"] for p in (await api_client.get("/v1/projects")).json()] == ["Mine"]
    [me] = (await api_client.get(f"/v1/projects/{r.json()['id']}/members")).json()
    assert (me["email"], me["role"]) == ("mk@example.com", "admin")


async def test_only_admins_switch_private_projects(app, api_client, as_user):
    as_user("usr_dev", "builder")
    r = await api_client.put("/v1/org/settings", json={"security": {"private_projects": True}})
    assert r.status_code == 403
    app.dependency_overrides.pop(current_user)
    await private(api_client)
    await private(api_client, False)
    async with app.state.db.sessionmaker() as s:
        rows = (await s.scalars(AuditLog.__table__.select().with_only_columns(AuditLog.detail))).all()
    assert any((d or {}).get("security", {}).get("private_projects") is True for d in rows)


async def test_the_inbox_shows_and_decides_by_the_role_in_each_project(app, triage_api, as_user):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    item = await _waiting(api, wf, "th_e10")
    project = (await api.get(f"/v1/workflows/{wf}")).json()["project_id"]
    await _member(app, "usr_rv", "rv@example.com", "viewer")
    await private(api)
    as_user("usr_rv", "viewer")
    assert (await api.get("/v1/inbox")).json() == []
    assert (await api.post(f"/v1/inbox/{item['id']}/decision", json=APPROVE)).status_code == 404
    app.dependency_overrides.pop(current_user)
    await bind(api, project, "reviewer", email="rv@example.com")
    as_user("usr_rv", "viewer")  # an organisation viewer, a reviewer of this project
    [mine] = (await api.get("/v1/inbox")).json()
    assert mine["id"] == item["id"] and mine["may_decide"] is True
    r = await api.post(f"/v1/inbox/{item['id']}/decision", params={"stream": "true"}, json=APPROVE)
    assert events(r.text)[-1]["outcome"] == "success", r.text


async def test_leaving_the_organisation_leaves_its_projects(app, api_client):
    team = await other_project(api_client)
    await _member(app, "usr_lv", "lv@example.com", "viewer")
    await bind(api_client, team, "builder", email="lv@example.com")
    async with app.state.db.sessionmaker() as s:
        await MemberRepo(s).remove(ORG, "usr_lv")
        s.add(Membership(org_id=ORG, user_id="usr_lv", role="viewer"))  # invited back later
        await s.commit()
        assert (await s.scalars(ProjectMember.__table__.select())).all() == []


async def test_an_invitation_added_to_a_project_carries_over_to_the_sign_in(app, api_client):
    """An admin invites someone who never signed in and adds them to a project; when they sign in with an account they
    already had, the project binding moves to it with the membership."""
    team = await other_project(api_client)
    assert (
        await api_client.put("/v1/org/members", json={"email": "new@example.com", "role": "viewer"})
    ).status_code == 200
    await bind(api_client, team, "builder", email="new@example.com")
    async with app.state.db.sessionmaker() as s:
        s.add(AppUser(id="usr_had", oidc_subject="oidc:had", email=""))
        await s.commit()
        user, _ = await UserRepo(s).provision("had", "new@example.com", None, email_verified=True)
        assert user.id == "usr_had"
        assert await s.get(AppUser, "usr_had") is not None
        assert (
            await s.scalar(AppUser.__table__.select().where(AppUser.oidc_subject == pending_subject("new@example.com")))
            is None
        )
        rows = (await s.execute(ProjectMember.__table__.select())).all()
    assert [(r.user_id, r.role) for r in rows] == [("usr_had", "builder")]


async def test_group_bindings_go_with_their_group(app, api_client):
    team = await other_project(api_client)
    async with app.state.db.sessionmaker() as s:
        s.add(ScimGroup(id="grp_ops", org_id=ORG, display_name="Ops"))
        await s.commit()
    await bind(api_client, team, "viewer", group="Ops")
    async with app.state.db.sessionmaker() as s:
        await s.delete(await s.get(ScimGroup, "grp_ops"))
        await s.commit()
    assert (await api_client.get(f"/v1/projects/{team}/members")).json() == []


async def test_approval_emails_go_only_to_people_who_see_the_project(app, triage_api, monkeypatch):  # noqa: F811
    """A private project's waiting item is announced to its reviewers (by their role there) and the organisation's
    admins, never to a reviewer of the organisation who is not in the project."""
    from datetime import UTC, datetime

    from test_notifier import Wire
    from test_versions import edit_node

    monkeypatch.setattr(app.state.settings, "smtp_host", "mail.example.com")
    monkeypatch.setattr(app.state.settings, "smtp_from", "AgentCanvas <approvals@example.com>")
    api, wf = triage_api, triage_api.wf_id
    assert (await api.put("/v1/org/notifications", json={"email": True})).status_code == 200
    project = (await api.get(f"/v1/workflows/{wf}")).json()["project_id"]
    await _member(app, "usr_in", "in@example.com", "viewer")  # a reviewer of this project only
    await _member(app, "usr_out", "out@example.com", "reviewer")  # a reviewer of the organisation, not in it
    await _member(app, "usr_grp", "grp@example.com", "viewer", groups=("Finance",))
    await bind(api, project, "reviewer", email="in@example.com")
    await private(api)
    await edit_node(api, wf, "n_ag", approvers={"roles": ["reviewer"], "groups": ["Finance"]})
    await _waiting(api, wf, "th_mail")
    wire = Wire(app.state.notifier)
    assert await app.state.notifier.tick(datetime.now(UTC)) == 1
    [(to, _, _)] = wire.mails
    assert to == ["in@example.com"]  # grp@ is in the group the step names, but not in the project


async def test_project_admins_read_and_set_their_projects_spending_caps(app, api_client, as_user):
    team = await other_project(api_client)
    r = await api_client.put("/v1/org/settings", json={"limits": {"max_cost_usd_month": 100}})
    assert r.status_code == 200, r.text
    await _member(app, "usr_cap", "cap@example.com", "viewer")
    await _member(app, "usr_bld", "bld@example.com", "viewer")
    await bind(api_client, team, "admin", email="cap@example.com")
    await bind(api_client, team, "builder", email="bld@example.com")
    as_user("usr_cap", "viewer")
    r = await api_client.put(f"/v1/projects/{team}/spending-caps", json={"max_cost_usd_month": 500})
    assert r.status_code == 422  # never above the organisation's
    r = await api_client.put(f"/v1/projects/{team}/spending-caps", json={"max_cost_usd_day": 5})
    assert r.status_code == 200, r.text
    caps = (await api_client.get(f"/v1/projects/{team}/spending-caps")).json()
    assert caps["own"] == {"max_cost_usd_day": 5.0, "max_cost_usd_month": None}
    assert caps["organisation"] == {"max_cost_usd_day": None, "max_cost_usd_month": 100.0}
    assert caps["day"]["limit_usd"] == 5.0 and caps["day"]["spent_usd"] == 0
    assert (await api_client.get(f"/v1/projects/{MAIN}/spending-caps")).status_code == 403  # a viewer there
    assert (await api_client.get("/v1/org/spending/caps")).status_code == 403  # the organisation's stay the admins'
    as_user("usr_bld", "viewer")
    assert (await api_client.get(f"/v1/projects/{team}/spending-caps")).status_code == 403
    r = await api_client.put(f"/v1/projects/{team}/spending-caps", json={"max_cost_usd_day": None})
    assert r.status_code == 403
