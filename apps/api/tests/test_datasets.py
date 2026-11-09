"""Datasets (S12 FR-EVAL-02): versioned examples from test cases, runs and CSV/JSONL files."""

from __future__ import annotations

import json

from test_efficiency import statements
from test_org_scope import act_as_b, org_b  # noqa: F401 - fixture
from test_publish import make_greeter

from agentcanvas.api.deps import current_user


async def new_dataset(api, wf: str, name: str = "Smoke") -> dict:
    r = await api.post(f"/v1/workflows/{wf}/datasets", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()


async def test_examples_are_checked_and_every_change_is_a_version(api_client):
    wf = await make_greeter(api_client)
    ds = await new_dataset(api_client, wf)
    assert ds["version"] == 1 and ds["items"] == []
    base = f"/v1/datasets/{ds['id']}"

    added = await api_client.post(
        f"{base}/examples",
        json={"examples": [{"input": {"who": "Ada"}, "reference": {"out": "Hello Ada"}, "split": "test"},
                           {"input": {"who": "Bob"}, "trajectory": ["search"]}]},
    )  # fmt: skip
    assert added.status_code == 200, added.text
    v2 = added.json()
    assert v2["version"] == 2 and v2["examples"] == 2 and v2["splits"] == {"test": 1, "": 1}
    first = v2["items"][0]
    assert first["id"].startswith("ex_") and first["reference"] == {"out": "Hello Ada"}

    bad = await api_client.post(f"{base}/examples", json={"examples": [{"input": {"who": 3}}, {"input": {"x": 1}}]})
    assert bad.status_code == 422
    assert bad.json()["problems"] == [
        "example 3: 'who': 3 is not of type 'string'",
        "example 4: 'x' is not an input of this workflow",
    ]

    patched = await api_client.patch(f"{base}/examples/{first['id']}", json={"reference": {"out": "Hi Ada"}})
    assert patched.json()["version"] == 3 and patched.json()["items"][0]["reference"] == {"out": "Hi Ada"}
    assert (await api_client.patch(f"{base}/examples/{first['id']}", json={"input": {"who": 1}})).status_code == 422
    removed = await api_client.delete(f"{base}/examples/{first['id']}")
    assert removed.json()["version"] == 4 and [e["input"] for e in removed.json()["items"]] == [{"who": "Bob"}]
    assert (await api_client.delete(f"{base}/examples/{first['id']}")).status_code == 404

    old = (await api_client.get(f"{base}?version=2")).json()  # versions never change
    assert old["version"] == 2 and old["items"][0]["reference"] == {"out": "Hello Ada"}
    assert (await api_client.get(f"{base}?version=0")).status_code == 404  # C-11: not the latest
    versions = (await api_client.get(f"{base}/versions")).json()
    assert [(v["number"], v["examples"]) for v in versions] == [(4, 1), (3, 2), (2, 2), (1, 0)]
    listed = (await api_client.get(f"/v1/workflows/{wf}/datasets")).json()
    assert [(d["id"], d["version"], d["examples"]) for d in listed] == [(ds["id"], 4, 1)]


async def test_the_dataset_list_and_version_history_read_no_examples(app, api_client):
    """Each edit writes a version holding the whole set (up to 10,000 examples): counting them must not load them."""
    wf = await make_greeter(api_client)
    ds = await new_dataset(api_client, wf)
    base = f"/v1/datasets/{ds['id']}"
    for who in ("Ada", "Bob"):
        body = {"examples": [{"input": {"who": who}, "split": "test" if who == "Ada" else ""}]}
        assert (await api_client.post(f"{base}/examples", json=body)).status_code == 200
    with statements(app) as seen:
        versions = (await api_client.get(f"{base}/versions")).json()
        listed = (await api_client.get(f"/v1/workflows/{wf}/datasets")).json()
    assert [(v["number"], v["examples"]) for v in versions] == [(3, 2), (2, 1), (1, 0)]
    assert [(d["examples"], d["splits"]) for d in listed] == [(2, {"test": 1, "": 1})]
    assert not [q for q in seen if "dataset_version.examples" in q]
    page = (await api_client.get(f"{base}/versions", params={"limit": 1, "before": 3})).json()
    assert [v["number"] for v in page] == [2]


def test_versions_written_before_get_their_counts(tmp_path):
    import sqlite3

    from agentcanvas.db.session import run_alembic

    db = tmp_path / "m.db"
    run_alembic(f"sqlite+aiosqlite:///{db}", "upgrade", "c4d13ac418cd")
    con = sqlite3.connect(db)
    examples = [{"input": {}, "split": "test"}, {"input": {}, "split": ""}, {"input": {}, "split": "test"}]
    for n, items in ((1, []), (2, examples)):
        con.execute("INSERT INTO dataset_version (id, dataset_id, number, examples) VALUES (?, 'ds_1', ?, ?)",
                    (f"dsv_{n}", n, json.dumps(items)))  # fmt: skip
    con.commit()
    con.close()
    run_alembic(f"sqlite+aiosqlite:///{db}", "upgrade", "head")
    con = sqlite3.connect(db)
    got = {i: (n, json.loads(sp)) for i, n, sp in con.execute("SELECT id, example_count, splits FROM dataset_version")}
    con.close()
    assert got == {"dsv_1": (0, {}), "dsv_2": (3, {"test": 2, "": 1})}


def test_csv_references_keep_the_type_of_the_output_they_are_compared_with():
    """E-8: every reference cell was parsed as JSON ("1" -> 1, "true" -> True, "null" -> None), so an exact match
    against the run's text output failed."""
    from agentcanvas.ir.models import Workflow
    from agentcanvas.services.datasets import parse_csv

    wf = Workflow.model_validate({
        "id": "wf_csv", "name": "csv",
        "state": {"channels": [
            {"name": "q"}, {"name": "answer"}, {"name": "count", "type": {"type": "integer"}},
            {"name": "tags", "type": {"type": "array"}}, {"name": "loose", "type": {}},
        ]},
    })  # fmt: skip
    csv = (
        'q,reference.answer,reference.count,reference.tags,reference.loose\na,1,2,"[""x""]",true\nb,null,null,[],1e5\n'
    )
    first, second = parse_csv(csv, wf, None)
    assert first.reference == {"answer": "1", "count": 2, "tags": ["x"], "loose": "true"}
    assert second.reference == {"answer": "null", "tags": [], "loose": "1e5"}  # a null count is no reference


async def test_import_and_export(api_client):
    wf = await make_greeter(api_client)
    base = f"/v1/datasets/{(await new_dataset(api_client, wf))['id']}"
    csv = "who,reference.out,split,source\nAda,Hello Ada,test,forum\nBob,,,\n"
    r = await api_client.post(f"{base}/import", json={"format": "csv", "content": csv, "split": "train"})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [(e["input"], e["reference"], e["split"], e["metadata"]) for e in items] == [
        ({"who": "Ada"}, {"out": "Hello Ada"}, "test", {"source": "forum"}),
        ({"who": "Bob"}, {}, "train", {}),
    ]
    mapped = await api_client.post(
        f"{base}/import",
        json={"format": "csv", "content": "name,expected\nCy,Hello Cy\n",
              "mapping": {"input": {"who": "name"}, "reference": {"out": "expected"}}},
    )  # fmt: skip
    assert mapped.json()["items"][-1]["input"] == {"who": "Cy"}
    missing = await api_client.post(
        f"{base}/import", json={"format": "csv", "content": "a\n1\n", "mapping": {"input": {"who": "name"}}}
    )
    assert missing.status_code == 422 and "no column 'name'" in missing.json()["detail"]

    exported = await api_client.get(f"{base}/export")
    assert exported.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in exported.text.splitlines()]
    assert [e["input"]["who"] for e in lines] == ["Ada", "Bob", "Cy"]

    copy = f"/v1/datasets/{(await new_dataset(api_client, wf, 'Copy'))['id']}"
    again = await api_client.post(f"{copy}/import", json={"format": "jsonl", "content": exported.text})
    assert [e["input"] for e in again.json()["items"]] == [e["input"] for e in lines]
    assert {e["id"] for e in again.json()["items"]}.isdisjoint({e["id"] for e in lines})  # new ids
    langsmith = '{"inputs": {"who": "Di"}, "outputs": {"out": "Hello Di"}}\n'
    r = await api_client.post(f"{copy}/import", json={"format": "jsonl", "content": langsmith})
    assert r.json()["items"][-1]["reference"] == {"out": "Hello Di"}
    broken = await api_client.post(f"{copy}/import", json={"format": "jsonl", "content": '{"input": {}}\n[1]\n'})
    assert broken.status_code == 422 and broken.json()["detail"].startswith("line 2:")


async def test_a_csv_with_a_long_cell_imports_and_a_broken_one_is_refused(api_client, monkeypatch):
    import csv as csv_module

    wf = await make_greeter(api_client)
    base = f"/v1/datasets/{(await new_dataset(api_client, wf))['id']}"
    long = "x" * 200_000  # over the csv module's 128 KB default
    r = await api_client.post(f"{base}/import", json={"format": "csv", "content": f"who\n{long}\n"})
    assert r.status_code == 200, r.text[:200]
    assert r.json()["items"][0]["input"] == {"who": long}

    def broken(*_a, **_k):
        raise csv_module.Error("unexpected end of data")

    monkeypatch.setattr(csv_module, "DictReader", broken)
    r = await api_client.post(f"{base}/import", json={"format": "csv", "content": "who\nAda\n"})
    assert r.status_code == 422 and "not a CSV file" in r.json()["detail"], r.text[:200]


async def test_a_dataset_named_outside_latin1_exports_under_its_name(api_client):
    wf = await make_greeter(api_client)
    ds = await new_dataset(api_client, wf, 'Tickets 日本 "q"')
    exported = await api_client.get(f"/v1/datasets/{ds['id']}/export")
    assert exported.status_code == 200, exported.text
    disposition = exported.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="Tickets _q_-v1.jsonl"'), disposition
    assert "filename*=UTF-8''Tickets%20%E6%97%A5%E6%9C%AC%20%22q%22-v1.jsonl" in disposition


async def test_examples_from_test_cases_and_runs(app, api_client):
    wf = await make_greeter(api_client)
    case = await api_client.post(
        f"/v1/workflows/{wf}/test-cases",
        json={"name": "hello", "input": {"who": "Ada"}, "expect": {"channels": {"out": "Hello Ada"}}},
    )
    base = f"/v1/datasets/{(await new_dataset(api_client, wf))['id']}"
    from_cases = (await api_client.post(f"{base}/examples/from-test-cases", json={})).json()
    assert from_cases["items"][0]["reference"] == {"out": "Hello Ada"}
    assert from_cases["items"][0]["metadata"] == {"test_case": "hello", "test_case_id": case.json()["id"]}

    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"who": "Eve"}, "stream": False})
    run_id = r.json()["run_id"]
    await app.state.runner.wait(run_id)
    from_runs = (await api_client.post(f"{base}/examples/from-runs", json={"run_ids": [run_id]})).json()
    assert from_runs["items"][-1]["input"] == {"who": "Eve"}
    assert from_runs["items"][-1]["reference"]["out"] == "Hello Eve"
    other = await make_greeter(api_client)
    other_base = f"/v1/datasets/{(await new_dataset(api_client, other))['id']}"
    r = await api_client.post(f"{other_base}/examples/from-runs", json={"run_ids": [run_id]})
    assert r.status_code == 404  # a run of another workflow


async def test_datasets_stay_in_their_organisation(app, api_client, org_b):  # noqa: F811
    wf = await make_greeter(api_client)
    ds = await new_dataset(api_client, wf)
    act_as_b(app)
    try:
        assert (await api_client.get(f"/v1/datasets/{ds['id']}")).status_code == 404
        assert (await api_client.get(f"/v1/workflows/{wf}/datasets")).status_code == 404
        r = await api_client.post(f"/v1/datasets/{ds['id']}/examples", json={"examples": [{"input": {}}]})
        assert r.status_code == 404
    finally:
        app.dependency_overrides.pop(current_user, None)


async def test_an_expected_tool_call_can_name_its_arguments(api_client):
    ds = await new_dataset(api_client, await make_greeter(api_client))
    base = f"/v1/datasets/{ds['id']}"
    args = {"input": {"who": "Cy"}, "trajectory": ["now", {"name": "search", "args": {"q": {"$regex": "^C"}}}]}
    with_args = await api_client.post(f"{base}/examples", json={"examples": [args]})
    assert with_args.status_code == 200 and with_args.json()["items"][-1]["trajectory"] == args["trajectory"]
    nameless = {"input": {"who": "Cy"}, "trajectory": [{"args": {}}]}
    assert (await api_client.post(f"{base}/examples", json={"examples": [nameless]})).status_code == 422
