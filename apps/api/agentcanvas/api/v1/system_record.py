"""A deployment's AI-system record (services/system_record.py): read it, fill it in."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note
from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.services import system_record
from agentcanvas.services.deployments import DeploymentService
from agentcanvas.services.system_record import RecordFields, SystemRecordOut

router = APIRouter(tags=["deployments"])


@router.get("/deployments/{dep_id}/system-record", response_model=SystemRecordOut)
async def get_system_record(
    dep_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> SystemRecordOut:
    """The deployment's AI-system record: what people wrote (purpose, owner, risk class, data categories, review
    date) with the evidence read now (version and changes, models and subprocessors, tools and pinned fingerprints,
    guardrails and policies, approvals, evaluation results, who published it, retention), and reminders (`notes`)."""
    dep = await DeploymentService(session).require(dep_id)
    return await system_record.build(session, request.app.state.settings, dep, user.org_id)


@router.put("/deployments/{dep_id}/system-record", response_model=SystemRecordOut)
async def update_system_record(
    dep_id: str,
    body: RecordFields,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> SystemRecordOut:
    """Replace what people wrote in the record (every field; leave one empty to clear it). The owner must be a
    member, and a risk class needs a rationale. The audit log keeps the risk class, owner and review date."""
    dep = await DeploymentService(session).require(dep_id)
    before = system_record.stored(dep)
    await system_record.save(session, dep, user.org_id, body, user.id)
    changed = [k for k in RecordFields.model_fields if getattr(before, k) != getattr(body, k)]
    note(request, changed=changed, risk_class=body.risk_class, owner_id=body.owner_id,
         review_date=body.review_date.isoformat() if body.review_date else None)  # fmt: skip
    return await system_record.build(session, request.app.state.settings, dep, user.org_id)
