"""G-8: a model block's `cheapest_of` list: each run uses the cheapest ready model the organisation's policies allow."""

from __future__ import annotations

from typing import Any

import pytest
from test_gateway import _events, _llm_ir, _make, _run

from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry
from agentcanvas.services import routing
from agentcanvas.validation import validate
from agentcanvas.validation.policies import Policy

MINI, HAIKU, BIG = "openai:gpt-5-mini", "anthropic:claude-haiku-4-5", "openai:gpt-5.5"
PRICES = {MINI: (1.0, 4.0), HAIKU: (0.8, 4.0), BIG: (5.0, 30.0)}  # USD per million tokens, in and out


@pytest.fixture(autouse=True)
def prices(monkeypatch):
    for model, (low, high) in PRICES.items():
        info = default_registry().model(model)
        assert info is not None, model
        monkeypatch.setattr(info, "price_in_per_mtok", low)
        monkeypatch.setattr(info, "price_out_per_mtok", high)
        monkeypatch.setattr(info, "retires", None)
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def block(model: str = BIG, **config: Any) -> dict[str, Any]:
    return {"id": "r_m", "type": "model.chat", "name": "m", "config": {"model": model, **config}}


def workflow(*resources: dict[str, Any], nodes: list[dict[str, Any]] | None = None, **rest: Any) -> Workflow:
    return Workflow.model_validate(
        {"id": "wf_r", "name": "routed", "resources": list(resources), "nodes": nodes or [], **rest}
    )


def pick(wf: Workflow, secrets: dict[str, str], policies: list[Policy] | None = None, **kw: Any):
    out, notes = routing.route(wf, default_registry(), secrets, policies or [], **kw)
    return out.resources[0].config, notes


def test_the_cheapest_ready_model_wins_and_the_notes_say_why():
    wf = workflow(block(cheapest_of=[BIG, MINI, HAIKU]))
    config, (note,) = pick(wf, {"OPENAI_API_KEY": "k", "ANTHROPIC_API_KEY": "k"})
    assert config["model"] == HAIKU  # 0.8 + 4 beats mini's 1 + 4 and the big model's 5 + 30
    assert note["chosen"] == HAIKU and note["default"] == BIG and note["resource"] == "m"
    assert {c["model"]: c.get("reason") for c in note["considered"]} == {
        BIG: f"dearer than {HAIKU}", MINI: f"dearer than {HAIKU}", HAIKU: None,
    }  # fmt: skip
    assert wf.resources[0].config["model"] == BIG  # the draft is untouched


def test_a_model_without_a_key_here_is_not_ready():
    config, (note,) = pick(workflow(block(cheapest_of=[HAIKU, MINI])), {"OPENAI_API_KEY": "k"})
    assert config["model"] == MINI
    assert [c["reason"] for c in note["considered"] if c["model"] == HAIKU] == ["no ANTHROPIC_API_KEY here"]


def test_the_blocks_own_key_makes_its_provider_ready(monkeypatch):
    own = block(BIG, cheapest_of=[MINI], api_key={"$secret": "MY_KEY"})
    assert pick(workflow(own), {})[0]["model"] == MINI
    assert pick(workflow(block(BIG, cheapest_of=[HAIKU], api_key={"$secret": "MY_KEY"})), {})[0]["model"] == BIG


def test_a_policy_that_forbids_a_model_skips_it():
    no_mini = Policy(name="no mini", scope="resource", match='resource.type == "model.chat"',
                     require=f'resource.config.model != "{MINI}"')  # fmt: skip
    wf = workflow(block(cheapest_of=[MINI, BIG, HAIKU]))
    config, (note,) = pick(wf, {"OPENAI_API_KEY": "k"}, [no_mini])
    assert config["model"] == BIG  # mini is not allowed, Anthropic has no key: the big one is the only one left
    assert [c["reason"] for c in note["considered"] if c["model"] == MINI] == [
        "an organisation policy does not allow it"
    ]
    # a rule about a step reads the models wired to it
    step = Policy(name="step models", scope="node", match='node.type == "core.llm"',
                  require=f'!node.models.exists(m, m == "{MINI}")')  # fmt: skip
    nodes = [{"id": "n", "type": "core.llm", "name": "ask", "config": {"prompt": "hi", "output": "out"}}]
    feed = [{"id": "w", "kind": "wiring", "source": {"node": "r_m"}, "target": {"node": "n", "port": "model"}}]
    wired = workflow(block(cheapest_of=[MINI, BIG]), nodes=nodes, edges=feed)
    assert pick(wired, {"OPENAI_API_KEY": "k"}, [step])[0]["model"] == BIG


def test_no_usable_candidate_keeps_the_blocks_own_model():
    config, (note,) = pick(workflow(block(cheapest_of=[HAIKU])), {})
    assert config["model"] == BIG and "no candidate usable" in note["reason"]


def test_unpriced_models_are_a_last_resort_and_local_ones_are_never_chosen(monkeypatch):
    monkeypatch.setattr(default_registry().model(MINI), "price_in_per_mtok", None)
    keys = {"OPENAI_API_KEY": "k", "ANTHROPIC_API_KEY": "k"}
    assert pick(workflow(block(cheapest_of=[MINI, BIG])), keys)[0]["model"] == BIG  # priced beats unpriced
    assert pick(workflow(block(cheapest_of=[MINI])), keys)[0]["model"] == MINI  # but unpriced beats nothing
    config, (note,) = pick(workflow(block(cheapest_of=["ollama:llama3.3", HAIKU])), keys)
    assert config["model"] == HAIKU
    assert "backup models" in next(c["reason"] for c in note["considered"] if c["model"] == "ollama:llama3.3")


def test_a_retired_model_is_not_ready(monkeypatch):
    from datetime import date

    monkeypatch.setattr(default_registry().model(MINI), "retires", date(2026, 1, 1))
    assert pick(workflow(block(cheapest_of=[MINI, BIG])), {"OPENAI_API_KEY": "k"})[0]["model"] == BIG


def test_backups_stay_and_the_winner_is_not_its_own_backup():
    wf = workflow(block(cheapest_of=[MINI], fallbacks=[MINI, HAIKU]))
    config, _ = pick(wf, {"OPENAI_API_KEY": "k"})
    assert config["model"] == MINI and config["fallbacks"] == [HAIKU]


def test_blocks_inside_a_subgraph_are_routed_too_and_the_rest_is_left_alone():
    inner = {"id": "wf_in", "name": "in", "resources": [block(BIG, cheapest_of=[MINI])]}
    sub = {"id": "n_sub", "type": "core.subgraph", "name": "inner", "config": {"workflow": inner}}
    plain = workflow(block(BIG), nodes=[sub])
    out, notes = routing.route(plain, default_registry(), {"OPENAI_API_KEY": "k"}, [])
    assert [(n["resource"], n["chosen"]) for n in notes] == [("inner/m", MINI)]
    assert out.nodes[0].config["workflow"]["resources"][0]["config"]["model"] == MINI
    assert out.resources[0].config["model"] == BIG
    unrouted = workflow(block(BIG))
    assert routing.route(unrouted, default_registry(), {}, []) == (unrouted, [])


def test_the_list_is_checked_by_the_editor():
    wf = workflow(block(cheapest_of=[MINI, MINI, "ollama:llama3.3", "openai:no-such-model"]))
    found = [(d.path, d.message) for d in validate(wf, default_registry()) if d.code == "W024"]
    assert [p for p, _ in found] == ["config.cheapest_of[1]", "config.cheapest_of[2]", "config.cheapest_of[3]"]
    assert "twice" in found[0][1] and "local model" in found[1][1] and "no catalog price" in found[2][1]
    assert not [d for d in validate(workflow(block(cheapest_of=[MINI, HAIKU])), default_registry()) if d.code == "W024"]


# ---------------------------------------------------------------- in a run ----
async def test_a_run_uses_the_chosen_model_and_says_so(app, api_client):
    from agentcanvas.testing import DemoResources

    demo = DemoResources()
    app.state.resources_factory = lambda thread_id, wf: demo
    assert (
        await api_client.put("/v1/projects/prj_default/secrets/OPENAI_API_KEY", json={"value": "sk-test-key-123"})
    ).status_code == 204
    wf = await _make(api_client, _llm_ir({"model": BIG, "cheapest_of": [BIG, MINI, HAIKU]}))
    events = _events((await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"q": "hi"}})).text)
    (note,) = [e["value"] for e in events if e.get("name") == "ac.route"]
    assert note["chosen"] == MINI and note["default"] == BIG
    used = {e["value"]["model"] for e in events if e.get("name") == "ac.usage"}
    assert used == {MINI}, used  # the cheaper model answered, and its price is what the run was charged
    fin = events[-1]
    assert fin["type"] == "RUN_FINISHED" and fin["usage"]["cost_usd"] > 0
    # the draft keeps both settings
    ir = (await api_client.get(f"/v1/workflows/{wf}")).json()["ir"]
    assert ir["resources"][0]["config"]["model"] == BIG


async def test_a_run_without_a_ready_candidate_uses_the_blocks_model(app, api_client):
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources()
    wf = await _make(api_client, _llm_ir({"model": BIG, "cheapest_of": [HAIKU]}))
    events = await _run(api_client, wf)
    (note,) = [e["value"] for e in events if e.get("name") == "ac.route"]
    assert note["chosen"] == BIG and "no candidate usable" in note["reason"]
    assert events[-1]["type"] == "RUN_FINISHED"
