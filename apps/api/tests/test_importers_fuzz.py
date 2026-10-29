"""Mutation fuzzing of the importers (Langflow, Flowise, n8n, Agent Spec).

An imported file is somebody else's JSON, from another tool's version, edited by hand or damaged on the way. The
importer must turn it into a workflow that validates, or refuse with its own `ImportError_` (a plain message the
person can act on). Any other exception is a 500 on `POST /workflows/import`, and a workflow it produces that fails
the IR model or crashes the validation rules is a broken document in the database.

Every sample file is damaged at random places (a value becomes null, a number, a list, a huge string, a key is
dropped or an entry duplicated). The seed is fixed, so a failure reproduces; `ROBUSTNESS_ROUNDS` and
`ROBUSTNESS_SEED` raise the rounds per file or change the choices, as in `test_workflow_fuzz.py`.
"""

from __future__ import annotations

import copy
import json
import os
import random
import traceback
from pathlib import Path
from typing import Any

import pytest

from agentcanvas.importers import ImportError_, import_document
from agentcanvas.ir.models import Workflow
from agentcanvas.validation import validate

ROUNDS = int(os.getenv("ROBUSTNESS_ROUNDS", "12")) * 4
SEED = int(os.getenv("ROBUSTNESS_SEED", "20261020"))
FILES = Path(__file__).parent / "importers"
NAMES = sorted(p.stem for p in FILES.glob("*.json"))
JUNK: list[Any] = [
    None, "", 0, -1, 10**30, 0.5, True, [], {}, "A" * 5000, "\x00", [None], [[]], {"": None}, ["a", 1, None],
    {"id": None}, "${x}", "../../etc/passwd",
]  # fmt: skip


def _paths(node: Any, here: tuple = ()) -> list[tuple]:
    out = [here]
    if isinstance(node, dict):
        for k, v in node.items():
            out += _paths(v, (*here, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _paths(v, (*here, i))
    return out


def _mutate(doc: Any, rng: random.Random) -> Any:
    doc = copy.deepcopy(doc)
    for _ in range(rng.randint(1, 4)):
        paths = [p for p in _paths(doc) if p]
        if not paths:
            break
        path = rng.choice(paths)
        parent = doc
        for key in path[:-1]:
            parent = parent[key]
        last = path[-1]
        action = rng.choice(["junk", "junk", "drop", "dup"])
        if action == "drop":
            if isinstance(parent, dict):
                del parent[last]
            else:
                parent.pop(last)
        elif action == "dup" and isinstance(parent, list):
            parent.insert(last, copy.deepcopy(parent[last]))
        else:
            parent[last] = copy.deepcopy(rng.choice(JUNK))
    return doc


def _where(exc: BaseException) -> str:
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if "agentcanvas" in f.filename]
    f = frames[-1] if frames else traceback.extract_tb(exc.__traceback__)[-1]
    return f"{type(exc).__name__}: {str(exc)[:80]} at {f.filename.split('agentcanvas/')[-1]}:{f.lineno} ({f.name})"


@pytest.mark.parametrize("name", NAMES)
def test_damaged_files_import_or_are_refused_plainly(name):
    original = json.loads((FILES / f"{name}.json").read_text())
    rng = random.Random(f"{SEED}:{name}")
    failures: dict[str, str] = {}
    imported = refused = 0
    for round_ in range(ROUNDS):
        doc = _mutate(original, rng)
        try:
            _, ir, _, layout = import_document(doc, wf_id="wf_new")
        except ImportError_:
            refused += 1
            continue
        except Exception as exc:  # noqa: BLE001 - anything else is the finding
            failures.setdefault(_where(exc), f"round {round_}")
            continue
        try:
            wf = Workflow.model_validate(ir)
            validate(wf)
            assert set(layout) <= {n.id for n in wf.nodes}
            imported += 1
        except Exception as exc:  # noqa: BLE001
            failures.setdefault("imported a document that is not a valid workflow: " + _where(exc), f"round {round_}")
    assert imported and refused, (imported, refused)  # the damage is neither always harmless nor always fatal
    assert not failures, f"{name}: " + "\n".join(f"{k} ({v})" for k, v in failures.items())


def _loop_back(doc: Any, rng: random.Random) -> Any:
    """The same file with one connection wired back to an earlier node: a cycle, which real files have (n8n
    `noOp` chains, Langflow loops) and the random damage above never makes."""
    doc = copy.deepcopy(doc)
    if isinstance(doc.get("connections"), dict):  # n8n: {source: {"main": [[{"node": target}]]}}
        names = [n["name"] for n in doc.get("nodes", []) if isinstance(n, dict) and "name" in n]
        if len(names) >= 2:
            a, b = rng.sample(names, 2)
            doc["connections"].setdefault(a, {}).setdefault("main", [[]])[0].append({"node": b, "type": "main"})
            doc["connections"].setdefault(b, {}).setdefault("main", [[]])[0].append({"node": a, "type": "main"})
        return doc
    graph = doc.get("data", doc) if isinstance(doc.get("data", doc), dict) else doc  # Langflow / Flowise
    edges, nodes = graph.get("edges"), graph.get("nodes")
    if isinstance(edges, list) and isinstance(nodes, list) and edges:
        e = copy.deepcopy(rng.choice(edges))
        if isinstance(e, dict):
            e["source"], e["target"] = e.get("target"), e.get("source")
            e["id"] = f"{e.get('id', 'e')}-back"
            edges.append(e)
    return doc


@pytest.mark.parametrize("name", NAMES)
def test_files_with_a_loop_import_or_are_refused_in_time(name):
    import concurrent.futures

    original = json.loads((FILES / f"{name}.json").read_text())
    rng = random.Random(f"{SEED}:loop:{name}")
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        for round_ in range(12):
            doc = _loop_back(original, rng)
            job = pool.submit(import_document, doc, wf_id="wf_new")
            try:
                job.result(timeout=10)
            except ImportError_:
                pass
            except concurrent.futures.TimeoutError:
                pytest.fail(f"{name}: round {round_} never finished importing (a loop in the file)")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def test_a_loop_of_pass_through_nodes_imports():
    doc = {"nodes": [
        {"name": "A", "type": "n8n-nodes-base.noOp", "parameters": {}},
        {"name": "B", "type": "n8n-nodes-base.noOp", "parameters": {}},
        {"name": "C", "type": "@n8n/n8n-nodes-langchain.chainLlm",
         "parameters": {"promptType": "define", "text": "={{ $json.foo }}"}},
    ], "connections": {"A": {"main": [[{"node": "B"}]]}, "B": {"main": [[{"node": "A"}, {"node": "C"}]]}}}  # fmt: skip
    _, ir, _, _ = import_document(doc, wf_id="wf_new")
    validate(Workflow.model_validate(ir))
