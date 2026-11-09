"""Publishing (M6-T4): pin a version behind API keys; the pre-publish checklist (FR-DEP-02).

One deployment per (workflow, environment). Republishing moves it to the new version and keeps its keys. A
deployment may have several named keys at once (E-8), each with an optional expiry, so a key is replaced without
downtime: make a new one, move the callers, revoke the old one (rotating does both, with an optional overlap).
Keys are shown once and stored as SHA-256 hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import (
    Deployment,
    DeploymentKey,
    DeploymentShare,
    ShareThread,
    WorkflowRow,
    WorkflowVersion,
)
from agentcanvas.errors import Conflict, InvalidInput, NotFound, Unauthorized
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow

KEY_PREFIX = "ac_"
MAX_KEYS = 20  # keys that work at once, per deployment
USED_EVERY = timedelta(minutes=1)  # how often a key's last use is written down


class Check(BaseModel):
    name: str  # validation | tests | secrets
    ok: bool
    detail: str = ""
    items: list[str] = Field(default_factory=list)


def new_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def secret_refs(value: Any) -> set[str]:
    if isinstance(value, dict):
        if isinstance(value.get("$secret"), str):
            return {value["$secret"]}
        return set().union(set(), *(secret_refs(v) for v in value.values()))
    if isinstance(value, list):
        return set().union(set(), *(secret_refs(v) for v in value))
    return set()


class DeploymentService:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def list(self, wf_id: str) -> list[tuple[Deployment, WorkflowVersion]]:
        q = (
            select(Deployment, WorkflowVersion)
            .join(WorkflowVersion, WorkflowVersion.id == Deployment.version_id)
            .where(Deployment.workflow_id == wf_id)
            .order_by(Deployment.id)
        )
        return [(d, v) for d, v in (await self.s.execute(q)).all()]

    async def require(self, dep_id: str) -> Deployment:
        dep = await self.s.get(Deployment, dep_id)
        if dep is None:
            raise NotFound(f"deployment '{dep_id}' not found")
        return dep

    async def checklist(
        self,
        wf: Workflow,
        errors: list[str],
        project_secrets: set[str],
        run_tests: Callable[[], Any],
        policies: list[str] | None = None,
    ) -> list[Check]:
        broken = policies or []
        checks = [
            Check(name="validation", ok=not errors, detail=f"{len(errors)} error(s)" if errors else "no errors",
                  items=errors[:10]),
            Check(name="policies", ok=not broken, items=broken[:10],
                  detail=f"{len(broken)} violation(s)" if broken else "every organisation policy holds"),
        ]  # fmt: skip
        missing = sorted(secret_refs(wf.model_dump(mode="json")) - project_secrets)
        checks.append(
            Check(name="secrets", ok=not missing, items=missing,
                  detail=f"missing: {', '.join(missing)}" if missing else "every $secret reference resolves")
        )  # fmt: skip
        if errors:  # a workflow with errors cannot run its tests
            checks.append(Check(name="tests", ok=False, detail="not run: fix the validation errors first"))
            return checks
        results = await run_tests()
        failed = [r for r in results if not r.passed]
        passed = len(results) - len(failed)
        checks.append(
            Check(
                name="tests",
                ok=not failed,
                detail=f"{passed}/{len(results)} test case(s) pass" if results else "no test cases",
                items=[f"{r.name}: {r.failures[0] if r.failures else 'failed'}" for r in failed],
            )
        )
        return checks

    async def publish(
        self, row: WorkflowRow, environment: str, version: WorkflowVersion, created_by: str | None = None
    ) -> tuple[Deployment, str | None]:
        """Point the environment's deployment at `version`; returns (deployment, new key or None). Two first
        publishes at once: the one that loses the race (unique per workflow and environment) moves the winner's."""
        q = select(Deployment).where(Deployment.workflow_id == row.id, Deployment.environment == environment)
        dep = await self.s.scalar(q)
        key = None
        if dep is None:
            dep = Deployment(id=new_id("dep"), workflow_id=row.id, version_id=version.id, environment=environment)
            try:
                async with self.s.begin_nested():
                    self.s.add(dep)
                    await self.s.flush()
                    _, key = self._add_key(dep.id, "default", created_by, None)
            except IntegrityError:
                key, dep = None, await self.s.scalar(q)
                if dep is None:
                    raise
        if key is None:
            if not dep.active:  # unpublished: its old keys were meant to be revoked, so they do not come back
                await self._revoke_all(dep.id, datetime.now(UTC))
                _, key = self._add_key(dep.id, "default", created_by, None)
            dep.version_id, dep.active = version.id, True
        await self.s.commit()
        await self.s.refresh(dep)
        return dep, key

    # ------------------------------------------------------------------------------------------------ keys ----
    def _add_key(
        self, dep_id: str, name: str, created_by: str | None, expires_at: datetime | None
    ) -> tuple[DeploymentKey, str]:
        value = new_key()
        row = DeploymentKey(
            id=new_id("dky"), deployment_id=dep_id, name=name, key_hash=hash_key(value), prefix=value[:10],
            created_by=created_by, expires_at=expires_at, created_at=datetime.now(UTC),  # to the microsecond: ordered
        )  # fmt: skip
        self.s.add(row)
        return row, value

    async def _revoke_all(self, dep_id: str, at: datetime) -> None:
        await self.s.execute(
            update(DeploymentKey)
            .where(DeploymentKey.deployment_id == dep_id, DeploymentKey.revoked_at.is_(None))
            .values(revoked_at=at)
        )

    async def keys(self, dep_id: str) -> list[DeploymentKey]:
        """The deployment's keys, revoked and expired ones included, newest first."""
        q = select(DeploymentKey).where(DeploymentKey.deployment_id == dep_id)
        return list(await self.s.scalars(q.order_by(DeploymentKey.created_at.desc(), DeploymentKey.id.desc())))

    async def newest_prefixes(self, dep_ids: list[str]) -> dict[str, str]:
        """The prefix of each deployment's newest working key (the one the list shows)."""
        now = datetime.now(UTC)
        q = (
            select(DeploymentKey.deployment_id, DeploymentKey.prefix)
            .where(DeploymentKey.deployment_id.in_(dep_ids), *_working(now))
            .order_by(DeploymentKey.created_at, DeploymentKey.id)
        )
        return {d: p for d, p in (await self.s.execute(q)).all()}  # the newest comes last and wins

    async def add_key(
        self, dep_id: str, name: str, created_by: str | None, expires_in_days: int | None = None
    ) -> tuple[DeploymentKey, str]:
        """A new key next to the ones that work already; returns it and its value (shown this once)."""
        row, value = await self._new_key(dep_id, name, created_by, expires_in_days, limit=True)
        await self.s.commit()
        return row, value

    async def _new_key(
        self, dep_id: str, name: str, created_by: str | None, expires_in_days: int | None, *, limit: bool
    ) -> tuple[DeploymentKey, str]:
        """Add a key, not committed, with the deployment's row locked until the commit: two changes of its keys at
        once take turns (counting them, or each revoking the other's new key)."""
        dep = await self.s.get(Deployment, dep_id, with_for_update=True, populate_existing=True)
        if dep is None:
            raise NotFound(f"deployment '{dep_id}' not found")
        if not dep.active:
            raise Conflict("this deployment is unpublished: publish it again, which makes a new key")
        now = datetime.now(UTC)
        if limit:
            working = await self.s.scalar(
                select(func.count())
                .select_from(DeploymentKey)
                .where(DeploymentKey.deployment_id == dep_id, *_working(now))
            )
            if (working or 0) >= MAX_KEYS:
                raise Conflict(f"a deployment has at most {MAX_KEYS} working keys: revoke one first")
        expires = now + timedelta(days=expires_in_days) if expires_in_days else None
        row, value = self._add_key(dep_id, name, created_by, expires)
        await self.s.flush()
        return row, value

    async def revoke_key(self, dep_id: str, key_id: str) -> DeploymentKey:
        """Revoke one of the deployment's keys: it stops working at once. Revoking twice is a no-op."""
        row = await self.s.get(DeploymentKey, key_id)
        if row is None or row.deployment_id != dep_id:
            raise NotFound(f"key '{key_id}' not found")
        if row.revoked_at is None:
            row.revoked_at = datetime.now(UTC)
            await self.s.commit()
        return row

    async def rotate(
        self, dep_id: str, created_by: str | None = None, overlap_hours: int = 0
    ) -> tuple[DeploymentKey, str]:
        """A new key; the deployment's other keys stop working now, or after `overlap_hours` (callers move over
        meanwhile; a key that would expire sooner keeps its own expiry)."""
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        new, value = await self._new_key(dep_id, f"rotated {stamp}", created_by, None, limit=False)
        now = datetime.now(UTC)
        others = (DeploymentKey.deployment_id == dep_id, DeploymentKey.id != new.id, *_working(now))
        if overlap_hours <= 0:
            await self.s.execute(update(DeploymentKey).where(*others).values(revoked_at=now))
        else:
            until = now + timedelta(hours=overlap_hours)
            await self.s.execute(
                update(DeploymentKey)
                .where(*others, or_(DeploymentKey.expires_at.is_(None), DeploymentKey.expires_at > until))
                .values(expires_at=until)
            )
        await self.s.commit()
        return new, value

    async def unpublish(self, dep_id: str) -> None:
        """Like the keys, the share link is revoked: sharing goes off with a new token and the visitors' conversations
        end, so publishing again does not bring back an old link or its conversations."""
        from agentcanvas.services.share import new_token

        dep = await self.require(dep_id)
        dep.active = False
        await self._revoke_all(dep_id, datetime.now(UTC))
        await self.s.execute(
            update(DeploymentShare)
            .where(DeploymentShare.deployment_id == dep_id)
            .values(enabled=False, token=new_token())
        )
        await self.s.execute(delete(ShareThread).where(ShareThread.deployment_id == dep_id))
        await self.s.commit()

    async def authenticate(self, dep_id: str, authorization: str | None) -> tuple[Deployment, Workflow]:
        """The deployment and its pinned workflow for a `Bearer <key>` header (401 on any mismatch)."""
        token = (authorization or "").removeprefix("Bearer ").strip()
        refused = Unauthorized("a valid API key for this deployment is required (Authorization: Bearer <key>)")
        dep = await self.s.get(Deployment, dep_id)
        if not token or dep is None or not dep.active:
            raise refused
        digest = hash_key(token)
        key = await self.s.scalar(select(DeploymentKey).where(DeploymentKey.key_hash == digest))
        if key is None or key.deployment_id != dep.id or not hmac.compare_digest(key.key_hash, digest):
            raise refused
        now = datetime.now(UTC)
        if key.revoked_at is not None:
            raise Unauthorized(f"this deployment key ({key.prefix}…) was revoked")
        if key.expires_at is not None and key.expires_at <= now:
            raise Unauthorized(f"this deployment key ({key.prefix}…) expired")
        version = await self.s.get(WorkflowVersion, dep.version_id)
        if version is None:
            raise InvalidInput("the deployment's version is missing")
        if key.last_used_at is None or key.last_used_at <= now - USED_EVERY:
            # the condition is repeated in the statement: replicas serving the same key at once write it only once
            stale = or_(DeploymentKey.last_used_at.is_(None), DeploymentKey.last_used_at <= now - USED_EVERY)
            await self.s.execute(
                update(DeploymentKey).where(DeploymentKey.id == key.id, stale).values(last_used_at=now)
            )
            await self.s.commit()
        return dep, Workflow.model_validate(version.ir)


def _working(now: datetime) -> tuple[Any, ...]:
    """The conditions of a key that works at `now`: not revoked, not expired."""
    return (
        DeploymentKey.revoked_at.is_(None),
        or_(DeploymentKey.expires_at.is_(None), DeploymentKey.expires_at > now),
    )
