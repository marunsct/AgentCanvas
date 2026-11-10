"""Personal API tokens (G-13): a person's own credential for scripts and CI, next to their sign-in.

`Authorization: Bearer act_…` acts as the person who made the token, in the one organisation it was made in, with
the lower of the role chosen then and the role they hold now: a token never outlives its owner's membership, and a
demotion applies to it at once. Like deployment keys, only the token's SHA-256 is stored and compared in constant
time; the token itself is shown once. Tokens may expire, and are revoked by their owner or an admin.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import AppUser, Membership, PersonalToken
from agentcanvas.errors import Forbidden, NotFound, Unauthorized
from agentcanvas.ids import new_id

PREFIX = "act_"
RANK = {"viewer": 0, "reviewer": 1, "builder": 2, "admin": 3}
USED_EVERY = timedelta(minutes=1)  # how often a token's last use is written down


def new_token() -> str:
    return PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def is_personal(token: str) -> bool:
    return token.startswith(PREFIX)


@dataclass
class TokenCaller:
    """Who a valid token acts as."""

    token_id: str
    user_id: str
    org_id: str
    role: str  # the lower of the token's and the person's role in the organisation
    email: str
    member_role: str = ""  # the person's role in the organisation
    token_role: str = ""  # the most the token may do, in any project too


class TokenService:
    def __init__(self, s: AsyncSession):
        self.s = s

    async def create(
        self, org_id: str, user_id: str, *, name: str, role: str, holder_role: str, expires_in_days: int | None
    ) -> tuple[PersonalToken, str]:
        """A new token and its value (returned this once). It may not do more than its holder may now."""
        if RANK[role] > RANK[holder_role]:
            raise Forbidden(f"a token cannot have more rights than you: your role is '{holder_role}'")
        value = new_token()
        expires = datetime.now(UTC) + timedelta(days=expires_in_days) if expires_in_days else None
        row = PersonalToken(
            id=new_id("tok"), org_id=org_id, user_id=user_id, name=name, token_hash=hash_token(value),
            prefix=value[:10], role=role, expires_at=expires,
        )  # fmt: skip
        self.s.add(row)
        await self.s.commit()
        return row, value

    async def list(self, org_id: str, user_id: str | None = None) -> list[tuple[PersonalToken, str]]:
        """The organisation's tokens (one person's, with `user_id`) and their owners' emails, newest first."""
        q = (
            select(PersonalToken, AppUser.email)
            .join(AppUser, AppUser.id == PersonalToken.user_id)
            .where(PersonalToken.org_id == org_id)
            .order_by(PersonalToken.created_at.desc(), PersonalToken.id.desc())
        )
        if user_id is not None:
            q = q.where(PersonalToken.user_id == user_id)
        return [(t, email) for t, email in (await self.s.execute(q)).all()]

    async def revoke(self, org_id: str, token_id: str, user_id: str | None = None) -> PersonalToken:
        """Revoke a token of the organisation (only one's own, with `user_id`). Revoking twice is a no-op."""
        row = await self.s.get(PersonalToken, token_id)
        if row is None or row.org_id != org_id or (user_id is not None and row.user_id != user_id):
            raise NotFound(f"token '{token_id}' not found")
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            await self.s.commit()
        return row

    async def authenticate(self, token: str) -> TokenCaller:
        """The person a token acts as (401 for an unknown, revoked or expired token, or one whose owner left)."""
        row = await self.s.scalar(select(PersonalToken).where(PersonalToken.token_hash == hash_token(token)))
        if row is None or not hmac.compare_digest(row.token_hash, hash_token(token)):
            raise Unauthorized("unknown API token")
        now = datetime.now(UTC)
        if row.revoked_at is not None:
            raise Unauthorized("this API token was revoked")
        if row.expires_at is not None and row.expires_at <= now:
            raise Unauthorized("this API token expired")
        member = await self.s.get(Membership, (row.org_id, row.user_id))
        if member is None or not member.accepted or member.suspended:
            raise Unauthorized("the person this API token belongs to is no longer an active member of its organisation")
        user = await self.s.get(AppUser, row.user_id)
        role = min(row.role, member.role, key=RANK.__getitem__)
        caller = TokenCaller(row.id, row.user_id, row.org_id, role, user.email if user else "", member.role, row.role)
        if row.last_used_at is None or row.last_used_at <= now - USED_EVERY:
            await self.s.execute(update(PersonalToken).where(PersonalToken.id == row.id).values(last_used_at=now))
            await self.s.commit()
        return caller
