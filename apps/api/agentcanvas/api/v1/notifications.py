"""Where an organisation's approval notifications go (E-6, Settings > Organisation > Notifications): e-mail, a Slack
incoming webhook and a signed webhook of its own; whether the messages carry Approve and Deny links (F-9); and whether
each project's weekly run digest goes there too (F-11). Admins only (`deps.required_role`): the webhook's address and
whether a Slack address is set are shown, the Slack address and the signing secret never are."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note
from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.models import AppUser, OrgNotifier
from agentcanvas.errors import Conflict, InvalidInput
from agentcanvas.services import notifier

router = APIRouter(prefix="/org", tags=["organisation"])
Email = Annotated[str, StringConstraints(pattern=r"^[^@\s]+@[^@\s]+$", max_length=320)]


class EmailOut(BaseModel):
    enabled: bool
    to: list[str] = Field(description="addresses that get the items whose step names nobody; empty: every reviewer")
    available: bool = Field(description="the server has a mail server (AGENTCANVAS_SMTP_HOST and _FROM)")


class NotificationsOut(BaseModel):
    email: EmailOut
    slack: bool = Field(description="a Slack incoming webhook is set (its address is never shown)")
    webhook_url: str | None
    webhook_secret_set: bool
    digest: bool = Field(default=False, description="each project's weekly run digest also goes to these channels")
    actions: bool = Field(
        default=False,
        description="approval messages carry Approve and Deny links: e-mails, bound to each person; Slack and the "
        "webhook, links that open the item in the Inbox",
    )
    updated_at: datetime | None = None


class NotificationsSaved(NotificationsOut):
    webhook_secret: str | None = Field(
        None, description="the webhook's signing secret, shown this once when the server made it"
    )


class NotificationsIn(BaseModel):
    """Only what is sent changes; `null` removes the Slack address or the webhook."""

    email: bool | None = None
    email_to: list[Email] | None = Field(None, max_length=notifier.MAX_EMAIL_TO)
    slack_url: str | None = Field(None, max_length=500)
    webhook_url: str | None = Field(None, max_length=2000)
    webhook_secret: str | None = Field(None, min_length=notifier.SECRET_MIN, max_length=200)
    digest: bool | None = None
    actions: bool | None = None


def _out(row: OrgNotifier | None, smtp: bool) -> NotificationsOut:
    if row is None:
        return NotificationsOut(
            email=EmailOut(enabled=False, to=[], available=smtp), slack=False, webhook_url=None,
            webhook_secret_set=False,
        )  # fmt: skip
    return NotificationsOut(
        email=EmailOut(enabled=row.email, to=list(row.email_to or []), available=smtp),
        slack=row.slack_url is not None, webhook_url=row.webhook_url,
        webhook_secret_set=row.webhook_secret is not None, digest=row.digest, actions=row.actions,
        updated_at=row.updated_at,
    )  # fmt: skip


@router.get("/notifications", response_model=NotificationsOut)
async def get_notifications(
    request: Request, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> NotificationsOut:
    """Where approval notifications go."""
    return _out(await session.get(OrgNotifier, user.org_id), notifier.smtp_ready(request.app.state.settings))


@router.put("/notifications", response_model=NotificationsSaved)
async def put_notifications(
    body: NotificationsIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> NotificationsSaved:
    """Change where approval notifications go. Setting a webhook without a secret makes one, returned this once."""
    settings = request.app.state.settings
    sent = body.model_fields_set
    if body.email and not notifier.smtp_ready(settings):
        raise Conflict("this server has no mail server: ask its operator to set AGENTCANVAS_SMTP_HOST and _FROM")
    row = await session.get(OrgNotifier, user.org_id, with_for_update=True, populate_existing=True)
    if row is None:
        row = OrgNotifier(org_id=user.org_id, email=False, email_to=[], digest=False, actions=False)
        session.add(row)
    f = await request.app.state.keyring.fernet(session, user.org_id)
    made: str | None = None
    if "email" in sent:
        row.email = bool(body.email)
    if "digest" in sent:
        row.digest = bool(body.digest)
    if "actions" in sent:
        row.actions = bool(body.actions)
    if "email_to" in sent:
        row.email_to = list(dict.fromkeys(e.strip() for e in body.email_to or []))
    if "slack_url" in sent:
        row.slack_url = f.encrypt(notifier.check_slack_url(body.slack_url).encode()) if body.slack_url else None
    if "webhook_url" in sent:
        if body.webhook_url:
            row.webhook_url = notifier.check_webhook_url(body.webhook_url, settings.notify_allow_private)
        else:
            row.webhook_url, row.webhook_secret = None, None
    if "webhook_secret" in sent:
        if body.webhook_secret is None:
            raise InvalidInput("a webhook needs a signing secret: send a new one instead of removing it")
        row.webhook_secret = f.encrypt(body.webhook_secret.encode())
    if row.webhook_url and row.webhook_secret is None:
        made = notifier.new_secret()
        row.webhook_secret = f.encrypt(made.encode())
    row.updated_by = user.id if await session.get(AppUser, user.id) else None
    await session.commit()
    await session.refresh(row)
    # what changed, never an address (it may hold a token) or the secret
    note(request, target=f"org_id={user.org_id}", changed=sorted(sent), email=row.email,
         slack=row.slack_url is not None, webhook_host=urlsplit(row.webhook_url).hostname if row.webhook_url else None,
         secret_made=made is not None, digest=row.digest, actions=row.actions)  # fmt: skip
    return NotificationsSaved(**_out(row, notifier.smtp_ready(settings)).model_dump(), webhook_secret=made)


class TestResult(BaseModel):
    channel: str
    ok: bool
    error: str | None


@router.post("/notifications/test", response_model=list[TestResult])
async def test_notifications(
    request: Request, session: AsyncSession = Depends(get_session), user: CurrentUser = Depends(current_user)
) -> list[TestResult]:
    """Send a sample notification on each channel that is on (e-mail to you), and say how each went."""
    email = user.email or (u.email if (u := await session.get(AppUser, user.id)) else None)
    results = await request.app.state.notifier.test(session, user.org_id, email)
    return [TestResult(**r) for r in results]
