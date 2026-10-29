"""Annotation queues (S13): rules route flagged production runs, reviewers score them and correct dataset examples."""

from __future__ import annotations

import pytest
from test_datasets import new_dataset
from test_online import call, deployed
from test_org_scope import act_as_b, org_b  # noqa: F401 - fixture
from test_publish import bearer, set_greeting

from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.db.models import AppUser, Membership
from agentcanvas.services.annotation import agreement, normalised, reasons

RUBRIC = [
    {"name": "helpful", "description": "answers the question", "scale": 5},
    {"name": "on topic", "scale": 2, "pass_at": 1},
]


@pytest.fixture
async def reviewers(app):
    async with app.state.db.sessionmaker() as s:
        for uid in ("usr_rev1", "usr_rev2"):
            s.add(AppUser(id=uid, oidc_subject=uid, email=f"{uid}@example.com"))
            await s.flush()
            s.add(Membership(org_id="org_default", user_id=uid, role="reviewer"))
        await s.commit()


def act_as(app, uid: str, role: str = "reviewer") -> None:
    app.dependency_overrides[current_user] = lambda: CurrentUser(id=uid, org_id="org_default", role=role)


async def queue(api, wf: str, **extra) -> dict:
    body = {"name": "Triage", "rubric": RUBRIC, **extra}
    r = await api.post(f"/v1/workflows/{wf}/annotation-queues", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_rules_route_runs_and_reviewers_annotate(app, api_client, reviewers):
    wf, dep, key, ev = await deployed(api_client)
    await api_client.post(f"/v1/deployments/{dep}/online-evaluators", json={"evaluator_id": ev["id"]})
    aq = await queue(api_client, wf, rules={"thumbs_down": True, "scores_below": {"greets": 0.5}}, reviewers_per_item=2)
    assert (
        await api_client.post(f"/v1/workflows/{wf}/annotation-queues", json={"name": "Triage", "rubric": RUBRIC})
    ).status_code == 409

    good = await call(app, api_client, dep, key, "Ada")
    assert (await api_client.get(f"/v1/annotation-queues/{aq['id']}/items")).json() == []
    # an end user rates the good answer down: it is queued
    fb = f"/v1/deployments/{dep}/runs/{good['run_id']}/feedback"
    assert (
        await api_client.post(fb, json={"rating": "down", "comment": "too short"}, headers=bearer(key))
    ).status_code == 204
    assert (await api_client.post(fb, json={"rating": "down"})).status_code == 401
    assert (
        await api_client.post(
            f"/v1/deployments/{dep}/runs/run_nope/feedback", json={"rating": "up"}, headers=bearer(key)
        )
    ).status_code == 404

    await set_greeting(api_client, wf, "Hey")  # a regression: the online evaluator scores 0, the rule queues it
    assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).status_code == 200
    bad = await call(app, api_client, dep, key, "Bob")
    items = (await api_client.get(f"/v1/annotation-queues/{aq['id']}/items")).json()
    assert [(i["run_id"], i["reason"]) for i in items] == [
        (good["run_id"], "thumbs_down"),
        (bad["run_id"], "score:greets"),
    ]

    ds = await new_dataset(api_client, wf, "Gate")
    item = items[1]
    act_as(app, "usr_rev1")
    try:
        detail = (await api_client.get(f"/v1/annotation-items/{item['id']}")).json()
        assert detail["input"] == {"who": "Bob"} and detail["output"]["out"] == "Hey Bob"
        assert [(s["evaluator"], s["score"]) for s in detail["scores"]] == [("greets", 0.0)]
        url = f"/v1/annotation-items/{item['id']}/annotations"
        bad_scores = await api_client.post(url, json={"scores": {"helpful": 6, "on topic": 1}})
        assert bad_scores.status_code == 422 and "1 to 5" in bad_scores.json()["detail"]
        assert (await api_client.post(url, json={"scores": {"helpful": 2}})).status_code == 422
        fix = {"dataset_id": ds["id"], "reference": {"out": "Hello Bob"}}
        r = await api_client.post(
            url, json={"scores": {"helpful": 2, "on topic": 2}, "comment": "wrong greeting", "add_to_dataset": fix}
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "pending" and r.json()["reviewed_by_me"]  # needs a second reviewer
        act_as(app, "usr_rev2")
        r = await api_client.post(url, json={"scores": {"helpful": 1, "on topic": 2}})
        assert r.json()["status"] == "done" and len(r.json()["annotations"]) == 2
        act_as(app, "usr_rev2", "viewer")
        assert (await api_client.post(url, json={"scores": {"helpful": 1, "on topic": 2}})).status_code == 403
    finally:
        app.dependency_overrides.pop(current_user)

    examples = (await api_client.get(f"/v1/datasets/{ds['id']}")).json()["items"]
    assert [(e["input"], e["reference"], e["metadata"]["run_id"]) for e in examples] == [
        ({"who": "Bob"}, {"out": "Hello Bob"}, bad["run_id"])
    ]
    scores = (await api_client.get(f"/v1/threads/{bad['thread_id']}/scores")).json()
    mine = [(s["evaluator"], s["score"], s["passed"]) for s in scores if s["created_by"] == "usr_rev1"]
    assert mine == [("helpful", 0.25, False), ("on topic", 1.0, True)]

    q = (await api_client.get(f"/v1/annotation-queues/{aq['id']}")).json()
    assert q["progress"] == {"total": 2, "pending": 1, "done": 1}
    assert q["agreement"] == {"helpful": 0.75, "on topic": 1.0}
    done = (await api_client.get(f"/v1/annotation-queues/{aq['id']}/items", params={"status": "done"})).json()
    assert [i["run_id"] for i in done] == [bad["run_id"]]
    audit = (await api_client.get("/v1/org/audit", params={"limit": 20})).json()
    assert any(e["target"] == f"item_id={item['id']}" for e in audit)


async def test_runs_are_added_by_hand(app, api_client, reviewers):
    wf, dep, key, _ = await deployed(api_client)
    aq = await queue(api_client, wf)
    run = await call(app, api_client, dep, key)
    act_as(app, "usr_rev1")
    try:
        url = f"/v1/annotation-queues/{aq['id']}/items"
        added = await api_client.post(url, json={"run_ids": [run["run_id"], run["run_id"]]})
        assert added.status_code == 200 and [i["reason"] for i in added.json()] == ["manual"]
        review = {"scores": {"helpful": 4, "on topic": 2}}
        r = await api_client.post(f"/v1/annotation-items/{added.json()[0]['id']}/annotations", json=review)
        assert r.status_code == 200, r.text
        [again] = (await api_client.post(url, json={"run_ids": [run["run_id"]]})).json()  # E-12: said 0 reviews
        assert again["reviews"] == 1 and again["reviewed_by_me"]
        assert (await api_client.post(url, json={"run_ids": ["run_nope"]})).status_code == 404
        assert (await api_client.delete(f"/v1/annotation-items/{added.json()[0]['id']}")).status_code == 403
        assert (await api_client.patch(f"/v1/annotation-queues/{aq['id']}", json={"name": "x"})).status_code == 403
    finally:
        app.dependency_overrides.pop(current_user)
    patched = await api_client.patch(
        f"/v1/annotation-queues/{aq['id']}", json={"rules": {"failed": True}, "instructions": "be kind"}
    )
    assert patched.json()["rules"]["failed"] and patched.json()["instructions"] == "be kind"
    [item] = (await api_client.get(f"/v1/annotation-queues/{aq['id']}/items", params={"status": "all"})).json()
    assert (await api_client.delete(f"/v1/annotation-items/{item['id']}")).status_code == 204
    assert (await api_client.delete(f"/v1/annotation-queues/{aq['id']}")).status_code == 204
    assert (await api_client.get(f"/v1/workflows/{wf}/annotation-queues")).json() == []


async def test_queues_are_org_scoped(app, api_client, org_b):  # noqa: F811
    wf, dep, key, _ = await deployed(api_client)
    aq = await queue(api_client, wf)
    run = await call(app, api_client, dep, key)
    [item] = (
        await api_client.post(f"/v1/annotation-queues/{aq['id']}/items", json={"run_ids": [run["run_id"]]})
    ).json()
    act_as_b(app)
    try:
        assert (await api_client.get(f"/v1/annotation-queues/{aq['id']}")).status_code == 404
        assert (await api_client.get(f"/v1/annotation-items/{item['id']}")).status_code == 404
        assert (await api_client.get(f"/v1/workflows/{wf}/annotation-queues")).status_code == 404
    finally:
        app.dependency_overrides.pop(current_user)


def test_rules_and_agreement():
    rules = {"failed": True, "thumbs_down": False, "scores_below": {"polite": 0.7}}
    assert reasons(rules, status="error", thumbs_down=True, scores={"polite": 0.2}) == ["failed", "score:polite"]
    assert reasons(rules, status="success", thumbs_down=True, scores={"polite": None}) == []
    assert normalised(3, 5) == 0.5 and normalised(2, 2) == 1.0
    rubric = [{"name": "a", "scale": 5}, {"name": "b", "scale": 2}]
    by_item = {"i1": [{"a": 5, "b": 2}, {"a": 1, "b": 2}, {"a": 5, "b": 1}], "i2": [{"a": 3, "b": 1}]}
    assert agreement(rubric, by_item) == {"a": 0.3333, "b": 0.3333}
    assert agreement(rubric, {"i2": [{"a": 3, "b": 1}]}) == {"a": None, "b": None}
