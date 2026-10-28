"""Organisation B against everything organisation A owns, on every route.

`test_org_scope.py` checks the routes someone thought of. This one finds the rest: it fills A's workspace with one of
everything (workflow, versions, deployment, run, thread, dataset, evaluators, experiment, gate, trigger, share link,
knowledge base, file, secret), then crawls the API as A to learn, for every GET route that takes an id, a URL that
answers 200. Each of those URLs, and every other method the same path accepts, is then sent as a user of organisation B,
who must be told 403 or 404 and never see anything of A's. A new route with an id is covered the day it is added; one
that A cannot reach with the seed below is listed as uncovered, so the seed grows with the product.
"""

from __future__ import annotations

import asyncio
import itertools
import re
from collections import defaultdict
from typing import Any

from test_annotation import RUBRIC
from test_experiments import setup
from test_org_scope import act_as_b, org_b  # noqa: F401 - fixture

from agentcanvas.api.deps import current_user
from agentcanvas.ir.models import Workflow

PROJECT = "prj_default"
# Not a tenant boundary: a share link is public by design (its token is the capability), so is the A2A agent card
# (protocols.py: clients read it before they authenticate; the deployment id is the capability); the streams stay open.
SKIP = {
    "/v1/share/{token}",
    "/v1/deployments/{dep_id}/a2a/.well-known/agent-card.json",
    "/v1/deployments/{dep_id}/a2a/.well-known/agent.json",
    "/v1/runs/{run_id}/events",
    "/v1/projects/{project_id}/events",
}
VALUE = re.compile(r"^[A-Za-z0-9_.:/@-]{1,80}$")
HINTS = {  # parameters whose values are not ids with a prefix
    "a": re.compile(r"^\d+\.\d+\.\d+$"),
    "b": re.compile(r"^\d+\.\d+\.\d+$"),
    "ref": re.compile(r"^\d+\.\d+\.\d+$"),
    "node_id": re.compile(r"^n_\w+$"),
}


def _harvest(node: Any, into: set[str]) -> None:
    if isinstance(node, str):
        if VALUE.match(node):
            into.add(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            if VALUE.match(str(k)) and isinstance(v, dict | list):
                into.add(str(k))
            _harvest(v, into)
    elif isinstance(node, list):
        for v in node:
            _harvest(v, into)


async def _seed(app, api) -> dict[str, str]:
    wf, ds = await setup(api)  # a published, versioned workflow with a dataset, examples and evaluators
    ir = Workflow.model_validate((await api.get(f"/v1/workflows/{wf}")).json()["ir"])
    h = await app.state.runner.start(ir, input={"who": "Ada"})
    await asyncio.wait_for(h.done.wait(), 15)
    dep = (await api.get(f"/v1/workflows/{wf}/deployments")).json()[0]["id"]
    made = {
        "share": await api.put(f"/v1/deployments/{dep}/share", json={"enabled": True}),
        "gate": await api.put(f"/v1/workflows/{wf}/gates/prod", json={"dataset_id": ds["id"]}),
        "trigger": await api.post(
            f"/v1/workflows/{wf}/triggers",
            json={"kind": "cron", "deployment_id": dep, "config": {"schedule": "*/5 * * * *"}},
        ),
        "queue": await api.post(f"/v1/workflows/{wf}/annotation-queues", json={"name": "Triage", "rubric": RUBRIC}),
        "knowledge": await api.post(f"/v1/projects/{PROJECT}/knowledge", json={"sample": "handbook"}),
        "file": await api.put(f"/v1/projects/{PROJECT}/files/scripts/mine.py", json={"content": "x = 1\n"}),
        "secret": await api.put(f"/v1/projects/{PROJECT}/secrets/A_TOKEN", json={"value": "a-secret-value"}),
        "experiment": await api.post(f"/v1/workflows/{wf}/experiments", json={"dataset_id": ds["id"]}),
    }
    bad = {k: (r.status_code, r.text[:120]) for k, r in made.items() if r.status_code >= 300}
    assert not bad, f"the seed failed: {bad}"
    exp = made["experiment"].json()["id"]
    task = app.state.experiments.tasks.get(exp)
    if task is not None:
        await asyncio.wait_for(asyncio.shield(task), 30)
    second = await api.post(f"/v1/workflows/{wf}/versions", json={"message": "second"})
    chat = await api.post(f"/v1/projects/{PROJECT}/workflows", json={"name": "Chat", "template": "chat_assistant"})
    assert second.status_code < 300 and chat.status_code < 300, (second.text[:120], chat.text[:120])
    return {"wf": wf, "dep": dep, "wf2": chat.json()["id"]}


def _learn(r, values: set[str]) -> None:
    try:
        _harvest(r.json(), values)
    except ValueError:  # JSON Lines and the like: nothing to learn
        pass


def _classes(values: set[str]) -> dict[str, list[str]]:
    """Ids grouped by their prefix (`wf_...`, `run_...`); everything without one is a class of its own ("")."""
    out: dict[str, list[str]] = defaultdict(list)
    for v in sorted(values):
        out[v.split("_")[0] if re.match(r"^[a-z]{1,5}_", v) else ""].append(v)
    return out


async def _crawl(api, app, own: dict[str, str]) -> dict[str, str]:
    """For every GET route with an id, a URL that answers 200 for organisation A."""
    spec = app.openapi()
    routes = sorted(p for p, item in spec["paths"].items() if "get" in item and p not in SKIP)
    values: set[str] = {PROJECT, *own.values()}
    found: dict[str, str] = {}
    owned = {"wf_id": [own["wf"], own["wf2"]], "dep_id": [own["dep"]]}
    for path in routes:  # everything A can list
        others = re.findall(r"\{(?!project_id)(\w+)\}", path)
        if len(others) > 1 or (others and others[0] not in owned):
            continue
        for value in owned[others[0]] if others else [None]:
            url = path.replace("{project_id}", PROJECT)
            url = url.replace("{" + others[0] + "}", value) if others else url
            r = await api.get(url)
            if r.status_code == 200:
                _learn(r, values)
                if not others and "{project_id}" in path:
                    found[path] = url  # a route with only the project in it
    for path in ("/v1/projects", "/v1/threads"):
        _harvest((await api.get(path)).json(), values)
    learned: dict[str, str] = {}  # parameter name -> the id class that fitted it
    for _ in range(4):  # what one URL shows names the ids of the next
        classes = _classes(values)
        for path in routes:
            names = [n for n in re.findall(r"\{(\w+)\}", path) if n != "project_id"]
            if path in found or not names:
                continue
            pools = []
            for n in names:
                if n in HINTS:  # a parameter whose values look like nothing else
                    pools.append([v for v in sorted(values) if HINTS[n].match(v)][:8])
                elif n in learned:
                    pools.append(classes[learned[n]][:12])
                else:  # one of each class, and a few of the plain ones (versions, names, paths)
                    pools.append([v[0] for c, v in classes.items() if c] + classes.get("", [])[:25])
            base = path.replace("{project_id}", PROJECT)
            for combo in itertools.islice(itertools.product(*pools), 700):
                url = base
                for n, v in zip(names, combo, strict=True):
                    url = url.replace("{" + n + "}", v)
                r = await api.get(url)
                if r.status_code == 200:
                    found[path] = url
                    for n, v in zip(names, combo, strict=True):
                        learned[n] = v.split("_")[0] if re.match(r"^[a-z]{1,5}_", v) else ""
                    _learn(r, values)
                    break
    return found


async def test_organisation_b_is_refused_everything_of_organisation_a(app, api_client, org_b):  # noqa: F811
    own = await _seed(app, api_client)
    found = await _crawl(api_client, app, own)
    spec = app.openapi()
    gets = {p for p, item in spec["paths"].items() if "get" in item and "{" in p and p not in SKIP}
    assert len(found) >= 50, f"the crawl reached only {len(found)} of {len(gets)} routes: {sorted(gets - set(found))}"
    secrets = {"a-secret-value", "x = 1"}

    act_as_b(app)
    leaks: list[str] = []
    calls = 0
    try:
        for path, url in sorted(found.items()):
            for method in ("get", "put", "patch", "post", "delete"):
                if method not in spec["paths"][path]:
                    continue
                r = await api_client.request(method.upper(), url, json={} if method != "get" else None)
                calls += 1
                if r.status_code not in (403, 404, 405) or any(s in r.text for s in secrets):
                    leaks.append(f"{method.upper()} {url} -> {r.status_code} {r.text[:100]}")
    finally:
        app.dependency_overrides.pop(current_user, None)
    assert calls >= 40
    assert not leaks, f"organisation B got through on {len(leaks)} calls:\n" + "\n".join(leaks[:30])
    # the crawl, and B's refusals, left A's data alone
    assert (await api_client.get(f"/v1/workflows/{own['wf']}")).status_code == 200
    print(
        f"cross-org: {len(found)}/{len(gets)} id routes reached, {calls} calls refused; "
        f"uncovered: {sorted(gets - set(found))}"
    )
