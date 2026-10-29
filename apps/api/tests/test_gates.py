"""Release gates (S12 FR-EVAL-05): publishing to a gated environment runs or reuses an evaluation first."""

from __future__ import annotations

from test_datasets import new_dataset
from test_publish import make_greeter, set_greeting

from agentcanvas.api.deps import CurrentUser, current_user

EXAMPLES = [{"input": {"who": w}, "reference": {"out": f"Hello {w}"}} for w in ("Ada", "Bob")]


async def gated(api, **gate) -> tuple[str, dict]:
    wf = await make_greeter(api)
    ds = await new_dataset(api, wf)
    assert (await api.post(f"/v1/datasets/{ds['id']}/examples", json={"examples": EXAMPLES})).status_code == 200
    body = {"name": "exact", "kind": "exact", "config": {}}
    assert (await api.post(f"/v1/workflows/{wf}/evaluators", json=body)).status_code == 201
    r = await api.put(f"/v1/workflows/{wf}/gates/prod", json={"dataset_id": ds["id"], **gate})
    assert r.status_code == 200, r.text
    return wf, ds


def check(body: dict, name: str = "evaluation") -> dict:
    checklist = body.get("checklist") or body.get("extensions", {}).get("checklist") or []
    return next(c for c in checklist if c["name"] == name)


async def test_a_gate_blocks_a_regression_and_reuses_evaluations(api_client):
    wf, _ = await gated(api_client, min_scores={"exact": 0.9}, max_regression=0.0)
    gates = (await api_client.get(f"/v1/workflows/{wf}/gates")).json()
    assert [(g["environment"], g["min_scores"], g["max_regression"]) for g in gates] == [("prod", {"exact": 0.9}, 0.0)]

    first = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert first.status_code == 200, first.text
    ev = check(first.json())
    assert ev["ok"] and ev["detail"] == "2 example(s) on dataset v2: every threshold met"
    assert ev["items"] == ["exact: mean 1.0 (needs 0.9)"]
    runs = (await api_client.get(f"/v1/workflows/{wf}/experiments")).json()
    assert [e["name"] for e in runs] == ["release gate: prod"]

    again = await api_client.post(f"/v1/workflows/{wf}/publish", json={})  # same workflow: the evaluation is reused
    assert again.status_code == 200 and check(again.json())["ok"]
    assert len((await api_client.get(f"/v1/workflows/{wf}/experiments")).json()) == 1

    await set_greeting(api_client, wf, "Hey")  # a regression
    refused = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert refused.status_code == 422, refused.text
    body = refused.json()
    assert "evaluation: 2 example(s) on dataset v2: 2 threshold(s) missed" in body["detail"]
    assert check(body)["items"] == [
        "exact: mean 0.0 (needs 0.9)",
        "pass rate 0% vs 100% for v1.0.0 (may drop 0%)",
    ]
    # the live version's evaluation was reused, only the candidate ran
    assert [len(e["targets"]) for e in (await api_client.get(f"/v1/workflows/{wf}/experiments")).json()] == [1, 1]

    over = await api_client.post(f"/v1/workflows/{wf}/publish", json={"override_reason": "hotfix, ticket 42"})
    assert over.status_code == 200, over.text
    assert check(over.json())["detail"].startswith("overridden by usr_dev: hotfix, ticket 42")
    [entry] = (await api_client.get("/v1/org/audit", params={"action": "publish", "limit": 1})).json()[:1]
    assert entry["detail"]["gate_override"] == "hotfix, ticket 42"
    assert entry["detail"]["gate_failures"][0] == "exact: mean 0.0 (needs 0.9)"

    staging = await api_client.post(f"/v1/workflows/{wf}/publish", json={"environment": "staging"})
    assert staging.status_code == 200 and all(c["name"] != "evaluation" for c in staging.json()["checklist"])
    assert (await api_client.delete(f"/v1/workflows/{wf}/gates/prod")).status_code == 204
    assert (await api_client.delete(f"/v1/workflows/{wf}/gates/prod")).status_code == 404


async def test_default_thresholds_come_from_the_evaluators(api_client):
    wf, _ = await gated(api_client)
    await set_greeting(api_client, wf, "Hey")
    refused = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert refused.status_code == 422 and check(refused.json())["items"] == ["exact: mean 0.0 (needs 1)"]


async def test_gates_are_checked_and_admin_only(app, api_client):
    wf, ds = await gated(api_client)
    other = await make_greeter(api_client)
    url = f"/v1/workflows/{wf}/gates/prod"
    assert (await api_client.put(url, json={"dataset_id": ds["id"], "min_scores": {"nope": 1}})).status_code == 422
    foreign = (await new_dataset(api_client, other))["id"]
    assert (await api_client.put(url, json={"dataset_id": foreign})).status_code == 404
    assert (await api_client.put(f"/v1/workflows/{wf}/gates/Prod!", json={"dataset_id": ds["id"]})).status_code == 422
    assert (await api_client.put(f"/v1/workflows/{other}/gates/prod", json={"dataset_id": foreign})).status_code == 422

    await set_greeting(api_client, wf, "Hey")
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_dev", org_id="org_default", role="builder")
    try:
        assert (await api_client.put(url, json={"dataset_id": ds["id"]})).status_code == 403
        assert (await api_client.delete(url)).status_code == 403
        r = await api_client.post(f"/v1/workflows/{wf}/publish", json={"override_reason": "trust me"})
        assert r.status_code == 403 and "admin" in r.json()["detail"]
        # nor can a builder lower the bar through what the gate measures with: the gate keeps its own copy
        [ev] = (await api_client.get(f"/v1/workflows/{wf}/evaluators")).json()
        r = await api_client.delete(f"/v1/datasets/{ds['id']}")  # would delete the gate with it
        assert r.status_code == 403 and "release gate" in r.json()["detail"], r.text
        easy = {"examples": [{"input": {"who": "x"}}]}
        assert (await api_client.post(f"/v1/datasets/{ds['id']}/examples", json=easy)).status_code == 200
        assert (await api_client.patch(f"/v1/evaluators/{ev['id']}", json={"threshold": 0})).status_code == 200
        refused = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
        assert refused.status_code == 422, refused.text  # still the saved dataset version and threshold
        assert check(refused.json())["items"] == ["exact: mean 0.0 (needs 1)"]
        assert "2 example(s) on dataset v2" in check(refused.json())["detail"]
    finally:
        app.dependency_overrides.pop(current_user)
    [gate] = (await api_client.get(f"/v1/workflows/{wf}/gates")).json()
    assert gate["dataset_version"] == 2 and gate["evaluators"] == ["exact"]
    resaved = await api_client.put(url, json={"dataset_id": ds["id"]})  # an admin picks up the changes
    assert resaved.json()["dataset_version"] == 3

    # a gate saved before it kept copies follows the live dataset and evaluators: those stay admin-only
    from sqlalchemy import null, update

    from agentcanvas.db.models import ReleaseGate

    async with app.state.db.sessionmaker() as s:
        await s.execute(update(ReleaseGate).values(dataset_version=None, evaluators=null()))  # as migrated
        await s.commit()
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_dev", org_id="org_default", role="builder")
    try:
        assert (await api_client.patch(f"/v1/evaluators/{ev['id']}", json={"threshold": 0})).status_code == 403
        assert (await api_client.post(f"/v1/datasets/{ds['id']}/examples", json=easy)).status_code == 403
    finally:
        app.dependency_overrides.pop(current_user)


async def test_examples_that_cannot_run_fail_the_gate(app, api_client, monkeypatch):
    wf, _ = await gated(api_client)
    runner = app.state.runner
    start = runner.start
    calls = 0

    async def flaky(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("no capacity")
        return await start(*args, **kwargs)

    monkeypatch.setattr(runner, "start", flaky)
    refused = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert refused.status_code == 422, refused.text
    assert check(refused.json())["items"][0] == "exact: mean 0.5 (needs 1)"  # the run that never started scores 0


async def test_an_evaluation_that_times_out_is_stopped_too(app, api_client, monkeypatch):
    """C-6: the gate fails on its timeout, and the experiment (and its model spend) stops with it."""
    import asyncio

    from agentcanvas.services import gates

    wf, _ = await gated(api_client)
    monkeypatch.setattr(gates, "GATE_TIMEOUT_S", 0.3)
    stuck = asyncio.Event()

    async def hangs(*_args, **_kwargs):
        await stuck.wait()  # a model call that never answers

    monkeypatch.setattr(app.state.runner, "start", hangs)
    refused = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert refused.status_code == 422, refused.text
    assert "did not finish" in check(refused.json())["detail"]
    [exp] = (await api_client.get(f"/v1/workflows/{wf}/experiments")).json()
    assert exp["status"] == "cancelled", exp
    assert not app.state.experiments.tasks


async def test_a_draft_changed_during_the_evaluation_is_not_published(api_client, monkeypatch):
    from agentcanvas.services import gates

    wf, _ = await gated(api_client)
    evaluate = gates.evaluate

    async def meanwhile(*args, **kwargs):
        check = await evaluate(*args, **kwargs)
        await set_greeting(api_client, wf, "Hey")  # an autosave lands while the gate runs
        return check

    monkeypatch.setattr(gates, "evaluate", meanwhile)
    r = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert r.status_code == 409 and "changed while it was being evaluated" in r.json()["detail"], r.text


async def test_a_builder_cannot_rewrite_a_gated_code_evaluator(app, api_client):
    """A gate pins the code its code evaluators run (and what that imports): rewriting it fails the gate."""
    from test_evaluators import P

    wf = await make_greeter(api_client)
    ds = await new_dataset(api_client, wf)
    assert (await api_client.post(f"/v1/datasets/{ds['id']}/examples", json={"examples": EXAMPLES})).status_code == 200
    files = {
        "scripts/strict.py": "def same(a, b):\n    return a == b\n",
        "scripts/gate_evals.py": "from scripts.strict import same\n\n\n"
        "def check(sample):\n    return float(same(sample['output'].get('out'), sample['reference'].get('out')))\n",
    }
    for path, src in files.items():
        assert (await api_client.put(f"{P}/files/{path}", json={"content": src})).status_code in (200, 201, 204)
    body = {"name": "same", "kind": "code", "config": {"function": "scripts.gate_evals:check", "pinned": "x"}}
    assert (await api_client.post(f"/v1/workflows/{wf}/evaluators", json=body)).status_code == 201
    [ev] = (await api_client.get(f"/v1/workflows/{wf}/evaluators")).json()
    assert "pinned" not in ev["config"]  # only a gate pins code
    url = f"/v1/workflows/{wf}/gates/prod"
    assert (await api_client.put(url, json={"dataset_id": ds["id"]})).status_code == 200
    await set_greeting(api_client, wf, "Hey")
    refused = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert refused.status_code == 422 and check(refused.json())["items"] == ["same: mean 0.0 (needs 1)"]

    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_dev", org_id="org_default", role="builder")
    try:  # a helper it imports, or the evaluator itself, now agrees with anything
        for path, src in {"scripts/strict.py": "def same(a, b):\n    return True\n",
                          "scripts/gate_evals.py": "def check(sample):\n    return 1.0\n"}.items():  # fmt: skip
            r = await api_client.put(f"{P}/files/{path}", json={"content": src})
            assert r.status_code in (200, 201, 204), r.text
            refused = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
            assert refused.status_code == 422, refused.text
            assert "changed since the release gate was saved" in check(refused.json())["detail"]
    finally:
        app.dependency_overrides.pop(current_user)
    assert (await api_client.put(url, json={"dataset_id": ds["id"]})).status_code == 200  # an admin accepts it
    r = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert r.status_code == 200, r.text


async def test_a_pinned_code_evaluator_scores_only_the_code_it_pinned():
    from agentcanvas.evaluation import Observation, Spec, score

    spec = Spec(name="c", kind="code", config={"function": "scripts.e:check", "pinned": "abc"})
    composite = Spec(name="all", kind="composite", config={"evaluators": ["c"], "parts": [spec.model_dump()]})
    for fingerprint, want in (("abc", 1.0), ("def", None)):
        tools = {"script": lambda _path: lambda _obs: _one(), "fingerprint": lambda _path, f=fingerprint: f}
        assert (await score(spec, Observation(), **tools)).score == want
        assert (await score(composite, Observation(), **tools)).score == (want if want else None)
    changed = await score(spec, Observation(), script=lambda _path: lambda _obs: _one())  # nothing to check against
    assert changed.score is None and "changed since the release gate was saved" in changed.comment


async def _one() -> float:
    return 1.0
