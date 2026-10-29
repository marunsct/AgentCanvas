"""Sprint 8 versions & diff (M6-T1) and saved test cases (M6-T9)."""

from __future__ import annotations

import copy

import pytest

from agentcanvas.ir.diff import diff_workflows
from agentcanvas.ir.models import Edge, Endpoint, Node
from agentcanvas.services.versions import next_semver

P = "/v1/projects/prj_default"


def test_semver_and_diff(triage):
    assert next_semver(None, "minor") == "1.0.0"
    assert [next_semver("1.2.3", b) for b in ("major", "minor", "patch")] == ["2.0.0", "1.3.0", "1.2.4"]

    after = copy.deepcopy(triage)
    after.require_node("n_cls").config["prompt"] = "Is this urgent? {{ state.ticket }}"
    after.require_node("n_rt").config["cases"][0]["when"] = 'state.urgency == "urgent"'
    after.nodes.append(Node(id="n_log", type="core.set", name="log", config={"values": {"summary": "x"}}))
    after.edges = [e for e in after.edges if e.id != triage.edges[0].id]
    after.edges.append(Edge(id="e_new", kind="flow", source=Endpoint(node="START"), target=Endpoint(node="n_log")))
    d = diff_workflows(triage, after)
    assert d.nodes.added == ["n_log"] and d.nodes.removed == []
    assert [(c.id, [f.path for f in c.fields]) for c in d.nodes.changed] == [
        ("n_cls", ["config.prompt"]),
        ("n_rt", ["config.cases.0.when"]),
    ]
    assert d.nodes.changed[1].fields[0].after == 'state.urgency == "urgent"'
    assert d.edges.added == ["e_new"] and d.edges.removed == [triage.edges[0].id]
    assert d.describe() == "1 step added, 2 steps changed, 1 edge added, 1 edge removed"
    assert diff_workflows(triage, triage).empty


async def new_triage(api) -> dict:
    wf = (await api.post(f"{P}/workflows", json={"name": "Triage", "template": "support_triage"})).json()
    assert "id" in wf, wf
    return wf


async def edit_node(api, wf_id: str, node_id: str, **config) -> dict:
    cur = (await api.get(f"/v1/workflows/{wf_id}")).json()
    ir = cur["ir"]
    next(n for n in ir["nodes"] if n["id"] == node_id)["config"].update(config)
    r = await api.put(f"/v1/workflows/{wf_id}", json={"ir": ir}, headers={"If-Match": str(cur["version"])})
    assert r.status_code == 200, r.text
    return r.json()


async def test_versions_diff_and_restore(api_client):
    """M6-T1 AC: the diff of two versions highlights exactly the edited node."""
    wf = await new_triage(api_client)
    base = f"/v1/workflows/{wf['id']}/versions"
    v1 = await api_client.post(base, json={"message": "first"})
    assert v1.status_code == 201 and v1.json()["semver"] == "1.0.0"
    assert v1.json()["workspace_commit"]  # the workspace code the version ran with
    again = await api_client.post(base, json={})
    assert again.status_code == 409 and "nothing changed since v1.0.0" in again.json()["detail"]

    await edit_node(api_client, wf["id"], "n_esc", values={"summary": "URGENT: {{ state.ticket }}"})
    v2 = (await api_client.post(base, json={"message": "louder", "bump": "patch"})).json()
    assert v2["semver"] == "1.0.1"
    diff = (await api_client.get(f"{base}/1.0.0/diff/{v2['id']}")).json()
    assert [c["id"] for c in diff["nodes"]["changed"]] == ["n_esc"]
    assert diff["nodes"]["changed"][0]["fields"] == [
        {
            "path": "config.values.summary",
            "before": "ESCALATED: {{ state.ticket }}",
            "after": "URGENT: {{ state.ticket }}",
        }
    ]
    assert diff["summary"] == "1 step changed" and not diff["nodes"]["added"] and not diff["edges"]["added"]
    assert (await api_client.get(f"{base}/v1.0.1/diff/draft")).json()["summary"] == "no changes"
    assert [v["semver"] for v in (await api_client.get(base)).json()] == ["1.0.1", "1.0.0"]
    detail = (await api_client.get(f"{base}/1.0.0")).json()
    assert next(n for n in detail["ir"]["nodes"] if n["id"] == "n_esc")["config"]["values"]["summary"].startswith("ESC")

    cur = (await api_client.get(f"/v1/workflows/{wf['id']}")).json()
    restored = await api_client.post(f"{base}/1.0.0/restore", headers={"If-Match": str(cur["version"])})
    assert restored.status_code == 200, restored.text
    assert (await api_client.get(f"{base}/1.0.0/diff/draft")).json()["summary"] == "no changes"
    assert (await api_client.get(f"{base}/9.9.9")).status_code == 404


HIGH_CASE = {
    "name": "urgent ticket escalates",
    "input": {"ticket": "Site down"},
    "mocks": {"fast_model": ["high"], "smart_model": ["Sorry about that."]},
    "expect": {
        "status": "interrupted",  # waits for the manager's approval
        "channels": {"summary": "ESCALATED: Site down", "urgency": "high"},
        "route": ["n_cls", "n_rt", "n_esc", "n_ok"],
    },
}


async def test_saved_test_cases_turn_red_when_the_router_changes(app, api_client):
    """M6-T9 AC: changing a router condition turns the affected case red."""
    wf = await new_triage(api_client)
    base = f"/v1/workflows/{wf['id']}/test-cases"
    case = (await api_client.post(base, json=HIGH_CASE)).json()
    low = {
        "name": "calm ticket gets a reply",
        "input": {"ticket": "How do I reset my password?"},
        "mocks": {"fast_model": ["low"], "smart_model": ["Use the reset link."]},
        "expect": {"status": "success", "route": ["n_cls", "n_rt", "n_ag"]},
    }
    await api_client.post(base, json=low)
    run = (await api_client.post(f"{base}/run", json={})).json()
    assert (run["passed"], run["failed"]) == (2, 0), run
    assert run["results"][0]["route"] == ["n_cls", "n_rt", "n_esc", "n_ok"]
    listed = (await api_client.get(base)).json()
    assert listed[0]["id"] == case["id"] and listed[0]["last_result"]["passed"] is True

    await api_client.post(f"/v1/workflows/{wf['id']}/versions", json={"message": "green"})
    cur = (await api_client.get(f"/v1/workflows/{wf['id']}")).json()
    ir = cur["ir"]
    next(n for n in ir["nodes"] if n["id"] == "n_rt")["config"]["cases"][0]["when"] = 'state.urgency == "urgent"'
    await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": ir}, headers={"If-Match": str(cur["version"])})
    run = (await api_client.post(f"{base}/run", json={})).json()
    assert (run["passed"], run["failed"]) == (1, 1)
    red = next(r for r in run["results"] if r["id"] == case["id"])
    assert "status: expected interrupted, got success" in red["failures"]
    assert "route: did not run n_esc, n_ok; also ran n_ag" in red["failures"]
    assert any(f.startswith("channel 'summary'") for f in red["failures"])

    old = (await api_client.post(f"{base}/run", json={"version": "1.0.0"})).json()  # the version is still green
    assert old["failed"] == 0
    assert (await api_client.get(base)).json()[0]["last_result"]["passed"] is False  # version runs are not recorded
    await api_client.delete(f"{base}/{case['id']}")
    assert len((await api_client.get(base)).json()) == 1


async def test_test_case_runs_and_publish_hold_no_database_connection_meanwhile(app, api_client, monkeypatch):
    """C-5: a case can run for minutes; the request's connection goes back to the pool while it does."""
    from agentcanvas.services import testcases

    pool = app.state.db.engine.pool
    held: list[int] = []
    run_case = testcases.run_case

    async def counted(*args, **kwargs):
        held.append(pool.checkedout())
        return await run_case(*args, **kwargs)

    monkeypatch.setattr(testcases, "run_case", counted)
    wf = await new_triage(api_client)
    base = f"/v1/workflows/{wf['id']}/test-cases"
    assert (await api_client.post(base, json=HIGH_CASE)).status_code == 201
    run = (await api_client.post(f"{base}/run", json={})).json()
    assert run["passed"] == 1, run
    assert (await api_client.get(base)).json()[0]["last_result"]["passed"] is True  # still recorded
    publish = await api_client.post(f"/v1/workflows/{wf['id']}/publish", json={})
    assert publish.status_code == 200, publish.text
    assert held == [0, 0]


@pytest.mark.parametrize("bad", [{"name": ""}, {"name": "x", "expect": {"status": "maybe"}}])
async def test_test_case_validation(api_client, bad):
    wf = await new_triage(api_client)
    assert (await api_client.post(f"/v1/workflows/{wf['id']}/test-cases", json=bad)).status_code == 422
