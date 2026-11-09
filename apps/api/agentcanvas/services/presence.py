"""Presence in the editor (G-4): who else has a workflow open, and which block each is selecting.

Each open editor tab (a *session*) posts a heartbeat every few seconds with the block it has selected and the
workflow version it has; the row it keeps in `presence` expires `TTL_S` seconds after the last one. Another tab reads
the others' rows from a Server-Sent-Events stream that sends the list whenever it changes. The rows live in the
database, so people on different API replicas see each other; a heartbeat handled on this replica also wakes this
replica's streams at once, the others catch up at the next poll (`POLL_S`). No CRDT: this only says who is here and
what they look at; saving stays the workflow's optimistic lock (`If-Match`).

A peer whose `version` is newer than yours has saved: the editor tells you and offers to reload.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from agentcanvas.db.models import AppUser, Presence

TTL_S = 30.0
POLL_S = 2.0
KEEPALIVE_S = 15.0
MAX_PEERS = 50


class Beat(BaseModel):
    session: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$", description="one per editor tab")
    selected: str | None = Field(default=None, max_length=200, description="the block (node or resource id) selected")
    version: int | None = Field(default=None, ge=0, description="the workflow version this tab has")


class Peer(BaseModel):
    session: str
    user_id: str
    name: str
    selected: str | None = None
    version: int | None = None
    seen_at: datetime


def display_name(user: AppUser | None, user_id: str) -> str:
    if user is None:
        return user_id
    return (user.display_name or user.email.split("@")[0] or user_id).strip()


class PresenceHub:
    def __init__(self, db: Any, ttl_s: float = TTL_S, poll_s: float = POLL_S):
        self.db, self.ttl_s, self.poll_s = db, ttl_s, poll_s
        self._wake: dict[str, asyncio.Event] = {}
        self.closed = False

    def close(self) -> None:
        """The server is stopping: every stream ends now (the editor reconnects, to another replica)."""
        self.closed = True
        for wake in self._wake.values():
            wake.set()

    def _event(self, wf_id: str) -> asyncio.Event:
        return self._wake.setdefault(wf_id, asyncio.Event())

    async def beat(self, wf_id: str, user_id: str, body: Beat, now: datetime | None = None) -> list[Peer]:
        """Record a tab's heartbeat; returns everyone here now (the tab included)."""
        now = now or datetime.now(UTC)
        changed = False
        for attempt in (1, 2):  # a tab's first two heartbeats can arrive together: the loser updates the winner's row
            try:
                changed = await self._record(wf_id, user_id, body, now)
                break
            except IntegrityError:
                if attempt == 2:
                    raise
        if changed:
            self._event(wf_id).set()
        return await self.peers(wf_id, now)

    async def leave(self, wf_id: str, user_id: str, session: str) -> None:
        async with self.db.sessionmaker() as s:
            await s.execute(
                delete(Presence).where(
                    Presence.workflow_id == wf_id, Presence.session_id == session, Presence.user_id == user_id
                )
            )
            await s.commit()
        self._event(wf_id).set()

    async def _record(self, wf_id: str, user_id: str, body: Beat, now: datetime) -> bool:
        async with self.db.sessionmaker() as s:
            row = await s.get(Presence, (wf_id, body.session))
            if row is not None and row.user_id != user_id:  # a session id is one person's: never take another's over
                from agentcanvas.errors import Conflict

                raise Conflict("this session belongs to someone else")
            changed = row is None or (row.selected, row.version) != (body.selected, body.version)
            if row is None:
                s.add(Presence(workflow_id=wf_id, session_id=body.session, user_id=user_id,
                               selected=body.selected, version=body.version, seen_at=now))  # fmt: skip
            else:
                row.selected, row.version, row.seen_at = body.selected, body.version, now
            await s.execute(delete(Presence).where(Presence.seen_at < now - timedelta(seconds=self.ttl_s * 4)))
            await s.commit()
            return changed

    async def peers(self, wf_id: str, now: datetime | None = None) -> list[Peer]:
        now = now or datetime.now(UTC)
        async with self.db.sessionmaker() as s:
            q = (
                select(Presence, AppUser)
                .join(AppUser, AppUser.id == Presence.user_id, isouter=True)
                .where(Presence.workflow_id == wf_id, Presence.seen_at >= now - timedelta(seconds=self.ttl_s))
                .order_by(Presence.seen_at.desc())
                .limit(MAX_PEERS)
            )
            rows = (await s.execute(q)).all()
        return sorted(
            (
                Peer(session=p.session_id, user_id=p.user_id, name=display_name(u, p.user_id),
                     selected=p.selected, version=p.version, seen_at=p.seen_at)
                for p, u in rows
            ),
            key=lambda p: (p.name.lower(), p.session),
        )  # fmt: skip

    async def stream(self, wf_id: str) -> AsyncIterator[str]:
        """SSE frames: the list now, then again whenever it changes (a comment every `KEEPALIVE_S` keeps proxies
        from closing a quiet connection)."""
        last: str | None = None
        quiet = 0.0
        wake = self._event(wf_id)
        while not self.closed:
            peers = await self.peers(wf_id)
            frame = json.dumps({"peers": [p.model_dump(mode="json", exclude={"seen_at"}) for p in peers]})
            if frame != last:
                last, quiet = frame, 0.0
                yield f"data: {frame}\n\n"
            elif quiet >= KEEPALIVE_S:
                quiet = 0.0
                yield ": keepalive\n\n"
            if self.closed:
                return
            wake.clear()
            try:
                await asyncio.wait_for(wake.wait(), self.poll_s)
            except TimeoutError:
                quiet += self.poll_s
