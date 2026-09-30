"""Sprint 8 record/replay (FR-X-13): a published run's model replies become a deterministic regression test."""

from __future__ import annotations

P = "/v1/projects/prj_default"


def bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


async def test_production_run_becomes_a_regression_test(app, api_client):
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources()  # "production" models (the demo model)
    wf = (await api_client.post(f"{P}/workflows", json={"name": "Desk", "template": "ticket_triage"})).json()
    pub = (await api_client.post(f"/v1/workflows/{wf['id']}/publish", json={})).json()
    res = (
        await api_client.post(
            f"/v1/deployments/{pub['deployment']['id']}/runs/wait", json={"input": {"ticket": "How do I log in?"}},
            headers=bearer(pub["api_key"]),
        )
    ).json()  # fmt: skip
    assert res["status"] == "success", res

    case = await api_client.post(f"/v1/runs/{res['run_id']}/test-case", json={"name": "login question"})
    assert case.status_code == 201, case.text
    tc = case.json()
    assert tc["input"] == {"ticket": "How do I log in?"}
    assert set(tc["mocks"]) == {"fast_model", "smart_model"}  # each model resource's recorded replies
    assert tc["expect"]["route"] == ["n_cls", "n_rt", "n_draft"]
    assert tc["expect"]["channels"]["reply"] == res["output"]["reply"]

    base = f"/v1/workflows/{wf['id']}/test-cases/run"
    app.state.resources_factory = lambda thread_id, wf: DemoResources()
    assert (await api_client.post(base, json={})).json()["failed"] == 0  # replays deterministically

    cur = (await api_client.get(f"/v1/workflows/{wf['id']}")).json()  # a new version routes differently
    ir = cur["ir"]
    urgency = res["output"]["urgency"]
    next(n for n in ir["nodes"] if n["id"] == "n_rt")["config"]["cases"][0]["when"] = f'state.urgency == "{urgency}"'
    await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(cur["version"])})
    red = (await api_client.post(base, json={})).json()
    assert red["failed"] == 1 and any(f.startswith("route:") for f in red["results"][0]["failures"])

    resumed = await api_client.post(f"/v1/runs/{red['results'][0]['run_id']}/test-case", json={"name": "x"})
    assert resumed.status_code == 422  # test runs are not recorded
