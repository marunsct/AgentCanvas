"""Triggers (M6-T5): manage webhook/cron triggers of a workflow; the signed webhook endpoint."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import get_session
from agentcanvas.db.models import Trigger
from agentcanvas.errors import InvalidInput
from agentcanvas.expr import ExpressionError
from agentcanvas.services.triggers import SIGNATURE_HEADER, TIMESTAMP_HEADER, TriggerIn, TriggerService, map_input

router = APIRouter(tags=["triggers"])
public_router = APIRouter(tags=["triggers"])  # signed webhooks, not user sign-in


class TriggerOut(BaseModel):
    id: str
    kind: str
    deployment_id: str | None
    enabled: bool
    config: dict[str, Any]  # without the webhook secret
    url: str | None = None  # webhooks: where to POST
    created_at: datetime


class TriggerCreated(TriggerOut):
    secret: str | None = None  # webhooks: the signing secret, shown once


class EnabledIn(BaseModel):
    enabled: bool


def _out(t: Trigger) -> dict[str, Any]:
    return {
        "id": t.id, "kind": t.kind, "deployment_id": t.deployment_id, "enabled": t.enabled,
        "config": {k: v for k, v in t.config.items() if k != "secret"}, "created_at": t.created_at,
        "url": f"/v1/hooks/{t.id}" if t.kind == "webhook" else None,
    }  # fmt: skip


def svc(request: Request, session: AsyncSession = Depends(get_session)):
    return TriggerService(session, request.app.state.keyring)


@router.post("/workflows/{wf_id}/triggers", response_model=TriggerCreated, status_code=201)
async def create_trigger(wf_id: str, body: TriggerIn, s: TriggerService = Depends(svc)) -> TriggerCreated:
    trig, secret = await s.create(wf_id, body)
    return TriggerCreated(**_out(trig), secret=secret)


@router.get("/workflows/{wf_id}/triggers", response_model=list[TriggerOut])
async def list_triggers(wf_id: str, s: TriggerService = Depends(svc)) -> list[TriggerOut]:
    return [TriggerOut(**_out(t)) for t in await s.list(wf_id)]


@router.patch("/triggers/{trigger_id}", response_model=TriggerOut)
async def enable_trigger(trigger_id: str, body: EnabledIn, s: TriggerService = Depends(svc)) -> TriggerOut:
    return TriggerOut(**_out(await s.set_enabled(trigger_id, body.enabled)))


@router.delete("/triggers/{trigger_id}", status_code=204)
async def delete_trigger(trigger_id: str, s: TriggerService = Depends(svc)) -> Response:
    await s.delete(trigger_id)
    return Response(status_code=204)


@public_router.post("/hooks/{trigger_id}", status_code=202)
async def webhook(
    trigger_id: str,
    request: Request,
    signature: str | None = Header(None, alias=SIGNATURE_HEADER),
    timestamp: str | None = Header(None, alias=TIMESTAMP_HEADER),
    s: TriggerService = Depends(svc),
) -> dict[str, Any]:
    """Start a run from a signed webhook delivery (401 on a bad, stale or missing signature, before anything else
    happens)."""
    raw = await request.body()
    trig = await s.verify(trigger_id, raw, signature, timestamp)
    try:
        body = json.loads(raw or b"{}")
        payload = map_input(trig.config.get("input_mapping"), body)
    except (ValueError, ExpressionError) as exc:
        raise InvalidInput(f"cannot map the webhook body to the workflow input: {exc}") from None
    wf, kw = await s.target(trig)
    h = await request.app.state.runner.start(wf, input=payload, origin="trigger", **kw)
    return {"run_id": h.run_id, "thread_id": h.thread_id}
