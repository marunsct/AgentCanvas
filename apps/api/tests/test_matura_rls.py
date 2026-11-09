"""Row-level security on AgentCanvas's own tables (TS26): in Matura mode a request's database session sees only its
organisation's rows, even when a query forgets its `org_id` filter; a connection that names no organisation sees
nothing; background work keeps seeing every organisation. Needs Postgres (TEST_DATABASE_URL), except the first test.
"""

import importlib.util
import os
import pathlib

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from test_matura import ACME, ANA, BEN, GLOBEX, ISSUER, SECRET, as_user

from agentcanvas.api.main import create_app
from agentcanvas.db import rls
from agentcanvas.db.models import Base, Project
from agentcanvas.db.repositories import ProjectRepo
from agentcanvas.db.session import async_url
from agentcanvas.security.auth import Authenticator
from agentcanvas.services import matura
from agentcanvas.settings import Settings

URL = os.getenv("TEST_DATABASE_URL") or ""
postgres = pytest.mark.skipif(not URL.startswith("postgres"), reason="row-level security needs Postgres")
APP_ROLE, PROBE_ROLE = "agentcanvas_rls_app", "agentcanvas_rls_probe"
MIGRATIONS = pathlib.Path(__file__).parents[1] / "migrations/versions"
MIGRATION = MIGRATIONS / "20261017_a3c7e9b1d5f2_matura_workspaces_rls.py"
LATER = [
    MIGRATIONS / "20261029_e1a5c9d3f7b2_git_link.py",
    MIGRATIONS / "20261029_e4a8c2f6b0d1_a2a_replies.py",
    MIGRATIONS / "20261029_f6b0d4a8c2e5_answer_cache.py",
    MIGRATIONS / "20261029_a7c1e5b9d3f2_stored_files.py",
    MIGRATIONS / "20261029_b7d1f5a9c3e8_personal_tokens.py",
    MIGRATIONS / "20261029_d2f6b0c4e8a1_scim.py",
    MIGRATIONS / "20261029_e5a9c1b7d3f2_oauth_connections.py",
    MIGRATIONS / "20261029_f3b7d1a9c5e8_run_search.py",
    MIGRATIONS / "20261029_a9d3f7b1c5e2_presence.py",
    MIGRATIONS / "20261110_af1afc823a01_spend_archive.py",
    MIGRATIONS / "20261110_2201e65b5be8_deployment_keys.py",
    MIGRATIONS / "20261110_c7bf8b0b2ba4_spend_cap_alert.py",
    MIGRATIONS / "20261110_29547c665705_model_spend.py",
]  # tables added after it, each with its own policy


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "auth_mode": "matura", "oidc_issuer": ISSUER.issuer, "oidc_audience": "agentcanvas",
            "oidc_client_id": "agentcanvas", "matura_webhook_secret": SECRET,
        }
    )  # fmt: skip


async def _client(app):
    matura.forget()
    app.state.authenticator = Authenticator(app.state.settings, jwks=ISSUER.jwks)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_every_table_has_a_policy_or_a_reason_not_to():
    tables = {t.name for t in Base.metadata.sorted_tables if not t.name.startswith("matura_")}
    assert tables == set(rls.TENANT_TABLES) | set(rls.EXEMPT), "add new tables to db/rls.py and a migration"
    assert not set(rls.TENANT_TABLES) & set(rls.EXEMPT)
    for table, (column, parent) in rls.TENANT_TABLES.items():  # each column and parent exists and leads upwards
        assert column in Base.metadata.tables[table].c
        assert parent is None or parent in rls.TENANT_TABLES
    seen: dict[str, tuple[str, str | None]] = {}
    for path in (MIGRATION, *LATER):  # the first migration, then each one that added tables (its own copy of them)
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert not set(module.TABLES) & set(seen)
        assert all(rls.TENANT_TABLES[t] == v for t, v in module.TABLES.items())  # the migration's copy matches
        assert all(module._condition(t) == rls.policy_condition(t) for t in module.TABLES)
        seen.update(module.TABLES)
    assert seen == rls.TENANT_TABLES  # every table with a policy got it from one of them


async def _seed(client) -> dict[str, dict[str, str]]:
    """A project, a workflow and a version in each of two organisations, made through the API."""
    out: dict[str, dict[str, str]] = {}
    for org, user, name in ((ACME, ANA, "Acme"), (GLOBEX, BEN, "Globex")):
        h = as_user(user, org, "builder")
        p = (await client.post("/v1/projects", json={"name": f"{name} bots"}, headers=h)).json()
        w = await client.post(f"/v1/projects/{p['id']}/workflows", json={"name": f"{name} flow"}, headers=h)
        assert w.status_code == 201, w.text
        v = await client.post(f"/v1/workflows/{w.json()['id']}/versions", json={"message": "first"}, headers=h)
        assert v.status_code == 201, v.text
        out[org] = {"project": p["id"], "workflow": w.json()["id"]}
    return out


async def _superuser(conn) -> bool:
    return bool((await conn.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user"))).scalar())


async def _grant(conn, role: str, login: bool) -> None:
    exists = (await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})).scalar()
    if not exists:  # neither superuser nor BYPASSRLS (the defaults)
        await conn.execute(text(f"CREATE ROLE {role} {'LOGIN PASSWORD ' + repr(role) if login else 'NOLOGIN'}"))
    await conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
    await conn.execute(text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}"))
    await conn.execute(text(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}"))


@postgres
async def test_policies_fail_closed_and_keep_organisations_apart(app):
    async with await _client(app) as client:
        ids = await _seed(client)
    engine = create_async_engine(async_url(URL))  # no all-tenants option: like any other client of the database
    try:
        async with engine.connect() as conn:
            superuser = await _superuser(conn)
            if superuser:  # superusers skip row-level security: check as an ordinary role
                await _grant(conn, PROBE_ROLE, login=False)
            await conn.commit()  # otherwise the owner: the policies are forced on it

            async def as_probe():
                if superuser:
                    await conn.execute(text(f"SET LOCAL ROLE {PROBE_ROLE}"))

            async def names(table: str) -> set[str]:
                return set((await conn.execute(text(f"SELECT name FROM {table}"))).scalars())

            async def count(table: str) -> int:
                return int((await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar() or 0)

            async with conn.begin():  # nothing named: nothing visible
                await as_probe()
                for table in ("project", "workflow", "workflow_version", "audit_log"):
                    assert await count(table) == 0, table

            async with conn.begin():  # Acme's rows only, down the chain project -> workflow -> version
                await as_probe()
                await conn.execute(text("SELECT set_config('matura.tenant_id', :o, true)"), {"o": ACME})
                assert await names("project") == {"Acme bots"}
                assert await names("workflow") == {"Acme flow"}
                assert await count("workflow_version") == 1
                assert await count("audit_log") >= 1  # Acme's own changes
                # another organisation's rows cannot be changed or deleted either
                r = await conn.execute(
                    text("UPDATE project SET name = 'taken' WHERE id = :p"), {"p": ids[GLOBEX]["project"]}
                )
                assert r.rowcount == 0
                r = await conn.execute(text("DELETE FROM workflow WHERE id = :w"), {"w": ids[GLOBEX]["workflow"]})
                assert r.rowcount == 0

            with pytest.raises(DBAPIError, match="row-level security"):  # nor written into
                async with conn.begin():
                    await as_probe()
                    await conn.execute(text("SELECT set_config('matura.tenant_id', :o, true)"), {"o": ACME})
                    await conn.execute(
                        text("""INSERT INTO workflow (id, project_id, name, draft_ir, ir_hash)
                            VALUES ('wf_sneaky', :p, 'sneaky', '{}', 'x')"""),
                        {"p": ids[GLOBEX]["project"]},
                    )

            async with conn.begin():  # all-tenants mode: the API's background work sees everything
                await as_probe()
                await conn.execute(text("SET LOCAL agentcanvas.all_tenants = 'on'"))
                assert {"Acme bots", "Globex bots"} <= await names("project")
    finally:
        await engine.dispose()


@postgres
async def test_a_matura_request_sees_only_its_organisation_even_without_filters(app, monkeypatch, tmp_path):
    """The API connects as an ordinary role; a list query that forgot its organisation filter still cannot leak."""
    async with await _client(app) as client:  # the fixture's app migrated the database as its owner
        await _seed(client)
    engine = create_async_engine(async_url(URL))
    async with engine.begin() as conn:
        await _grant(conn, APP_ROLE, login=True)
    await engine.dispose()
    url = make_url(async_url(URL)).set(username=APP_ROLE, password=APP_ROLE).render_as_string(hide_password=False)
    restricted = create_app(
        app.state.settings.model_copy(
            update={
                "database_url": url, "auto_migrate": False, "seed_dev_data": False, "persistence_url": "memory",
                "workspaces_dir": str(tmp_path / "ws"),
            }
        )
    )  # fmt: skip

    async def unfiltered(self, org_id, workspace_id=None):  # the bug RLS guards against
        return list((await self.s.scalars(select(Project).order_by(Project.name))).all())

    async with restricted.router.lifespan_context(restricted):
        async with restricted.state.db.sessionmaker() as s:  # background work: every organisation
            assert {"Acme bots", "Globex bots"} <= {p.name for p in await unfiltered(ProjectRepo(s), None)}
        monkeypatch.setattr(ProjectRepo, "list", unfiltered)
        async with await _client(restricted) as client:
            for org, user, mine in ((ACME, ANA, "Acme bots"), (GLOBEX, BEN, "Globex bots")):
                r = await client.get("/v1/projects", headers=as_user(user, org, "viewer"))
                assert r.status_code == 200 and [p["name"] for p in r.json()] == [mine]
            # and normal work goes on under the policies
            h = as_user(ANA, ACME, "builder")
            p = (await client.get("/v1/projects", headers=h)).json()[0]["id"]
            w = await client.post(f"/v1/projects/{p}/workflows", json={"name": "under rls"}, headers=h)
            assert w.status_code == 201, w.text
            assert (await client.get(f"/v1/workflows/{w.json()['id']}", headers=h)).status_code == 200
            assert (
                await client.get(f"/v1/workflows/{w.json()['id']}", headers=as_user(BEN, GLOBEX))
            ).status_code == 404
            # knowledge is searched in the database under the policies too (G-6), and stays the organisation's
            kb = await client.post(f"/v1/projects/{p}/knowledge", json={"name": "rls-docs"}, headers=h)
            assert kb.status_code == 201, kb.text
            k = f"/v1/knowledge/{kb.json()['id']}"
            added = await client.post(
                f"{k}/documents/text", json={"title": "IT", "text": "Report a lost laptop to IT."}, headers=h
            )
            assert added.status_code == 201, added.text
            for mode in ("hybrid", "semantic", "keyword"):
                r = await client.post(f"{k}/search", json={"query": "lost laptop", "mode": mode}, headers=h)
                assert r.status_code == 200 and [x["title"] for x in r.json()["results"]] == ["IT"], (mode, r.text)
            r = await client.post(f"{k}/search", json={"query": "lost laptop"}, headers=as_user(BEN, GLOBEX))
            assert r.status_code == 404


POOLED_ROLE, BARE_ROLE = "agentcanvas_rls_pooled", "agentcanvas_rls_bare"


async def _forget_default(role: str) -> None:
    engine = create_async_engine(async_url(URL))
    async with engine.begin() as conn:
        if (await conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})).scalar():
            await conn.execute(text(f"ALTER ROLE {role} RESET {rls.ALL_TENANTS}"))
    await engine.dispose()


def _as(role: str) -> str:
    return make_url(async_url(URL)).set(username=role, password=role).render_as_string(hide_password=False)


@postgres
async def test_background_work_sees_every_organisation_through_a_pooler_that_drops_startup_options(app):
    """PgBouncer refuses the libpq `options` startup parameter that put the API's connections in all-tenants mode,
    and its usual workaround (`ignore_startup_parameters = options`) drops it: every query then saw no rows. The
    API's role now carries the setting itself, so a connection without the option still sees every organisation."""
    from agentcanvas.db.app_role import ensure

    async with await _client(app) as client:
        await _seed(client)
    await _forget_default(POOLED_ROLE)  # roles outlive the test database: start from a role without the default
    ensure(URL, POOLED_ROLE, POOLED_ROLE)  # what the migration step runs, as the owner
    engine = create_async_engine(_as(POOLED_ROLE))  # no startup options: as through PgBouncer
    try:
        async with engine.connect() as conn:
            names = set((await conn.execute(text("SELECT name FROM project"))).scalars())
            assert {"Acme bots", "Globex bots"} <= names
            await conn.commit()
            async with conn.begin():  # a Matura request still narrows its own transactions
                await conn.execute(
                    text("SELECT set_config('matura.tenant_id', :o, true), set_config(:a, 'off', true)"),
                    {"o": ACME, "a": rls.ALL_TENANTS},
                )
                assert set((await conn.execute(text("SELECT name FROM project"))).scalars()) == {"Acme bots"}
            assert {"Acme bots", "Globex bots"} <= set((await conn.execute(text("SELECT name FROM project"))).scalars())
    finally:
        await engine.dispose()


@postgres
async def test_the_api_refuses_to_start_when_its_connections_would_see_no_rows(app, monkeypatch, tmp_path):
    """A role without the all-tenants default, behind a pooler that drops the startup option, saw an empty
    installation (and cron, hand-over and retention found nothing to do). The API now says so and stops."""
    from agentcanvas.db import session as db_session

    engine = create_async_engine(async_url(URL))
    async with engine.begin() as conn:
        await _grant(conn, BARE_ROLE, login=True)  # an ordinary role, made without the migration step's default
    await engine.dispose()
    await _forget_default(BARE_ROLE)
    monkeypatch.setattr(db_session, "connect_options", lambda url, args=None: dict(args or {}))  # the pooler
    lost = create_app(
        app.state.settings.model_copy(
            update={
                "database_url": _as(BARE_ROLE), "auto_migrate": False, "seed_dev_data": False,
                "persistence_url": "memory", "workspaces_dir": str(tmp_path / "ws"),
            }
        )
    )  # fmt: skip
    with pytest.raises(RuntimeError, match="all_tenants"):
        async with lost.router.lifespan_context(lost):
            pass


@postgres
async def test_an_owner_that_is_not_a_superuser_still_creates_the_api_role(app):
    """Only a superuser may give a role a default for a custom setting. A managed Postgres's owner (RDS, Cloud SQL)
    is not one: the role is still created, and the step says what it could not do instead of failing the upgrade."""
    from agentcanvas.db.app_role import ensure

    owner, role = "agentcanvas_rls_owner", "agentcanvas_rls_made"
    engine = create_async_engine(async_url(URL))
    async with engine.begin() as conn:
        for r in (role, owner):
            await conn.execute(text(f"DROP ROLE IF EXISTS {r}"))
        await conn.execute(text(f"CREATE ROLE {owner} LOGIN CREATEROLE PASSWORD '{owner}'"))
        await conn.execute(text(f"GRANT {make_url(URL).username} TO {owner}"))  # owns the tables, not a superuser
    try:
        ensure(_as(owner).replace("+psycopg", ""), role, "pw")
        async with engine.connect() as conn:
            made = await conn.execute(text("SELECT rolbypassrls FROM pg_roles WHERE rolname = :r"), {"r": role})
            assert made.scalar() is False
    finally:
        async with engine.begin() as conn:
            for r in (role, owner):
                await conn.execute(text(f"DROP OWNED BY {r}"))
                await conn.execute(text(f"DROP ROLE {r}"))
        await engine.dispose()
