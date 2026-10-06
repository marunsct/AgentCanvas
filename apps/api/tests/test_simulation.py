"""Simulated users (S13 FR-EVAL-07): an experiment holds a conversation per example with a chat workflow."""

from __future__ import annotations

from test_datasets import new_dataset
from test_evaluators import HAIKU, new
from test_experiments import finished
from test_publish import make_greeter

from agentcanvas.testing import DemoResources

P = "/v1/projects/prj_default"


def chat(wf_id: str) -> dict:
    return {
        "id": wf_id, "name": "Chat",
        "state": {"channels": [{"name": "messages", "type": {"type": "array"}, "reducer": "add_messages"}]},
        "resources": [{"id": "r_m", "type": "model.chat", "name": "model", "config": {"model": "fake:scripted"}}],
        "nodes": [{"id": "n_ag", "type": "core.agent", "name": "assistant", "config": {"system_prompt": "Help."}}],
        "edges": [
            {"id": "e1", "kind": "flow", "source": {"node": "START"}, "target": {"node": "n_ag"}},
            {"id": "e2", "kind": "flow", "source": {"node": "n_ag"}, "target": {"node": "END"}},
            {"id": "w1", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n_ag", "port": "model"}},
        ],
    }  # fmt: skip


async def make_chat(api) -> str:
    wf = (await api.post(f"{P}/workflows", json={"name": "Chat"})).json()
    r = await api.put(f"/v1/workflows/{wf['id']}", json={"ir": chat(wf["id"])}, headers={"If-Match": "1"})
    assert r.status_code == 200, r.text
    return wf["id"]


def script(app, monkeypatch, assistant: list[str], user: list[str]) -> list[str]:
    """Scripted assistant replies (one per turn) and simulated-user messages; returns the simulator's prompts."""
    from agentcanvas.services import experiments

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks({"model": assistant})
    lines, prompts = iter(user), []
    real = experiments.scorers

    async def simulator(system: str, prompt: str, model: str) -> str:
        prompts.append(f"{system}\n{prompt}")
        return next(lines)

    monkeypatch.setattr(
        experiments, "scorers", lambda runner, wf, usage=None: {**real(runner, wf, usage), "complete": simulator}
    )
    return prompts


SIM = {"model": HAIKU, "persona": "A curious traveller who writes short questions.", "goal": "plan a trip"}


async def experiment(app, api, wf: str, ds: str, **sim) -> tuple[dict, dict]:
    body = {"dataset_id": ds, "simulation": {**SIM, **sim}, "concurrency": 1}
    r = await api.post(f"/v1/workflows/{wf}/experiments", json=body)
    assert r.status_code == 202, r.text
    exp = await finished(app, api, r.json()["id"])
    [result] = (await api.get(f"/v1/experiments/{exp['id']}/results")).json()
    return exp, result


async def test_a_simulated_user_holds_a_conversation(app, api_client, monkeypatch):
    wf = await make_chat(api_client)
    ds = await new_dataset(api_client, wf)
    examples = [{"input": {}, "metadata": {"goal": "learn the capital of France"}}]
    assert (await api_client.post(f"/v1/datasets/{ds['id']}/examples", json={"examples": examples})).status_code == 200
    await new(api_client, wf, "mentions Paris", "regex", {"channel": "transcript", "pattern": "Paris"})
    prompts = script(
        app, monkeypatch, ["Paris.", "About 2 million people."],
        ["What is the capital of France?", "How many live there?", "DONE."],
    )  # fmt: skip

    exp, result = await experiment(app, api_client, wf, ds["id"], max_turns=5)
    assert exp["status"] == "done" and exp["simulation"]["max_turns"] == 5
    out = result["output"]
    assert out["transcript"] == [
        {"role": "user", "content": "What is the capital of France?"},
        {"role": "assistant", "content": "Paris."},
        {"role": "user", "content": "How many live there?"},
        {"role": "assistant", "content": "About 2 million people."},
    ]
    assert (out["turns"], out["ended"], result["run_status"]) == (2, "goal", "success")
    assert [(s["evaluator"], s["passed"]) for s in result["scores"]] == [("mentions Paris", True)]  # sees every turn
    history = (await api_client.get(f"/v1/threads/{result['thread_id']}/runs")).json()
    assert len(history) == 2  # one thread, a run per turn
    assert "Your goal: learn the capital of France" in prompts[0] and "has not started" in prompts[0]
    assert "Assistant: About 2 million people." in prompts[2] and "reply with exactly DONE" in prompts[2]


async def test_the_conversation_stops_at_max_turns_and_opens_with_the_example(app, api_client, monkeypatch):
    wf = await make_chat(api_client)
    ds = await new_dataset(api_client, wf)
    examples = [{"input": {"messages": [{"role": "user", "content": "Hi, I need a hotel."}]}}]
    assert (await api_client.post(f"/v1/datasets/{ds['id']}/examples", json={"examples": examples})).status_code == 200
    prompts = script(app, monkeypatch, ["Where to?", "Booked."], ["Rome, please.", "Thanks!"])
    _, result = await experiment(app, api_client, wf, ds["id"], max_turns=2)
    out = result["output"]
    assert [t["content"] for t in out["transcript"]] == ["Hi, I need a hotel.", "Where to?", "Rome, please.", "Booked."]
    assert (out["turns"], out["ended"]) == (2, "max_turns") and len(prompts) == 1


async def test_simulated_users_need_a_chat_workflow(api_client):
    greeter = await make_greeter(api_client)
    ds = await new_dataset(api_client, greeter)
    url = f"/v1/workflows/{greeter}/experiments"
    r = await api_client.post(url, json={"dataset_id": ds["id"], "simulation": SIM})
    assert r.status_code == 422  # no examples yet, or not a chat workflow: both refused
    examples = [{"input": {"who": "Ada"}}]
    assert (await api_client.post(f"/v1/datasets/{ds['id']}/examples", json={"examples": examples})).status_code == 200
    r = await api_client.post(url, json={"dataset_id": ds["id"], "simulation": SIM})
    assert r.status_code == 422 and "chat workflow" in r.json()["detail"]
    r = await api_client.post(url, json={"dataset_id": ds["id"], "simulation": {**SIM, "model": "nobody:nothing"}})
    assert r.status_code == 422 and "not a model in the catalog" in r.json()["detail"]
