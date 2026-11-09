"""Renaming and deleting a project (Settings > Project > General).

A deleted project takes everything in it: its workflows answer 404, a published endpoint stops answering, its
workspace folder is gone. An organisation keeps at least one project; only admins rename or delete.
"""

from __future__ import annotations

from pathlib import Path

from test_publish import bearer, greeter
from test_roles import act_as

from agentcanvas.api.deps import current_user


async def _project(api, name: str) -> str:
    r = await api.post("/v1/projects", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_rename_a_project(api_client):
    pid = await _project(api_client, "Support")
    r = await api_client.patch(f"/v1/projects/{pid}", json={"name": "  Customer support  "})
    assert r.status_code == 200 and r.json()["name"] == "Customer support"
    assert "Customer support" in [p["name"] for p in (await api_client.get("/v1/projects")).json()]
    taken = await api_client.patch(f"/v1/projects/{pid}", json={"name": "Default project"})
    assert taken.status_code == 409, taken.text
    for bad in ("", "   ", "x" * 101):
        assert (await api_client.patch(f"/v1/projects/{pid}", json={"name": bad})).status_code == 422, bad
    assert (await api_client.patch("/v1/projects/prj_nope", json={"name": "x"})).status_code == 404


async def test_delete_a_project_with_everything_in_it(app, api_client, settings):
    pid = await _project(api_client, "Doomed")
    wf = (await api_client.post(f"/v1/projects/{pid}/workflows", json={"name": "Greeter"})).json()["id"]
    r = await api_client.put(f"/v1/workflows/{wf}", json={"ir": greeter(wf, "Hi")}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    pub = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()
    call = f"/v1/deployments/{pub['deployment']['id']}/runs/wait"
    assert (
        await api_client.post(call, json={"input": {"who": "Ada"}}, headers=bearer(pub["api_key"]))
    ).status_code == 200
    assert (
        await api_client.put(f"/v1/projects/{pid}/files/scripts/mine.py", json={"content": "x = 1\n"})
    ).status_code < 300
    assert (await api_client.put(f"/v1/projects/{pid}/secrets/TOKEN", json={"value": "value-1"})).status_code < 300
    folder = Path(settings.workspaces_dir) / pid
    assert folder.is_dir()

    assert (await api_client.delete(f"/v1/projects/{pid}")).status_code == 204
    assert pid not in [p["id"] for p in (await api_client.get("/v1/projects")).json()]
    assert (await api_client.get(f"/v1/workflows/{wf}")).status_code == 404
    assert (
        await api_client.post(call, json={"input": {"who": "Ada"}}, headers=bearer(pub["api_key"]))
    ).status_code in (401, 404)
    assert not folder.exists()
    assert pid not in app.state.projects.secrets
    assert (await api_client.delete(f"/v1/projects/{pid}")).status_code == 404


async def test_the_last_project_stays(api_client):
    projects = (await api_client.get("/v1/projects")).json()
    for p in projects[1:]:
        assert (await api_client.delete(f"/v1/projects/{p['id']}")).status_code == 204
    last = await api_client.delete(f"/v1/projects/{projects[0]['id']}")
    assert last.status_code == 409 and "only project" in last.json()["detail"]


async def test_two_deletes_at_once_still_leave_one_project(api_client):
    """C-12: two admins deleting the organisation's last two projects at the same moment both passed the check."""
    import asyncio

    for round_ in range(5):
        ids = [p["id"] for p in (await api_client.get("/v1/projects")).json()]
        while len(ids) < 2:
            ids.append(await _project(api_client, f"Spare {round_}"))
        first, second = await asyncio.gather(*(api_client.delete(f"/v1/projects/{pid}") for pid in ids[:2]))
        assert sorted([first.status_code, second.status_code]) == [204, 409], (first.text, second.text)
        assert len((await api_client.get("/v1/projects")).json()) == 1


async def test_only_admins_rename_or_delete(app, api_client):
    pid = await _project(api_client, "Guarded")
    try:
        for role in ("viewer", "reviewer", "builder"):
            act_as(app, role)
            assert (await api_client.patch(f"/v1/projects/{pid}", json={"name": "Mine"})).status_code == 403, role
            assert (await api_client.delete(f"/v1/projects/{pid}")).status_code == 403, role
            moved = await api_client.put(f"/v1/projects/{pid}/workspace", json={"workspace_id": None})
            assert moved.status_code == 403, (role, moved.text)  # where a project lives is the organisation's too
    finally:
        app.dependency_overrides.pop(current_user, None)
    assert (await api_client.get("/v1/projects")).status_code == 200
    assert pid in [p["id"] for p in (await api_client.get("/v1/projects")).json()]


async def test_preferences_follow_the_person(app, api_client):
    """Theme and Simple mode are kept with the person (any role), merged field by field."""
    from test_roles import act_as

    from agentcanvas.api.deps import current_user

    assert (await api_client.get("/v1/me")).json()["preferences"] == {"theme": None, "simple_mode": None}
    assert (await api_client.put("/v1/me/preferences", json={"theme": "dark"})).json()["theme"] == "dark"
    out = (await api_client.put("/v1/me/preferences", json={"simple_mode": False})).json()
    assert out == {"theme": "dark", "simple_mode": False}
    assert (await api_client.get("/v1/me")).json()["preferences"] == {"theme": "dark", "simple_mode": False}
    assert (await api_client.put("/v1/me/preferences", json={"theme": "neon"})).status_code == 422
    act_as(app, "viewer")
    try:
        assert (await api_client.put("/v1/me/preferences", json={"theme": "light"})).status_code == 200
    finally:
        app.dependency_overrides.pop(current_user, None)
