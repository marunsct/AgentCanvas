"""Authentication on every route: with sign-in on (OIDC), a request with no token, or a forged one, gets nothing.

The routes come from the OpenAPI document, so a route added without its authentication fails here the day it is added.
Only the routes that are public on purpose may answer a stranger with success; each of those has its own check
(a webhook signature, a deployment key, a share token) and is listed below with its reason.
"""

from __future__ import annotations

import re

import httpx
import pytest
from test_auth import OTHER_KEY, SIGNING_JWK, bearer, settings, token  # noqa: F401 - `settings` turns OIDC sign-in on

from agentcanvas.security.auth import Authenticator

# public on purpose -> the reason; anything else must refuse a stranger
PUBLIC = {
    ("GET", "/healthz"): "load balancer liveness",
    ("GET", "/readyz"): "load balancer readiness",
    ("GET", "/v1/auth/config"): "the sign-in page needs the issuer and client id before anyone has a token",
}


def _fill(path: str) -> str:
    return re.sub(r"\{\w+\}", "does_not_exist", path)


@pytest.fixture
async def client(app):
    app.state.authenticator = Authenticator(app.state.settings, jwks={"keys": [SIGNING_JWK]})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_no_route_serves_a_stranger(app, client):
    spec = app.openapi()
    forged = bearer(token(key=OTHER_KEY))
    served: list[str] = []
    checked = 0
    for path, item in sorted(spec["paths"].items()):
        for method in item:
            if method not in ("get", "post", "put", "patch", "delete") or (method.upper(), path) in PUBLIC:
                continue
            for headers in ({}, forged, {"Authorization": "Bearer "}, {"Authorization": "Basic eDp5"}):
                r = await client.request(
                    method.upper(), _fill(path), headers=headers, json={} if method != "get" else None
                )
                checked += 1
                if r.status_code < 400 or r.status_code >= 500:
                    how = "with a bad token" if headers else "with none"
                    served.append(f"{method.upper()} {path} {how} -> {r.status_code}")
    assert checked > 600
    assert not served, "routes that answered a stranger:\n" + "\n".join(sorted(set(served)))
