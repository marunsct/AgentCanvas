"""Hostile query parameters against real data.

`test_api_robustness.py` sends them to ids that do not exist, so most routes stop at "not found" before their paging
and filtering run. Here the routes are the ones the crawl of `test_cross_org_every_route.py` reached with real ids
(a dataset, a thread, a run, a deployment...), so `?limit=`, `?cursor=`, `?version=` and the rest meet the code that
uses them. A 5xx, an escaped exception or a hang names the call.
"""

from __future__ import annotations

import asyncio
import re

from test_api_robustness import QUERY_JUNK, TIMEOUT_S
from test_cross_org_every_route import PROJECT, SKIP, _crawl, _seed


async def test_query_parameters_on_real_data_never_break_a_route(app, api_client):
    own = await _seed(app, api_client)
    found = await _crawl(api_client, app, own)
    spec = app.openapi()
    plain = {
        p: p.replace("{project_id}", PROJECT)
        for p, item in spec["paths"].items()
        if "get" in item and p not in SKIP and not [x for x in re.findall(r"\{(\w+)\}", p) if x != "project_id"]
    }
    failures: list[str] = []
    calls = 0
    for path, url in sorted({**plain, **found}.items()):
        for name in [p["name"] for p in spec["paths"][path]["get"].get("parameters", []) if p.get("in") == "query"]:
            for value in QUERY_JUNK:
                calls += 1
                try:
                    r = await asyncio.wait_for(api_client.get(url, params={name: value}), TIMEOUT_S)
                except Exception as exc:  # noqa: BLE001 - an escaped exception is the finding
                    failures.append(f"GET {path} ?{name}={value[:20]!r}: raised {type(exc).__name__}: {exc}")
                    continue
                if r.status_code >= 500 and r.status_code != 503:
                    failures.append(f"GET {path} ?{name}={value[:20]!r}: HTTP {r.status_code} {r.text[:100]}")
    assert calls > 400, calls
    assert not failures, f"{len(failures)} query values broke the API:\n" + "\n".join(sorted(set(failures))[:40])
