"""Approve and Deny from a notification (F-9): signed links to one Inbox item.

When an organisation switches them on (`org_notifier.actions`), an approval's notifications carry two links, Approve
and Deny. Each is a token signed with the server's secret key (HMAC-SHA256 under a key derived for this use only) that
binds the item, the action, the person it was sent to and an expiry: the item's own expiry, and at most `MAX_DAYS`
after it was sent. Nothing in it is secret but the signature; changing any field breaks it.

- An e-mail goes to each person who may decide on their own, with links bound to them (`recipient` = their user id).
  Such a link decides as that person, without a sign-in, after the same checks as the Inbox (their role in the
  project, who the step names, approving your own runs), from the organisation's allowed networks only. An
  organisation that requires multi-factor sign-in (or a server that requires a stronger sign-in) gets no decision
  without one: the link then opens the item in the Inbox.
- A Slack message or the organisation's webhook reaches a channel, not a person: its links (`recipient` None) only
  open the item in the Inbox, which needs a sign-in, and the decision is made there by whoever signs in.

The link opens a page of the web app (`/approve#<token>`, the token in the fragment so it never reaches a server's
access log or a Referer). Opening it changes nothing: the page reads what the link is for with a POST that changes
nothing, and decides only when the person presses the button, so a mail scanner or a link preview cannot approve. A
link decides once: the decision claims the item (`runner._decide_interrupts`), so the same link, or the other one,
then finds it decided.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

ACTIONS = ("approve", "deny")
KINDS = ("approval", "tool_approval")  # what a button can decide: a question needs an answer, written in the Inbox
MAX_DAYS = 7  # a link in a mailbox is a credential: a later reminder carries fresh ones
MAX_LEN = 2048


class InvalidLink(ValueError):
    """Not a link this server made, or one that has expired; the text is safe to show."""


@dataclass(frozen=True)
class Link:
    interrupt_id: str
    action: str  # approve | deny
    recipient: str | None  # the user id it was sent to; None: a channel (Slack, a webhook), decided in the Inbox
    expires: int  # unix time


def _key(secret: str) -> bytes:
    return hashlib.sha256(b"agentcanvas approval link\x00" + secret.encode()).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def expiry(item_expires: datetime | None, now: datetime) -> datetime:
    """When links sent now stop working: with the item, and at most `MAX_DAYS` from now."""
    latest = now + timedelta(days=MAX_DAYS)
    return min(item_expires, latest) if item_expires else latest


def sign(secret: str, link: Link) -> str:
    body = json.dumps(
        {"v": 1, "i": link.interrupt_id, "a": link.action, "u": link.recipient, "x": link.expires},
        separators=(",", ":"),
    ).encode()
    return f"{_b64(body)}.{_b64(hmac.new(_key(secret), body, hashlib.sha256).digest())}"


def issue(secret: str, interrupt_id: str, action: str, recipient: str | None, expires: datetime) -> str:
    assert action in ACTIONS
    return sign(secret, Link(interrupt_id, action, recipient, int(expires.timestamp())))


def verify(secret: str, token: Any, now: float | None = None) -> Link:
    """The link, if `token` is one this server signed and it has not expired; InvalidLink (saying why) otherwise."""
    if not isinstance(token, str) or token.count(".") != 1 or len(token) > MAX_LEN:
        raise InvalidLink("this link is not one AgentCanvas made: copy the whole link from the message")
    body_b64, tag_b64 = token.split(".")
    try:
        body, tag = _unb64(body_b64), _unb64(tag_b64)
    except (binascii.Error, ValueError):
        raise InvalidLink("this link is not one AgentCanvas made: copy the whole link from the message") from None
    if not hmac.compare_digest(hmac.new(_key(secret), body, hashlib.sha256).digest(), tag):
        raise InvalidLink("this link is not one AgentCanvas made: copy the whole link from the message")
    data = json.loads(body)
    link = Link(str(data["i"]), str(data["a"]), data.get("u"), int(data["x"]))
    if data.get("v") != 1 or link.action not in ACTIONS:
        raise InvalidLink("this link is not one AgentCanvas made: copy the whole link from the message")
    if link.expires < (now if now is not None else time.time()):
        raise InvalidLink("this link has expired: open the item in the Inbox")
    return link


def decision(kind: str, payload: dict[str, Any] | None, action: str, reason: str | None) -> dict[str, Any]:
    """What the Inbox's buttons send for this kind of item: approve as asked (no edits), or reject."""
    if kind == "tool_approval":
        asked = (payload or {}).get("action_requests") or []
        one = {"type": "approve"} if action == "approve" else {"type": "reject", "message": reason or "Rejected"}
        return {"decisions": [dict(one) for _ in range(max(len(asked), 1))]}
    return {"approved": action == "approve"}
