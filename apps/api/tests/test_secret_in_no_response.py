"""A stored secret's value appears in no response of the API, whatever route is asked.

`test_secrets.py` checks the places someone thought of. This one asks all of them: it stores a secret, makes the value
appear in a run's state and refers to it from a model resource, then reads every GET route it can reach (the crawl of
`test_cross_org_every_route.py`, plus every route without an id) as the project's admin and looks for the value in each
body and in each member of each zip. A new route that echoes configuration or state is covered the day it is added.
"""

from __future__ import annotations

import io
import re
import zipfile

from test_cross_org_every_route import PROJECT, SKIP, _crawl, _seed
from test_secrets import events, script_wf

SECRET = "a-secret-value"  # what test_cross_org_every_route._seed stores as A_TOKEN


def _bodies(r) -> list[tuple[str, str]]:
    if r.headers.get("content-type", "").startswith("application/zip"):
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            return [(n, z.read(n).decode("utf-8", "replace")) for n in z.namelist() if not n.endswith("/")]
    return [("", r.text)]


async def test_no_route_returns_a_stored_secret(app, api_client):
    own = await _seed(app, api_client)  # stores A_TOKEN = SECRET
    leaky = await script_wf(
        api_client,
        "leaks",
        "from agentcanvas.sdk import secret\n\n\ndef run(state):\n    return {'out': 'v ' + secret('A_TOKEN')}\n",
    )
    cur = (await api_client.get(f"/v1/workflows/{leaky}")).json()
    ir = {**cur["ir"], "resources": [
        {"id": "r_m", "type": "model.chat", "name": "m",
         "config": {"model": "openai:gpt-5-mini", "api_key": {"$secret": "A_TOKEN"}}}
    ]}  # fmt: skip
    assert (
        await api_client.put(f"/v1/workflows/{leaky}", json={"ir": ir}, headers={"If-Match": str(cur["version"])})
    ).status_code == 200
    assert (await api_client.post(f"/v1/workflows/{leaky}/versions", json={"message": "keyed"})).status_code < 300
    stream = (await api_client.post(f"/v1/workflows/{leaky}/runs/stream", json={"thread_id": "th_keyed"})).text
    assert events(stream)[-1]["output"]["out"] == "v ***" and SECRET not in stream  # the value was in the state

    found = await _crawl(api_client, app, {**own, "wf2": leaky})
    spec = app.openapi()
    plain = [
        p.replace("{project_id}", PROJECT)
        for p, item in spec["paths"].items()
        if "get" in item and p not in SKIP and not [x for x in re.findall(r"\{(\w+)\}", p) if x != "project_id"]
    ]  # routes without an id of their own
    again = [u.replace(own["wf"], leaky) for u in found.values() if own["wf"] in u]  # the keyed workflow's own
    urls = sorted({*found.values(), *again, *plain})
    leaks, scanned = [], 0
    for url in urls:
        r = await api_client.get(url)
        if r.status_code >= 400:
            continue
        for member, text in _bodies(r):
            scanned += 1
            if SECRET in text:
                leaks.append(f"GET {url} {member}")
    assert len(urls) > 70 and scanned > 100, (len(urls), scanned)
    assert not leaks, "the secret's value was returned by:\n" + "\n".join(leaks)
