"""Share links and chat widgets (S23): a published deployment as a chat page anyone with the link can use.

A share link carries an unguessable token (`sh_…`); the token is the only thing a visitor needs, so it is a
capability like an unlisted document link. Builders switch it off or regenerate it to revoke it; regenerating also
ends every conversation started on the old link.

Visitors are anonymous. Each browser keeps a random visitor secret; the conversations it starts are recorded with
the secret's hash, so a visitor continues only their own conversations, and only through the link they used.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from typing import Any

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import Deployment, DeploymentShare, ShareThread, WorkflowVersion
from agentcanvas.db.repositories import ScopeRepo
from agentcanvas.errors import InvalidInput, NotFound
from agentcanvas.ir.contract import contract
from agentcanvas.ir.files import accepts, is_file_schema
from agentcanvas.ir.models import Workflow
from agentcanvas.services import offboarding

TOKEN_PREFIX = "sh_"
TEXT_FIELDS = ("message", "question", "query", "input", "text", "request", "prompt", "ticket")
STRONG_ANSWER_FIELDS = ("answer", "reply", "response")
ANSWER_FIELDS = ("output", "result", "summary", "text", "message")
MAX_SUGGESTIONS = 6
MAX_TRANSCRIPT = 200  # messages kept per saved conversation; older ones drop off
# the chat page and widget always show an AI notice (AI Act Art. 50(1)): a link may reword it, never remove it
DEFAULT_AI_NOTICE = "You are chatting with an AI assistant. Answers can be wrong."
MAX_AI_NOTICE = 200


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(24)


def visitor_hash(visitor: str) -> str:
    return hashlib.sha256(visitor.encode()).hexdigest()


class InputShape(BaseModel):
    field: str | None = Field(default=None, description="the input field the visitor's message goes to")
    chat: bool = Field(default=False, description="true: the field is a message list; each message is appended to it")


MAX_FRAME_ORIGINS = 20
_ORIGIN = re.compile(r"^https?://(\*\.)?[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:\d{1,5})?$")


def clean_origin(value: str) -> str:
    """One site allowed to frame the chat page: `https://example.com`, `https://*.example.com` or
    `http://localhost:3000` (no path; `*` only as the whole first label). Lower-cased, trailing slash dropped."""
    origin = value.strip().rstrip("/").lower()
    if not _ORIGIN.match(origin) or len(origin) > 200:
        raise ValueError(f"'{value}' is not a site origin like https://example.com or https://*.example.com")
    return origin


def ai_notice(share: DeploymentShare | None) -> str:
    """What the link's chat page says to tell visitors they are chatting with an AI."""
    return (share.ai_notice if share is not None else "") or DEFAULT_AI_NOTICE


def frame_ancestors(share: DeploymentShare | None) -> str:
    """The CSP `frame-ancestors` value for a link's chat page: its allowed sites, `*` when none are set (the
    default), and only the app itself for a link that is unknown or off."""
    if share is None or not share.enabled:
        return "'self'"
    origins = [o for o in (share.frame_origins or []) if isinstance(o, str)]
    return " ".join(["'self'", *origins]) if origins else "*"


class ShareSettingsIn(BaseModel):
    enabled: bool | None = None
    title: str | None = Field(None, max_length=120)
    greeting: str | None = Field(None, max_length=2000)
    suggestions: list[str] | None = Field(None, max_length=MAX_SUGGESTIONS)
    input_field: str | None = Field(None, max_length=63, description="'' picks one from the workflow's inputs")
    frame_origins: list[str] | None = Field(
        None, description="sites allowed to show the chat page in a frame (the chat widget); empty: any site"
    )  # fmt: skip
    ai_notice: str | None = Field(
        None, max_length=MAX_AI_NOTICE,
        description="tells visitors they are chatting with an AI (always shown; may be reworded, not removed)",
    )  # fmt: skip

    @field_validator("ai_notice")
    @classmethod
    def _notice(cls, v: str | None) -> str | None:
        if v is not None and not v.strip():
            raise ValueError("the AI notice cannot be empty: visitors must be told they are chatting with an AI")
        return v.strip() if v is not None else None

    @field_validator("frame_origins")
    @classmethod
    def _origins(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        origins = list(dict.fromkeys(clean_origin(o) for o in v if o.strip()))
        if len(origins) > MAX_FRAME_ORIGINS:
            raise ValueError(f"at most {MAX_FRAME_ORIGINS} sites")
        return origins


def _messages_field(wf: Workflow) -> str | None:
    names = set(contract(wf).input)
    for c in wf.state.channels:
        if c.name in names and (
            c.reducer == "add_messages" or (c.name == "messages" and c.type.get("type") == "array")
        ):
            return c.name
    return None


def input_shape(wf: Workflow, chosen: str | None) -> InputShape:
    """Where a visitor's message goes: the chosen field, else a text input named like a question, else a message
    list, else the workflow's only text input."""
    c = contract(wf)
    messages = _messages_field(wf)
    if chosen and chosen in c.input:
        return InputShape(field=chosen, chat=chosen == messages)
    strings = [k for k, v in c.input.items() if v.get("type") in (None, "string")]
    if named := next((k for k in TEXT_FIELDS if k in strings), None):
        return InputShape(field=named)
    if messages is not None:
        return InputShape(field=messages, chat=True)
    only_in = [k for k in strings if k not in c.output]  # fields that are both in and out are rarely the question
    return InputShape(field=next((group[0] for group in (only_in, strings) if group), None))


def text_inputs(wf: Workflow) -> list[str]:
    """The inputs a visitor's message could go to: the text inputs and the message list, in the contract's order."""
    messages = _messages_field(wf)
    return [k for k, v in contract(wf).input.items() if k == messages or v.get("type") in (None, "string")]


class AttachShape(BaseModel):
    field: str = Field(description="the file input a visitor's attachments go to")
    accept: str | None = Field(default=None, description="the media types it takes, e.g. image/*")
    multiple: bool = Field(default=False, description="true: the field is a list of files")


def attach_shape(wf: Workflow) -> AttachShape | None:
    """The first file input of the workflow (G-5): where a visitor's attached files go."""
    inputs = contract(wf).input
    name = next((k for k, v in inputs.items() if is_file_schema(v)), None)
    if name is None:
        return None
    schema = inputs[name]
    return AttachShape(field=name, accept=accepts(schema), multiple=schema.get("type") == "array")


def run_input(shape: InputShape, text: str) -> dict[str, Any]:
    if shape.field is None:
        raise InvalidInput("this workflow has no text input a chat message could go to")
    if shape.chat:
        return {shape.field: [{"role": "user", "content": text}]}
    return {shape.field: text}


def _content(message: Any) -> str | None:
    if isinstance(message, str):
        return message
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if isinstance(content, list):  # content blocks: keep the text ones
        parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in content]
        content = "".join(parts)
    return content if isinstance(content, str) else None


def _last_ai(messages: Any) -> str | None:
    for m in reversed(messages if isinstance(messages, list) else []):
        role = (m.get("role") or m.get("type")) if isinstance(m, dict) else None
        if role in ("assistant", "ai", "AIMessage", "AIMessageChunk") and (text := _content(m)):
            return text
    return None


def answer_text(output: dict[str, Any] | None, shape: InputShape) -> str:
    """The visitor-facing answer in a run's output: the last assistant message of a chat workflow, else the most
    answer-like text field or assistant message, else the output as JSON."""
    out = dict(output or {})
    if shape.chat and shape.field:
        return _last_ai(out.get(shape.field)) or ""
    if shape.field:
        out.pop(shape.field, None)  # the question echoed back is not the answer
    strings = {k: v for k, v in out.items() if isinstance(v, str) and v}
    for k in STRONG_ANSWER_FIELDS:
        if k in strings:
            return strings[k]
    for v in out.values():
        if isinstance(v, list) and (text := _last_ai(v)):
            return text
    for k in ANSWER_FIELDS:
        if k in strings:
            return strings[k]
    if len(strings) == 1:
        return next(iter(strings.values()))
    if not out:
        return ""
    return json.dumps(out, indent=2, default=str, ensure_ascii=False)


def question_of(interrupt: dict[str, Any]) -> str:
    """A pause as a prompt a visitor can answer: the question it asks, or what it waits for."""
    payload = interrupt.get("payload")
    if isinstance(payload, dict):
        for k in ("question", "message", "title", "prompt", "description"):
            if isinstance(payload.get(k), str) and payload[k]:
                return payload[k]
    elif isinstance(payload, str) and payload:
        return payload
    return {
        "approval": "Do you approve?",
        "tool_approval": "May the assistant go ahead with this action?",
    }.get(interrupt.get("kind") or "", "Please answer to continue.")


class ShareService:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def get(self, dep_id: str) -> DeploymentShare | None:
        return await self.s.get(DeploymentShare, dep_id)

    async def ensure(self, dep_id: str) -> DeploymentShare:
        """The deployment's share settings, created (switched off) on first use. Two first requests at once both
        try to create them: the loser of the race reads the winner's row."""
        share = await self.get(dep_id)
        if share is None:
            share = DeploymentShare(deployment_id=dep_id, token=new_token(), enabled=False, title="", greeting="",
                                    suggestions=[])  # fmt: skip
            try:
                async with self.s.begin_nested():
                    self.s.add(share)
            except IntegrityError:
                share = await self.s.get(DeploymentShare, dep_id, populate_existing=True)
                if share is None:  # pragma: no cover - the row that clashed was deleted at once
                    raise
        return share

    async def update(self, dep_id: str, body: ShareSettingsIn, user_id: str) -> DeploymentShare:
        share = await self.ensure(dep_id)
        if body.enabled is not None:
            share.enabled = body.enabled
        if body.title is not None:
            share.title = body.title.strip()
        if body.greeting is not None:
            share.greeting = body.greeting.strip()
        if body.suggestions is not None:
            share.suggestions = [s.strip()[:200] for s in body.suggestions if s.strip()][:MAX_SUGGESTIONS]
        if body.input_field is not None:
            share.input_field = body.input_field or None
        if body.frame_origins is not None:
            share.frame_origins = body.frame_origins
        if body.ai_notice is not None:
            share.ai_notice = "" if body.ai_notice == DEFAULT_AI_NOTICE else body.ai_notice
        share.updated_by = user_id
        await self.s.commit()
        await self.s.refresh(share)
        return share

    async def regenerate(self, dep_id: str, user_id: str) -> DeploymentShare:
        """A new link; the old one and every conversation started on it stop working at once."""
        share = await self.ensure(dep_id)
        share.token, share.updated_by = new_token(), user_id
        await self.s.execute(delete(ShareThread).where(ShareThread.deployment_id == dep_id))
        await self.s.commit()
        await self.s.refresh(share)
        return share

    async def open(self, token: str) -> tuple[DeploymentShare, Deployment, Workflow]:
        """The share, its deployment and pinned workflow for a link; 404 when the link is unknown, switched off or
        its deployment unpublished (all look the same to a visitor)."""
        share = await self.s.scalar(select(DeploymentShare).where(DeploymentShare.token == token)) if token else None
        dep = await self.s.get(Deployment, share.deployment_id) if share is not None else None
        if (
            share is None
            or dep is None
            or not share.enabled
            or not dep.active
            or not hmac.compare_digest(share.token, token)
        ):
            raise NotFound("this link does not work any more; ask its owner for a new one")
        if await offboarding.closed(self.s, await ScopeRepo(self.s).org_of("deployment", dep.id) or ""):
            raise NotFound("this link does not work any more; ask its owner for a new one")  # E-7: being deleted
        version = await self.s.get(WorkflowVersion, dep.version_id)
        if version is None:
            raise NotFound("this link does not work any more; ask its owner for a new one")
        return share, dep, Workflow.model_validate(version.ir)

    async def check_thread(self, dep_id: str, thread_id: str, visitor: str) -> None:
        """The conversation was started on this link by this visitor (404 otherwise, so ids cannot be probed)."""
        row = await self.s.get(ShareThread, thread_id)
        if (
            row is None
            or row.deployment_id != dep_id
            or not hmac.compare_digest(row.visitor_hash, visitor_hash(visitor))
        ):
            raise NotFound(f"conversation '{thread_id}' not found")

    async def claim_thread(self, dep_id: str, thread_id: str, visitor: str) -> None:
        self.s.add(ShareThread(thread_id=thread_id, deployment_id=dep_id, visitor_hash=visitor_hash(visitor)))
        await self.s.commit()

    async def record(self, thread_id: str, *entries: dict[str, Any]) -> None:
        """Add what the visitor sent or was told to the conversation they will see again after a reload."""
        row = await self.s.get(ShareThread, thread_id, populate_existing=True)
        if row is None:  # the link was regenerated, or the conversation expired, while the run went on
            return
        row.transcript = [*(row.transcript or []), *entries][-MAX_TRANSCRIPT:]
        await self.s.commit()

    async def latest(self, dep_id: str, visitor: str) -> ShareThread | None:
        """The visitor's newest conversation on this link that they have not closed."""
        return await self.s.scalar(
            select(ShareThread)
            .where(
                ShareThread.deployment_id == dep_id,
                ShareThread.visitor_hash == visitor_hash(visitor),
                ShareThread.closed.is_(False),
            )
            .order_by(ShareThread.created_at.desc(), ShareThread.thread_id.desc())
            .limit(1)
        )

    async def close_conversations(self, dep_id: str, visitor: str) -> None:
        """ "New conversation": the visitor's conversations stay (the builders see their runs) but are not shown to
        them again."""
        await self.s.execute(
            update(ShareThread)
            .where(ShareThread.deployment_id == dep_id, ShareThread.visitor_hash == visitor_hash(visitor))
            .values(closed=True)
        )
        await self.s.commit()
