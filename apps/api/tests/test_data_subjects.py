"""E-7: data-subject rights. Erase a member, erase a deployment's end user, export a person's data."""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select
from test_org_scope import ORG_B, org_b  # noqa: F401 - a fixture
from test_publish import make_greeter

from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.db.models import (
    AppUser,
    AuditLog,
    CopilotConversation,
    Membership,
    OAuthConnection,
    PersonalToken,
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
        for table in (CopilotConversation, PersonalToken, OAuthConnection, StoredFile):
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
