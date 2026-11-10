"""E-7: data-subject rights. Erase a member, erase a deployment's end user, export a person's data."""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select, update
from test_org_scope import ORG_B, act_as_b, org_b  # noqa: F401 - a fixture
from test_publish import bearer, make_greeter

from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.db.models import (
    AppUser,
    AuditLog,
    CopilotConversation,
    Membership,
    OAuthConnection,
    PersonalToken,
    ProjectMember,
    Run,
    StoredFile,
    Thread,
)
from agentcanvas.ir.models import Workflow

ORG, ANA = "org_default", "usr_ana"


async def ana_with_data(app, api_client) -> dict[str, str]:
    """Ana, a builder, with a conversation, a memory, a Copilot chat, a token, a sign-in and a file."""
    async with app.state.db.sessionmaker() as s:
        s.add(AppUser(id=ANA, oidc_subject="oidc:ana", email="ana@example.com", display_name="Ana", preferences={}))
        await s.flush()
        s.add(Membership(org_id=ORG, user_id=ANA, role="builder"))
        await s.commit()
    wf_id = await make_greeter(api_client)
    wf = Workflow.model_validate((await api_client.get(f"/v1/workflows/{wf_id}")).json()["ir"])
    hers = await app.state.runner.start(wf, input={"who": "Ana"}, user_id=ANA)
    mine = await app.state.runner.start(wf, input={"who": "Dev"}, user_id="usr_dev")
    await asyncio.wait_for(asyncio.gather(hers.done.wait(), mine.done.wait()), 10)
    store = app.state.runner.adapter.store
    await store.aput((f"org:{ORG}", "prefs", "user", ANA), "tea", {"value": "green"})
    await store.aput((f"org:{ORG}", "prefs", "user", "usr_dev"), "tea", {"value": "black"})
    async with app.state.db.sessionmaker() as s:
        s.add(CopilotConversation(id="cc_ana", workflow_id=wf_id, user_id=ANA, messages=[{"role": "user"}]))
        s.add(PersonalToken(id="tok_ana", org_id=ORG, user_id=ANA, name="ci", token_hash="h", prefix="act_x",
                            role="builder"))  # fmt: skip
        s.add(OAuthConnection(id="oc_ana", org_id=ORG, owner=ANA, resource="https://mcp", issuer="i",
                              token_endpoint="t", client_id="c", access_token_enc=b"x"))  # fmt: skip
        s.add(StoredFile(id="fil_ana", project_id="prj_default", name="cv.txt", media_type="text/plain", size=2,
                         sha256="x", data=b"hi", created_by=ANA))  # fmt: skip
        s.add(AuditLog(org_id=ORG, actor_id=ANA, action="create_workflow", target="-"))
        s.add(ProjectMember(id="pmb_ana", project_id="prj_default", user_id=ANA, role="builder"))
        await s.commit()
    return {"wf": wf_id, "hers": hers.thread_id, "mine": mine.thread_id}


async def _erase(api, user_id: str = ANA, **body):
    return await api.post(f"/v1/org/members/{user_id}/erase", json={"reason": "DSAR 42", **body})


async def test_erasing_a_member_removes_what_they_left_and_pseudonymises_them(app, api_client):
    made = await ana_with_data(app, api_client)
    r = await _erase(api_client, conversations=True)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "tokens": 1, "connections": 1, "memories": 1, "copilot_conversations": 1, "conversations": 1, "files": 1,
        "account_erased": True,
    }  # fmt: skip
    async with app.state.db.sessionmaker() as s:
        ana = await s.get(AppUser, ANA)
        assert ana is not None and ana.email == "" and ana.display_name == "Erased person" and ana.preferences is None
        assert ana.oidc_subject == f"erased:{ANA}"  # her next sign-in makes a new account
        assert await s.get(Membership, (ORG, ANA)) is None
        assert await s.get(Thread, made["hers"]) is None and await s.get(Thread, made["mine"]) is not None
        for table in (CopilotConversation, PersonalToken, OAuthConnection, StoredFile, ProjectMember):
            assert not await s.scalar(select(func.count()).select_from(table))
        # the audit log keeps her id, which now leads to no name or email
        assert await s.scalar(select(AuditLog.id).where(AuditLog.actor_id == ANA, AuditLog.action == "create_workflow"))
        entry = await s.scalar(select(AuditLog).where(AuditLog.action == "erase_member"))
        assert entry is not None and entry.target == f"user_id={ANA}" and entry.detail["reason"] == "DSAR 42"
        assert entry.detail["memories"] == 1 and "ana@example.com" not in str(entry.detail)
    store = app.state.runner.adapter.store
    assert await store.aget((f"org:{ORG}", "prefs", "user", ANA), "tea") is None
    assert await store.aget((f"org:{ORG}", "prefs", "user", "usr_dev"), "tea") is not None
    checkpointer = app.state.runner.adapter._persist().checkpointer
    assert await checkpointer.aget_tuple({"configurable": {"thread_id": made["hers"]}}) is None
    # a former member who acted here (and belongs nowhere) can be erased again: nothing is left, nothing fails
    again = await _erase(api_client)
    assert again.status_code == 200 and again.json()["memories"] == again.json()["tokens"] == 0


async def test_conversations_stay_unless_asked_and_a_member_elsewhere_keeps_the_account(app, api_client, org_b):  # noqa: F811
    made = await ana_with_data(app, api_client)
    async with app.state.db.sessionmaker() as s:
        s.add(Membership(org_id=ORG_B, user_id=ANA, role="viewer"))
        await s.commit()
    r = await _erase(api_client)
    assert r.status_code == 200, r.text
    assert r.json()["account_erased"] is False and r.json()["conversations"] == 0
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(AppUser, ANA)).email == "ana@example.com"  # type: ignore[union-attr]
        assert await s.get(Thread, made["hers"]) is not None
        assert await s.get(Membership, (ORG_B, ANA)) is not None


async def test_only_this_organisations_people_and_never_yourself(app, api_client, org_b):  # noqa: F811
    assert (await _erase(api_client, "usr_b")).status_code == 404  # another organisation's member
    assert (await _erase(api_client, "usr_nobody")).status_code == 404
    assert (await _erase(api_client, "usr_dev")).status_code == 409
    await ana_with_data(app, api_client)
    for role in ("builder", "viewer"):
        app.dependency_overrides[current_user] = lambda role=role: CurrentUser(id="usr_dev", org_id=ORG, role=role)
        try:
            assert (await _erase(api_client)).status_code == 403
        finally:
            app.dependency_overrides.pop(current_user)
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="usr_dev", org_id=ORG, role="admin", token_id="tok_x"
    )
    try:
        assert (await _erase(api_client)).status_code == 403  # an API token cannot
    finally:
        app.dependency_overrides.pop(current_user)
    assert (await api_client.post(f"/v1/org/members/{ANA}/erase", json={})).status_code == 422  # a reason


async def published(api) -> tuple[str, str, str]:
    wf = await make_greeter(api)
    pub = (await api.post(f"/v1/workflows/{wf}/publish", json={})).json()
    return wf, pub["deployment"]["id"], pub["api_key"]


async def talk(api, dep: str, key: str, user_id: str) -> str:
    r = await api.post(
        f"/v1/deployments/{dep}/runs/wait", json={"input": {"who": user_id}, "user_id": user_id}, headers=bearer(key)
    )
    assert r.status_code == 200, r.text
    return r.json()["thread_id"]


async def test_a_deployment_erases_its_end_user_by_the_user_id_it_sent(app, api_client):
    _, dep, key = await published(api_client)
    bob = [await talk(api_client, dep, key, "bob") for _ in range(3)]
    carol, lookalike = await talk(api_client, dep, key, "carol"), await talk(api_client, dep, key, "x:bob")
    async with app.state.db.sessionmaker() as s:
        thread = await s.get(Thread, bob[0])
        assert thread is not None and thread.end_user == f"api:{dep}:bob"
        # a conversation from before threads kept their end user: found by its runs' user
        await s.execute(update(Thread).where(Thread.id == bob[2]).values(end_user=None))
        await s.execute(update(Run).where(Run.thread_id == bob[2]).values(context={"uid": f"api:{dep}:bob"}))
        s.add(
            StoredFile(
                id="fil_01BOBSFILE0000000000000000",
                project_id="prj_default",
                name="id.png",
                media_type="image/png",
                size=1,
                sha256="x",
                data=b"x",
                source="api",
                deployment_id=dep,
            )
        )
        s.add(
            StoredFile(
                id="fil_01CAROLSFILE000000000000000",
                project_id="prj_default",
                name="c.png",
                media_type="image/png",
                size=1,
                sha256="x",
                data=b"x",
                source="api",
                deployment_id=dep,
            )
        )
        used = {"input": {"f": {"id": "fil_01BOBSFILE0000000000000000"}}}
        await s.execute(update(Run).where(Run.thread_id == bob[1]).values(recording=used))
        await s.commit()
    store = app.state.runner.adapter.store
    await store.aput((f"org:{ORG}", "prefs", "user", f"api:{dep}:bob"), "likes", {"value": "tea"})
    await store.aput((f"org:{ORG}", "prefs", "user", f"api:{dep}:carol"), "likes", {"value": "coffee"})

    erase = f"/v1/deployments/{dep}/end-users/erase"
    assert (await api_client.post(erase, json={"user_id": "bob"})).status_code == 401  # the key is required
    r = await api_client.post(erase, json={"user_id": "bob"}, headers=bearer(key))
    assert r.status_code == 200, r.text
    assert r.json() == {"conversations": 3, "memories": 1, "files": 1}
    async with app.state.db.sessionmaker() as s:
        for t in bob:
            assert await s.get(Thread, t) is None
        assert await s.get(Thread, carol) is not None and await s.get(Thread, lookalike) is not None
        assert await s.get(StoredFile, "fil_01CAROLSFILE000000000000000") is not None
        entry = await s.scalar(select(AuditLog).where(AuditLog.action == "api_erase_end_user"))
        assert entry is not None and entry.target.startswith("end_user_sha256=")
        assert "bob" not in entry.target + str(entry.detail) and entry.detail["caller"] == "deployment key"
    assert await store.aget((f"org:{ORG}", "prefs", "user", f"api:{dep}:bob"), "likes") is None
    assert await store.aget((f"org:{ORG}", "prefs", "user", f"api:{dep}:carol"), "likes") is not None
    again = await api_client.post(erase, json={"user_id": "bob"}, headers=bearer(key))
    assert again.json() == {"conversations": 0, "memories": 0, "files": 0}


async def test_an_admin_erases_an_end_user_across_every_deployment(app, api_client, org_b):  # noqa: F811
    _, dep1, key1 = await published(api_client)
    _, dep2, key2 = await published(api_client)
    t1, t2 = await talk(api_client, dep1, key1, "carol"), await talk(api_client, dep2, key2, "carol")
    body = {"user_id": "carol", "reason": "DSAR 7"}
    r = await api_client.post("/v1/org/end-users/erase", json={**body, "deployment_id": dep1})
    assert r.status_code == 200 and r.json()["conversations"] == 1
    r = await api_client.post("/v1/org/end-users/erase", json=body)
    assert r.status_code == 200 and r.json()["conversations"] == 1
    async with app.state.db.sessionmaker() as s:
        assert await s.get(Thread, t1) is None and await s.get(Thread, t2) is None
    assert (
        await api_client.post("/v1/org/end-users/erase", json={**body, "deployment_id": "dep_x"})
    ).status_code == 404
    act_as_b(app)
    try:  # another organisation's admin: not their deployment, and nothing of theirs to erase
        assert (
            await api_client.post("/v1/org/end-users/erase", json={**body, "deployment_id": dep1})
        ).status_code == 404
    finally:
        app.dependency_overrides.pop(current_user)
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_dev", org_id=ORG, role="builder")
    try:
        assert (await api_client.post("/v1/org/end-users/erase", json=body)).status_code == 403
    finally:
        app.dependency_overrides.pop(current_user)


async def test_an_admin_exports_a_members_data_and_the_log_says_so(app, api_client, org_b):  # noqa: F811
    made = await ana_with_data(app, api_client)
    async with app.state.db.sessionmaker() as s:
        s.add(Membership(org_id=ORG_B, user_id=ANA, role="viewer"))
        await s.commit()
    r = await api_client.get(f"/v1/org/members/{ANA}/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"].startswith("attachment;")
    data = r.json()
    assert data["person"]["email"] == "ana@example.com"
    assert [m["org_id"] for m in data["memberships"]] == [ORG]  # not where else she belongs
    assert [(b["project_id"], b["role"]) for b in data["project_memberships"]] == [("prj_default", "builder")]
    assert [t["id"] for t in data["api_tokens"]] == ["tok_ana"] and "token_hash" not in data["api_tokens"][0]
    assert [c["id"] for c in data["conversations"]] == [made["hers"]]
    run = data["conversations"][0]["runs"][0]
    assert run["status"] == "success" and run["output"]["out"] == "Hello Ana" and run["input"] == {"who": "Ana"}
    assert [m["value"] for m in data["memories"]] == [{"value": "green"}]
    assert data["memories"][0]["namespace"] == ["prefs", "user", ANA]
    assert [c["id"] for c in data["copilot_conversations"]] == ["cc_ana"]
    assert [f["id"] for f in data["files"]] == ["fil_ana"] and [c["id"] for c in data["connections"]] == ["oc_ana"]
    assert [a["action"] for a in data["audit_log"]] == ["create_workflow"]
    async with app.state.db.sessionmaker() as s:
        entry = await s.scalar(select(AuditLog).where(AuditLog.action == "export_member"))
        assert entry is not None and entry.actor_id == "usr_dev" and entry.target == f"user_id={ANA}"
    assert (await api_client.get("/v1/org/members/usr_b/export")).status_code == 404  # another organisation's
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_dev", org_id=ORG, role="builder")
    try:
        assert (await api_client.get(f"/v1/org/members/{ANA}/export")).status_code == 403
    finally:
        app.dependency_overrides.pop(current_user)


async def test_everyone_exports_their_own_data(app, api_client, org_b):  # noqa: F811
    made = await ana_with_data(app, api_client)
    async with app.state.db.sessionmaker() as s:
        s.add(Membership(org_id=ORG_B, user_id=ANA, role="viewer"))
        await s.commit()
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id=ANA, org_id=ORG, role="viewer", memberships={ORG: "viewer", ORG_B: "viewer"}
    )
    try:
        r = await api_client.get("/v1/me/export")
    finally:
        app.dependency_overrides.pop(current_user)
    assert r.status_code == 200, r.text
    data = r.json()
    assert sorted(m["org_id"] for m in data["memberships"]) == sorted([ORG, ORG_B])  # all of her own
    assert [c["id"] for c in data["conversations"]] == [made["hers"]]
    async with app.state.db.sessionmaker() as s:
        assert await s.scalar(select(AuditLog.id).where(AuditLog.action == "export_member", AuditLog.actor_id == ANA))


async def test_an_end_users_data_is_exported_by_key_or_by_an_admin(app, api_client):
    _, dep, key = await published(api_client)
    bob, carol = await talk(api_client, dep, key, "bob"), await talk(api_client, dep, key, "carol")
    await app.state.runner.adapter.store.aput((f"org:{ORG}", "prefs", "user", f"api:{dep}:bob"), "k", {"value": 1})
    r = await api_client.post(f"/v1/deployments/{dep}/end-users/export", json={"user_id": "bob"}, headers=bearer(key))
    assert r.status_code == 200, r.text
    assert [c["id"] for c in r.json()["conversations"]] == [bob] and len(r.json()["memories"]) == 1
    assert r.json()["conversations"][0]["runs"][0]["output"]["out"] == "Hello bob"
    r = await api_client.post("/v1/org/end-users/export", json={"user_id": "carol", "reason": "DSAR 9"})
    assert r.status_code == 200 and [c["id"] for c in r.json()["conversations"]] == [carol]
    assert r.json()["memories"] == []
    async with app.state.db.sessionmaker() as s:
        actions = set((await s.scalars(select(AuditLog.action))).all())
        assert {"api_export_end_user", "export_end_user"} <= actions
    no_key = await api_client.post(f"/v1/deployments/{dep}/end-users/export", json={"user_id": "bob"})
    assert no_key.status_code == 401
