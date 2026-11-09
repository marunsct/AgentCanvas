"""A robustness sweep of the whole HTTP API, driven by its own OpenAPI document.

Every operation is called with hostile input: no body, a body that is not JSON, JSON of the wrong shape, every field
set to null, a number, a huge string, a NUL character or a deeply nested array; path ids that do not exist, are
absurdly long or try to climb out of a folder. Whatever the input, the API must answer with a client error (or
succeed): never a 5xx, never an exception that escapes, never a hang. A new route is covered by this test the day it
is added, because the routes come from `app.openapi()`.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx
import pytest

# Long-lived streams: they answer 200 and stay open by design, so a hang is expected (they are checked separately).
STREAMS = {
    ("GET", "/v1/runs/{run_id}/events"),
    ("GET", "/v1/projects/{project_id}/events"),
}
# Webhook-style routes authenticate by a secret in the URL or a signature, not by the session.
TIMEOUT_S = 20

PATH_IDS = [
    "does_not_exist",
    "x" * 4000,
    "..%2F..%2Fetc%2Fpasswd",
    "%00",
    "ünï-çødé-日本",
    "' OR 1=1 --",
]
NESTED: Any = "leaf"
for _ in range(60):
    NESTED = [NESTED]
HOSTILE = [
    None,
    0,
    -1,
    2**63,
    1.5e308,
    True,
    "",
    "A" * 70_000,
    "\x00",
    "${jndi:ldap://x}",
    "{{ 7*7 }}",
    "../../x",
    [],
    {},
    NESTED,
]


def _operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    out = []
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method in ("get", "post", "put", "patch", "delete") and (method.upper(), path) not in STREAMS:
                out.append((method.upper(), path, op))
    return sorted(out, key=lambda o: (o[1], o[0]))


def _resolve(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in schema:
        node: Any = spec
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        schema = node
    for key in ("anyOf", "oneOf"):
        if key in schema:  # the first alternative that has fields
            for option in schema[key]:
                alt = _resolve(spec, option)
                if alt.get("properties"):
                    return alt
    return schema


def _body_fields(spec: dict[str, Any], op: dict[str, Any]) -> list[str]:
    content = (op.get("requestBody") or {}).get("content") or {}
    schema = (content.get("application/json") or {}).get("schema")
    return list(_resolve(spec, schema).get("properties", {})) if schema else []


def _bodies(fields: list[str]) -> list[Any]:
    bodies: list[Any] = [None, [], {}, "just a string", 42, NESTED]
    for value in HOSTILE:
        if fields:
            bodies.append({f: value for f in fields})
            bodies.append({fields[0]: value})
    return bodies


def _fill(path: str, value: str, ids: dict[str, str]) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: ids.get(m.group(1), value), path)


async def _call(
    client: httpx.AsyncClient, method: str, url: str, body: Any, raw: bytes | None = None
) -> httpx.Response:
    kwargs: dict[str, Any] = {}
    if raw is not None:
        kwargs = {"content": raw, "headers": {"content-type": "application/json"}}
    elif body is not None and method not in ("GET", "DELETE"):
        kwargs = {"json": body}
    return await asyncio.wait_for(client.request(method, url, **kwargs), TIMEOUT_S)


async def test_no_route_answers_hostile_input_with_a_server_error(api_client, app):
    """Hostile bodies and ids against every operation; a 5xx, an escaped exception or a hang names the call."""
    spec = app.openapi()
    operations = _operations(spec)
    assert len(operations) > 150  # the sweep really covers the API
    wf = (await api_client.post("/v1/projects/prj_default/workflows", json={"name": "Sweep"})).json()
    real = {"project_id": "prj_default", "wf_id": wf["id"]}
    failures: list[str] = []
    calls = 0
    seen: dict[int, int] = {}
    for method, path, op in operations:
        fields = _body_fields(spec, op)
        shapes: list[tuple[dict[str, str], Any, bytes | None]] = [({}, None, None)]
        for value in PATH_IDS:
            shapes.append(({"*": value}, None, None))
        for body in _bodies(fields):
            shapes.append((real, body, None))
            shapes.append(({}, body, None))
        shapes.append((real, None, b"{not json"))
        shapes.append((real, None, b"\xff\xfe\x00\x01"))
        for ids, body, raw in shapes:
            url = _fill(path, ids.get("*", "does_not_exist"), ids)
            calls += 1
            try:
                r = await _call(api_client, method, url, body, raw)
            except Exception as exc:  # noqa: BLE001 - an escaped exception is the finding
                failures.append(f"{method} {url[:80]} body={str(body)[:60]!r}: raised {type(exc).__name__}: {exc}")
                continue
            seen[r.status_code] = seen.get(r.status_code, 0) + 1
            if r.status_code >= 500 and r.status_code != 503:  # 503 = a dependency said it is not set up
                failures.append(f"{method} {url[:80]} body={str(body)[:60]!r}: HTTP {r.status_code} {r.text[:120]}")
    assert calls > 3000
    assert seen.get(422, 0) > 500 and seen.get(404, 0) > 500, seen  # the hostile input really reached validation
    assert not failures, f"{len(failures)} hostile calls broke the API:\n" + "\n".join(sorted(set(failures))[:40])


@pytest.mark.parametrize("junk", ["Bearer ", "Bearer a.b.c", "Basic eDp5", "bearer \x00", "Bearer " + "A" * 20_000])
async def test_odd_authorization_headers_never_break_the_server(api_client, junk):
    r = await api_client.get("/v1/projects", headers={"authorization": junk})
    assert r.status_code < 500


QUERY_JUNK = [
    "", "-1", "0", "9" * 40, "1e999", "NaN", "null", "true", "\x00", "A" * 5000, "%00", "../../x", "' OR 1=1 --", "💥",
    "[]", "{}",
]  # fmt: skip


async def test_no_query_parameter_breaks_a_route(api_client, app):
    """Every declared query parameter of every GET route gets values of the wrong type, size and kind: a 5xx, an
    escaped exception or a hang names the call. (Limits, cursors, versions and flags are where paging breaks.)"""
    spec = app.openapi()
    wf = (await api_client.post("/v1/projects/prj_default/workflows", json={"name": "Query sweep"})).json()
    real = {"project_id": "prj_default", "wf_id": wf["id"]}
    failures: list[str] = []
    calls = 0
    for method, path, op in _operations(spec):
        if method != "GET":
            continue
        params = [p["name"] for p in op.get("parameters", []) if p.get("in") == "query"]
        if not params:
            continue
        base = _fill(path, "does_not_exist", real)
        for name in params:
            for value in QUERY_JUNK:
                calls += 1
                try:
                    r = await asyncio.wait_for(api_client.get(base, params={name: value}), TIMEOUT_S)
                except Exception as exc:  # noqa: BLE001 - an escaped exception is the finding
                    failures.append(f"GET {path} ?{name}={value[:20]!r}: raised {type(exc).__name__}: {exc}")
                    continue
                if r.status_code >= 500 and r.status_code != 503:
                    failures.append(f"GET {path} ?{name}={value[:20]!r}: HTTP {r.status_code} {r.text[:100]}")
    assert calls > 300, calls  # the sweep really covers the API's query parameters
    assert not failures, f"{len(failures)} query values broke the API:\n" + "\n".join(sorted(set(failures))[:40])


def test_the_api_docs_carry_no_planning_ids(app):
    """Route docstrings name the plan's work items for the code's readers; /docs and the OpenAPI document do not."""
    import re

    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("description", "summary") and isinstance(v, str) and re.search(r"\((?:S\d+|M\d+-T\d+)", v):
                    found.append(v[:80])
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(app.openapi())
    assert not found, found
