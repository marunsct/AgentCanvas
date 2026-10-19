"""Mutation fuzzing of workflow documents through every endpoint that reads one.

A workflow is JSON the user (or the Copilot, or an import) can put anything into: `config` is free-form, so a value
of the wrong type passes the IR model and reaches the validation rules, the code view, the portability and blast
radius analyses, the contract, the export plan, the bridges and the Agent Spec export. Each of those must answer a
document it does not like with a diagnostic or a client error, never a 5xx.

Every template is damaged at random places (a value becomes null, a number, a list, a huge string, a key is dropped
or an entry duplicated); the seed is fixed, so a failure reproduces. `ROBUSTNESS_ROUNDS` raises the number of
mutated documents per template (default 12) and `ROBUSTNESS_SEED` changes the random choices, for a longer soak.
"""

from __future__ import annotations

import copy
import os
import random
import traceback
from typing import Any

import pytest

ROUNDS = int(os.getenv("ROBUSTNESS_ROUNDS", "12"))
SEED = int(os.getenv("ROBUSTNESS_SEED", "20261020"))
READERS = [
    "/v1/workflows/{id}",
    "/v1/workflows/{id}/code",
    "/v1/workflows/{id}/portability",
    "/v1/workflows/{id}/blast-radius",
    "/v1/workflows/{id}/agent-spec",
    "/v1/workflows/{id}/contract",
    "/v1/workflows/{id}/export/plan",
    "/v1/workflows/{id}/bridges",
    "/v1/workflows/{id}/spend",
]
JUNK: list[Any] = [
    None,
    "",
    0,
    -1,
    10**30,
    0.5,
    True,
    [],
    {},
    "A" * 5000,
    "\x00",
    [None],
    [[]],
    {"": None},
    ["a", 1, None],
]


def _where(exc: BaseException) -> str:
    """The innermost frame inside agentcanvas that raised: what a failure needs to be fixed."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if "agentcanvas" in f.filename]
    f = frames[-1] if frames else traceback.extract_tb(exc.__traceback__)[-1]
    return f"{type(exc).__name__}: {exc} at {f.filename.split('agentcanvas/')[-1]}:{f.lineno} ({f.name})"


def _paths(node: Any, prefix: tuple = ()) -> list[tuple]:
    out: list[tuple] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.append((*prefix, k))
            out += _paths(v, (*prefix, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.append((*prefix, i))
            out += _paths(v, (*prefix, i))
    return out


def _mutate(doc: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    doc = copy.deepcopy(doc)
    for _ in range(rng.randint(1, 3)):
        paths = _paths(doc)
        if not paths:
            break
        *parents, last = rng.choice(paths)
        node: Any = doc
        for p in parents:
            node = node[p]
        action = rng.choice(["junk", "junk", "junk", "drop", "dup"])
        if action == "junk":
            node[last] = copy.deepcopy(rng.choice(JUNK))
        elif action == "drop" and isinstance(node, dict):
            node.pop(last, None)
        elif action == "drop":
            node.pop(last)
        elif action == "dup" and isinstance(node, list):
            node.append(copy.deepcopy(node[last]))
        elif action == "dup":
            node[f"{last}_copy"] = copy.deepcopy(node[last])
    return doc


async def _templates(api) -> list[str]:
    return sorted(t["id"] for t in (await api.get("/v1/templates")).json())


async def test_mutated_workflows_never_crash_a_reader(api_client):
    templates = await _templates(api_client)
    assert len(templates) >= 20
    rng = random.Random(SEED)
    failures: list[str] = []
    stored = 0
    for template in templates:
        r = await api_client.post("/v1/projects/prj_default/workflows", json={"name": template, "template": template})
        assert r.status_code == 201, r.text
        wf = r.json()
        base = wf["ir"]
        etag = f'"{wf["version"]}"'
        for round_ in range(ROUNDS):
            doc = _mutate(base, rng)
            try:
                put = await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": doc}, headers={"if-match": etag})
            except Exception as exc:  # noqa: BLE001
                failures.append(f"[{template}#{round_}] PUT raised {_where(exc)}")
                continue
            if put.status_code >= 500:
                failures.append(f"[{template}#{round_}] PUT -> {put.status_code} {put.text[:150]}")
            if put.status_code != 200:
                continue  # rejected as invalid: that is the right answer for a bad document
            stored += 1
            etag = put.headers.get("etag", etag)
            for reader in READERS:
                try:
                    g = await api_client.get(reader.format(id=wf["id"]))
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"[{template}#{round_}] GET {reader} raised {_where(exc)}")
                    continue
                if g.status_code >= 500:
                    failures.append(f"[{template}#{round_}] GET {reader} -> {g.status_code} {g.text[:150]}")
            v = await api_client.post(f"/v1/workflows/{wf['id']}/validate")
            if v.status_code >= 500:
                failures.append(f"[{template}#{round_}] POST validate -> {v.status_code} {v.text[:150]}")
        await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": base}, headers={"if-match": etag})
    assert stored > len(templates), "the mutations were all rejected: the fuzz never reached the readers"
    assert not failures, f"{len(failures)} mutated documents broke a reader:\n" + "\n".join(failures[:40])


@pytest.mark.parametrize("template", ["support_triage", "classify_and_route"])
async def test_every_command_on_a_mutated_node_is_a_clean_error_or_a_valid_workflow(api_client, template):
    """UpdateNode with a hostile patch must end in a valid stored workflow or a 4xx: never half-applied, never a 5xx."""
    rng = random.Random(7)
    wf = (await api_client.post("/v1/projects/prj_default/workflows", json={"name": "c", "template": template})).json()
    node = wf["ir"]["nodes"][0]["id"]
    etag = f'"{wf["version"]}"'
    for _ in range(40):
        patch = {rng.choice(["config", "label", "policies", "disabled", "metadata"]): rng.choice(JUNK)}
        r = await api_client.post(
            f"/v1/workflows/{wf['id']}/commands",
            json={"commands": [{"op": "UpdateNode", "id": node, "patch": patch}]},
            headers={"if-match": etag},
        )
        assert r.status_code < 500, (patch, r.text[:200])
        if r.status_code == 200:
            etag = r.headers.get("etag", etag)
            got = await api_client.get(f"/v1/workflows/{wf['id']}")
            assert got.status_code == 200


# ------------------------------------------------------------------ running ----
def _pipeline() -> dict[str, Any]:
    """START -> set -> PII guard -> router -> (two sets) -> schema guard -> END: every step is deterministic code."""

    def node(nid: str, type_: str, config: dict[str, Any]) -> dict[str, Any]:
        return {"id": nid, "type": type_, "name": nid.removeprefix("n_"), "config": config}

    def edge(i: int, a: str, b: str, route: str | None = None) -> dict[str, Any]:
        e: dict[str, Any] = {"id": f"e{i}", "kind": "flow", "source": {"node": a}, "target": {"node": b}}
        if route:
            e["source"]["port"] = f"route:{route}"
        return e

    return {
        "name": "pipeline",
        "state": {"channels": [{"name": c} for c in ("text", "flagged", "report", "out")]},
        "nodes": [
            node("n_set", "core.set", {"values": {"text": "mail me at a@b.com about {{ input }}"}}),
            node("n_pii", "guard.pii", {"input": "text", "on_fail": "redact", "flag_output": "flagged",
                                         "report_output": "report"}),
            node("n_rt", "core.router", {"cases": [{"when": "state.flagged == true", "route": "yes"}],
                                         "default": "no"}),
            node("n_yes", "core.set", {"values": {"out": "flagged: {{ state.text }}"}}),
            node("n_no", "core.set", {"values": {"out": "clean"}}),
            node("n_sch", "guard.schema", {"input": "out", "schema": {"type": "string"}, "on_fail": "flag"}),
        ],
        "edges": [
            edge(0, "START", "n_set"), edge(1, "n_set", "n_pii"), edge(2, "n_pii", "n_rt"),
            edge(3, "n_rt", "n_yes", "yes"), edge(4, "n_rt", "n_no", "no"), edge(5, "n_yes", "n_sch"),
            edge(6, "n_no", "n_sch"), edge(7, "n_sch", "END"),
        ],
    }  # fmt: skip


async def test_runs_of_damaged_workflows_end_cleanly(api_client):
    """A damaged step may fail the run or have it refused up front; it may not 500, hang or end without a verdict."""
    import asyncio
    import json

    rng = random.Random(SEED)
    wf = (await api_client.post("/v1/projects/prj_default/workflows", json={"name": "pipeline"})).json()
    base = {**_pipeline(), "id": wf["id"]}
    etag = f'"{wf["version"]}"'
    failures: list[str] = []
    verdicts: dict[str, int] = {}
    for round_ in range(ROUNDS * 4):
        doc = copy.deepcopy(base)
        target = rng.choice(doc["nodes"])
        for _ in range(rng.randint(1, 2)):
            paths = _paths(target["config"])
            if not paths:
                break
            *parents, last = rng.choice(paths)
            holder: Any = target["config"]
            for p in parents:
                holder = holder[p]
            holder[last] = copy.deepcopy(rng.choice(JUNK))
        put = await api_client.put(f"/v1/workflows/{wf['id']}", json={"ir": doc}, headers={"if-match": etag})
        if put.status_code != 200:
            if put.status_code >= 500:
                failures.append(f"#{round_} PUT -> {put.status_code} {put.text[:150]}")
            continue
        etag = put.headers.get("etag", etag)
        try:
            r = await asyncio.wait_for(
                api_client.post(f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"input": "hello"}}), 30
            )
        except TimeoutError:
            failures.append(f"#{round_} {target['id']}: the run hung (30 s)")
            continue
        except Exception as exc:  # noqa: BLE001
            failures.append(f"#{round_} {target['id']}: the run raised {_where(exc)}")
            continue
        if r.status_code >= 500:
            failures.append(f"#{round_} {target['id']}: HTTP {r.status_code} {r.text[:150]}")
        elif r.status_code == 200:
            evs = [json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
            last_event = evs[-1]["type"] if evs else "none"
            verdicts[last_event] = verdicts.get(last_event, 0) + 1
            if last_event not in ("RUN_FINISHED", "RUN_ERROR"):
                failures.append(f"#{round_} {target['id']}: the stream ended with {last_event}")
    assert verdicts.get("RUN_FINISHED", 0) > 0, verdicts  # runs really started
    assert not failures, f"{len(failures)} damaged runs misbehaved:\n" + "\n".join(failures[:40])


# ---------------------------------------------------------------- regressions ----
@pytest.mark.parametrize(
    "where, bad",
    [
        ("n_rt", {"cases": 5}),
        ("n_rt", {"cases": ["a", None, 3]}),
        ("n_rt", {"cases": [{"when": "state.flagged == true", "route": ["x"]}]}),
        ("n_rt", {"cases": [{"when": "state.flagged == true"}]}),
        ("n_set", {"values": "text"}),
        ("n_set", {"values": ["text"]}),
        ("tool", {"url": "https://api.example.com", "allowed_domains": 7}),
    ],
)
async def test_exports_read_free_form_settings_of_the_wrong_type_without_crashing(api_client, where, bad):
    """Found by the mutation fuzz: the Agent Spec export, the bridges and the export plan assumed their free-form
    settings had the shape the form gives them; a hand-edited or imported document may not."""
    doc = _pipeline()
    doc["resources"] = [{"id": "tool", "type": "tool.http", "name": "tool", "config": {"url": "https://x.example.com"}}]
    holder = next(x for x in doc["nodes"] + doc["resources"] if x["id"] == where)
    holder["config"] = {**holder["config"], **bad}
    wf = (await api_client.post("/v1/projects/prj_default/workflows", json={"name": "bad"})).json()
    put = await api_client.put(
        f"/v1/workflows/{wf['id']}", json={"ir": {**doc, "id": wf["id"]}}, headers={"if-match": '"1"'}
    )
    if put.status_code != 200:
        return  # refused up front is also fine
    for reader in ("agent-spec", "bridges", "export/plan", "portability", "contract", "code"):
        r = await api_client.get(f"/v1/workflows/{wf['id']}/{reader}")
        assert r.status_code < 500, (reader, r.text[:200])
