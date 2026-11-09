"""LangSmith dataset sync (S13): explicit push and pull of dataset versions, against a fake LangSmith."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from test_datasets import new_dataset
from test_publish import make_greeter

from agentcanvas.services import langsmith_sync

EXAMPLES = [
    {"input": {"who": "Ada"}, "reference": {"out": "Hello Ada"}, "split": "test", "metadata": {"source": "ticket 1"}},
    {"input": {"who": "Bob"}, "context": {"tier": "gold"}, "trajectory": ["lookup"]},
]


class FakeLangSmith:
    """The few client calls the sync makes, in memory."""

    def __init__(self) -> None:
        self.datasets: dict[str, dict[str, Any]] = {}
        self.tags: list[str] = []

    def has_dataset(self, *, dataset_id: str) -> bool:
        return dataset_id in self.datasets

    def create_dataset(self, name: str, *, description: str | None = None, metadata: dict | None = None) -> Any:
        ds_id = str(uuid.uuid4())
        self.datasets[ds_id] = {"name": name, "examples": {}}
        return self.read_dataset(dataset_id=ds_id)

    def read_dataset(self, *, dataset_id: str) -> Any:
        d = self.datasets[dataset_id]
        return SimpleNamespace(id=dataset_id, name=d["name"], url=f"https://smith.example/datasets/{dataset_id}")

    def list_examples(self, *, dataset_id: str) -> list[Any]:
        return list(self.datasets[dataset_id]["examples"].values())

    def delete_examples(self, *, example_ids: list[str]) -> None:
        for d in self.datasets.values():
            for i in example_ids:
                d["examples"].pop(i, None)

    def create_examples(self, *, dataset_id: str, examples: list[dict]) -> None:
        for e in examples:
            eid = str(uuid.uuid4())
            meta = {**e["metadata"], **({"dataset_split": [e["split"]]} if e.get("split") else {})}
            self.datasets[dataset_id]["examples"][eid] = SimpleNamespace(
                id=eid, inputs=e["inputs"], outputs=e["outputs"], metadata=meta
            )

    def update_dataset_tag(self, *, dataset_id: str, as_of: Any, tag: str) -> None:
        self.tags.append(tag)


@pytest.fixture
def smith(monkeypatch):
    fake = FakeLangSmith()
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_test")
    monkeypatch.setattr(langsmith_sync, "make_client", lambda key: fake)
    return fake


async def test_push_then_pull_round_trips(api_client, smith):
    wf = await make_greeter(api_client)
    ds = await new_dataset(api_client, wf)
    assert (await api_client.post(f"/v1/datasets/{ds['id']}/examples", json={"examples": EXAMPLES})).status_code == 200
    url = f"/v1/datasets/{ds['id']}/langsmith"
    assert (await api_client.post(f"{url}/pull")).status_code == 422  # not linked yet

    pushed = await api_client.post(f"{url}/push", json={})
    assert pushed.status_code == 200, pushed.text
    link = pushed.json()["langsmith"]
    assert (
        link["name"] == "Greeter / Smoke" and link["pushed_version"] == 2 and link["url"].endswith(link["dataset_id"])
    )
    [ls] = smith.datasets.values()
    assert len(ls["examples"]) == 2 and smith.tags == ["agentcanvas-v2"]

    # someone edits the dataset in LangSmith: one example gets a new reference, one is added
    first = next(e for e in ls["examples"].values() if e.inputs == {"who": "Ada"})
    first.outputs = {"out": "Hello there, Ada"}
    smith.create_examples(dataset_id=link["dataset_id"], examples=[
        {"inputs": {"who": "Cy"}, "outputs": {"out": "Hello Cy"}, "metadata": {}, "split": "train"},
    ])  # fmt: skip
    pulled = await api_client.post(f"{url}/pull")
    assert pulled.status_code == 200, pulled.text
    body = pulled.json()
    assert body["version"] == 3 and body["langsmith"]["pulled_version"] == 3
    by_who = {e["input"]["who"]: e for e in body["items"]}
    assert by_who["Ada"]["reference"] == {"out": "Hello there, Ada"} and by_who["Ada"]["split"] == "test"
    assert by_who["Ada"]["metadata"] == {"source": "ticket 1"}
    assert by_who["Bob"]["context"] == {"tier": "gold"} and by_who["Bob"]["trajectory"] == ["lookup"]
    assert by_who["Cy"]["split"] == "train" and by_who["Cy"]["id"].startswith("ex_")
    before = {
        e["input"]["who"]: e["id"]
        for e in (await api_client.get(f"/v1/datasets/{ds['id']}", params={"version": 2})).json()["items"]
    }
    assert by_who["Ada"]["id"] == before["Ada"]  # examples keep their identity across the round trip

    again = await api_client.post(f"{url}/push", json={"version": 2})  # the same LangSmith dataset, older content
    assert again.json()["langsmith"]["dataset_id"] == link["dataset_id"] and len(smith.datasets) == 1
    assert len(ls["examples"]) == 2 and smith.tags[-1] == "agentcanvas-v2"


async def test_sync_needs_a_key(api_client, monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    wf = await make_greeter(api_client)
    ds = await new_dataset(api_client, wf)
    r = await api_client.post(f"/v1/datasets/{ds['id']}/langsmith/push", json={})
    assert r.status_code == 422 and "LANGSMITH_API_KEY" in r.json()["detail"]


async def test_the_hosted_edition_does_not_lend_the_servers_key(app, api_client, monkeypatch):
    """The operator's LANGSMITH_API_KEY (which the LangSmith sandbox also reads) is not a tenant's: in the hosted
    edition a project syncs with its own key only, so no tenant pushes data into the operator's LangSmith."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_operator")
    monkeypatch.setattr(langsmith_sync, "make_client", lambda key: pytest.fail("the server's key was used"))
    monkeypatch.setattr(app.state.settings, "edition", "hosted")
    wf = await make_greeter(api_client)
    ds = await new_dataset(api_client, wf)
    r = await api_client.post(f"/v1/datasets/{ds['id']}/langsmith/push", json={})
    assert r.status_code == 422 and "project secret" in r.json()["detail"], r.text
    assert "server" not in r.json()["detail"]
