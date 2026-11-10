"""E-8: several named, expiring API keys per deployment; rotation without downtime; when each key was last used."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text, update
from test_publish import bearer, make_greeter

from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.db.models import AuditLog, DeploymentKey
from agentcanvas.db.session import run_alembic
from agentcanvas.services.deployments import MAX_KEYS, hash_key


async def _published(api) -> tuple[str, str, str]:
    """A published greeter: (workflow id, deployment id, its first key)."""
    wf = await make_greeter(api)
    pub = (await api.post(f"/v1/workflows/{wf}/publish", json={})).json()
    return wf, pub["deployment"]["id"], pub["api_key"]


async def _call(api, dep: str, key: str):
    return await api.post(f"/v1/deployments/{dep}/runs/wait", json={"input": {"who": "Ada"}}, headers=bearer(key))


async def test_a_deployment_has_several_keys_and_each_is_revoked_on_its_own(api_client):
    _, dep, first = await _published(api_client)
    keys = (await api_client.get(f"/v1/deployments/{dep}/keys")).json()
    assert [(k["name"], k["prefix"], k["active"], k["created_by"]) for k in keys] == [
        ("default", first[:10], True, "usr_dev")
    ]
    made = await api_client.post(f"/v1/deployments/{dep}/keys", json={"name": "  CI pipeline ", "expires_in_days": 30})
    assert made.status_code == 201, made.text
    second = made.json()
    assert second["name"] == "CI pipeline" and second["api_key"].startswith("ac_")
    assert second["prefix"] == second["api_key"][:10] and second["expires_at"] is not None
    assert (await _call(api_client, dep, first)).status_code == 200
    assert (await _call(api_client, dep, second["api_key"])).status_code == 200

    listed = (await api_client.get(f"/v1/deployments/{dep}/keys")).json()
    assert [k["name"] for k in listed] == ["CI pipeline", "default"]  # newest first
    assert all("api_key" not in k for k in listed)  # shown once, never listed
    deps = (await api_client.get(f"/v1/workflows/{(await _wf_of(api_client, dep))}/deployments")).json()
    assert deps[0]["key_prefix"] == second["prefix"]  # the newest working key

    assert (await api_client.delete(f"/v1/deployments/{dep}/keys/{keys[0]['id']}")).status_code == 204
    refused = await _call(api_client, dep, first)
    assert refused.status_code == 401 and "was revoked" in refused.json()["detail"]
    assert (await _call(api_client, dep, second["api_key"])).status_code == 200
    assert (await api_client.delete(f"/v1/deployments/{dep}/keys/{keys[0]['id']}")).status_code == 204  # no-op
    assert (await api_client.delete(f"/v1/deployments/{dep}/keys/dky_missing")).status_code == 404


async def _wf_of(api, dep: str) -> str:
    for wf in (await api.get("/v1/projects/prj_default/workflows")).json()["items"]:
        if any(d["id"] == dep for d in (await api.get(f"/v1/workflows/{wf['id']}/deployments")).json()):
            return wf["id"]
    raise AssertionError(dep)


async def test_an_expired_key_is_refused_and_says_so(app, api_client):
    _, dep, key = await _published(api_client)
    async with app.state.db.sessionmaker() as s:
        await s.execute(
            update(DeploymentKey)
            .where(DeploymentKey.key_hash == hash_key(key))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()
    r = await _call(api_client, dep, key)
    assert r.status_code == 401 and "expired" in r.json()["detail"]
    assert [k["active"] for k in (await api_client.get(f"/v1/deployments/{dep}/keys")).json()] == [False]


async def test_a_key_opens_only_its_own_deployment(api_client):
    _, dep_a, key_a = await _published(api_client)
    _, dep_b, key_b = await _published(api_client)
    r = await _call(api_client, dep_b, key_a)
    assert r.status_code == 401 and r.json()["detail"].startswith("a valid API key for this deployment")
    assert (await _call(api_client, dep_b, key_b)).status_code == 200
    # another deployment's key id cannot be revoked through this one
    other = (await api_client.get(f"/v1/deployments/{dep_a}/keys")).json()[0]["id"]
    assert (await api_client.delete(f"/v1/deployments/{dep_b}/keys/{other}")).status_code == 404
    assert (await _call(api_client, dep_a, key_a)).status_code == 200


async def test_rotating_with_an_overlap_keeps_the_old_key_working_meanwhile(api_client):
    _, dep, old = await _published(api_client)
    r = await api_client.post(f"/v1/deployments/{dep}/rotate-key", json={"overlap_hours": 24})
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["key"]["prefix"] == new["api_key"][:10] and new["key"]["name"].startswith("rotated ")
    assert (await _call(api_client, dep, old)).status_code == 200  # callers move over meanwhile
    assert (await _call(api_client, dep, new["api_key"])).status_code == 200
    keys = {k["prefix"]: k for k in (await api_client.get(f"/v1/deployments/{dep}/keys")).json()}
    ends = datetime.fromisoformat(keys[old[:10]]["expires_at"])
    assert timedelta(hours=23) < ends - datetime.now(UTC) <= timedelta(hours=24)
    assert keys[new["api_key"][:10]]["expires_at"] is None

    # without an overlap (the old behaviour) every other key stops at once
    newest = (await api_client.post(f"/v1/deployments/{dep}/rotate-key")).json()["api_key"]
    assert (await _call(api_client, dep, old)).status_code == 401
    assert (await _call(api_client, dep, new["api_key"])).status_code == 401
    assert (await _call(api_client, dep, newest)).status_code == 200
    assert (await api_client.post(f"/v1/deployments/{dep}/rotate-key", json={"overlap_hours": 721})).status_code == 422


async def test_two_rotations_at_once_leave_one_working_key(api_client):
    """Each rotation revokes the others: run at once, they take turns, so the last one's key works."""
    _, dep, _ = await _published(api_client)
    a, b = await asyncio.gather(*(api_client.post(f"/v1/deployments/{dep}/rotate-key") for _ in range(2)))
    assert a.status_code == b.status_code == 200
    works = [(await _call(api_client, dep, r.json()["api_key"])).status_code for r in (a, b)]
    assert sorted(works) == [200, 401]
    assert [k["active"] for k in (await api_client.get(f"/v1/deployments/{dep}/keys")).json()].count(True) == 1


async def test_last_use_is_written_at_most_once_a_minute(app, api_client):
    _, dep, key = await _published(api_client)

    async def last_used() -> datetime | None:
        async with app.state.db.sessionmaker() as s:
            return await s.scalar(select(DeploymentKey.last_used_at).where(DeploymentKey.key_hash == hash_key(key)))

    assert await last_used() is None
    assert (await _call(api_client, dep, key)).status_code == 200
    first = await last_used()
    assert first is not None
    assert (await _call(api_client, dep, key)).status_code == 200
    assert await last_used() == first  # within the minute: not written again
    async with app.state.db.sessionmaker() as s:
        earlier = datetime.now(UTC) - timedelta(minutes=2)
        await s.execute(
            update(DeploymentKey).where(DeploymentKey.key_hash == hash_key(key)).values(last_used_at=earlier)
        )
        await s.commit()
    assert (await _call(api_client, dep, key)).status_code == 200
    assert (await last_used()) > first  # type: ignore[operator]
    listed = (await api_client.get(f"/v1/deployments/{dep}/keys")).json()[0]
    assert listed["last_used_at"] is not None


async def test_unpublishing_revokes_every_key_and_a_new_key_needs_a_publish(api_client):
    wf, dep, key = await _published(api_client)
    extra = (await api_client.post(f"/v1/deployments/{dep}/keys", json={"name": "spare"})).json()["api_key"]
    assert (await api_client.delete(f"/v1/deployments/{dep}")).status_code == 204
    assert all(k["revoked_at"] for k in (await api_client.get(f"/v1/deployments/{dep}/keys")).json())
    r = await api_client.post(f"/v1/deployments/{dep}/keys", json={"name": "late"})
    assert r.status_code == 409 and "publish it again" in r.json()["detail"]
    assert (await api_client.post(f"/v1/deployments/{dep}/rotate-key")).status_code == 409
    again = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()
    assert (await _call(api_client, dep, again["api_key"])).status_code == 200
    for dead in (key, extra):
        assert (await _call(api_client, dep, dead)).status_code == 401


async def test_a_deployment_has_a_bounded_number_of_working_keys(api_client):
    _, dep, _ = await _published(api_client)
    for i in range(MAX_KEYS - 1):
        assert (await api_client.post(f"/v1/deployments/{dep}/keys", json={"name": f"k{i}"})).status_code == 201
    r = await api_client.post(f"/v1/deployments/{dep}/keys", json={"name": "one too many"})
    assert r.status_code == 409 and "revoke one first" in r.json()["detail"]
    assert (await api_client.post(f"/v1/deployments/{dep}/keys", json={"name": " "})).status_code == 422


async def test_keys_are_for_builders_and_their_changes_are_audited(app, api_client):
    _, dep, _ = await _published(api_client)
    made = (await api_client.post(f"/v1/deployments/{dep}/keys", json={"name": "partner"})).json()
    assert (await api_client.delete(f"/v1/deployments/{dep}/keys/{made['id']}")).status_code == 204
    async with app.state.db.sessionmaker() as s:
        rows = list(await s.scalars(select(AuditLog).where(AuditLog.action.like("%deployment_key"))))
    assert [r.action for r in sorted(rows, key=lambda r: r.id)] == ["create_deployment_key", "revoke_deployment_key"]
    assert all(r.detail["prefix"] == made["prefix"] and made["api_key"] not in str(r.detail) for r in rows)

    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="usr_dev", org_id="org_default", role="viewer", memberships={"org_default": "viewer"}
    )
    try:
        assert (await api_client.get(f"/v1/deployments/{dep}/keys")).status_code == 403
        assert (await api_client.post(f"/v1/deployments/{dep}/keys", json={"name": "x"})).status_code == 403
    finally:
        app.dependency_overrides.pop(current_user)


async def test_the_key_of_the_previous_release_survives_the_upgrade_and_a_downgrade(settings, tmp_path):
    """The migration copies `deployment.api_key_hash` into `deployment_key`; its downgrade puts the newest working key
    back, so the previous release still accepts it."""
    from contextlib import asynccontextmanager

    import httpx
    from sqlalchemy.ext.asyncio import create_async_engine

    from agentcanvas.api.main import create_app
    from agentcanvas.db.session import async_url

    @asynccontextmanager
    async def started():
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                yield c

    async with started() as api:
        _, dep, key = await _published(api)
    await asyncio.to_thread(run_alembic, settings.database_url, "downgrade", "1ec6eb9f62ed")
    engine = create_async_engine(async_url(settings.database_url))
    async with engine.begin() as c:
        row = (await c.execute(text("SELECT api_key_hash, key_prefix FROM deployment WHERE id = :d"), {"d": dep})).one()
    await engine.dispose()
    assert tuple(row) == (hash_key(key), key[:10])
    async with started() as api:  # migrates up again
        assert (await _call(api, dep, key)).status_code == 200
        keys = (await api.get(f"/v1/deployments/{dep}/keys")).json()
        assert [(k["name"], k["prefix"], k["active"]) for k in keys] == [("default", key[:10], True)]
