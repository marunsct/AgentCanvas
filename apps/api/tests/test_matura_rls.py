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
MIGRATION = pathlib.Path(__file__).parents[1] / "migrations/versions/20261017_a3c7e9b1d5f2_matura_workspaces_rls.py"


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
    spec = importlib.util.spec_from_file_location("ts26_migration", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.TABLES == rls.TENANT_TABLES  # the migration's copy matches
    assert all(module._condition(t) == rls.policy_condition(t) for t in rls.TENANT_TABLES)


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
