"""Approve and Deny links from approval notifications (F-9, `services/approval_links.py`): public, the signed token
is the capability.

`POST /v1/approval-links/inspect` says what a link is for and changes nothing: the web app's confirmation page shows
it before anyone presses a button. `POST /v1/approval-links/decision` decides with a link sent to one person, as that
person, after the Inbox's own checks; it is audited as `decide_by_link` with them as the actor. Both take the token in
the body, never the address, so it is not written to access logs. A link posted to a channel, and any link of an
organisation that requires multi-factor sign-in (or that switched links off), only opens the item in the Inbox.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note, payload_hash
from agentcanvas.api.deps import RANK, CurrentUser, get_session, get_settings_dep
from agentcanvas.api.v1.runs import _not_your_own, _thread, _thread_target, runner
from agentcanvas.db.models import (
    AppUser,
    InterruptTask,
    Membership,
    Organization,
    OrgNotifier,
    Project,
    Thread,
    WorkflowRow,
)
from agentcanvas.errors import Conflict, Forbidden, NotFound
from agentcanvas.runtime.runner import Runner
from agentcanvas.runtime.types import ResumeSpec
from agentcanvas.services import approval_links, approvers, mfa, networks, offboarding, project_access
from agentcanvas.settings import Settings

public_router = APIRouter(prefix="/approval-links", tags=["inbox"])


class LinkIn(BaseModel):
    token: str = Field(max_length=approval_links.MAX_LEN, description="the link's token (after `#` in its address)")


class LinkDecision(LinkIn):
    reason: str | None = Field(None, max_length=500, description="a denied tool call: the reason the agent reads")


class LinkOut(BaseModel):
    interrupt_id: str
    action: str = Field(description="approve or deny")
    kind: str
    status: str = Field(description="the item's: pending, decided, expired or cancelled")
    workflow_name: str
    project_name: str
    step: str
    deciders: str
    recipient: str | None = Field(description="the e-mail address the link was sent to; null: a channel")
    expires_at: datetime = Field(description="when the link stops working")
    decided_at: datetime | None = None
    sign_in: bool = Field(description="the link cannot decide by itself: open the item in the Inbox and sign in")
    why_sign_in: str | None = None
    inbox_path: str


class Resolved:
    def __init__(self, link: approval_links.Link, task: InterruptTask, wf: WorkflowRow, project: Project):
        self.link, self.task, self.wf, self.project = link, task, wf, project
        self.why_sign_in: str | None = None
        self.email: str | None = None


async def _resolve(request: Request, session: AsyncSession, settings: Settings, token: str) -> Resolved:
    try:
        link = approval_links.verify(settings.secret_key, token)
    except approval_links.InvalidLink as exc:
        raise NotFound(str(exc)) from None
    q = (
        select(InterruptTask, WorkflowRow, Project)
        .join(Thread, Thread.id == InterruptTask.thread_id)
        .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
        .join(Project, Project.id == WorkflowRow.project_id)
        .where(InterruptTask.id == link.interrupt_id)
    )
    found = (await session.execute(q)).first()
    if found is None:
        raise NotFound("this item no longer exists: its conversation was deleted")
    out = Resolved(link, *found)
    org_id = out.project.org_id
    own = await session.scalar(select(Organization.settings).where(Organization.id == org_id))
    networks.check(request, org_id, own, "approval link")
    on = await session.scalar(select(OrgNotifier.actions).where(OrgNotifier.org_id == org_id))
    if link.recipient is not None:
        out.email = await session.scalar(select(AppUser.email).where(AppUser.id == link.recipient))
    if link.recipient is None:
        out.why_sign_in = "this link was posted to a channel, so whoever decides signs in first"
    elif not on:
        out.why_sign_in = "your organisation decides approvals in the Inbox only now"
    elif out.task.kind not in approval_links.KINDS:
        out.why_sign_in = "this item needs an answer, written in the Inbox"
    elif settings.auth_mode == "matura":
        out.why_sign_in = "roles come from the sign-in on this server, so deciding needs one"
    elif mfa.org_requires(own) or settings.oidc_required_acr or settings.oidc_required_amr:
        out.why_sign_in = "your organisation requires a multi-factor sign-in to decide"
    return out


def _step(ir: dict[str, Any] | None, node_id: str) -> str:
    node = next((n for n in (ir or {}).get("nodes", []) if n.get("id") == node_id), None) or {}
    return node.get("label") or str(node.get("name") or node_id).replace("_", " ")


@public_router.post("/inspect", response_model=LinkOut)
async def inspect_approval_link(
    body: LinkIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
) -> LinkOut:
    """What a link is for, and whether it can decide by itself. Changes nothing (it is a POST so the token stays out
    of the address): opening a link never decides."""
    r = await _resolve(request, session, settings, body.token)
    task = r.task
    return LinkOut(
        interrupt_id=task.id, action=r.link.action, kind=task.kind, status=task.status, workflow_name=r.wf.name,
        project_name=r.project.name, step=_step(r.wf.draft_ir, task.canvas_node_id),
        deciders=approvers.Approvers.of(task.approvers).summary(task.assignee_role), recipient=r.email,
        expires_at=datetime.fromtimestamp(r.link.expires, UTC), decided_at=task.decided_at,
        sign_in=r.why_sign_in is not None, why_sign_in=r.why_sign_in, inbox_path=f"/inbox?item={task.id}",
    )  # fmt: skip


@public_router.post("/decision", status_code=202)
async def decide_by_link(
    body: LinkDecision,
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings_dep),
    r: Runner = Depends(runner),
) -> Response:
    """Approve or deny with a link sent to one person, as that person: the same checks as the Inbox (their role in
    the project, who the step names, approving your own runs). Decides once."""
    found = await _resolve(request, session, settings, body.token)
    if found.why_sign_in:
        raise Forbidden(f"{found.why_sign_in}: open the item in the Inbox")
    task, uid, org_id = found.task, found.link.recipient, found.project.org_id
    assert uid is not None
    if task.status == "expired":
        raise Conflict(approvers.EXPIRED)
    if task.status != "pending":
        raise Conflict(f"this item was already {task.status}")
    member_role = await session.scalar(
        select(Membership.role).where(
            Membership.org_id == org_id,
            Membership.user_id == uid,
            Membership.accepted.is_(True),
            Membership.suspended.is_(False),
        )
    )
    if member_role is None:
        raise Forbidden("this link was sent to someone who is no longer a member of the organisation")
    closed = await offboarding.closed(session, org_id)
    if closed is not None:
        raise offboarding.refusal(closed)
    access = await project_access.load(session, org_id, uid, member_role)
    role = access.role_in(found.project.id)
    if role is None or RANK.get(role, -1) < RANK["reviewer"]:
        raise Forbidden("your role in this project cannot decide: it needs 'reviewer' or above")
    user = CurrentUser(id=uid, org_id=org_id, role=role, email=found.email or "", memberships={org_id: member_role})
    request.state.user = user  # the audit log's actor: the person the link was sent to
    t = await _thread(session, task.thread_id)
    await _not_your_own(session, settings, user, t, [task])
    await approvers.check(session, [task], approvers.Decider(uid, role, org_id), t.created_by)
    value = approval_links.decision(task.kind, task.payload, found.link.action, body.reason)
    note(request, target=f"interrupt_id={task.id}", kind=task.kind, action=found.link.action, via="e-mail link",
         payload_sha256=payload_hash(value))  # fmt: skip
    wf, target_kw = await _thread_target(session, settings, t)
    h = await r.start(
        wf, thread_id=t.id, resume=ResumeSpec(value, task.id), user_id=uid, decided_by=uid, **target_kw
    )  # fmt: skip
    return Response(
        json.dumps({"run_id": h.run_id, "thread_id": h.thread_id, "action": found.link.action}),
        status_code=202,
        media_type="application/json",
    )
