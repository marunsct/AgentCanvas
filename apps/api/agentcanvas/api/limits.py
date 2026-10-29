"""A cap on request bodies inside the API (2026-10-18 review).

nginx limits bodies to 20 MB, but a replica reached directly (Helm with another ingress, a port left open) read any
body whole, webhooks before checking their signature. This middleware refuses a body over the limit with 413:
from its Content-Length at once, or as soon as a chunked body grows past it.
"""

from __future__ import annotations

import ipaddress
import json
from typing import Any

from starlette.exceptions import HTTPException

MAX_BODY_BYTES = 20 * 1024 * 1024  # the same as nginx's client_max_body_size


class BodyLimit:
    def __init__(self, app: Any, max_bytes: int = MAX_BODY_BYTES):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length is not None and length.isdigit() and int(length) > self.max_bytes:
            await self._refuse(send)
            return
        seen = 0

        async def counted() -> dict[str, Any]:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.max_bytes:  # a chunked body: the app's exception handlers answer 413
                    raise HTTPException(413, f"request bodies are limited to {self.max_bytes // (1024 * 1024)} MB")
            return message

        await self.app(scope, counted, send)

    async def _refuse(self, send: Any) -> None:
        body = json.dumps({
            "type": "https://agentcanvas.dev/errors/too-large", "title": "Request body too large", "status": 413,
            "detail": f"request bodies are limited to {self.max_bytes // (1024 * 1024)} MB",
        }).encode()  # fmt: skip
        await send({
            "type": "http.response.start", "status": 413,
            "headers": [(b"content-type", b"application/problem+json"), (b"content-length", str(len(body)).encode())],
        })  # fmt: skip
        await send({"type": "http.response.body", "body": body})


class RefuseClients:
    """403 for every request from the given networks (`refuse_clients`): the network the API shares with the sandbox
    service so it can call it. Workspace code runs there and could otherwise call the API from inside, past nginx and
    its limits; the API never needs a request from that side (2026-10-29 review, S-3)."""

    def __init__(self, app: Any, networks: list[str]):
        self.app = app
        self.networks = [ipaddress.ip_network(n, strict=False) for n in networks]

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        client = scope.get("client")
        if scope["type"] in ("http", "websocket") and client and self._refused(client[0]):
            body = json.dumps({
                "type": "https://agentcanvas.dev/errors/forbidden", "title": "Forbidden", "status": 403,
                "detail": "requests from the sandbox network are not served",
            }).encode()  # fmt: skip
            await send({
                "type": "http.response.start", "status": 403,
                "headers": [(b"content-type", b"application/problem+json"),
                            (b"content-length", str(len(body)).encode())],
            })  # fmt: skip
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)

    def _refused(self, host: str) -> bool:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(ip in net for net in self.networks)
