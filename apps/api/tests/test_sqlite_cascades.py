"""Deleting a row deletes what belongs to it, on SQLite too (the schema's ON DELETE CASCADE).

SQLite enforces foreign keys only on connections that ask for it; before they did, a deleted workflow's published
endpoint kept answering with its old key, and its versions, threads and runs stayed in the database.
"""

from __future__ import annotations

from sqlalchemy import func, select
from test_publish import bearer, make_greeter

from agentcanvas.db.models import Deployment, Run, Thread, WorkflowVersion


async def test_a_deleted_workflow_takes_its_deployments_versions_and_runs_with_it(app, api_client):
    wf = await make_greeter(api_client)
    pub = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()
    dep, key = pub["deployment"]["id"], pub["api_key"]
    call = f"/v1/deployments/{dep}/runs/wait"
    assert (await api_client.post(call, json={"input": {"who": "Ada"}}, headers=bearer(key))).status_code == 200

    assert (await api_client.delete(f"/v1/workflows/{wf}")).status_code == 204
    assert (await api_client.post(call, json={"input": {"who": "Ada"}}, headers=bearer(key))).status_code in (401, 404)
    async with app.state.db.sessionmaker() as s:
        for model in (Deployment, WorkflowVersion, Thread):
            left = await s.scalar(select(func.count()).select_from(model).where(model.workflow_id == wf))
            assert left == 0, f"{model.__name__} rows of the deleted workflow are left"
        runs = await s.scalar(
            select(func.count())
            .select_from(Run)
            .join(Thread, Run.thread_id == Thread.id, isouter=True)
            .where(Thread.id.is_(None))
        )
        assert runs == 0, "runs whose thread is gone are left"
