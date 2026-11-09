"""Fine-tuning export (G-10): accepted trajectories and datasets as JSONL (OpenAI chat format, plain messages)."""

from __future__ import annotations

import json
from types import SimpleNamespace

from test_datasets import new_dataset
from test_org_scope import act_as_b, org_b  # noqa: F401 - fixture
from test_run_search import ASKS, converse
from test_simulation import make_chat

from agentcanvas.services import finetune

URL = "/v1/workflows/{}/finetune-export"


def score(source: str, value: float | None, name: str = "helpful") -> SimpleNamespace:
    return SimpleNamespace(source=source, score=value, evaluator=name)


def test_a_run_is_accepted_on_human_scores_and_a_thumbs_down_vetoes_it():
    ok = [score("annotation", 1.0), score("annotation", 0.75, "polite")]
    assert finetune.accepted(ok, 0.8, False) == (True, "")
    assert finetune.accepted([score("annotation", 0.5)], 0.8, False) == (False, "its scores average 0.50, under 0.8")
    assert (
        finetune.accepted([score("annotation", 1.0), score("feedback", 0.0)], 0.5, False)[1] == "it got a thumbs-down"
    )
    assert finetune.accepted([], 0.8, False) == (False, "nobody has scored it")
    assert finetune.accepted([score("online", 1.0)], 0.8, False)[0] is False  # a judge's score is not a person's
    assert finetune.accepted([score("online", 1.0)], 0.8, True)[0] is True
    assert finetune.accepted([score("annotation", None)], 0.8, False)[0] is False


def reply(node: str, agent: str | None, value) -> dict:
    return {
        "type": "ac.model_reply",
        "value": {"resource": "m", "canvas_node_id": node, "agent": agent, "reply": value},
    }


EVENTS = [
    reply("n_cls", None, "high"),  # an earlier step of the workflow
    reply(
        "n_ag", "bot", {"content": "", "tool_calls": [{"name": "lookup", "args": {"order": "A-7", "express": True}}]}
    ),
    reply("n_ag", "researcher", "x"),  # a helper agent's
    {
        "type": "ac.tool_io",
        "value": {"agent": "bot", "tool": "lookup", "args": {"order": "A-7"}, "output": {"eta": "Friday"}},
    },
    reply("n_ag", "bot", "It arrives Friday."),
]


def test_the_final_model_steps_turns_become_messages_with_their_tool_results():
    turns = finetune.turns(EVENTS)
    assert turns == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"name": "lookup", "arguments": {"order": "A-7", "express": True}}],
        },
        {"role": "tool", "name": "lookup", "content": '{"eta": "Friday"}'},
        {"role": "assistant", "content": "It arrives Friday."},
    ]  # the classifier's reply and the helper agent's are left out
    assert finetune.turns([]) == []


def test_openai_format_has_json_string_arguments_matching_tool_call_ids_and_the_tools_listed():
    messages = [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Where is A-7?"},
        *finetune.turns(EVENTS),
    ]
    out = finetune.render(messages, "openai", {"run_id": "r1"})
    assert set(out) == {"messages", "tools"}  # the metadata is for the plain format
    assistant, tool, answer = out["messages"][2:]
    [call] = assistant["tool_calls"]
    assert call["type"] == "function" and call["function"]["name"] == "lookup"
    assert json.loads(call["function"]["arguments"]) == {"order": "A-7", "express": True}
    assert assistant["content"] is None and tool == {
        "role": "tool",
        "tool_call_id": call["id"],
        "content": '{"eta": "Friday"}',
    }
    assert answer == {"role": "assistant", "content": "It arrives Friday."}
    [fn] = out["tools"]
    assert fn["function"]["name"] == "lookup"
    assert fn["function"]["parameters"] == {
        "type": "object", "properties": {"order": {"type": "string"}, "express": {"type": "boolean"}},
    }  # fmt: skip


def test_unanswered_clipped_or_truncated_turns_are_left_out_with_the_reason():
    """I-10: a tool call without a recorded result made an invalid OpenAI file and shifted the ids of the next
    results onto the wrong calls; a run whose events or texts were cut was exported as if it were whole."""
    from agentcanvas.runtime.trajectory import MAX_TEXT

    def call(*names: str) -> dict:
        return {"role": "assistant", "content": "", "tool_calls": [{"name": n, "arguments": {}} for n in names]}

    def result(name: str) -> dict:
        return {"role": "tool", "name": name, "content": "ok"}

    two_steps = [call("a", "b"), result("a"), call("c"), result("c"), {"role": "assistant", "content": "done"}]
    assert finetune.unanswered(two_steps) == ["b"]
    assert finetune.incomplete([], two_steps) == "the tool call b has no recorded result"
    out = finetune.render(two_steps, "openai", {})["messages"]
    assert out[3]["tool_call_id"] == out[2]["tool_calls"][0]["id"]  # c's result answers c, not the unanswered b

    whole = [call("a"), result("a"), {"role": "assistant", "content": "done"}]
    assert finetune.unanswered(whole) == [] and finetune.incomplete([], whole) == ""
    assert "too many" in finetune.incomplete([{"type": "ac.truncated"}], whole)
    cut = [call("a"), {**result("a"), "content": "x" * MAX_TEXT + "…"}, {"role": "assistant", "content": "done"}]
    assert "cut short" in finetune.incomplete([], cut)


def test_the_plain_format_keeps_arguments_as_objects_and_carries_the_metadata():
    out = finetune.render(
        [{"role": "user", "content": "hi"}, *finetune.turns(EVENTS)[:2]], "messages", {"run_id": "r1"}
    )
    assert out["metadata"] == {"run_id": "r1"} and "tools" not in out
    assert out["messages"][1]["tool_calls"] == [{"name": "lookup", "arguments": {"order": "A-7", "express": True}}]
    assert finetune.to_jsonl([out, out]).count("\n") == 2 and finetune.to_jsonl([]) == ""


async def review(app, api, wf: str, run_ids: list[str], scores: dict[str, int]) -> None:
    aq = (await api.post(f"/v1/workflows/{wf}/annotation-queues", json={
        "name": "Review", "rubric": [{"name": "helpful", "scale": 5}]})).json()  # fmt: skip
    items = (await api.post(f"/v1/annotation-queues/{aq['id']}/items", json={"run_ids": run_ids})).json()
    for item in items:
        points = scores[item["thread_id"]]
        r = await api.post(f"/v1/annotation-items/{item['id']}/annotations", json={"scores": {"helpful": points}})
        assert r.status_code == 200, r.text


async def test_reviewed_conversations_are_exported_in_both_formats(app, api_client):
    wf = await make_chat(api_client)
    await converse(app, api_client, wf)
    runs = {r["thread_id"]: r["id"] for t in ASKS for r in (await api_client.get(f"/v1/threads/{t}/runs")).json()}
    await review(app, api_client, wf, list(runs.values()), {"th_refund": 5, "th_hotel": 2, "th_reset": 5})
    res = await api_client.post(URL.format(wf), json={"format": "openai"})
    assert res.status_code == 200, res.text
    assert (
        res.headers["content-type"].startswith("application/x-ndjson")
        and "attachment" in res.headers["content-disposition"]
    )
    assert (res.headers["x-examples"], res.headers["x-skipped"]) == ("2", "0")  # the hotel chat scored 2 of 5: left out
    lines = [json.loads(x) for x in res.text.splitlines()]
    assert {x["messages"][1]["content"] for x in lines} == {ASKS["th_refund"], ASKS["th_reset"]}
    assert (
        lines[0]["messages"][0] == {"role": "system", "content": "Help."}
        and lines[0]["messages"][-1]["role"] == "assistant"
    )
    assert all(set(x) == {"messages"} for x in lines)

    plain = await api_client.post(URL.format(wf), json={"format": "messages", "min_score": 0.1, "system": ""})
    rows = [json.loads(x) for x in plain.text.splitlines()]
    assert len(rows) == 3 and rows[0]["messages"][0]["role"] == "user"  # an empty system prompt: none
    assert {r["metadata"]["workflow_id"] for r in rows} == {wf} and rows[0]["metadata"]["scores"] == {
        "helpful": rows[0]["metadata"]["scores"]["helpful"]
    }

    prev = await api_client.post(URL.format(wf) + "?preview=true", json={"run_ids": [runs["th_hotel"], "run_nope"]})
    body = prev.json()
    assert body["examples"] == 0 and [(s["ref"], s["reason"]) for s in body["skipped"]] == [
        (runs["th_hotel"], "its scores average 0.25, under 0.8"),
        ("run_nope", "not a successful run of this workflow"),
    ]


async def test_a_dataset_is_exported_as_user_and_assistant_turns(api_client):
    wf = await make_chat(api_client)
    ds = await new_dataset(api_client, wf)
    examples = [
        {"input": {"messages": [{"role": "user", "content": "Capital of France?"}]}, "reference": {"answer": "Paris"},
         "split": "train"},
        {"input": {"messages": [{"role": "user", "content": "Capital of Spain?"}]}, "reference": {"answer": "Madrid"},
         "split": "test"},
        {"input": {"messages": [{"role": "user", "content": "No reference here"}]}},
    ]  # fmt: skip
    assert (await api_client.post(f"/v1/datasets/{ds['id']}/examples", json={"examples": examples})).status_code == 200
    r = await api_client.post(
        URL.format(wf), json={"source": "dataset", "dataset_id": ds["id"], "system": "Geography."}
    )
    rows = [json.loads(x) for x in r.text.splitlines()]
    assert [x["messages"] for x in rows] == [
        [{"role": "system", "content": "Geography."}, {"role": "user", "content": "Capital of France?"},
         {"role": "assistant", "content": "Paris"}],
        [{"role": "system", "content": "Geography."}, {"role": "user", "content": "Capital of Spain?"},
         {"role": "assistant", "content": "Madrid"}],
    ]  # fmt: skip
    assert r.headers["x-skipped"] == "1"
    test_only = await api_client.post(
        URL.format(wf), json={"source": "dataset", "dataset_id": ds["id"], "split": "test"}
    )
    assert len(test_only.text.splitlines()) == 1
    assert (await api_client.post(URL.format(wf), json={"source": "dataset"})).status_code == 422
    other = await api_client.post(URL.format(wf), json={"source": "dataset", "dataset_id": "ds_nope"})
    assert other.status_code == 404


async def test_the_export_stays_in_the_organisation(app, api_client, org_b):  # noqa: F811
    wf = await make_chat(api_client)
    act_as_b(app)
    try:
        assert (await api_client.post(URL.format(wf), json={})).status_code == 404
    finally:
        app.dependency_overrides.clear()
