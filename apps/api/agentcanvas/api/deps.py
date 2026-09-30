"""FastAPI dependencies: settings, database session, current user, preconditions."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.repositories import DEV_ORG, DEV_USER
from agentcanvas.db.session import Database
from agentcanvas.errors import InvalidInput, PreconditionRequired
from agentcanvas.settings import Settings


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    db: Database = request.app.state.db
    async with db.sessionmaker() as s:
        yield s


@dataclass
class CurrentUser:
    id: str
    org_id: str
    role: str


def current_user() -> CurrentUser:
    """Until OIDC lands (M7 / Sprint 10) every request acts as the seeded dev admin."""
    return CurrentUser(id=DEV_USER, org_id=DEV_ORG, role="admin")


def if_match(if_match: str | None = Header(default=None, alias="If-Match")) -> int:
    """Parse `If-Match: "<version>"` (optimistic locking). Missing header -> 428."""
    if if_match is None:
        raise PreconditionRequired("send If-Match with the workflow version you edited (from ETag)")
    try:
        return int(if_match.strip().removeprefix("W/").strip('"'))
    except ValueError as exc:
        raise InvalidInput(f"If-Match must be a version number, got '{if_match}'") from exc
