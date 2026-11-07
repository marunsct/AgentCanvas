"""Publishing (M6-T4): pin a version behind an API key; the pre-publish checklist (FR-DEP-02).

One deployment per (workflow, environment). Republishing moves it to the new version and keeps its key;
rotating the key invalidates the old one at once. Keys are shown once and stored as SHA-256 hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import Deployment, DeploymentShare, ShareThread, WorkflowRow, WorkflowVersion
from agentcanvas.errors import InvalidInput, NotFound, Unauthorized
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow

KEY_PREFIX = "ac_"


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
        self, row: WorkflowRow, environment: str, version: WorkflowVersion
    ) -> tuple[Deployment, str | None]:
        """Point the environment's deployment at `version`; returns (deployment, new key or None). Two first
        publishes at once: the one that loses the race (unique per workflow and environment) moves the winner's."""
        q = select(Deployment).where(Deployment.workflow_id == row.id, Deployment.environment == environment)
        dep = await self.s.scalar(q)
        key = None
        if dep is None:
            key = new_key()
            dep = Deployment(
                id=new_id("dep"),
                workflow_id=row.id,
                version_id=version.id,
                environment=environment,
                api_key_hash=hash_key(key),
                key_prefix=key[:10],
            )
            try:
                async with self.s.begin_nested():
                    self.s.add(dep)
            except IntegrityError:
                key, dep = None, await self.s.scalar(q)
                if dep is None:
                    raise
        if key is None:
            if not dep.active:  # unpublished: its old key was meant to be revoked, so it does not come back
                key = new_key()
                dep.api_key_hash, dep.key_prefix = hash_key(key), key[:10]
            dep.version_id, dep.active = version.id, True
        await self.s.commit()
        await self.s.refresh(dep)
        return dep, key

    async def rotate(self, dep_id: str) -> str:
        dep = await self.require(dep_id)
        key = new_key()
        dep.api_key_hash, dep.key_prefix = hash_key(key), key[:10]
        await self.s.commit()
        return key

    async def unpublish(self, dep_id: str) -> None:
        """Like the key, the share link is revoked: sharing goes off with a new token and the visitors' conversations
        end, so publishing again does not bring back an old link or its conversations."""
        from agentcanvas.services.share import new_token

        dep = await self.require(dep_id)
        dep.active = False
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
        dep = await self.s.get(Deployment, dep_id)
        if not token or dep is None or not dep.active or not hmac.compare_digest(dep.api_key_hash, hash_key(token)):
            raise Unauthorized("a valid API key for this deployment is required (Authorization: Bearer <key>)")
        version = await self.s.get(WorkflowVersion, dep.version_id)
        if version is None:
            raise InvalidInput("the deployment's version is missing")
        return dep, Workflow.model_validate(version.ir)
