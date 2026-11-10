"""Spending caps per organisation and per project (OWASP LLM10, unbounded consumption).

Workflow budgets are set, and removed, by whoever builds the workflow, and bind only that workflow. These caps are
the administrator's: what the whole organisation, or one project, may spend a UTC day and a calendar month (UTC).

- The organisation's caps are among its limits (`max_cost_usd_day`, `max_cost_usd_month` in services/org_settings.py),
  within the server's own (`AGENTCANVAS_MAX_COST_USD_DAY`, `_MONTH`, the operator's cap for every organisation).
- A project's caps are in its settings (`spending_caps`), never above the organisation's when they are saved; the
  organisation's caps bind its projects as well.
- Spending counts every run of the scope, whatever started it (the editor's draft and debug runs, test cases,
  experiments, deployments, schedules, webhooks, event handlers), what conversations deleted since had spent
  (`spend_archive`), and the model calls outside runs: the Copilot's answers and AI judges (`model_spend`, written
  by `SpendCaps.record`). A new Copilot answer is refused at a cap like a run; judges are counted, not stopped (they
  score runs that the caps already stop).

Where they are enforced: before a run starts (`Runner.start`), a scope at or over a cap refuses the run with 429;
and after each model call of a run in flight (`Runner._pump`), the run saves what it has spent on its row, and stops
when a scope reached a cap. What the other runs spent is read from the database at most every `ttl` seconds per
server (a run's own spending is always current), so runs going at once on several servers stop within a few seconds'
spending of each other. A scope without a cap costs nothing: no spending query is made for it.

A cost alert is raised, once per scope, period and share, when spending reaches the warn share
(`cost_warn_percent`, 80% by default) and when it reaches the cap; both are audit-log entries, forwarded like the
other security-relevant ones (services/audit_sink.py).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import (
    AuditLog,
    ModelSpend,
    Organization,
    Project,
    Run,
    SpendArchive,
    SpendCapAlert,
    Thread,
    WorkflowRow,
)
from agentcanvas.db.repositories import AuditRepo
from agentcanvas.errors import InvalidInput, SpendingCapReached
from agentcanvas.ids import new_id
from agentcanvas.log import get_logger
from agentcanvas.services import org_settings
from agentcanvas.settings import Settings

log = get_logger(__name__)

PROJECT_KEY = "spending_caps"  # in `Project.settings`
CAP_FIELDS = ("max_cost_usd_day", "max_cost_usd_month")
PERIOD_OF = {"max_cost_usd_day": "day", "max_cost_usd_month": "month"}


def periods(now: datetime | None = None) -> dict[str, tuple[str, datetime]]:
    """The current budget periods (UTC): name -> (label, start)."""
    day = (now or datetime.now(UTC)).replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    return {"day": (day.strftime("%Y-%m-%d"), day), "month": (month.strftime("%Y-%m"), month)}


def period_end(period: str, now: datetime | None = None) -> datetime:
    """When the current day or month (UTC) ends."""
    start = periods(now)[period][1]
    if period == "day":
        return start + timedelta(days=1)
    return (start + timedelta(days=32)).replace(day=1)


class SpendCapStop(Exception):
    """A run in flight stopped: its organisation or project reached a spending cap."""


class ProjectCaps(BaseModel):
    """A project's spending caps in USD; null: none (the organisation's still apply)."""

    max_cost_usd_day: float | None = Field(None, gt=0, le=100_000_000, description="USD a UTC day for the project")
    max_cost_usd_month: float | None = Field(
        None, gt=0, le=100_000_000, description="USD a calendar month (UTC) for the project"
    )


def project_caps(settings: dict[str, Any] | None) -> dict[str, float]:
    """The caps a project's settings hold (only those with a value)."""
    raw = (settings or {}).get(PROJECT_KEY) or {}
    return {k: float(raw[k]) for k in CAP_FIELDS if isinstance(raw.get(k), int | float) and raw[k] > 0}


def check_project(changes: dict[str, Any], org_effective: dict[str, Any]) -> None:
    """Refuse a project cap above the organisation's (it could never apply)."""
    for key in CAP_FIELDS:
        value, ceiling = changes.get(key), org_effective.get(key) or 0
        if value is not None and ceiling > 0 and value > ceiling:
            raise InvalidInput(
                f"the organisation's {PERIOD_OF[key]} spending cap is {ceiling:g} USD: a project's cannot be higher"
            )


@dataclass(frozen=True)
class Cap:
    scope: str  # "" the organisation, else the project's id
    label: str  # "the organisation", "project 'Support'"
    period: str  # "day" | "month"
    limit: float


@dataclass(frozen=True)
class Config:
    org_id: str
    caps: list[Cap]
    warn: float  # the share of a cap at which an alert is raised


@dataclass
class Usage:
    """What a scope spent today and this month (UTC), when it was read."""

    day: float = 0.0
    month: float = 0.0
    # the runs still going then: id -> (what they had saved on their row, started today)
    running: dict[str, tuple[float, bool]] = field(default_factory=dict)

    def spent(self, period: str, run_id: str | None = None, own: float = 0.0) -> float:
        """The period's spending, with `run_id` at `own` (its current spending) instead of what it had saved."""
        total = self.day if period == "day" else self.month
        if run_id is not None and run_id in self.running:
            saved, today = self.running[run_id]
            if period == "month" or today:
                total -= saved
        return total + own


async def usage_of(s: AsyncSession, org_id: str, project_id: str | None = None) -> Usage:
    """What the organisation (or one of its projects) spent today and this month: its runs, by when they started,
    and what conversations deleted since had spent."""
    p = periods()
    day, month = p["day"][1], p["month"][1]
    scope = [Project.org_id == org_id] + ([WorkflowRow.project_id == project_id] if project_id else [])
    runs = (
        select(
            func.coalesce(func.sum(case((Run.started_at >= day, Run.cost_usd), else_=0)), 0),
            func.coalesce(func.sum(Run.cost_usd), 0),
        )
        .select_from(Run)
        .join(Thread, Thread.id == Run.thread_id)
        .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
        .join(Project, Project.id == WorkflowRow.project_id)
        # starting a run touches its conversation: only conversations touched this month can hold a run of this
        # month (a day early, for clocks a little apart)
        .where(Run.started_at >= month, Thread.updated_at >= month - timedelta(days=1), *scope)
    )
    today, this_month = (await s.execute(runs)).one()
    gone = (
        select(
            func.coalesce(func.sum(case((SpendArchive.hour >= day, SpendArchive.cost_usd), else_=0)), 0),
            func.coalesce(func.sum(SpendArchive.cost_usd), 0),
        )
        .select_from(SpendArchive)
        .join(WorkflowRow, WorkflowRow.id == SpendArchive.workflow_id)
        .join(Project, Project.id == WorkflowRow.project_id)
        .where(SpendArchive.hour >= month, *scope)
    )
    gone_today, gone_month = (await s.execute(gone)).one()
    calls = (  # the Copilot and AI judges
        select(
            func.coalesce(func.sum(case((ModelSpend.hour >= day, ModelSpend.cost_usd), else_=0)), 0),
            func.coalesce(func.sum(ModelSpend.cost_usd), 0),
        )
        .select_from(ModelSpend)
        .join(Project, Project.id == ModelSpend.project_id)
        .where(ModelSpend.hour >= month, Project.org_id == org_id)
    )
    if project_id:
        calls = calls.where(ModelSpend.project_id == project_id)
    calls_today, calls_month = (await s.execute(calls)).one()
    live = (
        select(Run.id, Run.cost_usd, case((Run.started_at >= day, 1), else_=0))
        .join(Thread, Thread.id == Run.thread_id)
        .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
        .join(Project, Project.id == WorkflowRow.project_id)
        .where(Run.status == "running", Run.started_at >= month, *scope)
    )
    running = {rid: (float(cost or 0), bool(t)) for rid, cost, t in (await s.execute(live)).all()}
    return Usage(
        day=float(today) + float(gone_today) + float(calls_today),
        month=float(this_month) + float(gone_month) + float(calls_month),
        running=running,
    )


async def spent_by_project(s: AsyncSession, org_id: str) -> dict[str, tuple[float, float]]:
    """What each of the organisation's projects spent today and this month (UTC), as `usage_of` counts it: project
    id -> (day, month), only projects that spent something."""
    p = periods()
    day, month = p["day"][1], p["month"][1]
    runs = (
        select(
            WorkflowRow.project_id,
            func.coalesce(func.sum(case((Run.started_at >= day, Run.cost_usd), else_=0)), 0),
            func.coalesce(func.sum(Run.cost_usd), 0),
        )
        .select_from(Run)
        .join(Thread, Thread.id == Run.thread_id)
        .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
        .join(Project, Project.id == WorkflowRow.project_id)
        .where(Run.started_at >= month, Thread.updated_at >= month - timedelta(days=1), Project.org_id == org_id)
        .group_by(WorkflowRow.project_id)
    )
    gone = (
        select(
            WorkflowRow.project_id,
            func.coalesce(func.sum(case((SpendArchive.hour >= day, SpendArchive.cost_usd), else_=0)), 0),
            func.coalesce(func.sum(SpendArchive.cost_usd), 0),
        )
        .select_from(SpendArchive)
        .join(WorkflowRow, WorkflowRow.id == SpendArchive.workflow_id)
        .join(Project, Project.id == WorkflowRow.project_id)
        .where(SpendArchive.hour >= month, Project.org_id == org_id)
        .group_by(WorkflowRow.project_id)
    )
    calls = (
        select(
            ModelSpend.project_id,
            func.coalesce(func.sum(case((ModelSpend.hour >= day, ModelSpend.cost_usd), else_=0)), 0),
            func.coalesce(func.sum(ModelSpend.cost_usd), 0),
        )
        .join(Project, Project.id == ModelSpend.project_id)
        .where(ModelSpend.hour >= month, Project.org_id == org_id)
        .group_by(ModelSpend.project_id)
    )
    out: dict[str, tuple[float, float]] = {}
    for q in (runs, gone, calls):
        for pid, today, this_month in (await s.execute(q)).all():
            d, m = out.get(pid, (0.0, 0.0))
            out[pid] = (d + float(today), m + float(this_month))
    return out


def _message(cap: Cap, share: float, spent: float) -> str:
    what = "reached its" if share >= 1 else f"used {share:.0%} of its"
    who = cap.label[0].upper() + cap.label[1:]
    return f"{who} {what} {cap.period} spending cap: {spent:.4g} of {cap.limit:g} USD"


class SpendCaps:
    """The caps that apply to each project, and what their scopes spent, each read at most every few seconds."""

    def __init__(self, db: Any, server: Settings, ttl: float = 5.0, config_ttl: float = 15.0):
        self.db, self.server = db, server
        self.ttl, self.config_ttl = ttl, config_ttl
        self._config: dict[str, tuple[float, Config | None]] = {}
        self._usage: dict[tuple[str, str], tuple[float, Usage]] = {}
        self._raised: set[tuple[str, str, str, str, float]] = set()  # alerts this server knows were raised
        self.alert_hooks: list[Callable[[SpendCapAlert], Any]] = []  # each new alert (metrics)

    def forget(self) -> None:
        """Read the caps afresh (a save on this server; the others see it within `config_ttl`)."""
        self._config.clear()
        self._usage.clear()

    async def config(self, project_id: str | None) -> Config | None:
        """The caps that bind a project's runs (its organisation's and its own); None: none."""
        if not project_id:
            return None
        now = time.monotonic()
        hit = self._config.get(project_id)
        if hit is not None and hit[0] >= now:
            return hit[1]
        async with self.db.sessionmaker() as s:
            row = (
                await s.execute(
                    select(Project.org_id, Project.name, Project.settings, Organization.settings)
                    .join(Organization, Organization.id == Project.org_id)
                    .where(Project.id == project_id)
                )
            ).one_or_none()
        conf = None
        if row is not None:
            org_id, name, own, org_own = row
            eff = org_settings.effective(self.server, org_own)
            caps = [Cap("", "the organisation", PERIOD_OF[k], float(eff[k])) for k in CAP_FIELDS if eff[k] > 0]
            caps += [Cap(project_id, f"project '{name}'", PERIOD_OF[k], v) for k, v in project_caps(own).items()]
            conf = Config(org_id, caps, eff["cost_warn_percent"] / 100) if caps else None
        self._config[project_id] = (now + self.config_ttl, conf)
        if len(self._config) > 1000:  # a long-running server with many projects
            self._config = {k: v for k, v in self._config.items() if v[0] >= now}
        return conf

    async def usage(self, org_id: str, scope: str) -> Usage:
        key = (org_id, scope)
        now = time.monotonic()
        hit = self._usage.get(key)
        if hit is not None and hit[0] >= now:
            return hit[1]
        async with self.db.sessionmaker() as s:
            found = await usage_of(s, org_id, scope or None)
        self._usage[key] = (now + self.ttl, found)
        if len(self._usage) > 1000:
            self._usage = {k: v for k, v in self._usage.items() if v[0] >= now}
        return found

    async def record(
        self, project_id: str | None, kind: str, cost: float | None, tokens_in: int = 0, tokens_out: int = 0
    ) -> None:
        """Add what a model call outside a run spent (the Copilot's, an AI judge's) to its project's hour. Never
        raises: an answer already given is not lost because its accounting failed (that is logged)."""
        if not project_id or not (cost or tokens_in or tokens_out):
            return
        hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        insert = pg_insert if self.db.engine.dialect.name == "postgresql" else sqlite_insert
        stmt = insert(ModelSpend).values(
            project_id=project_id, hour=hour, kind=kind, calls=1, tokens_in=tokens_in, tokens_out=tokens_out,
            cost_usd=round(cost or 0.0, 6),
        )  # fmt: skip
        stmt = stmt.on_conflict_do_update(
            index_elements=[ModelSpend.project_id, ModelSpend.hour, ModelSpend.kind],
            set_={
                "calls": ModelSpend.calls + 1,
                "tokens_in": ModelSpend.tokens_in + stmt.excluded.tokens_in,
                "tokens_out": ModelSpend.tokens_out + stmt.excluded.tokens_out,
                "cost_usd": ModelSpend.cost_usd + stmt.excluded.cost_usd,
            },
        )
        try:
            async with self.db.sessionmaker() as s:
                await s.execute(stmt)
                await s.commit()
        except Exception:
            log.exception("model_spend.save_failed", project_id=project_id, kind=kind, cost_usd=cost)
            return
        self._usage.clear()  # this server counts it at once

    async def refuse(self, project_id: str | None) -> None:
        """Before a run (or a Copilot answer) starts: 429 when its organisation or project reached a cap."""
        conf = await self.config(project_id)
        if conf is None:
            return
        for cap in conf.caps:
            spent = (await self.usage(conf.org_id, cap.scope)).spent(cap.period)
            await self._alerts(conf, cap, spent, None)
            if spent >= cap.limit:
                ends = period_end(cap.period)
                again = "tomorrow (00:00 UTC)" if cap.period == "day" else f"on {ends:%Y-%m-%d} (UTC)"
                raise SpendingCapReached(
                    f"{cap.label} reached its {cap.period} spending cap ({spent:.4g} of {cap.limit:g} USD): new runs "
                    f"start again {again}, or once an admin raises the cap in Settings > Spending",
                    retry_after=max(1, int((ends - datetime.now(UTC)).total_seconds())),
                    scope="organisation" if not cap.scope else "project",
                    period=cap.period,
                )

    async def check(self, project_id: str | None, run_id: str, cost: float) -> tuple[list[dict[str, Any]], str | None]:
        """After a model call of a run in flight that has spent `cost` so far: the alerts it raised (as event values)
        and, when a cap is reached, why the run must stop."""
        conf = await self.config(project_id)
        if conf is None:
            return [], None
        events, over = [], None
        for cap in conf.caps:
            spent = (await self.usage(conf.org_id, cap.scope)).spent(cap.period, run_id, cost)
            events += await self._alerts(conf, cap, spent, run_id)
            if spent >= cap.limit and over is None:
                over = f"spending cap reached: {cap.label} spent {spent:.4g} of {cap.limit:g} USD this {cap.period}"
        return events, over

    async def _alerts(self, conf: Config, cap: Cap, spent: float, run_id: str | None) -> list[dict[str, Any]]:
        """Raise the alerts `spent` crosses that nobody raised yet: once per scope, period and share, however many
        runs and servers cross it together."""
        label = periods()[cap.period][0]
        out = []
        for share in sorted({conf.warn, 1.0}):
            key = (conf.org_id, cap.scope, cap.period, label, share)
            if spent < cap.limit * share or key in self._raised:
                continue
            self._raised.add(key)
            message = _message(cap, share, spent)
            alert = SpendCapAlert(
                id=new_id("sca"), org_id=conf.org_id, scope=cap.scope, project_id=cap.scope or None, run_id=run_id,
                period=cap.period, period_start=label, share=share, spent_usd=spent, limit_usd=cap.limit,
                message=message,
            )  # fmt: skip
            action = "spend_cap_reached" if share >= 1 else "spend_cap_warning"
            target = f"project_id={cap.scope}" if cap.scope else f"org_id={conf.org_id}"
            detail = {
                "caller": "spending cap", "period": cap.period, "period_start": label, "share": share,
                "spent_usd": round(spent, 6), "limit_usd": cap.limit, "run_id": run_id, "message": message,
            }  # fmt: skip
            try:
                async with self.db.sessionmaker() as s:
                    s.add(alert)
                    row = AuditLog(org_id=conf.org_id, actor_id=None, action=action, target=target, detail=detail)
                    await AuditRepo(s).add(row)  # with the alert; the outbox takes it to the SIEM
            except IntegrityError:
                continue  # another run or server raised it first
            except Exception:
                log.exception("spend_cap_alert.save_failed", org_id=conf.org_id, scope=cap.scope)
                self._raised.discard(key)
                continue
            log.warning("spend_cap_alert", org_id=conf.org_id, scope=cap.scope or "organisation", period=cap.period,
                        share=share, spent_usd=spent, limit_usd=cap.limit)  # fmt: skip
            for hook in self.alert_hooks:
                try:
                    hook(alert)
                except Exception:
                    log.exception("spend_cap_alert.hook_failed")
            out.append({"scope": "organisation" if not cap.scope else "project", "period": cap.period,
                        "period_start": label, "share": share, "spent_usd": round(spent, 6), "limit_usd": cap.limit,
                        "message": message})  # fmt: skip
        if len(self._raised) > 10_000:  # alerts of past periods are never raised again
            current = {periods()[p][0] for p in ("day", "month")}
            self._raised = {k for k in self._raised if k[3] in current}
        return out
