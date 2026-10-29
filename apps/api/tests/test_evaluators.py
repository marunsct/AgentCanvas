"""Evaluators (S12 FR-EVAL-03): exact, regex, JSON schema, LLM judge, trajectory and workspace-code scoring."""

from __future__ import annotations

import pytest
from test_org_scope import act_as_b, org_b  # noqa: F401 - fixture
from test_publish import make_greeter

from agentcanvas.api.deps import current_user
from agentcanvas.evaluation import Observation, Spec, score, trajectory_of, trajectory_score

P = "/v1/projects/prj_default"
HAIKU = "anthropic:claude-haiku-4-5"
SHAPE = {"channel": "out", "schema": {"type": "object", "required": ["id"]}}


async def new(api, wf: str, name: str, kind: str, config: dict, **extra) -> dict:
    r = await api.post(f"/v1/workflows/{wf}/evaluators", json={"name": name, "kind": kind, "config": config, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def attempt(api, evaluator: dict, **obs) -> dict:
    r = await api.post(f"/v1/evaluators/{evaluator['id']}/try", json=obs)
    assert r.status_code == 200, r.text
    return r.json()


async def test_deterministic_evaluators(api_client):
    wf = await make_greeter(api_client)
    exact = await new(api_client, wf, "exact", "exact", {})
    assert exact["effective_threshold"] == 1.0 and exact["threshold"] is None
    hit = await attempt(api_client, exact, output={"out": "Hello Ada"}, reference={"out": "Hello Ada"})
    assert hit == {"evaluator": "exact", "score": 1.0, "passed": True, "comment": ""}
    miss = await attempt(api_client, exact, output={"out": "Hi"}, reference={"out": "Hello Ada", "n": 1})
    assert miss["score"] == 0.0 and not miss["passed"] and miss["comment"] == "differs: out, n"
    none = await attempt(api_client, exact, output={"out": "Hi"})
    assert none["score"] is None and not none["passed"]

    rx = await new(api_client, wf, "greets", "regex", {"channel": "out", "pattern": r"^Hello \w+$"})
    assert (await attempt(api_client, rx, output={"out": "Hello Ada"}))["passed"]
    assert (await attempt(api_client, rx, output={"other": 1}))["comment"] == "the run did not produce 'out'"

    js = await new(api_client, wf, "shape", "json_schema", SHAPE)
    assert js["config"]["schema"]["required"] == ["id"]
    assert (await attempt(api_client, js, output={"out": {"id": 1}}))["passed"]
    bad = await attempt(api_client, js, output={"out": {}})
    assert not bad["passed"] and "'id' is a required property" in bad["comment"]

    traj = await new(api_client, wf, "tools", "trajectory", {"mode": "subset"}, threshold=1)
    ok = await attempt(api_client, traj, trajectory=["search", "fetch", "search"], expected_trajectory=["search"])
    assert ok["passed"]
    assert not (await attempt(api_client, traj, trajectory=[], expected_trajectory=["search"]))["passed"]

    listed = (await api_client.get(f"/v1/workflows/{wf}/evaluators")).json()
    assert [e["name"] for e in listed] == ["exact", "greets", "shape", "tools"]


def test_trajectory_modes_and_events():
    assert trajectory_score("strict", ["a", "b"], ["a", "b"]) == 1
    assert trajectory_score("strict", ["b", "a"], ["a", "b"]) == 0
    assert trajectory_score("unordered", ["b", "a"], ["a", "b"]) == 1
    assert trajectory_score("unordered", ["a", "a", "b"], ["a", "b"]) == 0
    assert trajectory_score("subset", ["a", "c", "b"], ["a", "b"]) == 1
    assert trajectory_score("subset", ["a"], ["a", "a"]) == 0

    def reply(**r) -> dict:
        return {"type": "CUSTOM", "name": "ac.model_reply", "value": {"reply": r}}

    events = [
        reply(tool_calls=[{"name": "search"}]),
        {"type": "STEP_STARTED"},
        reply(content="done"),
        reply(tool_calls=[{"name": "a"}, {"name": "b"}]),
    ]
    assert trajectory_of(events) == ["search", "a", "b"]


async def test_configs_are_validated(api_client):
    wf = await make_greeter(api_client)
    for kind, config, says in [
        ("regex", {"channel": "out", "pattern": "("}, "invalid pattern"),
        ("regex", {"pattern": "x"}, "channel"),
        ("json_schema", {"channel": "out", "schema": {"type": "nope"}}, "invalid schema"),
        ("llm_judge", {"model": "nobody:nothing", "rubric": "polite?"}, "not a catalog model"),
        ("llm_judge", {"model": HAIKU, "rubric": ""}, "rubric"),
        ("code", {"function": "os:system"}, "scripts.<module>:<function>"),
        ("trajectory", {"mode": "fuzzy"}, "mode"),
    ]:
        r = await api_client.post(f"/v1/workflows/{wf}/evaluators", json={"name": "x", "kind": kind, "config": config})
        assert r.status_code == 422 and says in r.json()["detail"], f"{kind} {config}: {r.text}"
    e = await new(api_client, wf, "exact", "exact", {})
    dup = await api_client.post(f"/v1/workflows/{wf}/evaluators", json={"name": "exact", "kind": "trajectory"})
    assert dup.status_code == 409
    assert (await api_client.patch(f"/v1/evaluators/{e['id']}", json={"config": {"channels": 3}})).status_code == 422
    patched = (await api_client.patch(f"/v1/evaluators/{e['id']}", json={"threshold": 0.5, "name": "eq"})).json()
    assert patched["effective_threshold"] == 0.5 and patched["name"] == "eq"
    assert (await api_client.delete(f"/v1/evaluators/{e['id']}")).status_code == 204
    assert (await api_client.get(f"/v1/workflows/{wf}/evaluators")).json() == []


async def test_llm_judge_scores_with_the_model(app, api_client):
    from agentcanvas.testing import DemoResources

    wf = await make_greeter(api_client)
    judge = await new(api_client, wf, "polite", "llm_judge", {"model": HAIKU, "rubric": "Is it polite?"})
    assert judge["effective_threshold"] == 0.7

    def judge_says(reply: str) -> None:
        app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks({"judge": [reply]})

    judge_says('{"score": 0.9, "reasoning": "warm greeting"}')
    first = await attempt(api_client, judge, input={"who": "Ada"}, output={"out": "Hello Ada"})
    assert first == {"evaluator": "polite", "score": 0.9, "passed": True, "comment": "warm greeting"}
    judge_says("I'd say 0.4 at most.")
    second = await attempt(api_client, judge, output={"out": "Go away"})
    assert second["score"] == 0.4 and not second["passed"]
    judge_says("no idea")
    broken = await attempt(api_client, judge, output={"out": "?"})
    assert broken["score"] is None and "no score" in broken["comment"]


def test_the_judge_score_is_read_only_where_the_judge_gave_one():
    """E-7: any stray 0 or 1 in the judge's reply was taken as the score, "8/10" gave none, and a greedy match
    across two JSON objects failed to parse."""
    from agentcanvas.evaluation import _grade

    for reply in ("It lists 1 item then fails badly.", "Uses version 1.0.2 of the API; poor answer.", "no idea"):
        with pytest.raises(ValueError, match="no score"):
            _grade(reply)
    assert _grade("Solid answer, 8/10.")[0] == 0.8
    assert _grade("Score: 80%")[0] == 0.8
    assert _grade("score = 3 out of 5")[0] == 0.6
    assert _grade("The score is 0.25.")[0] == 0.25
    assert _grade("I'd say 0.4 at most.")[0] == 0.4
    two = 'Example: {"score": 1, "reasoning": "x"}. My verdict: {"score": 0.3, "reasoning": "rude"}'
    assert _grade(two) == (0.3, "rude")
    assert _grade('```json\n{"score": 0.5, "reasoning": "ok", "notes": {"tone": "flat"}}\n```') == (0.5, "ok")


async def test_code_evaluator_runs_workspace_code(api_client):
    wf = await make_greeter(api_client)
    src = (
        "def check(sample):\n"
        "    ok = sample['output'].get('out', '').endswith(sample['input']['who'])\n"
        "    return {'score': 1 if ok else 0, 'comment': 'names the user' if ok else 'wrong name'}\n"
    )
    assert (await api_client.put(f"{P}/files/scripts/evals.py", json={"content": src})).status_code in (200, 201, 204)
    code = await new(api_client, wf, "names", "code", {"function": "scripts.evals:check"})
    ok = await attempt(api_client, code, input={"who": "Ada"}, output={"out": "Hello Ada"})
    assert ok == {"evaluator": "names", "score": 1.0, "passed": True, "comment": "names the user"}
    assert (await attempt(api_client, code, input={"who": "Ada"}, output={"out": "Hello Bob"}))["score"] == 0.0
    missing = await new(api_client, wf, "missing", "code", {"function": "scripts.nope:check"})
    gone = await attempt(api_client, missing, input={"who": "Ada"})
    assert gone["score"] is None and gone["comment"]


async def test_evaluators_never_raise():
    spec = Spec(name="j", kind="llm_judge", config={"model": "x", "rubric": "r"})
    assert (await score(spec, Observation())).comment == "no model available for the judge"

    async def slow(system: str, prompt: str, model: str) -> str:
        raise TimeoutError("judge timed out")

    out = await score(spec, Observation(), complete=slow)
    assert out.score is None and not out.passed and "TimeoutError" in out.comment

    async def nan(system: str, prompt: str, model: str) -> str:
        return '{"score": NaN, "reasoning": "?"}'

    out = await score(spec, Observation(), complete=nan)  # min/max would have made NaN a perfect 1.0
    assert out.score is None and not out.passed and "finite" in out.comment

    code = Spec(name="c", kind="code", config={"function": "scripts.evals:check"})
    assert (await score(code, Observation(), script=lambda path: lambda obs: _number(float("inf")))).score is None


async def _number(value: float) -> float:
    return value


async def test_a_catastrophic_regex_times_out_without_blocking(monkeypatch):
    """`(a+)+$` takes minutes on 30 a's; it runs in a worker process with a time limit and the loop stays free."""
    import asyncio
    import time

    from agentcanvas import safe_regex

    monkeypatch.setattr(safe_regex, "TIMEOUT_S", 0.5)
    spec = Spec(name="r", kind="regex", config={"channel": "out", "pattern": "(a+)+$"})
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.05)
            ticks += 1

    task = asyncio.create_task(ticker())
    started = time.perf_counter()
    out = await score(spec, Observation(output={"out": "a" * 40 + "b"}))
    task.cancel()
    assert out.score is None and "TimeoutError" in out.comment
    assert time.perf_counter() - started < 10 and ticks >= 5  # the event loop kept running meanwhile
    ok = Spec(name="r", kind="regex", config={"channel": "out", "pattern": r"\bHello\b"})
    assert (await score(ok, Observation(output={"out": "Hello Ada"}))).score == 1.0  # a fresh pool after the timeout


async def test_evaluators_stay_in_their_organisation(app, api_client, org_b):  # noqa: F811
    wf = await make_greeter(api_client)
    e = await new(api_client, wf, "exact", "exact", {})
    act_as_b(app)
    try:
        assert (await api_client.get(f"/v1/workflows/{wf}/evaluators")).status_code == 404
        assert (await api_client.patch(f"/v1/evaluators/{e['id']}", json={"name": "mine"})).status_code == 404
        assert (await api_client.post(f"/v1/evaluators/{e['id']}/try", json={})).status_code == 404
        assert (await api_client.delete(f"/v1/evaluators/{e['id']}")).status_code == 404
    finally:
        app.dependency_overrides.pop(current_user)


async def test_every_kind_has_a_form(api_client):
    """The Evaluation page builds each kind's form from this: a label, what it checks, and a config schema without
    the fields the server sets; a judge's model is optional (the project's judge model fills it)."""
    kinds = {k["kind"]: k for k in (await api_client.get("/v1/evaluator-kinds")).json()}
    assert set(kinds) == {"exact", "regex", "json_schema", "llm_judge", "trajectory", "code", "composite", "pairwise",
                          "retrieval"}  # fmt: skip
    assert kinds["regex"]["config_schema"]["required"] == ["channel", "pattern"]
    assert "model" not in kinds["llm_judge"]["config_schema"]["required"] and kinds["llm_judge"]["label"]
    assert "pinned" not in kinds["code"]["config_schema"]["properties"]
    assert "parts" not in kinds["composite"]["config_schema"]["properties"]
    assert "schema" in kinds["json_schema"]["config_schema"]["properties"]  # the alias, as the API takes it
    assert kinds["pairwise"]["experiments_only"] and kinds["llm_judge"]["default_threshold"] == 0.7
