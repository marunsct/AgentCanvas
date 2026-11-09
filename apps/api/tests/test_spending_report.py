"""G-8: what an organisation's runs spent, per project, workflow and person, over a date range."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.db.models import AppUser, Membership, Organization, Project, Run, Thread, WorkflowRow

NOW = datetime.now(UTC).replace(microsecond=0)


def _run(thread: str, cost: str, days_ago: float, *, tin: int = 100, tout: int = 50, hits: int = 0, fb: int = 0) -> Run:
    return Run(
        id=f"run_{thread}_{days_ago}_{cost}", thread_id=thread, ir_hash="h", status="success", tokens_in=tin,
        tokens_out=tout, cost_usd=Decimal(cost), cache_hits=hits, fallbacks=fb,
        started_at=NOW - timedelta(days=days_ago),
    )  # fmt: skip


async def _seed(app, api) -> dict[str, str]:
    """Org A (the dev organisation): two projects, three workflows, two people. Org B: one project with a run."""
    p2 = (await api.post("/v1/projects", json={"name": "Support bots"})).json()["id"]
    wf1 = (await api.post("/v1/projects/prj_default/workflows", json={"name": "Triage"})).json()["id"]
    wf2 = (await api.post("/v1/projects/prj_default/workflows", json={"name": "Drafts"})).json()["id"]
    wf3 = (await api.post(f"/v1/projects/{p2}/workflows", json={"name": "Answers"})).json()["id"]
    async with app.state.db.sessionmaker() as s:
        s.add_all([AppUser(id="usr_ana", oidc_subject="ana", email="ana@example.com", display_name="Ana"),
                   AppUser(id="usr_ben", oidc_subject="ben", email="ben@example.com"),
                   Organization(id="org_b", name="B")])  # fmt: skip
        await s.flush()
        s.add_all([Membership(org_id="org_default", user_id="usr_ana", role="builder"),
                   Membership(org_id="org_default", user_id="usr_ben", role="viewer"),
                   Project(id="prj_b", org_id="org_b", name="B's project")])  # fmt: skip
        await s.flush()
        s.add(WorkflowRow(id="wf_b", project_id="prj_b", name="B flow", draft_ir={}, ir_hash="h"))
        await s.flush()
        threads = {"t_ana1": (wf1, "usr_ana"), "t_ana2": (wf3, "usr_ana"), "t_ben": (wf1, "usr_ben"),
                   "t_dep": (wf2, None), "t_b": ("wf_b", None)}  # fmt: skip
        for tid, (wf, who) in threads.items():
            s.add(Thread(id=tid, workflow_id=wf, created_by=who))
        await s.flush()
        s.add_all([
            _run("t_ana1", "1.5", 1, hits=2), _run("t_ana1", "0.5", 2, fb=1), _run("t_ana1", "9", 40),  # 40 days old
            _run("t_ana2", "0.25", 1), _run("t_ben", "2", 3, hits=1), _run("t_dep", "0", 1, tin=10, tout=5),
            _run("t_b", "7", 1),
        ])  # fmt: skip
        await s.commit()
    return {"p2": p2, "wf1": wf1, "wf2": wf2, "wf3": wf3}


async def test_spending_by_project_workflow_and_person(app, api_client):
    ids = await _seed(app, api_client)
    r = await api_client.get("/v1/org/spending")  # the last 30 days
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["total"] == {"runs": 5, "tokens_in": 410, "tokens_out": 205, "cost_usd": 4.25, "cache_hits": 3,
                            "fallbacks": 1}  # fmt: skip
    assert [(p["id"], p["cost_usd"]) for p in rep["by_project"]] == [("prj_default", 4.0), (ids["p2"], 0.25)]
    assert [(w["name"], w["cost_usd"], w["runs"]) for w in rep["by_workflow"]] == [
        ("Triage", 4.0, 3), ("Answers", 0.25, 1), ("Drafts", 0.0, 1),
    ]  # fmt: skip
    assert rep["by_workflow"][1]["project_id"] == ids["p2"]
    people = {p["id"]: p for p in rep["by_person"]}
    assert people["usr_ana"]["name"] == "Ana" and people["usr_ana"]["cost_usd"] == 2.25
    assert people["usr_ana"]["cache_hits"] == 2 and people["usr_ana"]["tokens_in"] == 300
    assert people["usr_ben"]["name"] == "ben@example.com" and people["usr_ben"]["cost_usd"] == 2.0
    assert people[None]["name"].startswith("No person") and people[None]["runs"] == 1
    assert [p["id"] for p in rep["by_person"]] == ["usr_ana", "usr_ben", None]  # biggest spender first
    assert "B flow" not in str(rep) and "prj_b" not in str(rep)  # nothing of another organisation


async def test_spending_takes_a_date_range_and_a_limit(app, api_client):
    await _seed(app, api_client)
    since, until = (NOW - timedelta(days=45)).isoformat(), (NOW - timedelta(days=30)).isoformat()
    old = (await api_client.get("/v1/org/spending", params={"since": since, "until": until})).json()
    assert old["total"]["runs"] == 1 and old["total"]["cost_usd"] == 9.0 and len(old["by_workflow"]) == 1
    day = (NOW - timedelta(days=2, hours=1)).isoformat()  # the runs of the last two days
    recent = (await api_client.get("/v1/org/spending", params={"since": day})).json()
    assert recent["total"]["runs"] == 4 and recent["total"]["cost_usd"] == 2.25
    r = await api_client.get("/v1/org/spending", params={"limit": 1})
    assert [w["name"] for w in r.json()["by_workflow"]] == ["Triage"] and r.json()["total"]["runs"] == 5
    bad = await api_client.get("/v1/org/spending", params={"since": until, "until": since})
    assert bad.status_code == 422


async def test_spending_is_for_admins_and_of_the_callers_organisation(app, api_client):
    await _seed(app, api_client)
    for role in ("viewer", "reviewer", "builder"):
        app.dependency_overrides[current_user] = lambda role=role: CurrentUser(
            id="usr_dev", org_id="org_default", role=role, memberships={"org_default": role}
        )
        assert (await api_client.get("/v1/org/spending")).status_code == 403, role
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="usr_x", org_id="org_b", role="admin", memberships={"org_b": "admin"}
    )
    try:
        rep = (await api_client.get("/v1/org/spending")).json()
    finally:
        app.dependency_overrides.pop(current_user, None)
    assert rep["total"]["cost_usd"] == 7.0 and [w["name"] for w in rep["by_workflow"]] == ["B flow"]


async def test_spending_by_deployment_names_the_deployments_and_the_runs_outside_them(app, api_client):
    from test_publish import make_greeter

    from agentcanvas.db.models import Thread

    await _seed(app, api_client)
    wf = await make_greeter(api_client)
    dep = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()["deployment"]["id"]
    async with app.state.db.sessionmaker() as s:
        s.add(Thread(id="t_published", workflow_id=wf, created_by=None, deployment_id=dep))
        await s.flush()
        s.add(_run("t_published", "3", 1))
        await s.commit()
    rep = (await api_client.get("/v1/org/spending")).json()
    by = {d["id"]: d for d in rep["by_deployment"]}
    assert by[dep]["name"] == "Greeter (prod)" and by[dep]["cost_usd"] == 3.0 and by[dep]["workflow_id"] == wf
    outside = by[None]  # the seeded conversations that no deployment started
    assert outside["name"].startswith("Not through a deployment") and outside["workflow_id"] is None
    assert outside["runs"] == 5 and outside["cost_usd"] == 4.25
    assert [d["id"] for d in rep["by_deployment"]] == [None, dep]  # biggest spender first (4.25, then 3)
    assert round(sum(d["cost_usd"] for d in rep["by_deployment"]), 6) == rep["total"]["cost_usd"]
