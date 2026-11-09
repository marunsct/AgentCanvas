"""Offboarding (E-7, GDPR Art. 17 and 28(3)(g)): deleting an organisation with a grace period.

An admin's request suspends the organisation at once: every sign-in, API token, deployment key, share link, SCIM call
and run start is refused (`refusal`), and the kill switch stops what is running. During the grace period
(`AGENTCANVAS_ORG_DELETION_GRACE_DAYS`) an admin can still read the request, take it back and export the data.
After it, `OffboardingJob` deletes everything: conversations with their checkpoints, long-term memories, projects
with their workflows, files, knowledge, secrets, run-search vectors and workspaces, members' tokens and sign-ins,
policies, SCIM groups and the data key. People who belonged to no other organisation are pseudonymised. The
organisation's row stays as a tombstone with the audit entries of its deletion, so the deletion itself can be
proven.

Every API replica runs the job. One replica at a time works on an organisation (a lease on its row, renewed while
it works); the steps can be repeated, so a replica that stops half-way leaves the rest to the next one.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas import observability
from agentcanvas.adapters.spi import MemoryAbility, supports
from agentcanvas.db.models import (
    AppUser,
    AuditLog,
    InterruptTask,
    Membership,
    OAuthClient,
    OAuthConnection,
    OAuthFlow,
    Organization,
    OrgDataKey,
    OrgPolicy,
    PersonalToken,
    Project,
    Run,
    RunSearch,
    ScimGroup,
    Thread,
    WorkflowRow,
)
from agentcanvas.errors import Conflict, Forbidden
from agentcanvas.log import get_logger

log = get_logger(__name__)

BATCH = 200  # conversations deleted per transaction
LEASE = timedelta(minutes=10)  # how long a replica's claim on an organisation lasts without being renewed
# audit entries an organisation's deletion keeps: the request and the job's record of what it deleted
KEPT_ACTIONS = ("delete_organization", "purge_organization")
TOMBSTONE_NAME = "Deleted organisation"
ERASED_NAME = "Erased person"


@dataclass(frozen=True)
class Closed:
    """An organisation that refuses access: being deleted (`delete_after` in the future) or deleted."""

    org_id: str
    suspended_at: datetime
    delete_after: datetime | None
    deleted_at: datetime | None


async def closed(session: AsyncSession, org_id: str) -> Closed | None:
    """The organisation's deletion state, or None when it is open."""
    row = (
        await session.execute(
            select(Organization.suspended_at, Organization.delete_after, Organization.deleted_at).where(
                Organization.id == org_id
            )
        )
    ).first()
    if row is None or (row.suspended_at is None and row.deleted_at is None):
        return None
    return Closed(org_id, row.suspended_at or row.deleted_at, row.delete_after, row.deleted_at)


async def closed_among(session: AsyncSession, org_ids: list[str]) -> set[str]:
    """Which of `org_ids` refuse access."""
    if not org_ids:
        return set()
    q = select(Organization.id).where(
        Organization.id.in_(org_ids), or_(Organization.suspended_at.is_not(None), Organization.deleted_at.is_not(None))
    )
    return set((await session.scalars(q)).all())


def refusal(state: Closed) -> Forbidden:
    if state.deleted_at is not None:
        return Forbidden(f"organisation '{state.org_id}' was deleted", code="organisation_deleted")
    when = state.delete_after.date().isoformat() if state.delete_after else "soon"
    return Forbidden(
        f"organisation '{state.org_id}' is suspended: it will be deleted on {when}. Until then an admin can take the "
        "deletion back in Settings > Organisation",
        code="organisation_deleting",
        delete_after=state.delete_after.isoformat() if state.delete_after else None,
    )


async def refuse_if_closed(session: AsyncSession, org_id: str | None) -> None:
    """Raise the refusal when `org_id` is being deleted or was deleted."""
    if org_id and (state := await closed(session, org_id)) is not None:
        raise refusal(state)


async def request_deletion(session: AsyncSession, org_id: str, user_id: str, grace_days: int) -> Organization:
    """Suspend the organisation now and set when it is deleted. Commits."""
    org = await session.get(Organization, org_id, with_for_update=True, populate_existing=True)
    if org is None or org.deleted_at is not None:
        raise Conflict(f"organisation '{org_id}' was deleted")
    if org.suspended_at is not None:
        raise Conflict(f"organisation '{org_id}' is already being deleted, on {org.delete_after}")
    now = datetime.now(UTC)
    org.suspended_at, org.delete_after = now, now + timedelta(days=grace_days)
    org.deletion_requested_by = user_id
    await session.commit()
    return org


async def cancel_deletion(session: AsyncSession, org_id: str) -> None:
    """Take a deletion back while its grace period lasts (409 once the job has started on it). Commits."""
    org = await session.get(Organization, org_id, with_for_update=True, populate_existing=True)
    if org is None or org.deleted_at is not None:
        raise Conflict(f"organisation '{org_id}' was deleted")
    if org.suspended_at is None:
        raise Conflict("this organisation is not being deleted")
    now = datetime.now(UTC)
    if org.purge_lease_until is not None or (org.delete_after is not None and org.delete_after <= now):
        raise Conflict("the deletion has already started and cannot be taken back")
    org.suspended_at = org.delete_after = org.deletion_requested_by = None
    await session.commit()


async def erase_profile(session: AsyncSession, user_id: str) -> bool:
    """Pseudonymise a person's account: email, name and preferences go, and their sign-in subject no longer matches
    their identity provider's (a later sign-in makes a new account). The id stays, so audit entries and history still
    point at someone. Does not commit; False when there is no such person."""
    res = await session.execute(
        update(AppUser)
        .where(AppUser.id == user_id)
        .values(email="", display_name=ERASED_NAME, preferences=None, oidc_subject=f"erased:{user_id}")
    )
    return bool(res.rowcount)  # type: ignore[attr-defined]


def _projects(org_id: str) -> Any:
    return select(Project.id).where(Project.org_id == org_id)


class OffboardingJob:
    """Deletes organisations whose grace period is over. `projects` is the app's ProjectRegistry (caches and the
    workspaces directory)."""

    def __init__(
        self, db: Any, runner: Any, projects: Any, workspaces_dir: str, interval_s: float = 60.0, sink: Any = None
    ):
        self.db, self.runner, self.projects, self.sink = db, runner, projects, sink
        self.workspaces_dir, self.interval_s = Path(workspaces_dir), interval_s
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()

    def start(self) -> None:
        observability.job_started("offboarding")
        self._task = asyncio.create_task(self._loop(), name="offboarding")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    def wake(self) -> None:
        """Look now instead of at the next interval (new work was queued on this replica)."""
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                with observability.job_round("offboarding"):
                    await self.tick(datetime.now(UTC))
            except Exception:
                log.exception("offboarding.tick_failed")
            try:
                await asyncio.wait_for(self._wake.wait(), self.interval_s)
            except TimeoutError:
                pass
            self._wake.clear()

    async def tick(self, now: datetime) -> list[str]:
        """Delete the organisations due by `now`; returns the ids this replica finished."""
        async with self.db.sessionmaker() as s:
            due = list(
                (
                    await s.scalars(
                        select(Organization.id).where(
                            Organization.suspended_at.is_not(None),
                            Organization.deleted_at.is_(None),
                            Organization.delete_after <= now,
                        )
                    )
                ).all()
            )
        done = []
        for org_id in due:
            if await self.purge(org_id, now) is not None:
                done.append(org_id)
        return done

    async def _claim(self, org_id: str, now: datetime) -> bool:
        """Hold the organisation for this replica (or renew the hold); False when another replica holds it."""
        async with self.db.sessionmaker() as s:
            res = await s.execute(
                update(Organization)
                .where(
                    Organization.id == org_id,
                    Organization.suspended_at.is_not(None),
                    Organization.deleted_at.is_(None),
                    Organization.delete_after <= now,
                    or_(Organization.purge_lease_until.is_(None), Organization.purge_lease_until < now),
                )
                .values(purge_lease_until=now + LEASE)
            )
            await s.commit()
        return res.rowcount == 1  # type: ignore[attr-defined]

    async def _renew(self, org_id: str) -> None:
        async with self.db.sessionmaker() as s:
            await s.execute(
                update(Organization)
                .where(Organization.id == org_id)
                .values(purge_lease_until=datetime.now(UTC) + LEASE)
            )
            await s.commit()

    async def purge(self, org_id: str, now: datetime) -> dict[str, int] | None:
        """Delete everything the organisation holds; None when it is not due or another replica is on it."""
        if not await self._claim(org_id, now):
            return None
        log.info("offboarding.purge_started", org_id=org_id)
        from agentcanvas.services.kill_switch import stop_runs

        async with self.db.sessionmaker() as s:  # anything started before the suspension took effect everywhere
            await stop_runs(s, self.runner, org_id)
        counts = {"conversations": await self._conversations(org_id)}
        counts["memories"] = await self._memories(org_id)
        counts["projects"] = await self._delete_projects(org_id)
        counts["people_erased"], counts["members"] = await self._people(org_id)
        await self._org_rows(org_id, counts)
        log.info("offboarding.purged", org_id=org_id, **counts)
        if self.sink is not None:
            from agentcanvas.services.audit_sink import AuditEvent

            await self.sink.emit(AuditEvent(org_id, None, "purge_organization", f"org_id={org_id}", {"counts": counts}))
        return counts

    async def _conversations(self, org_id: str) -> int:
        """Every conversation: its saved state first (the rows are the only index of it), then its rows."""
        gone = 0
        while True:
            async with self.db.sessionmaker() as s:
                ids = list(
                    (
                        await s.scalars(
                            select(Thread.id)
                            .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
                            .where(WorkflowRow.project_id.in_(_projects(org_id)))
                            .order_by(Thread.id)
                            .limit(BATCH)
                        )
                    ).all()
                )
            if not ids:
                return gone
            await self.runner.forget_threads(ids)
            async with self.db.sessionmaker() as s:
                for table in (InterruptTask, RunSearch, Run):
                    await s.execute(delete(table).where(table.thread_id.in_(ids)))
                await s.execute(delete(Thread).where(Thread.id.in_(ids)))
                await s.commit()
            gone += len(ids)
            await self._renew(org_id)

    async def _memories(self, org_id: str) -> int:
        if not supports(self.runner.adapter, "memory_store"):
            return 0
        return await cast(MemoryAbility, self.runner.adapter).memory_erase((f"org:{org_id}",))

    async def _delete_projects(self, org_id: str) -> int:
        """Each project with what is left in it (workflows, deployments, files, knowledge, secrets, datasets...: the
        database deletes those with it) and its workspace on disk."""
        async with self.db.sessionmaker() as s:
            ids = list((await s.scalars(_projects(org_id))).all())
        for project_id in ids:
            async with self.db.sessionmaker() as s:
                await s.execute(delete(Project).where(Project.id == project_id))
                await s.commit()
            if self.projects is not None:
                await self.projects.forget(project_id)
            await asyncio.to_thread(shutil.rmtree, self.workspaces_dir / project_id, ignore_errors=True)
            await self._renew(org_id)
        return len(ids)

    async def _people(self, org_id: str) -> tuple[int, int]:
        """Pseudonymise the members who belong to no other organisation; returns (erased, members)."""
        async with self.db.sessionmaker() as s:
            members = list((await s.scalars(select(Membership.user_id).where(Membership.org_id == org_id))).all())
            elsewhere = set(
                (
                    await s.scalars(
                        select(Membership.user_id).where(Membership.user_id.in_(members), Membership.org_id != org_id)
                    )
                ).all()
            )
            erased = 0
            for user_id in members:
                if user_id not in elsewhere and await erase_profile(s, user_id):
                    erased += 1
            await s.commit()
        return erased, len(members)

    async def _org_rows(self, org_id: str, counts: dict[str, int]) -> None:
        """The organisation's own rows, its audit log but for the deletion's entries, and the tombstone."""
        async with self.db.sessionmaker() as s:
            for table in (PersonalToken, OAuthConnection, OAuthFlow, OAuthClient, OrgPolicy, ScimGroup, OrgDataKey,
                          Membership):  # fmt: skip
                await s.execute(delete(table).where(table.org_id == org_id))
            res = await s.execute(
                delete(AuditLog).where(AuditLog.org_id == org_id, AuditLog.action.not_in(KEPT_ACTIONS))
            )
            counts["audit_entries"] = res.rowcount or 0  # type: ignore[attr-defined]
            now = datetime.now(UTC)
            await s.execute(
                update(Organization)
                .where(Organization.id == org_id)
                .values(
                    name=TOMBSTONE_NAME, retention_days=None, environments=None, settings=None, sso_groups=None,
                    scim_token_hash=None, scim_token_prefix=None, scim_token_created_at=None, deleted_at=now,
                    purge_lease_until=None,
                )
            )  # fmt: skip
            s.add(
                AuditLog(
                    org_id=org_id, actor_id=None, action="purge_organization", target=f"org_id={org_id}",
                    detail={"counts": counts},
                )
            )  # fmt: skip
            await s.commit()
