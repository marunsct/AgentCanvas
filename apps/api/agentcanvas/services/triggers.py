"""Webhook and cron triggers (M6-T5).

- **Webhook** `POST /v1/hooks/{trigger_id}`: the delivery is signed with the trigger's secret —
  `X-AgentCanvas-Timestamp: <unix seconds>` and `X-AgentCanvas-Signature: sha256=<hex HMAC-SHA256 of
  "<timestamp>." + raw body>`; deliveries more than 5 minutes old are refused, and a delivery seen before is a 409
  (replays) — and mapped to the workflow
  input with templates over `body` (e.g. `{"ticket": "{{ body.issue.title }}"}`); without a mapping the body
  itself is the input.
- **Cron**: a 5-field schedule (`*/5 9-17 * * 1-5`, UTC) checked by one scheduler task per API process. Each
  minute fires at most once across every replica: a scheduler claims it by moving the trigger's `last_fired`
  forward in one conditional UPDATE, and only the replica whose update matched starts the run.

A trigger runs its deployment's pinned version when it has one, otherwise the draft.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import Deployment, EventDedup, Project, Trigger, WorkflowRow, WorkflowVersion
from agentcanvas.errors import Conflict, InvalidInput, NotFound, Unauthorized
from agentcanvas.expr import render_template
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.security.kms import Keyring

log = structlog.get_logger()
SIGNATURE_HEADER = "X-AgentCanvas-Signature"
TIMESTAMP_HEADER = "X-AgentCanvas-Timestamp"
TOLERANCE_S = 300  # how old a signed delivery may be
_ORG_KEY = "v2:"  # webhook secrets encrypted with the organisation's data key; unprefixed = legacy key
_RANGES = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]  # day of week: 0 and 7 are Sunday


# ------------------------------------------------------------------------------------------------- cron ----
def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        rng, _, step_s = part.partition("/")
        step = int(step_s) if step_s else 1
        if step < 1:
            raise ValueError(f"bad step in '{part}'")
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            a, b = (int(x) for x in rng.split("-", 1))
        else:
            a = int(rng)
            b = hi if step_s else a
        if not (lo <= a <= b <= hi):
            raise ValueError(f"'{part}' is outside {lo}-{hi}")
        out.update(range(a, b + 1, step))
    return out


class Cron:
    """A standard 5-field cron expression (minute hour day-of-month month day-of-week), UTC."""

    def __init__(self, expr: str):
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError("a schedule has 5 fields: minute hour day-of-month month day-of-week")
        try:
            self.fields = [_field(p, lo, hi) for p, (lo, hi) in zip(parts, _RANGES, strict=True)]
        except ValueError as exc:
            raise ValueError(f"invalid schedule '{expr}': {exc}") from None
        self.fields[4] = {d % 7 for d in self.fields[4]}
        self.dom_any, self.dow_any = parts[2] == "*", parts[4] == "*"

    def matches(self, t: datetime) -> bool:
        minute, hour, dom, month, dow = self.fields
        if t.minute not in minute or t.hour not in hour or t.month not in month:
            return False
        day_ok, wd_ok = t.day in dom, (t.isoweekday() % 7) in dow
        if self.dom_any or self.dow_any:  # cron semantics: both restricted -> either may match
            return day_ok and wd_ok
        return day_ok or wd_ok


# ----------------------------------------------------------------------------------------------- config ----
class WebhookConfig(BaseModel):
    input_mapping: dict[str, str] | None = Field(
        default=None, description="channel -> template over `body`; omit to use the body as the input"
    )


class CronConfig(BaseModel):
    schedule: str
    input: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schedule")
    @classmethod
    def _valid(cls, v: str) -> str:
        Cron(v)
        return v


class TriggerIn(BaseModel):
    kind: Literal["webhook", "cron"]
    deployment_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


def sign(secret: str, body: bytes, timestamp: int | str) -> str:
    return "sha256=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def map_input(mapping: dict[str, str] | None, body: Any) -> dict[str, Any]:
    if mapping is None:
        if not isinstance(body, dict):
            raise InvalidInput("the webhook body must be a JSON object (or configure an input mapping)")
        return body
    return {name: render_template(tpl, {}, body=body) for name, tpl in mapping.items()}


def person_connectors(draft_ir: dict[str, Any] | None) -> list[str]:
    """Names of the app connectors that sign in as the person who started the run (`oauth.identity: starter`)."""
    names = []
    for res in (draft_ir or {}).get("resources") or []:
        oauth = (res.get("config") or {}).get("oauth") if res.get("type") == "mcp.server" else None
        if isinstance(oauth, dict) and oauth.get("identity", "starter") == "starter":
            names.append(str(res.get("name") or res.get("id")))
    return names


class TriggerService:
    def __init__(self, session: AsyncSession, keyring: Keyring):
        self.s = session
        self.keys = keyring

    async def _org_fernet(self, wf_id: str):
        org_id = await self.s.scalar(
            select(Project.org_id)
            .join(WorkflowRow, WorkflowRow.project_id == Project.id)
            .where(WorkflowRow.id == wf_id)
        )
        if org_id is None:
            raise NotFound(f"workflow '{wf_id}' not found")
        return await self.keys.fernet(self.s, org_id)

    async def create(self, wf_id: str, body: TriggerIn) -> tuple[Trigger, str | None]:
        row = await self.s.get(WorkflowRow, wf_id)
        if row is None:
            raise NotFound(f"workflow '{wf_id}' not found")
        if body.deployment_id:
            dep = await self.s.get(Deployment, body.deployment_id)
            if dep is None or dep.workflow_id != wf_id:
                raise InvalidInput(f"deployment '{body.deployment_id}' is not a deployment of this workflow")
        secret = None
        if body.kind == "webhook":
            config = WebhookConfig.model_validate(body.config).model_dump()
            secret = "whsec_" + secrets.token_urlsafe(24)
            config["secret"] = _ORG_KEY + (await self._org_fernet(wf_id)).encrypt(secret.encode()).decode()
        else:
            try:
                config = CronConfig.model_validate(body.config).model_dump()
            except ValidationError as exc:
                err = exc.errors()[0]
                raise InvalidInput(f"{'.'.join(map(str, err['loc']))}: {err['msg']}") from None
        trig = Trigger(
            id=new_id("trg"), workflow_id=wf_id, deployment_id=body.deployment_id, kind=body.kind, config=config
        )
        self.s.add(trig)
        await self.s.commit()
        await self.s.refresh(trig)
        return trig, secret

    async def warnings(self, wf_id: str) -> list[str]:
        """What a schedule or webhook of this workflow would hit: a trigger starts runs no person started, so a
        connector that signs in as the starter has no connection to use and its calls fail (N-5)."""
        row = await self.s.get(WorkflowRow, wf_id)
        return [
            f"'{name}' signs in as the person who started the run, but a trigger's runs have no person: "
            "set it to the service connection (an admin connects it) or its calls will fail"
            for name in person_connectors(row.draft_ir if row else None)
        ]

    async def list(self, wf_id: str) -> list[Trigger]:
        q = select(Trigger).where(Trigger.workflow_id == wf_id).order_by(Trigger.id)
        return list((await self.s.scalars(q)).all())

    async def require(self, trigger_id: str) -> Trigger:
        trig = await self.s.get(Trigger, trigger_id)
        if trig is None:
            raise NotFound(f"trigger '{trigger_id}' not found")
        return trig

    async def delete(self, trigger_id: str) -> None:
        await self.s.delete(await self.require(trigger_id))
        await self.s.commit()

    async def set_enabled(self, trigger_id: str, enabled: bool) -> Trigger:
        trig = await self.require(trigger_id)
        trig.enabled = enabled
        await self.s.commit()
        return trig

    async def verify(
        self, trigger_id: str, body: bytes, signature: str | None, timestamp: str | None, now: float | None = None
    ) -> Trigger:
        """The enabled webhook trigger this delivery is signed for. Every failure, an unknown trigger included,
        is the same 401, so callers cannot probe for trigger ids."""
        refused = Unauthorized(f"invalid, stale or missing {SIGNATURE_HEADER} / {TIMESTAMP_HEADER} headers")
        trig = await self.s.get(Trigger, trigger_id)
        if trig is None or trig.kind != "webhook" or not trig.enabled or not signature or not timestamp:
            raise refused
        try:
            age = abs((now if now is not None else datetime.now(UTC).timestamp()) - int(timestamp))
        except ValueError:
            raise refused from None
        stored: str = trig.config["secret"]
        if stored.startswith(_ORG_KEY):
            f = await self._org_fernet(trig.workflow_id)
            secret = f.decrypt(stored.removeprefix(_ORG_KEY).encode()).decode()
        else:
            secret = self.keys.legacy.decrypt(stored.encode()).decode()
        if age > TOLERANCE_S or not hmac.compare_digest(sign(secret, body, timestamp), signature.strip()):
            raise refused
        return trig

    async def claim_delivery(self, trig: Trigger, signature: str) -> str:
        """Refuse a replay within the tolerance window: each signed delivery (its signature covers the timestamp)
        starts one run. Remembered in the events' dedup table, so every replica sees it; returns the claim's id."""
        project_id = await self.s.scalar(select(WorkflowRow.project_id).where(WorkflowRow.id == trig.workflow_id))
        if project_id is None:  # not a replay: there is nothing left to run
            raise NotFound("the workflow of this webhook no longer exists")
        handler = f"webhook:{trig.id}"
        cutoff = datetime.now(UTC) - timedelta(seconds=2 * TOLERANCE_S)  # older deliveries are refused as stale
        await self.s.execute(
            delete(EventDedup).where(
                EventDedup.project_id == project_id, EventDedup.handler == handler, EventDedup.created_at < cutoff
            )
        )
        claim = EventDedup(
            id=new_id("dd"), project_id=project_id, handler=handler, value=signature.strip(), event_id=""
        )
        self.s.add(claim)
        try:
            await self.s.commit()
        except IntegrityError:
            await self.s.rollback()
            raise Conflict("this webhook delivery was already received") from None
        return claim.id

    async def release_delivery(self, claim_id: str) -> None:
        """Forget a claimed delivery whose run did not start, so the sender's retry is accepted."""
        await self.s.execute(delete(EventDedup).where(EventDedup.id == claim_id))
        await self.s.commit()

    async def project_of(self, trig: Trigger) -> str:
        """The project a trigger's workflow belongs to (the workflow id when it no longer exists)."""
        return await self.s.scalar(select(WorkflowRow.project_id).where(WorkflowRow.id == trig.workflow_id)) or (
            trig.workflow_id
        )

    async def target(self, trig: Trigger) -> tuple[Workflow, dict[str, Any]]:
        """The workflow a trigger runs (its deployment's version, or the draft) and `Runner.start` kwargs."""
        from agentcanvas.services.projects import PROJECT_OF

        row = await self.s.get(WorkflowRow, trig.workflow_id)
        if row is None:
            raise NotFound("the trigger's workflow no longer exists")
        PROJECT_OF[row.id] = row.project_id
        if trig.deployment_id:
            dep = await self.s.get(Deployment, trig.deployment_id)
            if dep is None or not dep.active:
                raise InvalidInput("the trigger's deployment is unpublished")
            ver = await self.s.get(WorkflowVersion, dep.version_id)
            assert ver is not None
            return Workflow.model_validate(ver.ir), {"deployment_id": dep.id, "version_id": ver.id}
        return Workflow.model_validate(row.draft_ir), {}


# ------------------------------------------------------------------------------------------- scheduler ----
class CronScheduler:
    """Fires due cron triggers once per minute, across replicas (see module docstring)."""

    def __init__(self, db: Any, runner: Any, keyring: Keyring, tick_s: float = 15.0):
        self.db, self.runner, self.keyring, self.tick_s = db, runner, keyring, tick_s
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="cron-scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick(datetime.now(UTC))
            except Exception:
                log.exception("cron.tick_failed")
            await asyncio.sleep(self.tick_s)

    async def tick(self, now: datetime) -> list[str]:
        """Start the runs due at `now`; returns their run ids."""
        minute = now.strftime("%Y-%m-%dT%H:%M")
        started = []
        async with self.db.sessionmaker() as s:
            rows = (await s.scalars(select(Trigger).where(Trigger.kind == "cron", Trigger.enabled))).all()
            svc = TriggerService(s, self.keyring)
            for trig in rows:
                try:  # one schedule that no longer parses must not stop every other trigger
                    due = Cron(trig.config["schedule"]).matches(now)
                except Exception as exc:  # noqa: BLE001 - also a missing or non-text schedule
                    log.warning("cron.bad_schedule", trigger=trig.id, error=str(exc))
                    continue
                if not due or not await self._claim(s, trig.id, minute):
                    continue
                try:
                    wf, kw = await svc.target(trig)
                    h = await self.runner.start(wf, input=dict(trig.config.get("input") or {}), origin="trigger", **kw)
                    started.append(h.run_id)
                    log.info("cron.fired", trigger=trig.id, run=h.run_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("cron.failed", trigger=trig.id, error=str(exc))
        return started

    @staticmethod
    async def _claim(s: AsyncSession, trigger_id: str, minute: str) -> bool:
        """True for exactly one scheduler per trigger and minute, however many replicas tick."""
        claimed = await s.execute(
            update(Trigger)
            .where(Trigger.id == trigger_id, or_(Trigger.last_fired.is_(None), Trigger.last_fired < minute))
            .values(last_fired=minute)
        )
        await s.commit()
        return claimed.rowcount == 1  # type: ignore[attr-defined]
