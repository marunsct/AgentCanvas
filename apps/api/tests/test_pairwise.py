"""Pairwise and composite evaluators (S13, FR-EVAL-03): position-swapped comparisons and combined scores."""

from __future__ import annotations

import json
import re

import pytest
from test_datasets import new_dataset
from test_evaluators import HAIKU, attempt, new
from test_experiments import EXAMPLES, finished, setup
from test_gates import check
from test_publish import make_greeter, set_greeting

from agentcanvas.evaluation import Observation, Spec, pairwise
from agentcanvas.services import experiments

JUDGE = {"model": HAIKU, "rubric": "Which greeting is the house style ('Hello')?"}


async def prefers_hello(system: str, prompt: str, model: str) -> str:
    """A judge that reads both outputs: the one saying Hello wins."""
    a, b = re.search(r"Output A:\n(.*)\n\nOutput B:\n(.*)", prompt, re.S).groups()  # type: ignore[union-attr]
    winner = "tie" if ("Hello" in a) == ("Hello" in b) else ("A" if "Hello" in a else "B")
    return json.dumps({"winner": winner, "reasoning": f"{winner} uses the house greeting"})


async def always_a(system: str, prompt: str, model: str) -> str:
    return '{"winner": "A", "reasoning": "first is best"}'


@pytest.fixture
def judge(monkeypatch):
    real = experiments.scorers

    def patched(runner, wf, usage=None):
        return {**real(runner, wf, usage), "complete": prefers_hello}

    monkeypatch.setattr(experiments, "scorers", patched)


async def test_pairwise_swaps_positions():
    spec = Spec(name="better", kind="pairwise", config=JUDGE)
    hello, hey = Observation(output={"out": "Hello Ada"}), Observation(output={"out": "Hey Ada"})
    assert (await pairwise(spec, {"who": "Ada"}, hey, hello, prefers_hello)).score == 1.0
    assert (await pairwise(spec, {"who": "Ada"}, hello, hey, prefers_hello)).score == 0.0
    biased = await pairwise(spec, {"who": "Ada"}, hey, hello, always_a)  # prefers a position, not an output
    assert biased.score == 0.5 and biased.passed and "disagreed" in biased.comment
    failed = Observation(status="error", error="boom")
    assert (await pairwise(spec, {}, failed, hey, always_a)).score == 1.0
    assert (await pairwise(spec, {}, failed, failed, always_a)).score == 0.5

    async def mute(system: str, prompt: str, model: str) -> str:
        return "no idea"

    broken = await pairwise(spec, {}, hello, hey, mute)
    assert broken.score is None and "no winner" in broken.comment


async def test_composite_scores_its_parts(api_client):
    wf, _ = await setup(api_client)
    both = await new(api_client, wf, "both", "composite", {"evaluators": ["exact", "greets"], "weights": {"exact": 3}})
    either = await new(api_client, wf, "either", "composite", {"evaluators": ["exact", "greets"], "mode": "any"})
    assert both["effective_threshold"] == 0.7
    hit = await attempt(api_client, both, output={"out": "Hello Ada"}, reference={"out": "Hello Ada"})
    assert hit["score"] == 1.0 and hit["passed"] and hit["comment"] == "exact 1.00, greets 1.00"
    half = await attempt(api_client, both, output={"out": "Hey Ada"}, reference={"out": "Hello Ada"})
    assert half["score"] == 0.25 and not half["passed"]
    assert (await attempt(api_client, either, output={"out": "Hey Ada"}, reference={"out": "Hello Ada"}))[
        "score"
    ] == 1.0
    unscored = await attempt(api_client, both, output={"out": "Hey"})  # no reference: only greets scores
    assert unscored["score"] == 1.0

    await new(api_client, wf, "better", "pairwise", JUDGE)
    url = f"/v1/workflows/{wf}/evaluators"
    for config, error in [
        ({"evaluators": ["nope"]}, "'nope' is not another evaluator"),
        ({"evaluators": ["exact", "both"]}, "is a composite evaluator"),
        ({"evaluators": ["better"]}, "is a pairwise evaluator"),
        ({"evaluators": ["exact"], "weights": {"greets": 1}}, "weights for evaluators it does not combine"),
        ({"evaluators": ["exact"], "weights": {"exact": 0}}, "weights are positive"),
    ]:
        r = await api_client.post(url, json={"name": "c", "kind": "composite", "config": config})
        assert r.status_code == 422 and error in r.json()["detail"], (config, r.text)
    r = await api_client.post(url, json={"name": "self", "kind": "composite", "config": {"evaluators": ["self"]}})
    assert r.status_code == 422


async def test_pairwise_in_experiments(app, api_client, judge):
    wf, ds = await setup(api_client)  # v1.0.0 says Hello, the draft says Hey
    better = await new(api_client, wf, "better", "pairwise", JUDGE)
    both = await new(api_client, wf, "both", "composite", {"evaluators": ["exact", "greets"]})
    tried = await attempt(api_client, better, output={"out": "Hello"})
    assert tried["score"] is None and "experiments only" in tried["comment"]
    body = {"dataset_id": ds["id"], "targets": [{"version": "1.0.0"}, {}], "evaluator_ids": [better["id"], both["id"]]}
    r = await api_client.post(f"/v1/workflows/{wf}/experiments", json=body)
    assert r.status_code == 202, r.text
    exp = await finished(app, api_client, r.json()["id"])
    assert exp["status"] == "done", exp
    v1, draft = ({e["evaluator"]: e for e in t["evaluators"]} for t in exp["summary"])
    assert (v1["better"]["mean"], draft["better"]["mean"]) == (1.0, 0.0)
    assert draft["better"]["ci"] == [0.0, 0.0] and v1["both"]["ci"] is None
    assert draft["both"]["mean"] == 0.5 and v1["both"]["mean"] == 0.8333  # Cy expects "Hi Cy"
    for target, by_name in zip(exp["summary"], (v1, draft), strict=True):  # a pairwise loss is not a failed answer
        assert target["pass_rate"] == by_name["both"]["pass_rate"]
    results = (await api_client.get(f"/v1/experiments/{exp['id']}/results")).json()
    verdicts = [s for res in results for s in res["scores"] if s["evaluator"] == "better"]
    assert len(verdicts) == 2 * len(EXAMPLES) and all("house greeting" in s["comment"] for s in verdicts)

    [dep] = (await api_client.get(f"/v1/workflows/{wf}/deployments")).json()
    online = await api_client.post(
        f"/v1/deployments/{dep['id']}/online-evaluators", json={"evaluator_id": better["id"]}
    )
    assert online.status_code == 422


async def test_a_gate_on_a_pairwise_judge(app, api_client, judge):
    wf = await make_greeter(api_client)
    ds = await new_dataset(api_client, wf)
    examples = [{"input": {"who": w}} for w in ("Ada", "Bob", "Cy")]
    assert (await api_client.post(f"/v1/datasets/{ds['id']}/examples", json={"examples": examples})).status_code == 200
    await new(api_client, wf, "better", "pairwise", JUDGE)
    r = await api_client.put(f"/v1/workflows/{wf}/gates/prod", json={"dataset_id": ds["id"]})
    assert r.status_code == 200, r.text

    first = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert first.status_code == 200, first.text
    assert check(first.json())["items"] == ["better: no live version to compare with"]

    await set_greeting(api_client, wf, "Hey")
    refused = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert refused.status_code == 422, refused.text
    assert check(refused.json())["items"] == ["better: win rate vs live 0.0 (needs 0.5)"]

    await set_greeting(api_client, wf, "Hello")  # as good as live: ties pass
    same = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert same.status_code == 200, same.text
    assert check(same.json())["items"] == ["better: win rate vs live 0.5 (needs 0.5)"]


async def test_pairwise_judges_each_repetition_against_its_own(app, api_client, judge):
    """F-6: with repeated runs, the n-th run of an example is compared with the first target's n-th run (pairing by
    example alone kept only the last run of each, so the others got no verdict)."""
    wf, ds = await setup(api_client)
    better = await new(api_client, wf, "better", "pairwise", JUDGE)
    body = {"dataset_id": ds["id"], "targets": [{"version": "1.0.0"}, {}], "evaluator_ids": [better["id"]],
            "repetitions": 2}  # fmt: skip
    r = await api_client.post(f"/v1/workflows/{wf}/experiments", json=body)
    assert r.status_code == 202, r.text
    exp = await finished(app, api_client, r.json()["id"])
    assert exp["status"] == "done", exp
    results = (await api_client.get(f"/v1/experiments/{exp['id']}/results")).json()
    assert len(results) == 2 * 2 * len(EXAMPLES)
    assert all(len([s for s in x["scores"] if s["evaluator"] == "better"]) == 1 for x in results)
