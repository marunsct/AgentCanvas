"""A cap on request bodies inside the API (2026-10-18 review).

nginx limits bodies to 20 MB, but a replica reached directly (Helm with another ingress, a port left open) read any
body whole, webhooks before checking their signature. This middleware refuses a body over the limit with 413:
from its Content-Length at once, or as soon as a chunked body grows past it.
"""

from __future__ import annotations

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
