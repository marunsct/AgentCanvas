"""Matura's signed webhooks (TS9a): changes to organisations, members and roles, as they happen.

Not signed in: the `Matura-Signature` header (HMAC-SHA256 with `AGENTCANVAS_MATURA_WEBHOOK_SECRET`, at most five
minutes old) is the authentication. Only answered with `AGENTCANVAS_AUTH_MODE=matura` and a webhook secret.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, Response

from agentcanvas.api.deps import get_settings_dep
from agentcanvas.errors import InvalidInput, NotFound, Unauthorized
from agentcanvas.services import matura
from agentcanvas.settings import Settings
from agentcanvas.vendor.matura import BadSignature, verify

public_router = APIRouter(tags=["matura"])


@public_router.post("/matura/events", status_code=204, include_in_schema=False)
async def matura_event(request: Request, settings: Settings = Depends(get_settings_dep)) -> Response:
    """Apply one event (CloudEvents, structured mode). Duplicates and out-of-order deliveries are harmless."""
    if settings.auth_mode != "matura" or not settings.matura_webhook_secret:
        raise NotFound("this server does not take Matura events")
    body = await request.body()
    try:
        verify(settings.matura_webhook_secret, request.headers.get("matura-signature"), body)
    except BadSignature as exc:
        raise Unauthorized(str(exc)) from None
    try:
        event = json.loads(body)
    except ValueError:
        raise InvalidInput("the event is not JSON") from None
    if not isinstance(event, dict):
        raise InvalidInput("the event is not a JSON object")
    await matura.apply(request.app.state.db, event)
    return Response(status_code=204)
