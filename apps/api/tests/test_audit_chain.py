"""The audit log's hash chain (E-3): each organisation's entries are linked, and verifying finds an entry that was
changed, deleted or slipped in afterwards; retention deletes the start of a chain without breaking it."""

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.db.models import AuditAnchor, AuditLog, Organization
from agentcanvas.db.repositories import AuditRepo
from agentcanvas.db.session import async_url
from agentcanvas.services import audit_chain


async def verify(api) -> dict:
    r = await api.get("/v1/org/audit/verify")
    assert r.status_code == 200, r.text
    return r.json()


async def changes(api, n: int, prefix: str = "W") -> None:
    for i in range(n):
        assert (await api.post("/v1/projects/prj_default/workflows", json={"name": f"{prefix}{i}"})).status_code == 201


async def entries(app, org_id: str = "org_default") -> list[AuditLog]:
    async with app.state.db.sessionmaker() as s:
        return list((await s.scalars(select(AuditLog).where(AuditLog.org_id == org_id).order_by(AuditLog.id))).all())


async def test_entries_are_chained_and_the_chain_verifies(app, api_client):
    await changes(api_client, 3)
    rows = await entries(app)
    assert len(rows) == 3 and rows[0].prev_hash is None
    assert [r.prev_hash for r in rows[1:]] == [r.row_hash for r in rows[:-1]]
    assert all(r.row_hash == audit_chain.row_hash(r) for r in rows)
    report = await verify(api_client)
    assert report["ok"] and report["checked"] == 3 and report["problems"] == []
    assert report["head_hash"] == rows[-1].row_hash and report["last_id"] == rows[-1].id


async def test_a_changed_entry_is_found(app, api_client):
    await changes(api_client, 3)
    middle = (await entries(app))[1]
    async with app.state.db.sessionmaker() as s:  # someone with database access edits history
        await s.execute(update(AuditLog).where(AuditLog.id == middle.id).values(target="name=innocent"))
        await s.commit()
    report = await verify(api_client)
    assert not report["ok"]
    assert [(p["id"], p["kind"]) for p in report["problems"]] == [(middle.id, "changed")]


async def test_a_deleted_entry_is_found(app, api_client):
    await changes(api_client, 3)
    _, middle, last = await entries(app)
    async with app.state.db.sessionmaker() as s:
        await s.execute(delete(AuditLog).where(AuditLog.id == middle.id))
        await s.commit()
    report = await verify(api_client)
    assert not report["ok"] and [(p["id"], p["kind"]) for p in report["problems"]] == [(last.id, "missing")]


async def test_an_entry_slipped_in_without_a_hash_is_found_and_the_chain_goes_on(app, api_client):
    await changes(api_client, 1)
    async with app.state.db.sessionmaker() as s:
        forged = AuditLog(org_id="org_default", action="publish", target="wf_id=x", detail={})
        s.add(forged)
        await s.commit()
    await changes(api_client, 1, prefix="after")
    report = await verify(api_client)
    assert [(p["id"], p["kind"]) for p in report["problems"]] == [(forged.id, "unchained")]
    assert report["checked"] == 2  # the next entry still links to the last chained one


async def test_entries_written_before_the_chain_are_counted_not_flagged(app, api_client):
    async with app.state.db.sessionmaker() as s:
        s.add(AuditLog(org_id="org_default", action="old", target="-", detail={}))
        await s.commit()
    await changes(api_client, 2)
    report = await verify(api_client)
    assert report["ok"] and report["unchained"] == 1 and report["checked"] == 2


async def test_details_hash_as_they_are_stored(app, api_client):
    """Postgres keeps JSON numbers its own way (1e20 comes back as an integer): the hash covers what is read back."""
    detail = {"big": 1e20, "one": 1.0, "n": 3, "x": 0.1, "flag": True, "none": None, "list": (1, "a"), "k": {"2": [{}]}}
    async with app.state.db.sessionmaker() as s:
        await AuditRepo(s).add(AuditLog(org_id="org_default", action="put_secret", target="name=X", detail=detail))
    report = await verify(api_client)
    assert report["ok"], report


async def test_retention_deletes_the_start_of_a_chain_without_breaking_it(app, api_client):
    await changes(api_client, 4)
    rows = await entries(app)
    async with app.state.db.sessionmaker() as s:  # the first two are past retention
        for r in rows[:2]:
            await s.execute(
                update(AuditLog).where(AuditLog.id == r.id).values(created_at=datetime.now(UTC) - timedelta(days=401))
            )
        await s.commit()
    # the update above changed the dates the hashes cover: that is the point of the chain, so give the rows their
    # hashes back as if they had been written then
    async with app.state.db.sessionmaker() as s:
        prev = None
        for r in await s.scalars(select(AuditLog).where(AuditLog.org_id == "org_default").order_by(AuditLog.id)):
            r.prev_hash = prev
            r.row_hash = prev = audit_chain.row_hash(r)
        await s.commit()
    assert (await verify(api_client))["ok"]
    assert await app.state.retention.expire_audit(datetime.now(UTC)) == 2
    report = await verify(api_client)
    assert report["ok"], report
    assert report["checked"] == 2 and report["anchor"]["through_id"] == rows[1].id and report["anchor"]["rows"] == 2
    await changes(api_client, 1, prefix="later")
    assert (await verify(api_client))["ok"]

    # everything expires: the next entry links to where retention left the chain
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(AuditLog).values(created_at=datetime.now(UTC) - timedelta(days=401)))
        await s.commit()
    await app.state.retention.expire_audit(datetime.now(UTC))
    assert await entries(app) == []
    async with app.state.db.sessionmaker() as s:
        anchor = (await s.scalars(select(AuditAnchor).order_by(AuditAnchor.id.desc()))).first()
    await changes(api_client, 1, prefix="fresh")
    [fresh] = await entries(app)
    assert anchor is not None and fresh.prev_hash == anchor.last_hash
    assert (await verify(api_client))["ok"]


async def test_each_organisation_has_its_own_chain(app, api_client):
    async with app.state.db.sessionmaker() as s:
        s.add(Organization(id="org_b", name="B"))
        await s.commit()
        for i in range(2):
            await AuditRepo(s).add(AuditLog(org_id="org_b", action="put_secret", target=f"name=B{i}", detail={}))
    await changes(api_client, 2)
    b = await entries(app, "org_b")
    assert b[0].prev_hash is None and b[1].prev_hash == b[0].row_hash
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(AuditLog).where(AuditLog.id == b[1].id).values(target="name=other"))
        await s.commit()
    assert (await verify(api_client))["ok"]  # org_default's chain: B's tampering is not its concern
    async with app.state.db.sessionmaker() as s:
        report = await audit_chain.verify(s, "org_b")
    assert not report.ok and report.problems[0]["id"] == b[1].id


async def test_entries_written_at_once_by_several_servers_form_one_chain(app, api_client):
    """Each write holds the organisation's row while it reads the chain's head (Postgres; on SQLite the database's
    write lock), so concurrent writers cannot both link to the same entry."""

    async def write(i: int) -> None:
        async with app.state.db.sessionmaker() as s:
            await AuditRepo(s).add(AuditLog(org_id="org_default", action="put_secret", target=f"name=S{i}", detail={}))

    await asyncio.gather(*(write(i) for i in range(20)))
    rows = await entries(app)
    assert len(rows) == 20 and len({r.prev_hash for r in rows}) == 20
    assert (await verify(api_client))["ok"]


@pytest.mark.parametrize("role", ["builder", "viewer", "reviewer"])
async def test_only_admins_verify_the_log(app, api_client, role):
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_dev", org_id="org_default", role=role)
    try:
        assert (await api_client.get("/v1/org/audit/verify")).status_code == 403
    finally:
        app.dependency_overrides.pop(current_user)


async def test_the_command_line_verifies_every_organisation(app, api_client):
    from agentcanvas import audit as audit_cli

    await changes(api_client, 2)
    [report] = await audit_cli.verify(app.state.db)
    assert report.ok and report.org_id == "org_default"
    async with app.state.db.sessionmaker() as s:
        await s.execute(text("UPDATE audit_log SET ip = '10.9.9.9'"))
        await s.commit()
    [report] = await audit_cli.verify(app.state.db, "org_default")
    assert not report.ok and {p["kind"] for p in report.problems} == {"changed"}


async def test_retention_never_deletes_an_entry_younger_than_the_retention(app, api_client):
    """The start of the chain goes up to the first entry that is still kept: an older entry after it (a server's
    clock behind the others') waits rather than taking younger ones with it."""
    now = datetime.now(UTC)
    async with app.state.db.sessionmaker() as s:
        for action, days in (("a", 500), ("b", 10), ("c", 450)):
            s.add(AuditLog(org_id="org_default", action=action, target="-", detail={},
                           created_at=now - timedelta(days=days)))  # fmt: skip
            await s.flush()
        await s.commit()
    assert await app.state.retention.expire_audit(now) == 1
    assert [r.action for r in await entries(app)] == ["b", "c"]


async def test_a_membership_change_and_an_audit_write_wait_for_each_other_without_a_deadlock(app, api_client):
    """A membership change holds the organisation's row (FOR UPDATE) and then writes its audit entry; a request's
    entry written meanwhile waits for it, then links after it."""
    if not app.state.db.is_postgres:
        pytest.skip("row locks need Postgres")
    async with app.state.db.sessionmaker() as member_change:
        await member_change.execute(select(Organization.id).where(Organization.id == "org_default").with_for_update())

        async def request_entry() -> None:
            async with app.state.db.sessionmaker() as s:
                await AuditRepo(s).add(AuditLog(org_id="org_default", action="publish", target="wf_id=w", detail={}))

        waiting = asyncio.create_task(request_entry())
        await asyncio.sleep(0.3)
        assert not waiting.done()  # it waits for the membership change
        row = AuditLog(org_id="org_default", action="set_member", target="user_id=u", detail={})
        await asyncio.wait_for(AuditRepo(member_change).add(row), 5)
    await asyncio.wait_for(waiting, 5)
    assert [r.action for r in await entries(app)] == ["set_member", "publish"]
    assert (await verify(api_client))["ok"]


APP_ROLE = "agentcanvas_e3_audit_app"  # roles belong to the whole Postgres server: a name of this test's own


@pytest.mark.skipif(not (os.getenv("TEST_DATABASE_URL") or "").startswith("postgres"), reason="roles need Postgres")
async def test_the_api_role_may_add_audit_entries_but_never_change_or_delete_them(app, api_client):
    """ASVS V16.4.2: a SQL injection or a compromised API server, connected as the API's role, cannot rewrite
    history. Retention still deletes old entries, through the owner's function, which refuses recent ones."""
    from agentcanvas.db.app_role import ensure
    from agentcanvas.db.session import Database
    from agentcanvas.services.retention import RetentionJob

    url = os.environ["TEST_DATABASE_URL"]
    await changes(api_client, 3)
    ensure(url, APP_ROLE, APP_ROLE)  # what the deployment's migration step runs, as the owner
    as_app = Database(make_url(async_url(url)).set(username=APP_ROLE, password=APP_ROLE).render_as_string(False))
    try:
        async with as_app.sessionmaker() as s:  # adding is what the API does
            await AuditRepo(s).add(AuditLog(org_id="org_default", action="put_secret", target="name=X", detail={}))
        for sql in (
            "UPDATE audit_log SET target = 'name=innocent'",
            "DELETE FROM audit_log",
            "TRUNCATE audit_log CASCADE",
            "INSERT INTO audit_anchor (org_id, through_id, last_hash, rows) VALUES ('org_default', 1, NULL, 0)",
            "SELECT audit_expire('org_default', now())",  # recent entries: refused by the function itself
        ):
            async with as_app.sessionmaker() as s:
                with pytest.raises(DBAPIError) as refused:
                    await s.execute(text(sql))
            assert "permission denied" in str(refused.value) or "younger than a day" in str(refused.value), sql
        async with app.state.db.sessionmaker() as s:  # the owner ages the first two (and re-hashes them)
            rows = list((await s.scalars(select(AuditLog).order_by(AuditLog.id))).all())
            prev = None
            for i, r in enumerate(rows):
                if i < 2:
                    r.created_at = datetime.now(UTC) - timedelta(days=500)
                r.prev_hash, r.row_hash = prev, None
                r.row_hash = prev = audit_chain.row_hash(r)
            await s.commit()
        job = RetentionJob(as_app, app.state.runner, 90, audit_days=400)
        assert await job.expire_audit(datetime.now(UTC)) == 2  # as the API's role
        report = await verify(api_client)
        assert report["ok"] and report["checked"] == 2 and report["anchor"]["rows"] == 2, report
    finally:
        await as_app.dispose()
        engine = create_async_engine(async_url(url))
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP OWNED BY {APP_ROLE}"))
            await conn.execute(text(f"DROP ROLE {APP_ROLE}"))
        await engine.dispose()


@pytest.mark.skipif(not (os.getenv("TEST_DATABASE_URL") or "").startswith("postgres"), reason="roles need Postgres")
async def test_the_api_role_is_created_on_an_empty_database_before_a_restore(capfd):
    """restore.sh creates the role before it loads the dump, on an empty database: the audit log's privileges wait
    for the run after the migrations instead of failing the restore."""
    from agentcanvas.db.app_role import ensure

    url = make_url(os.environ["TEST_DATABASE_URL"])
    empty = f"{url.database}_empty"
    engine = create_async_engine(async_url(str(url.render_as_string(False))), isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.execute(text(f"DROP DATABASE IF EXISTS {empty}"))
        await conn.execute(text(f"CREATE DATABASE {empty}"))
    try:
        ensure(url.set(database=empty).render_as_string(False), APP_ROLE, APP_ROLE)
        assert "audit log's tables are not there yet" in capfd.readouterr().err
    finally:
        async with engine.connect() as conn:
            await conn.execute(text(f"DROP DATABASE {empty}"))
            await conn.execute(text(f"DROP ROLE IF EXISTS {APP_ROLE}"))
        await engine.dispose()
