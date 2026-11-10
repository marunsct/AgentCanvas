"""E-7: deleting an organisation. Suspended at once, deleted for good after the grace period, by any replica."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from test_org_scope import ORG_B, PROJECT_B, act_as_b, org_b  # noqa: F401 - a fixture
from test_publish import bearer, greeter, make_greeter

from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.db import rls
from agentcanvas.db.models import AppUser, AuditLog, Membership, Organization, PersonalToken, Thread
from agentcanvas.errors import Forbidden
from agentcanvas.ir.models import Workflow

ORG = "org_default"
NAME = "Default organisation"


async def _delete(api, confirm: str = NAME, reason: str = "contract ended"):
    return await api.request("DELETE", "/v1/org", json={"confirm": confirm, "reason": reason})


def owned(table: str) -> str:
    """SQL: the rows of `table` that belong to the organisation `:org`, following the row-level security chain."""
    column, parent = rls.TENANT_TABLES[table]
    if parent is None:
        return f'"{table}".{column} = :org'
    return f'"{table}".{column} IN (SELECT "{parent}".id FROM "{parent}" WHERE {owned(parent)})'


async def rows_of(app, org_id: str) -> dict[str, int]:
    """Every tenant table's rows of an organisation (audit-log entries of its deletion apart)."""
    found = {}
    async with app.state.db.sessionmaker() as s:
        for table in rls.TENANT_TABLES:
            where = owned(table)
            if table == "audit_log":
                where += " AND action NOT IN ('delete_organization', 'purge_organization')"
            n = await s.scalar(text(f'SELECT count(*) FROM "{table}" WHERE {where}'), {"org": org_id})
            if n:
                found[table] = n
    return found


async def test_deleting_needs_the_typed_name_an_admin_and_a_real_sign_in(app, api_client):
    assert (await _delete(api_client, confirm="default organisation")).status_code == 422
    assert (await api_client.request("DELETE", "/v1/org", json={"confirm": NAME})).status_code == 422  # a reason
    for role in ("builder", "viewer"):
        app.dependency_overrides[current_user] = lambda role=role: CurrentUser(id="usr_dev", org_id=ORG, role=role)
        try:
            assert (await _delete(api_client)).status_code == 403
            assert (await api_client.get("/v1/org/deletion")).status_code == 403
        finally:
            app.dependency_overrides.pop(current_user)
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="usr_dev", org_id=ORG, role="admin", token_id="tok_x"
    )
    try:
        r = await _delete(api_client)
        assert r.status_code == 403 and "API token" in r.json()["detail"]
    finally:
        app.dependency_overrides.pop(current_user)
    assert (await api_client.get("/v1/org/deletion")).json()["scheduled"] is False


async def test_a_deleted_organisation_is_suspended_at_once_and_can_be_taken_back(app, api_client):
    wf_id = await make_greeter(api_client)
    pub = (await api_client.post(f"/v1/workflows/{wf_id}/publish", json={})).json()
    dep, key = pub["deployment"]["id"], pub["api_key"]
    share = (await api_client.put(f"/v1/deployments/{dep}/share", json={"enabled": True})).json()["token"]
    wf = Workflow.model_validate((await api_client.get(f"/v1/workflows/{wf_id}")).json()["ir"])

    r = await _delete(api_client)
    assert r.status_code == 202, r.text
    due = datetime.fromisoformat(r.json()["delete_after"])
    assert r.json()["scheduled"] and timedelta(days=6.9) < due - datetime.now(UTC) < timedelta(days=7.1)
    assert (await _delete(api_client)).status_code == 403  # suspended: once is all it takes

    refused = await api_client.get("/v1/projects")
    assert refused.status_code == 403 and refused.json()["code"] == "organisation_deleting"
    assert "Settings > Organisation" in refused.json()["detail"]
    call = f"/v1/deployments/{dep}/runs/wait"
    assert (await api_client.post(call, json={"input": {"who": "Ada"}}, headers=bearer(key))).status_code == 403
    assert (await api_client.get(f"/v1/share/{share}")).status_code == 404
    with pytest.raises(Forbidden):  # schedules, events and hand-overs start runs here
        await app.state.runner.start(wf, input={"who": "Ada"})
    # what an admin may still do
    assert (await api_client.get("/v1/me")).status_code == 200
    assert (await api_client.get("/v1/org/deletion")).json()["requested_by"] == "usr_dev"

    back = await api_client.delete("/v1/org/deletion")
    assert back.status_code == 200 and back.json()["scheduled"] is False
    assert (await api_client.get("/v1/projects")).status_code == 200
    assert (await api_client.post(call, json={"input": {"who": "Ada"}}, headers=bearer(key))).status_code == 200
    assert (await api_client.get(f"/v1/share/{share}")).status_code == 200
    assert (await api_client.delete("/v1/org/deletion")).status_code == 409  # nothing to take back
    actions = [e["action"] for e in (await api_client.get("/v1/org/audit")).json()]
    assert {"delete_organization", "cancel_organization_deletion"} <= set(actions)
    entry = next(e for e in (await api_client.get("/v1/org/audit")).json() if e["action"] == "delete_organization")
    assert entry["detail"]["reason"] == "contract ended"


async def test_deleting_stops_the_runs_in_flight(app, api_client):
    from agentcanvas.db.models import Run

    wf_id = await make_greeter(api_client)
    wf = Workflow.model_validate((await api_client.get(f"/v1/workflows/{wf_id}")).json()["ir"])
    h = await app.state.runner.start(wf, input={"who": "Ada"})
    await asyncio.wait_for(h.done.wait(), 10)
    async with app.state.db.sessionmaker() as s:  # a run another server is running
        await s.execute(text("UPDATE run SET status = 'running' WHERE id = :r"), {"r": h.run_id})
        await s.commit()
    assert (await _delete(api_client)).status_code == 202
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(Run, h.run_id)).stop_requested  # type: ignore[union-attr]


async def test_only_the_organisation_being_deleted_is_closed(app, api_client, org_b):  # noqa: F811
    """What a sign-in passes over when it picks someone's organisation (the one used last, unless it is closed)."""
    from agentcanvas.services import offboarding

    assert (await _delete(api_client)).status_code == 202
    async with app.state.db.sessionmaker() as s:
        assert await offboarding.closed_among(s, [ORG, ORG_B]) == {ORG}
        assert await offboarding.closed(s, ORG_B) is None


async def test_the_job_deletes_everything_after_the_grace_period(app, api_client, org_b, settings):  # noqa: F811
    """Rows of every tenant table, checkpoints, memories and workspaces go; the organisation stays as a tombstone with
    its deletion's audit entries; another organisation keeps all of its own."""
    wf_id = await make_greeter(api_client)
    wf = Workflow.model_validate((await api_client.get(f"/v1/workflows/{wf_id}")).json()["ir"])
    h = await app.state.runner.start(wf, input={"who": "Ada"})
    await asyncio.wait_for(h.done.wait(), 10)
    pub = (await api_client.post(f"/v1/workflows/{wf_id}/publish", json={})).json()
    await api_client.put(f"/v1/deployments/{pub['deployment']['id']}/share", json={"enabled": True})
    assert (await api_client.put("/v1/projects/prj_default/secrets/TOKEN", json={"value": "s3cret"})).status_code == 204
    w = await api_client.put("/v1/projects/prj_default/files/scripts/x.py", json={"content": "x = 1"})
    assert w.status_code in (200, 201, 204), w.text
    tok = await api_client.post("/v1/me/tokens", json={"name": "ci", "role": "viewer"})
    assert tok.status_code == 201, tok.text
    policy = {"name": "p", "require": "true"}
    assert (await api_client.post("/v1/org/policies", json=policy)).status_code == 201
    store = app.state.runner.adapter.store
    await store.aput((f"org:{ORG}", "prefs", "user", "usr_dev"), "tea", {"value": "green"})
    await store.aput((f"org:{ORG_B}", "prefs", "user", "usr_b"), "tea", {"value": "black"})
    act_as_b(app)
    try:
        wf_b = (await api_client.post(f"/v1/projects/{PROJECT_B}/workflows", json={"name": "B flow"})).json()["id"]
        put = await api_client.put(f"/v1/workflows/{wf_b}", json={"ir": greeter(wf_b, "Hi")}, headers={"If-Match": "1"})
        assert put.status_code == 200, put.text
    finally:
        app.dependency_overrides.pop(current_user)
    hb = await app.state.runner.start(Workflow.model_validate(greeter(wf_b, "Hi")), input={"who": "Bo"})
    await asyncio.wait_for(hb.done.wait(), 10)
    b_before = await rows_of(app, ORG_B)
    workspace = Path(settings.workspaces_dir) / "prj_default"
    assert workspace.exists()
    assert await rows_of(app, ORG)

    assert (await _delete(api_client)).status_code == 202
    job = app.state.offboarding
    assert await job.tick(datetime.now(UTC)) == []  # not before the grace period ends
    later = datetime.now(UTC) + timedelta(days=8)
    assert await job.tick(later) == [ORG]
    assert await job.tick(later) == []  # once

    assert await rows_of(app, ORG) == {}
    assert await rows_of(app, ORG_B) == b_before  # the other organisation keeps everything
    checkpointer = app.state.runner.adapter._persist().checkpointer
    assert await checkpointer.aget_tuple({"configurable": {"thread_id": h.thread_id}}) is None
    assert await checkpointer.aget_tuple({"configurable": {"thread_id": hb.thread_id}}) is not None
    assert await store.aget((f"org:{ORG}", "prefs", "user", "usr_dev"), "tea") is None
    assert await store.aget((f"org:{ORG_B}", "prefs", "user", "usr_b"), "tea") is not None
    assert not workspace.exists()
    async with app.state.db.sessionmaker() as s:
        org = await s.get(Organization, ORG)
        assert org is not None and org.deleted_at is not None and org.name == "Deleted organisation"
        assert org.settings is None and org.purge_lease_until is None
        dev = await s.get(AppUser, "usr_dev")  # a member of no other organisation: pseudonymised
        assert dev is not None and dev.email == "" and dev.oidc_subject == "erased:usr_dev"
        assert (await s.get(AppUser, "usr_b")).email == "b@example.com"  # type: ignore[union-attr]
        assert not await s.scalar(select(func.count()).select_from(Membership).where(Membership.org_id == ORG))
        assert not await s.scalar(select(func.count()).select_from(PersonalToken))
        kept = (await s.scalars(select(AuditLog.action).where(AuditLog.org_id == ORG))).all()
        assert sorted(kept) == ["delete_organization", "purge_organization"]
        purged = await s.scalar(select(AuditLog).where(AuditLog.action == "purge_organization"))
        assert purged is not None and purged.detail["counts"]["conversations"] >= 1
        assert await s.scalar(select(func.count()).select_from(Thread).where(Thread.workflow_id == wf_b)) == 1

    gone = await api_client.get("/v1/org/deletion")
    assert gone.status_code == 403 and gone.json()["code"] == "organisation_deleted"


async def test_one_replica_at_a_time_and_a_cut_short_purge_is_finished_later(app, api_client):
    await make_greeter(api_client)
    assert (await _delete(api_client)).status_code == 202
    job = app.state.offboarding
    later = datetime.now(UTC) + timedelta(days=8)
    assert await job._claim(ORG, later)  # another replica holds it
    assert await job.purge(ORG, later) is None
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(Organization, ORG)).deleted_at is None  # type: ignore[union-attr]
    assert await job.tick(later + timedelta(minutes=11)) == [ORG]  # its hold ran out: this one finishes the work
    assert await rows_of(app, ORG) == {}
    assert (await api_client.delete("/v1/org/deletion")).status_code == 403  # deleted: nothing to take back
