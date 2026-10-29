"""Share links and chat widgets (S23): a published deployment as a chat page anyone with its link can use.

Builders manage a deployment's link under `/v1/deployments/{dep_id}/share` (signed in). Visitors use the public
`/v1/share/{token}` endpoints: no sign-in, no key; the token is the capability. They see the page's title, greeting
and suggestions, never the workflow, its keys or secrets, and the run stream they get carries only the answer text
and the questions a paused run asks them. Visitors answer a step that asks for input; approvals are left to the
builders (Runs page), since anyone with the link could otherwise approve.

Runs started here are ordinary deployment runs (Runs, online evaluators, feedback) with the same rate limits as calls
with the deployment's key, plus a per-client-address limit on each link; their end user is
`share:<deployment>:<visitor>`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.api.v1.deployments import FeedbackIn, _continue_on, count_call
from agentcanvas.api.v1.protocols import _pending, answer_value
from agentcanvas.api.v1.runs import SSE_HEADERS, _context
from agentcanvas.db.models import Deployment, DeploymentShare, Project, Run, RunScore, WorkflowRow
from agentcanvas.errors import InvalidInput, NotFound
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.observability import counted_stream
from agentcanvas.runtime.runner import RunHandle
from agentcanvas.runtime.types import ResumeSpec
from agentcanvas.services import annotation
from agentcanvas.services.deployments import DeploymentService
from agentcanvas.services.share import (
    InputShape,
    ShareService,
    ShareSettingsIn,
    answer_text,
    frame_ancestors,
    input_shape,
    question_of,
    run_input,
    visitor_hash,
)

router = APIRouter(tags=["share"])  # signed in: a builder's settings of a deployment's link
public_router = APIRouter(tags=["share"])  # visitors of a link: no sign-in

Visitor = Annotated[
    str,
    Field(
        min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$",
        description="a random secret the visitor's browser keeps; only it continues the conversations it started",
    ),
]  # fmt: skip


# ------------------------------------------------------------------------------------- builders ----
class ShareOut(BaseModel):
    deployment_id: str
    enabled: bool
    token: str | None = Field(description="none until the settings are first saved (switching sharing on makes it)")
    path: str | None = Field(description="the chat page, relative to the web app (/s/<token>); none without a token")
    title: str
    greeting: str
    suggestions: list[str]
    input_field: str | None
    input: InputShape
    frame_origins: list[str] = Field(description="sites allowed to show the chat page in a frame; empty: any site")
    updated_at: datetime | None = None


async def _share_out(session: AsyncSession, share: DeploymentShare | None, dep: Deployment) -> ShareOut:
    """`share` None: a deployment never shared, shown with the settings it would start with."""
    wf = await _pinned(session, dep)
    if share is None:
        share = DeploymentShare(deployment_id=dep.id, enabled=False, title="", greeting="", suggestions=[])
    return ShareOut(
        deployment_id=dep.id, enabled=share.enabled, token=share.token,
        path=f"/s/{share.token}" if share.token else None,
        title=share.title or wf.name, greeting=share.greeting, suggestions=list(share.suggestions or []),
        input_field=share.input_field, input=input_shape(wf, share.input_field),
        frame_origins=list(share.frame_origins or []), updated_at=share.updated_at,
    )  # fmt: skip


async def _pinned(session: AsyncSession, dep: Deployment) -> Workflow:
    from agentcanvas.db.models import WorkflowVersion

    version = await session.get(WorkflowVersion, dep.version_id)
    if version is None:
        raise NotFound(f"the version of deployment '{dep.id}' is missing")
    return Workflow.model_validate(version.ir)


@router.get("/deployments/{dep_id}/share", response_model=ShareOut)
async def get_share(dep_id: str, session: AsyncSession = Depends(get_session)) -> ShareOut:
    """The deployment's share link and chat page settings (sharing starts switched off). Reading them creates
    nothing: the link is made when the settings are first saved."""
    dep = await DeploymentService(session).require(dep_id)
    return await _share_out(session, await ShareService(session).get(dep_id), dep)


@router.put("/deployments/{dep_id}/share", response_model=ShareOut)
async def update_share(
    dep_id: str,
    body: ShareSettingsIn,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> ShareOut:
    """Switch the share link on or off and set the chat page's title, greeting and suggested prompts."""
    dep = await DeploymentService(session).require(dep_id)
    if body.input_field:
        wf = await _pinned(session, dep)
        if input_shape(wf, body.input_field).field != body.input_field:
            raise InvalidInput(f"'{body.input_field}' is not an input of the published version")
    share = await ShareService(session).update(dep_id, body, user.id)
    return await _share_out(session, share, dep)


@router.post("/deployments/{dep_id}/share/regenerate", response_model=ShareOut)
async def regenerate_share(
    dep_id: str, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> ShareOut:
    """A new link: the old one, and every conversation started on it, stop working at once."""
    dep = await DeploymentService(session).require(dep_id)
    share = await ShareService(session).regenerate(dep_id, user.id)
    return await _share_out(session, share, dep)


# ------------------------------------------------------------------------------------- visitors ----
class ShareInfo(BaseModel):
    title: str
    greeting: str
    suggestions: list[str]
    input: InputShape


class ShareRunIn(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    visitor: Visitor
    thread_id: str | None = Field(None, description="continue one of this visitor's conversations")


class ShareResumeIn(BaseModel):
    visitor: Visitor
    interrupt_id: str | None = None
    approve: bool | None = Field(None, description="not used: approvals cannot be given on a share link")
    text: str | None = Field(None, max_length=20_000, description="the answer to a question")


class ShareFeedbackIn(FeedbackIn):
    visitor: Visitor


async def _open(request: Request, session: AsyncSession, token: str, count: bool = True):
    share, dep, wf = await ShareService(session).open(token)
    if count:  # the deployment's and project's limits, and this link's per-client one (visitor secrets are free)
        client = request.client.host if request.client else "unknown"
        per_client = request.app.state.settings.rate_limit_per_share_client
        await count_call(request, session, dep, wf, (f"visitor:{dep.id}:{client}", per_client))
    return share, dep, wf


@public_router.get("/share/{token}", response_model=ShareInfo)
async def share_info(token: str, request: Request, session: AsyncSession = Depends(get_session)) -> ShareInfo:
    """What a chat page shows before the first message. 404 when the link is off or the deployment unpublished."""
    share, _, wf = await _open(request, session, token, count=False)
    return ShareInfo(
        title=share.title or wf.name, greeting=share.greeting, suggestions=list(share.suggestions or []),
        input=input_shape(wf, share.input_field),
    )  # fmt: skip


@public_router.get("/share/{token}/frame", status_code=204, include_in_schema=False)
async def share_frame(token: str, session: AsyncSession = Depends(get_session)) -> Response:
    """For the web server: which sites may frame this link's chat page, as an `X-Frame-Ancestors` header (nginx asks
    before it serves the page and puts the answer in the page's Content-Security-Policy)."""
    share = await session.scalar(select(DeploymentShare).where(DeploymentShare.token == token)) if token else None
    return Response(status_code=204, headers={"X-Frame-Ancestors": frame_ancestors(share)})


async def _visitor_context(session: AsyncSession, dep: Deployment, wf: Workflow, visitor: str) -> dict[str, Any]:
    """Visitors are end users of the deployment ("share:<deployment>:<visitor>", from the hash of their secret);
    context fields a caller would pass are left empty."""
    row = await session.get(WorkflowRow, dep.workflow_id)
    project = await session.get(Project, row.project_id) if row else None
    end_user = f"share:{dep.id}:{visitor_hash(visitor)[:24]}"
    return _context(wf, {}, CurrentUser(id=end_user, org_id=project.org_id if project else "", role="api"))


APPROVAL_WAIT = "This needs approval from the people who run this assistant before it can continue."
GUARD_PREFIX = "guard."
STREAMED = ("core.llm", "core.agent")  # steps whose written text is the answer when they finish the run


def _answer_steps(wf: Workflow) -> set[str]:
    """The steps whose text may reach a visitor as it is written: AI steps and agents that finish the run. None when
    the workflow has a safety block anywhere (subflows included): text written before the check must not leak, so
    the visitor gets only the final, checked answer. Text of earlier steps (drafts, classifiers, tool reasoning)
    is never streamed."""
    found_guard = False

    def walk(value: Any) -> None:
        nonlocal found_guard
        if isinstance(value, dict):
            if isinstance(value.get("type"), str) and value["type"].startswith(GUARD_PREFIX):
                found_guard = True
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(wf.model_dump(mode="json"))
    if found_guard:
        return set()
    last = {e.source.node for e in wf.edges if e.kind == "flow" and e.target.node == "END"}
    return {n.id for n in wf.nodes if n.type in STREAMED and n.id in last}


def _visitor_events(h: RunHandle, runner: Any, shape: InputShape, wf: Workflow) -> AsyncIterator[str]:
    """The run's stream as a visitor may see it: the answer text as it is written (`_answer_steps` only), then the
    final answer or the question a pause asks; step names, state and error details stay inside."""
    streamed = _answer_steps(wf)

    async def gen() -> AsyncIterator[str]:
        def sse(ev: dict[str, Any]) -> str:
            return f"data: {json.dumps(ev, default=str)}\n\n"

        yield sse({"type": "RUN_STARTED", "runId": h.run_id, "threadId": h.thread_id})
        async for ev in runner.subscribe(h.run_id):
            kind = ev.get("type")
            if (
                kind == "TEXT_MESSAGE_CONTENT"
                and isinstance(ev.get("delta"), str)
                and ev.get("canvas_node_id") in streamed
            ):
                yield sse({"type": "TEXT_MESSAGE_CONTENT", "delta": ev["delta"]})
            elif kind == "RUN_FINISHED":
                paused = ev.get("interrupts") or []
                interrupts = [  # only questions: approvals are not the visitor's to give
                    {"id": i.get("id"), "kind": "input", "question": question_of(i)}
                    for i in paused
                    if (i.get("kind") or "input") == "input"
                ]
                answer = APPROVAL_WAIT if paused and not interrupts else answer_text(ev.get("output"), shape)
                yield sse({
                    "type": "RUN_FINISHED", "runId": h.run_id, "threadId": h.thread_id,
                    "status": "interrupted" if paused else (ev.get("status") or "success"),
                    "answer": "" if interrupts else answer, "interrupts": interrupts,
                })  # fmt: skip
            elif kind == "RUN_ERROR":
                busy = ev.get("code") == "redeploy"
                message = "The service restarted; please send your message again." if busy else (
                    "Sorry, something went wrong. Please try again."
                )  # fmt: skip
                yield sse({"type": "RUN_ERROR", "runId": h.run_id, "message": message})

    return gen()


def _stream(request: Request, h: RunHandle, shape: InputShape, wf: Workflow) -> Response:
    headers = {**SSE_HEADERS, "x-run-id": h.run_id, "x-thread-id": h.thread_id}
    return StreamingResponse(
        counted_stream(_visitor_events(h, request.app.state.runner, shape, wf)),
        media_type="text/event-stream",
        headers=headers,
    )


@public_router.post("/share/{token}/runs/stream")
async def share_run_stream(
    token: str, body: ShareRunIn, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """Send a visitor's message: starts a conversation (its id comes back in RUN_STARTED and `x-thread-id`) or
    continues one of theirs, streaming the answer (SSE)."""
    share, dep, wf = await _open(request, session, token)
    shares = ShareService(session)
    shape = input_shape(wf, share.input_field)
    payload = run_input(shape, body.message)
    version_id, thread_id = dep.version_id, body.thread_id
    if thread_id:
        await shares.check_thread(dep.id, thread_id, body.visitor)
        wf, version_id = await _continue_on(session, dep, wf, thread_id)
    context = await _visitor_context(session, dep, wf, body.visitor)
    new = thread_id is None
    thread_id = thread_id or new_id("th")
    h = await request.app.state.runner.start(
        wf, thread_id=thread_id, input=payload, context=context, deployment_id=dep.id, version_id=version_id,
        labels={"channel": "share"},
    )  # fmt: skip
    if new:
        await shares.claim_thread(dep.id, thread_id, body.visitor)
    await session.close()
    return _stream(request, h, shape, wf)


@public_router.post("/share/{token}/threads/{thread_id}/resume")
async def share_resume(
    token: str, thread_id: str, body: ShareResumeIn, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """Answer the question a visitor's paused conversation asks, streaming what follows (SSE). Approvals are not
    given here: anyone with the link could approve, so they wait for the builders on the Runs page."""
    share, dep, wf = await _open(request, session, token)
    await ShareService(session).check_thread(dep.id, thread_id, body.visitor)
    pending = await _pending(request, session, thread_id, body.interrupt_id)
    if pending is None:
        raise InvalidInput("this conversation is not waiting for an answer")
    if (pending.get("kind") or "input") != "input":
        raise InvalidInput(
            "this conversation is waiting for approval from the people who run this assistant; it cannot be "
            "approved from a share link"
        )
    value = answer_value(pending, None, body.text, None)
    wf, version_id = await _continue_on(session, dep, wf, thread_id)
    h = await request.app.state.runner.start(
        wf, thread_id=thread_id, resume=ResumeSpec(value, pending.get("id")), deployment_id=dep.id,
        version_id=version_id, decided_by=None, labels={"channel": "share"},
    )  # fmt: skip
    await session.close()
    return _stream(request, h, input_shape(wf, share.input_field), wf)


@public_router.post("/share/{token}/runs/{run_id}/feedback", status_code=204)
async def share_feedback(
    token: str, run_id: str, body: ShareFeedbackIn, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    """A visitor's thumbs up or down on an answer in one of their conversations (a later rating replaces the
    earlier one). A thumbs-down puts the run in the annotation queues that ask for them."""
    _, dep, _ = await _open(request, session, token)
    thread_id = await session.scalar(select(Run.thread_id).where(Run.id == run_id))
    if thread_id is None:
        raise NotFound(f"run '{run_id}' not found")
    await ShareService(session).check_thread(dep.id, thread_id, body.visitor)
    await session.execute(delete(RunScore).where(RunScore.run_id == run_id, RunScore.source == "feedback"))
    down = body.rating == "down"
    session.add(
        RunScore(
            id=new_id("rsc"), run_id=run_id, thread_id=thread_id, workflow_id=dep.workflow_id, deployment_id=dep.id,
            evaluator="user feedback", source="feedback", score=0.0 if down else 1.0, passed=not down,
            comment=body.comment, created_by="share",
        )
    )  # fmt: skip
    await session.commit()
    if down:
        await annotation.route(session, dep.workflow_id, run_id, thread_id, thumbs_down=True)
    return Response(status_code=204)
