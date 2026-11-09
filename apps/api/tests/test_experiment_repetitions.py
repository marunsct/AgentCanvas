"""F-6: an experiment runs each example k times and reports pass@k, pass^k, flaky examples and score spread."""

from __future__ import annotations

from types import SimpleNamespace

from test_experiments import EXAMPLES, finished, setup

from agentcanvas.ir.models import Workflow
from agentcanvas.services.experiments import MAX_REPETITIONS, summary, uncached


def _row(example: str, passed: bool | None, score: float | None = None, target: int = 0):
    scores = [] if passed is None else [{"evaluator": "ok", "score": score if score is not None else float(passed),
                                         "passed": passed}]  # fmt: skip
    return SimpleNamespace(target=target, example_id=example, status="done", run_status="success", scores=scores,
                           latency_ms=10, tokens_in=1, tokens_out=1, cost_usd=0.0)  # fmt: skip


def test_pass_at_k_pass_all_k_and_flaky_examples():
    exp = SimpleNamespace(repetitions=3, targets=[{"label": "draft"}],
                          evaluators=[{"name": "ok", "kind": "exact"}])  # fmt: skip
    rows = [
        *(_row("always", True) for _ in range(3)),
        _row("sometimes", True, 0.9),
        _row("sometimes", False, 0.3),
        _row("sometimes", True, 0.9),
        *(_row("never", False) for _ in range(3)),
    ]
    [t] = summary(exp, rows)  # type: ignore[arg-type]
    assert (t.examples, t.runs, t.completed, t.repetitions) == (3, 9, 9, 3)
    assert t.pass_at_k == round(2 / 3, 4)  # always and sometimes passed at least once
    assert t.pass_all_k == round(1 / 3, 4)  # only always passed every time
    assert t.flaky == ["sometimes"]
    assert t.pass_rate == round(5 / 9, 4)  # per run, as before
    [ok] = t.evaluators
    assert ok.spread is not None and 0 < ok.spread < 0.2  # only "sometimes" varied (0.9, 0.3, 0.9)


def test_one_repetition_reports_as_before():
    exp = SimpleNamespace(repetitions=1, targets=[{"label": "draft"}], evaluators=[{"name": "ok", "kind": "exact"}])
    [t] = summary(exp, [_row("a", True), _row("b", False)])  # type: ignore[arg-type]
    assert (t.examples, t.runs, t.pass_at_k, t.pass_all_k, t.flaky, t.evaluators[0].spread) == (2, 2, None, None,
                                                                                                [], None)  # fmt: skip


def test_an_example_not_finished_every_time_is_not_counted_as_passing_every_time():
    exp = SimpleNamespace(repetitions=2, targets=[{"label": "draft"}], evaluators=[{"name": "ok", "kind": "exact"}])
    pending = SimpleNamespace(**{**vars(_row("a", True)), "status": "pending"})
    [t] = summary(exp, [_row("a", True), pending])  # type: ignore[arg-type]
    assert t.pass_at_k == 1.0 and t.pass_all_k == 0.0


def test_repeated_runs_do_not_take_answers_from_the_cache():
    wf = Workflow.model_validate({
        "id": "wf_x", "name": "x", "state": {"channels": [{"name": "out"}]}, "nodes": [], "edges": [],
        "resources": [{"id": "r_m", "type": "model.chat", "name": "m",
                       "config": {"model": "openai:gpt-5.5", "cache_ttl": 600}}],
    })  # fmt: skip
    plain = uncached(wf)
    assert "cache_ttl" not in plain.resources[0].config and wf.resources[0].config["cache_ttl"] == 600


async def test_an_experiment_runs_each_example_k_times(app, api_client):
    wf, ds = await setup(api_client)
    body = {"dataset_id": ds["id"], "repetitions": 3}
    r = await api_client.post(f"/v1/workflows/{wf}/experiments", json=body)
    assert r.status_code == 202, r.text
    exp = await finished(app, api_client, r.json()["id"])
    assert exp["status"] == "done" and exp["repetitions"] == 3
    [t] = exp["summary"]
    assert (t["examples"], t["runs"], t["completed"]) == (len(EXAMPLES), 3 * len(EXAMPLES), 3 * len(EXAMPLES))
    assert t["pass_at_k"] == t["pass_all_k"] and t["flaky"] == []  # the greeter answers the same every time
    results = (await api_client.get(f"/v1/experiments/{exp['id']}/results")).json()
    assert sorted(x["repetition"] for x in results if x["example_id"] == results[0]["example_id"]) == [0, 1, 2]
    assert len({x["run_id"] for x in results}) == 3 * len(EXAMPLES)  # every repetition is its own run
    for bad in (0, MAX_REPETITIONS + 1):
        refused = await api_client.post(f"/v1/workflows/{wf}/experiments", json={**body, "repetitions": bad})
        assert refused.status_code == 422
