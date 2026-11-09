"""E-7: exporting an organisation. Built by a background job, downloaded once through a short-lived link."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from test_org_scope import ORG_B, PROJECT_B, act_as_b, org_b  # noqa: F401 - a fixture
from test_publish import make_greeter

from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.db.models import AuditLog, OrgExport, OrgExportPart, StoredFile
from agentcanvas.ir.models import Workflow

ORG = "org_default"


async def built(app, api) -> dict:
    r = await api.post("/v1/org/export")
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    await app.state.offboarding.tick(datetime.now(UTC))
    done = (await api.get(f"/v1/org/export/{r.json()['id']}")).json()
    assert done["status"] == "ready", done
    return done


def lines(zf: zipfile.ZipFile, name: str) -> list[dict]:
    return [json.loads(x) for x in zf.read(name).decode().splitlines() if x]


async def test_an_export_holds_the_organisations_data_and_no_credentials(app, api_client, org_b):  # noqa: F811
    wf_id = await make_greeter(api_client)
    wf = Workflow.model_validate((await api_client.get(f"/v1/workflows/{wf_id}")).json()["ir"])
    h = await app.state.runner.start(wf, input={"who": "Ada"})
    await asyncio.wait_for(h.done.wait(), 10)
    pub = (await api_client.post(f"/v1/workflows/{wf_id}/publish", json={})).json()
    trig = await api_client.post(f"/v1/workflows/{wf_id}/triggers", json={"kind": "webhook", "config": {}})
    assert trig.status_code == 201, trig.text
    assert (await api_client.put("/v1/projects/prj_default/secrets/TOKEN", json={"value": "s3cret"})).status_code == 204
    assert (await api_client.put("/v1/projects/prj_default/files/scripts/x.py", json={"content": "x = 1"})).is_success
    token = (await api_client.post("/v1/me/tokens", json={"name": "ci", "role": "viewer"})).json()["token"]
    async with app.state.db.sessionmaker() as s:
        s.add(StoredFile(id="fil_a", project_id="prj_default", name="../notes.txt", media_type="text/plain", size=5,
                         sha256="x", data=b"hello"))  # fmt: skip
        await s.commit()
    await app.state.runner.adapter.store.aput((f"org:{ORG}", "prefs", "user", "usr_dev"), "tea", {"value": "green"})
    act_as_b(app)
    try:
        assert (await api_client.post(f"/v1/projects/{PROJECT_B}/workflows", json={"name": "B secret flow"})).is_success
    finally:
        app.dependency_overrides.pop(current_user)

    first = await api_client.post("/v1/org/export")
    assert (await api_client.post("/v1/org/export")).status_code == 409  # one at a time
    await app.state.offboarding.tick(datetime.now(UTC))
    done = (await api_client.get(f"/v1/org/export/{first.json()['id']}")).json()
    assert done["status"] == "ready" and done["size"] > 0
    link = await api_client.post(f"/v1/org/export/{done['id']}/link")
    assert link.status_code == 200, link.text
    path = link.json()["path"]
    assert path.startswith("/v1/exports/acx_")
    r = await api_client.get(path)  # no sign-in needed: the link is the capability
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert hashlib.sha256(r.content).hexdigest() == done["sha256"]
    assert (await api_client.get(path)).status_code == 404  # once

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    assert {"manifest.json", "organisation.json", "members.ndjson", "memories.ndjson"} <= names
    assert json.loads(zf.read("organisation.json"))["id"] == ORG
    assert [m["user_id"] for m in lines(zf, "members.ndjson")] == ["usr_dev"]
    assert [w["id"] for w in lines(zf, "workflow.ndjson")] == [wf_id]  # not organisation B's
    assert [t["id"] for t in lines(zf, "thread.ndjson")] == [h.thread_id]
    assert lines(zf, "run.ndjson")[0]["recording"]["output"]["out"] == "Hello Ada"
    deployment = lines(zf, "deployment.ndjson")[0]
    assert deployment["id"] == pub["deployment"]["id"] and "api_key_hash" not in deployment
    assert "secret" not in lines(zf, "trigger.ndjson")[0]["config"]
    assert [x["name"] for x in lines(zf, "secret.ndjson")] == ["TOKEN"] and "ciphertext" not in lines(
        zf, "secret.ndjson"
    )[0]
    assert "token_hash" not in lines(zf, "personal_token.ndjson")[0]
    assert [m["value"] for m in lines(zf, "memories.ndjson")] == [{"value": "green"}]
    assert zf.read("files/fil_a/notes.txt") == b"hello"
    assert zf.read("workspaces/prj_default/scripts/x.py") == b"x = 1"
    assert not any("/.git/" in n for n in names)
    assert "org_data_key.ndjson" not in names
    body = r.content.decode("latin-1")
    assert token not in body and "s3cret" not in body and "B secret flow" not in body
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["counts"]["workflow.ndjson"] == 1 and manifest["format"] == 1
    async with app.state.db.sessionmaker() as s:  # downloaded: the archive is gone, the row stays as a record
        assert not await s.scalar(select(func.count()).select_from(OrgExportPart))
        actions = set((await s.scalars(select(AuditLog.action))).all())
        assert {"create_org_export", "create_org_export_link", "download_org_export"} <= actions
    after = (await api_client.get(f"/v1/org/export/{done['id']}")).json()
    assert after["status"] == "downloaded" and after["downloaded_at"]
    assert (await api_client.post(f"/v1/org/export/{done['id']}/link")).status_code == 409


async def test_links_expire_a_new_one_replaces_the_old_and_archives_are_deleted(app, api_client):
    done = await built(app, api_client)
    old = (await api_client.post(f"/v1/org/export/{done['id']}/link")).json()["path"]
    new = (await api_client.post(f"/v1/org/export/{done['id']}/link")).json()["path"]
    assert (await api_client.get(old)).status_code == 404
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(OrgExport).values(link_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()
    assert (await api_client.get(new)).status_code == 404
    assert (await api_client.get("/v1/exports/acx_made-up")).status_code == 404
    assert (await api_client.get("/v1/exports/sh_share-token")).status_code == 404
    later = datetime.now(UTC) + timedelta(hours=25)
    assert await app.state.offboarding.exports.expire(later) == 1
    async with app.state.db.sessionmaker() as s:
        assert not await s.scalar(select(func.count()).select_from(OrgExportPart))
    assert (await api_client.get(f"/v1/org/export/{done['id']}")).json()["status"] == "expired"


async def test_a_builder_that_stopped_is_taken_over_and_a_failure_is_reported(app, api_client, monkeypatch):
    r = await api_client.post("/v1/org/export")
    export_id = r.json()["id"]
    job = app.state.offboarding.exports
    now = datetime.now(UTC)
    assert await job._claim(export_id, now)  # another replica started it, then stopped
    assert await job.tick(now) == []  # its hold has not run out
    assert await job.tick(now + timedelta(minutes=11)) == [export_id]
    assert (await api_client.get(f"/v1/org/export/{export_id}")).json()["status"] == "ready"

    second = (await api_client.post("/v1/org/export")).json()["id"]

    async def broken(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(job, "_write", broken)
    await job.tick(datetime.now(UTC))
    failed = (await api_client.get(f"/v1/org/export/{second}")).json()
    assert failed["status"] == "failed" and failed["error"] == "disk full"
    assert (await api_client.post(f"/v1/org/export/{second}/link")).status_code == 409


async def test_only_admins_with_a_sign_in_and_only_their_own_exports(app, api_client, org_b):  # noqa: F811
    done = await built(app, api_client)
    for role in ("builder", "viewer"):
        app.dependency_overrides[current_user] = lambda role=role: CurrentUser(id="usr_dev", org_id=ORG, role=role)
        try:
            assert (await api_client.post("/v1/org/export")).status_code == 403
            assert (await api_client.get("/v1/org/export")).status_code == 403
        finally:
            app.dependency_overrides.pop(current_user)
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_dev", org_id=ORG, role="admin", token_id="t")
    try:
        assert (await api_client.post(f"/v1/org/export/{done['id']}/link")).status_code == 403
    finally:
        app.dependency_overrides.pop(current_user)
    act_as_b(app)
    try:
        assert (await api_client.get(f"/v1/org/export/{done['id']}")).status_code == 404
        assert (await api_client.post(f"/v1/org/export/{done['id']}/link")).status_code == 404
        assert (await api_client.get("/v1/org/export")).json() == []
    finally:
        app.dependency_overrides.pop(current_user)


async def test_an_organisation_being_deleted_can_still_be_exported(app, api_client):
    r = await api_client.request("DELETE", "/v1/org", json={"confirm": "Default organisation", "reason": "leaving"})
    assert r.status_code == 202
    done = await built(app, api_client)
    path = (await api_client.post(f"/v1/org/export/{done['id']}/link")).json()["path"]
    assert (await api_client.get(path)).status_code == 200
    assert (await api_client.get("/v1/projects")).status_code == 403  # the rest stays closed
