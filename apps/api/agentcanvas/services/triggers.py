"""Webhook and cron triggers (M6-T5).

- **Webhook** `POST /v1/hooks/{trigger_id}`: the body is signed with the trigger's secret —
  header `X-AgentCanvas-Signature: sha256=<hex HMAC-SHA256 of the raw body>` — and mapped to the workflow
  input with templates over `body` (e.g. `{"ticket": "{{ body.issue.title }}"}`); without a mapping the body
  itself is the input.
- **Cron**: a 5-field schedule (`*/5 9-17 * * 1-5`, UTC) checked by one scheduler task per API process; each
  minute fires at most once. Multi-instance deployments need a leader lock (Postgres advisory lock, S11).

A trigger runs its deployment's pinned version when it has one, otherwise the draft.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import Deployment, Trigger, WorkflowRow, WorkflowVersion
from agentcanvas.errors import InvalidInput, NotFound, Unauthorized
from agentcanvas.expr import render_template
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.services.secrets import _fernet

log = structlog.get_logger()
SIGNATURE_HEADER = "X-AgentCanvas-Signature"
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


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def map_input(mapping: dict[str, str] | None, body: Any) -> dict[str, Any]:
    if mapping is None:
        if not isinstance(body, dict):
            raise InvalidInput("the webhook body must be a JSON object (or configure an input mapping)")
        return body
    return {name: render_template(tpl, {}, body=body) for name, tpl in mapping.items()}


class TriggerService:
    def __init__(self, session: AsyncSession, secret_key: str):
        self.s = session
        self.f = _fernet(secret_key)

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
            config["secret"] = self.f.encrypt(secret.encode()).decode()
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

    def verify(self, trig: Trigger, body: bytes, signature: str | None) -> None:
        if trig.kind != "webhook" or not trig.enabled:
            raise NotFound(f"trigger '{trig.id}' is not an enabled webhook")
        secret = self.f.decrypt(trig.config["secret"].encode()).decode()
        if not signature or not hmac.compare_digest(sign(secret, body), signature.strip()):
            raise Unauthorized(f"invalid or missing {SIGNATURE_HEADER} header")

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
    """Fires due cron triggers once per minute (single API instance; see module docstring)."""

    def __init__(self, db: Any, runner: Any, secret_key: str, tick_s: float = 15.0):
        self.db, self.runner, self.secret_key, self.tick_s = db, runner, secret_key, tick_s
        self.fired: dict[str, str] = {}  # trigger id -> "YYYY-mm-ddTHH:MM" it last fired for
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
            svc = TriggerService(s, self.secret_key)
            for trig in rows:
                if self.fired.get(trig.id) == minute or not Cron(trig.config["schedule"]).matches(now):
                    continue
                self.fired[trig.id] = minute
                try:
                    wf, kw = await svc.target(trig)
                    h = await self.runner.start(wf, input=dict(trig.config.get("input") or {}), origin="trigger", **kw)
                    started.append(h.run_id)
                    log.info("cron.fired", trigger=trig.id, run=h.run_id)
                except Exception as exc:  # noqa: BLE001
                    log.warning("cron.failed", trigger=trig.id, error=str(exc))
        return started
