"""Datasets (S12 FR-EVAL-02): versioned examples from test cases, runs and CSV/JSONL files."""

from __future__ import annotations

import json

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
    versions = (await api_client.get(f"{base}/versions")).json()
    assert [(v["number"], v["examples"]) for v in versions] == [(4, 1), (3, 2), (2, 2), (1, 0)]
    listed = (await api_client.get(f"/v1/workflows/{wf}/datasets")).json()
    assert [(d["id"], d["version"], d["examples"]) for d in listed] == [(ds["id"], 4, 1)]


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
