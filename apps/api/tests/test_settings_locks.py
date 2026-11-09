"""Project and organisation `settings` are JSON merged in a read-modify-write: each write locks the row before it
reads, so two saves at once (Copilot settings and knowledge defaults, say) never lose one another's change (CP-8)."""

import asyncio

import pytest
from sqlalchemy import select, update

from agentcanvas.db.models import Organization, Project
from agentcanvas.db.repositories import ProjectRepo

P = "/v1/projects/prj_default"
WRITES = [  # (method, path, body, the key of `settings` it writes)
    ("PUT", f"{P}/knowledge-defaults", {"chunking": {}}, "knowledge_defaults"),
    ("PUT", f"{P}/model-defaults", {"chat": "openai:gpt-4o-mini"}, "model_defaults"),
    ("PUT", f"{P}/copilot-settings", {"enabled": True}, "copilot"),
    ("DELETE", f"{P}/trace-export", None, None),
    ("POST", f"{P}/setup/skip", None, "setup_done"),
    ("PUT", f"{P}/setup", {"model": "openai:gpt-4o-mini"}, "setup_done"),
]


@pytest.mark.parametrize(("method", "path", "body", "key"), WRITES, ids=[w[1].rsplit("/", 1)[1] for w in WRITES])
async def test_a_settings_write_waits_for_a_concurrent_one_and_keeps_its_change(
    app, api_client, monkeypatch, method, path, body, key
):
    if not app.state.db.is_postgres:  # SQLite has one writer and no row locks: the write asks for the lock
        asked: list[bool] = []
        require = ProjectRepo.require

        async def spy(self, project_id, *, lock=False):
            asked.append(lock)
            return await require(self, project_id, lock=lock)

        monkeypatch.setattr(ProjectRepo, "require", spy)
        r = await api_client.request(method, path, json=body)
        assert r.status_code == 200, r.text
        assert True in asked
        return

    await api_client.get(f"{P}/setup")  # the project exists
    async with app.state.db.sessionmaker() as other:
        await other.execute(select(Project).where(Project.id == "prj_default").with_for_update())
        write = asyncio.create_task(api_client.request(method, path, json=body))
        await asyncio.sleep(0.5)
        assert not write.done()  # waiting for the row
        current = await other.scalar(select(Project.settings).where(Project.id == "prj_default"))
        await other.execute(
            update(Project).where(Project.id == "prj_default").values(settings={**(current or {}), "theirs": 1})
        )
        await other.commit()
    r = await asyncio.wait_for(write, 10)
    assert r.status_code == 200, r.text
    async with app.state.db.sessionmaker() as s:
        saved = await s.scalar(select(Project.settings).where(Project.id == "prj_default")) or {}
    assert saved.get("theirs") == 1  # the concurrent change is kept
    if key:
        assert key in saved


async def test_an_organisation_settings_write_waits_for_a_concurrent_one(app, api_client):
    if not app.state.db.is_postgres:
        pytest.skip("row locks need Postgres")
    await api_client.get("/v1/org/settings")
    async with app.state.db.sessionmaker() as other:
        await other.execute(select(Organization).where(Organization.id == "org_default").with_for_update())
        write = asyncio.create_task(api_client.put("/v1/org/settings", json={"limits": {"rate_limit_per_project": 5}}))
        await asyncio.sleep(0.5)
        assert not write.done()
        await other.execute(
            update(Organization).where(Organization.id == "org_default").values(settings={"memory_retention_days": 3})
        )
        await other.commit()
    r = await asyncio.wait_for(write, 10)
    assert r.status_code == 200, r.text
    async with app.state.db.sessionmaker() as s:
        saved = await s.scalar(select(Organization.settings).where(Organization.id == "org_default")) or {}
    assert saved == {"memory_retention_days": 3, "rate_limit_per_project": 5}
