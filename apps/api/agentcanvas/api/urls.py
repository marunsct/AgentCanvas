"""Absolute addresses of this API as its callers reach it (agent cards, SCIM `Location`).

Behind the web image's nginx the API is served under `/api` (nginx strips the prefix), so the address the API sees
is not the one callers use. `AGENTCANVAS_PUBLIC_URL` (where people reach the web app) wins: the API is its `/api`.
Without it, the request's own address is used, with the scheme a trusted proxy forwarded (uvicorn's
`--proxy-headers`, `FORWARDED_ALLOW_IPS`) and the `X-Forwarded-Prefix` nginx sets.
"""

from __future__ import annotations

import re

from fastapi import Request

_PREFIX = re.compile(r"^(/[A-Za-z0-9._~-]+)*$")  # a plain path: never a host, a query or a scheme


def api_base(request: Request) -> str:
    """The API's public base address, without a trailing slash (`https://canvas.example.com/api`)."""
    public = (request.app.state.settings.public_url or "").rstrip("/")
    if public:
        return public + "/api"
    prefix = request.headers.get("x-forwarded-prefix", "").rstrip("/")
    if not _PREFIX.match(prefix):
        prefix = ""
    return str(request.base_url).rstrip("/") + prefix


def api_url(request: Request, route: str, **params: str) -> str:
    """The public address of a named route of this app."""
    return api_base(request) + str(request.app.url_path_for(route, **params))
