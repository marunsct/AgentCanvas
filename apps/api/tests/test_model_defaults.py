"""One model rule (Settings > Models): what names its model uses it; otherwise the project's default for the purpose,
then the project's model, else a sentence saying what to choose. Defaults are applied when something is saved."""

from __future__ import annotations

from test_datasets import new_dataset
from test_simulation import SIM, make_chat
from test_versions import new_triage

P = "/v1/projects/prj_default"
JUDGE = {"rubric": "Is it polite?"}


async def test_every_purpose_resolves_and_says_why(api_client):
    out = (await api_client.get(f"{P}/model-defaults")).json()
    by = {r["purpose"]: r for r in out["resolved"]}
    assert set(by) == {"chat", "judge", "simulated_user", "embedding"} and out["defaults"] == {}
    assert by["chat"]["model"] is None and "Settings > Models" in by["chat"]["problem"]
    assert by["embedding"]["model"] == "builtin"

    await api_client.put(f"{P}/setup", json={"model": "openai:gpt-5.5"})
    judge = (await api_client.get(f"{P}/models/resolve", params={"purpose": "judge"})).json()
    assert judge == {"purpose": "judge", "model": "openai:gpt-5.5", "origin": "the project's model (Settings > Models)",
                     "problem": None}  # fmt: skip

    r = await api_client.put(f"{P}/model-defaults", json={"judge": "anthropic:claude-haiku-4-5"})
    assert r.status_code == 200 and r.json()["defaults"] == {"judge": "anthropic:claude-haiku-4-5"}
    judge = (await api_client.get(f"{P}/models/resolve", params={"purpose": "judge"})).json()
    assert judge["model"] == "anthropic:claude-haiku-4-5" and "default for judge evaluators" in judge["origin"]
    # leaving a purpose out keeps it; null clears it
    kept = (await api_client.put(f"{P}/model-defaults", json={"simulated_user": "openai:gpt-5-mini"})).json()
    assert kept["defaults"] == {"judge": "anthropic:claude-haiku-4-5", "simulated_user": "openai:gpt-5-mini"}
    cleared = (await api_client.put(f"{P}/model-defaults", json={"judge": None})).json()
    assert cleared["defaults"] == {"simulated_user": "openai:gpt-5-mini"}
    for bad in ({"judge": "nobody:nothing"}, {"judge": "no-colon"}, {"embedding": "x"}):
        assert (await api_client.put(f"{P}/model-defaults", json=bad)).status_code == 422, bad
    assert (await api_client.get(f"{P}/models/resolve", params={"purpose": "nope"})).status_code == 422


async def test_a_judge_saved_without_a_model_gets_the_projects(api_client):
    wf = (await new_triage(api_client))["id"]
    url = f"/v1/workflows/{wf}/evaluators"
    refused = await api_client.post(url, json={"name": "polite", "kind": "llm_judge", "config": JUDGE})
    assert refused.status_code == 422 and "Settings > Models" in refused.json()["detail"]

    await api_client.put(f"{P}/model-defaults", json={"judge": "anthropic:claude-haiku-4-5"})
    made = (await api_client.post(url, json={"name": "polite", "kind": "llm_judge", "config": JUDGE})).json()
    assert made["config"]["model"] == "anthropic:claude-haiku-4-5"
    await api_client.put(f"{P}/model-defaults", json={"judge": "openai:gpt-5-mini"})  # later defaults: not retroactive
    kept = (await api_client.get(url)).json()
    assert [e["config"]["model"] for e in kept if e["name"] == "polite"] == ["anthropic:claude-haiku-4-5"]
    named = await api_client.post(
        url, json={"name": "own", "kind": "pairwise", "config": {**JUDGE, "model": "openai:gpt-5.5"}}
    )
    assert named.json()["config"]["model"] == "openai:gpt-5.5"  # a named model wins


async def test_a_simulated_user_and_a_knowledge_base_take_the_projects_defaults(api_client):
    wf = await make_chat(api_client)
    ds = await new_dataset(api_client, wf)
    examples = {"examples": [{"input": {}, "metadata": {"goal": "plan a trip"}}]}
    assert (await api_client.post(f"/v1/datasets/{ds['id']}/examples", json=examples)).status_code == 200
    sim = {k: v for k, v in SIM.items() if k != "model"}
    refused = await api_client.post(f"/v1/workflows/{wf}/experiments", json={"dataset_id": ds["id"], "simulation": sim})
    assert refused.status_code == 422 and "simulated users" in refused.json()["detail"]
    await api_client.put(f"{P}/model-defaults", json={"simulated_user": "anthropic:claude-haiku-4-5"})
    made = await api_client.post(f"/v1/workflows/{wf}/experiments", json={"dataset_id": ds["id"], "simulation": sim})
    assert made.status_code == 202, made.text
    exp = (await api_client.get(f"/v1/experiments/{made.json()['id']}")).json()
    assert exp["simulation"]["model"] == "anthropic:claude-haiku-4-5"

    await api_client.put(f"{P}/model-defaults", json={"embedding": "openai:text-embedding-3-small"})
    kb = await api_client.post(f"{P}/knowledge", json={"name": "handbook"})  # the default is used (and checked):
    assert kb.status_code == 422 and "openai:text-embedding-3-small" in kb.json()["detail"]  # no key on this server
    own = {"name": "own", "embedding": {"model": "builtin"}}  # a knowledge base that names its model keeps it
    assert (await api_client.post(f"{P}/knowledge", json=own)).status_code == 201
