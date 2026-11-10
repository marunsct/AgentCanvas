"""Weekly run digests (F-11): what a project's runs did in a UTC week (Monday 00:00 to Monday 00:00).

A digest counts the week's runs (people's runs only: test and experiment runs are left out, as in run search),
how many failed and why, grouped the way experiments group failures (the error with numbers and quoted values taken
out, so alike errors group), what they spent, how long they took (median and 95th percentile), the busiest
workflows and the runs most often flagged (failing evaluator scores, thumbs-down feedback, review queue items). It
is computed from the database alone: no model is asked.

Every API server runs the job. Each tick it looks for projects that had runs in the week that just ended and have
no digest for it, computes one and inserts it; the unique key (project, week) is the claim, so whatever the number
of servers each digest is made once, and a server that loses the race drops its copy. Digests older than a year are
deleted. A digest holds no inputs or answers, only counts, workflow names and errors with their values taken out.

An organisation can opt in (Settings > Organisation > Notifications) to have each digest sent to the channels it set
for approval notifications. The notifications are queued in the transaction that claims the digest (one row per
channel in `digest_notice`), and sent by a round of the notifier's loop, claimed, retried and given up like approval
notifications. They carry counts and workflow names, not the errors' text (see `DigestMessage`).
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from prometheus_client import Counter
from sqlalchemy import case, delete, extract, func, select, update
from sqlalchemy.exc import IntegrityError

from agentcanvas import observability
from agentcanvas.db.models import (
    AnnotationItem,
    AnnotationQueue,
    AppUser,
    DigestNotice,
    Membership,
    OrgNotifier,
    Project,
    Run,
    RunDigest,
    RunScore,
    Thread,
    WorkflowRow,
)
from agentcanvas.ids import new_id
from agentcanvas.log import get_logger
from agentcanvas.services import notifier
from agentcanvas.services.experiments import failure_reason
from agentcanvas.services.notifier import NotifierJob
from agentcanvas.services.run_search import PEOPLES
from agentcanvas.services.secrets import hide_secrets
from agentcanvas.services.triggers import sign

log = get_logger(__name__)

DIGESTS_SENT = Counter(
    "agentcanvas_digest_notifications_total",
    "Weekly run digests sent to an organisation's channels, by channel and outcome (sent, retry, failed, dropped)",
    ["channel", "outcome"],
)

WEEK = timedelta(days=7)
KEEP = timedelta(days=366)  # digests older than this are deleted
BATCH = 20  # projects one tick makes digests for; the rest wait for the next tick
FAILURES_READ = 5000  # newest failed runs of a week grouped by their error
GROUPS = 5
FLAGGED = 5
WORKFLOWS = 5
DURATIONS_READ = 50_000  # finished runs whose duration a SQLite database reads (Postgres computes it in SQL)


def week_of(t: datetime) -> datetime:
    """The Monday 00:00 UTC that starts the week holding `t`."""
    t = t.astimezone(UTC)
    return datetime(t.year, t.month, t.day, tzinfo=UTC) - timedelta(days=t.weekday())


def _percentile(values: list[float], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(q * len(ordered)) - 1)])


async def compute(s: Any, project_id: str, start: datetime, end: datetime, secrets: dict[str, str]) -> dict[str, Any]:
    """The digest of a project's runs that started in [start, end)."""
    week = (
        WorkflowRow.project_id == project_id,
        Run.started_at >= start,
        Run.started_at < end,
        PEOPLES,
    )

    def runs_q(*columns: Any) -> Any:
        q = select(*columns).select_from(Run).join(Thread, Thread.id == Run.thread_id)
        return q.join(WorkflowRow, WorkflowRow.id == Thread.workflow_id).where(*week)

    failed = Run.status == "error"
    failures = func.coalesce(func.sum(case((failed, 1), else_=0)), 0)
    total, n_failed, cost, tokens = (
        await s.execute(
            runs_q(
                func.count(),
                failures,
                func.coalesce(func.sum(Run.cost_usd), 0),
                func.coalesce(func.sum(Run.tokens_in + Run.tokens_out), 0),
            )
        )
    ).one()
    total, n_failed = int(total or 0), int(n_failed or 0)

    # how long they took: in SQL on Postgres; SQLite (one server, small) reads the durations
    finished = (Run.ended_at.is_not(None), Run.status != "running")
    if s.bind.dialect.name == "postgresql":
        ms = extract("epoch", Run.ended_at - Run.started_at) * 1000
        p50, p95 = (
            await s.execute(
                runs_q(
                    func.percentile_disc(0.5).within_group(ms),
                    func.percentile_disc(0.95).within_group(ms),
                ).where(*finished)
            )
        ).one()
        p50 = round(float(p50)) if p50 is not None else None
        p95 = round(float(p95)) if p95 is not None else None
    else:
        ms = (func.julianday(Run.ended_at) - func.julianday(Run.started_at)) * 86_400_000
        took = [float(v) for v in (await s.scalars(runs_q(ms).where(*finished).limit(DURATIONS_READ))).all()]
        p50, p95 = _percentile(took, 0.5), _percentile(took, 0.95)

    # why runs failed, grouped like an experiment's failures
    groups: dict[str, dict[str, Any]] = {}
    rows = await s.execute(
        runs_q(Run.id, Run.thread_id, Run.error, WorkflowRow.id, WorkflowRow.name)
        .where(failed)
        .order_by(Run.started_at.desc(), Run.id.desc())
        .limit(FAILURES_READ)
    )
    for run_id, thread_id, error, wf_id, wf_name in rows.all():
        reason = failure_reason(hide_secrets(error or "failed", secrets))
        g = groups.setdefault(
            reason,
            {"reason": reason, "runs": 0, "workflows": [], "example": {"run_id": run_id, "thread_id": thread_id,
                                                                     "workflow_id": wf_id}},
        )  # fmt: skip
        g["runs"] += 1
        if wf_name not in g["workflows"] and len(g["workflows"]) < 5:
            g["workflows"].append(wf_name)
    failure_groups = sorted(groups.values(), key=lambda g: (-g["runs"], g["reason"]))[:GROUPS]

    # the busiest workflows
    by_wf = await s.execute(
        runs_q(
            WorkflowRow.id, WorkflowRow.name, func.count(), failures, func.coalesce(func.sum(Run.cost_usd), 0),
        )
        .group_by(WorkflowRow.id, WorkflowRow.name)
        .order_by(func.count().desc(), WorkflowRow.id)
        .limit(WORKFLOWS)
    )  # fmt: skip
    workflows = [
        {"id": i, "name": name, "runs": int(n), "failed": int(f or 0), "cost_usd": round(float(c or 0), 6)}
        for i, name, n, f, c in by_wf.all()
    ]

    # the runs flagged most: failing scores (evaluators, thumbs-down) and review queue items
    flags: dict[str, dict[str, Any]] = {}
    scored = await s.execute(
        runs_q(Run.id, Run.thread_id, WorkflowRow.id, WorkflowRow.name, RunScore.evaluator)
        .join(RunScore, RunScore.run_id == Run.id)
        .where(RunScore.passed.is_(False))
    )
    queued = await s.execute(
        runs_q(Run.id, Run.thread_id, WorkflowRow.id, WorkflowRow.name, AnnotationQueue.name)
        .join(AnnotationItem, AnnotationItem.run_id == Run.id)
        .join(AnnotationQueue, AnnotationQueue.id == AnnotationItem.queue_id)
    )
    reasons = [tuple(r) for r in scored.all()] + [(*r[:4], f"review: {r[4]}") for r in queued.all()]
    for run_id, thread_id, wf_id, wf_name, why in reasons:
        f = flags.setdefault(run_id, {"run_id": run_id, "thread_id": thread_id, "workflow_id": wf_id,
                                      "workflow_name": wf_name, "flags": 0, "why": []})  # fmt: skip
        f["flags"] += 1
        if why not in f["why"] and len(f["why"]) < 5:
            f["why"].append(why)
    flagged = sorted(flags.values(), key=lambda f: (-f["flags"], f["run_id"]))[:FLAGGED]

    return {
        "runs": total, "failed": n_failed, "failure_rate": round(n_failed / total, 4) if total else None,
        "cost_usd": round(float(cost or 0), 6), "tokens": int(tokens or 0),
        "latency_p50_ms": p50, "latency_p95_ms": p95,
        "failure_groups": failure_groups, "workflows": workflows, "flagged": flagged,
    }  # fmt: skip


# ------------------------------------------------------------------------------------------------ messages ---
@dataclass
class DigestMessage:
    """What a notification says about one digest: counts, spend, latency, how many runs each kind of failure hit and
    in which workflows, and the flagged runs' workflows; never the errors' text (they can hold what a run was given)
    nor what a run was asked or answered. The link opens the Runs page, where signed-in members read the rest."""

    digest_id: str
    org_id: str
    project_id: str
    project_name: str
    week_start: datetime
    summary: dict[str, Any]
    link: str

    @property
    def week(self) -> str:
        last = self.week_start + WEEK - timedelta(days=1)
        return f"{self.week_start:%d %b} to {last:%d %b %Y}"

    @property
    def subject(self) -> str:
        return " ".join(f"Weekly runs: {self.project_name}, {self.week}".split())[:250]

    def lines(self) -> list[str]:
        d = self.summary
        rate = f" ({round(d['failure_rate'] * 100)}%)" if d.get("failure_rate") is not None else ""
        lines = [
            f"{self.project_name}, {self.week} (UTC): {d['runs']} runs, {d['failed']} failed{rate}, "
            f"${d['cost_usd']:.2f} spent.",
        ]
        if d.get("latency_p95_ms") is not None:
            lines.append(f"They took {_took(d['latency_p50_ms'])} typically, {_took(d['latency_p95_ms'])} at the 95th "
                         "percentile.")  # fmt: skip
        groups = d.get("failure_groups") or []
        if groups:
            kinds = "; ".join(f"{g['runs']} runs in {', '.join(g['workflows'])}" for g in groups[:3])
            lines.append(f"Failures: {len(groups)} kind{'s' if len(groups) != 1 else ''} of error, most often {kinds}.")
        if flagged := d.get("flagged") or []:
            most = "; ".join(f"{f['workflow_name']} ({f['flags']})" for f in flagged[:3])
            lines.append(f"Flagged most: {most}.")
        return lines

    @property
    def text(self) -> str:
        return "\n".join([*self.lines(), f"Open the Runs page: {self.link}"]) + "\n"

    def payload(self, notice_id: str) -> dict[str, Any]:
        d = self.summary
        keep = ("runs", "failed", "failure_rate", "cost_usd", "tokens", "latency_p50_ms", "latency_p95_ms")
        return {
            "type": "digest.weekly", "id": notice_id, "digest_id": self.digest_id, "organisation_id": self.org_id,
            "project": {"id": self.project_id, "name": self.project_name},
            "week_start": self.week_start.isoformat(), "week_end": (self.week_start + WEEK).isoformat(),
            **{k: d.get(k) for k in keep},
            "failure_groups": [{"runs": g["runs"], "workflows": g["workflows"]} for g in d.get("failure_groups") or []],
            "flagged": [{k: f[k] for k in ("run_id", "thread_id", "workflow_id", "workflow_name", "flags")}
                        for f in d.get("flagged") or []],
            "workflows": d.get("workflows") or [], "url": self.link,
        }  # fmt: skip

    def slack(self) -> dict[str, Any]:
        text = f"*{notifier.slack_escape(self.subject)}*\n" + "\n".join(notifier.slack_escape(x) for x in self.lines())
        return {"text": notifier.slack_escape(self.subject), "blocks": [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"<{self.link}|Open the Runs page>"}},
        ]}  # fmt: skip


def _took(ms: int | None) -> str:
    if ms is None:
        return "?"
    return f"{ms} ms" if ms < 1000 else f"{ms / 1000:.1f} s" if ms < 60_000 else f"{round(ms / 60_000)} min"


# ----------------------------------------------------------------------------------------------------- job ---
class DigestJob:
    """Makes the digests (its own loop) and sends them (a round of the notifier's loop, `deliver`), through the
    channels and the delivery code of approval notifications (`notifier`)."""

    def __init__(
        self,
        db: Any,
        interval_s: float,
        secrets: Callable[[str], dict[str, str]] = lambda _: {},
        notifier: NotifierJob | None = None,
    ):
        self.db, self.interval_s, self.secrets, self.notifier = db, interval_s, secrets, notifier
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self.interval_s <= 0:
            return
        observability.job_started("run_digest")
        self._task = asyncio.create_task(self._loop(), name="run-digest")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                with observability.job_round("run_digest"):
                    await self.tick(datetime.now(UTC))
            except Exception:
                log.exception("run_digest.tick_failed")
            await asyncio.sleep(self.interval_s)

    async def tick(self, now: datetime) -> int:
        """Make the digests of the week that just ended for the projects that ran something in it and have none yet;
        returns how many this server made."""
        end = week_of(now)
        start = end - WEEK
        async with self.db.sessionmaker() as s:
            done = select(RunDigest.project_id).where(RunDigest.week_start == start)
            q = (
                select(WorkflowRow.project_id)
                .join(Thread, Thread.workflow_id == WorkflowRow.id)
                .join(Run, Run.thread_id == Thread.id)
                .where(Run.started_at >= start, Run.started_at < end, PEOPLES, WorkflowRow.project_id.not_in(done))
                .group_by(WorkflowRow.project_id)
                .order_by(WorkflowRow.project_id)
                .limit(BATCH)
            )
            projects = list((await s.scalars(q)).all())
            await s.execute(delete(RunDigest).where(RunDigest.week_start < start - KEEP))
            await s.commit()
        made = 0
        for project_id in projects:
            try:
                made += await self.make(project_id, start, now)
            except Exception:  # one project's failure must not stop the others
                log.exception("run_digest.failed", project_id=project_id, week=start.date().isoformat())
        return made

    async def make(self, project_id: str, start: datetime, now: datetime | None = None) -> bool:
        """Compute and insert one digest, with its notifications when the organisation opted in; False when another
        server inserted it first (its notifications too: they are queued in the same transaction)."""
        async with self.db.sessionmaker() as s:
            summary = await compute(s, project_id, start, start + WEEK, self.secrets(project_id))
        async with self.db.sessionmaker() as s:
            row = RunDigest(id=new_id("rdg"), project_id=project_id, week_start=start, summary=summary)
            s.add(row)
            try:
                await s.flush()
                queued = await self._queue(s, row, now or datetime.now(UTC))
                await s.commit()
            except IntegrityError:
                await s.rollback()
                return False  # another server made it
        log.info("run_digest.made", project_id=project_id, week=start.date().isoformat(), runs=summary["runs"],
                 notices=queued)  # fmt: skip
        return True

    async def _queue(self, s: Any, row: RunDigest, now: datetime) -> int:
        if self.notifier is None:
            return 0
        org_id = await s.scalar(select(Project.org_id).where(Project.id == row.project_id))
        config = await s.get(OrgNotifier, org_id) if org_id else None
        if config is None or not config.digest:
            return 0
        channels = notifier.channels_on(config, notifier.smtp_ready(self.notifier.settings))
        for channel in channels:
            s.add(DigestNotice(id=new_id("dgn"), org_id=org_id, digest_id=row.id, channel=channel, next_at=now))
        return len(channels)

    # ------------------------------------------------------------------------------------------- deliver ---
    async def deliver(self, now: datetime) -> int:
        """Send the digest notifications that are due, each claimed for a lease first (a round of the notifier's
        loop); returns how many were sent."""
        if self.notifier is None:
            return 0
        async with self.db.sessionmaker() as s:
            due = (DigestNotice.status == "pending", DigestNotice.next_at <= now)
            q = select(DigestNotice.id).where(*due).order_by(DigestNotice.next_at, DigestNotice.id).limit(BATCH)
            mine: list[str] = []
            for notice_id in list(await s.scalars(q)):
                res = await s.execute(
                    update(DigestNotice)
                    .where(DigestNotice.id == notice_id, *due)
                    .values(next_at=now + timedelta(seconds=notifier.LEASE_S), attempts=DigestNotice.attempts + 1)
                )
                if res.rowcount == 1:  # type: ignore[attr-defined]
                    mine.append(notice_id)
            await s.commit()
        sent = 0
        for notice_id in mine:  # a handful a week per organisation: one at a time
            sent += await self._send(notice_id, now)
        return sent

    async def _send(self, notice_id: str, now: datetime) -> bool:
        job = self.notifier
        assert job is not None
        async with self.db.sessionmaker() as s:
            row = await s.get(DigestNotice, notice_id)
            if row is None:
                return False
            channel, attempts = row.channel, row.attempts
            found = (
                await s.execute(
                    select(RunDigest, Project.name, Project.org_id)
                    .join(Project, Project.id == RunDigest.project_id)
                    .where(RunDigest.id == row.digest_id)
                )
            ).first()
            config = await s.get(OrgNotifier, row.org_id)
            channels = await notifier.load(s, job.keyring, row.org_id) if config is not None else None
            to = await self._recipients(s, row.org_id, channels) if channel == "email" else []
        outcome, error = "sent", None
        if found is None or config is None or not config.digest:
            outcome = "dropped"  # the digest went with its project, or the organisation opted out meanwhile
        else:
            digest, project_name, org_id = found
            message = DigestMessage(
                digest_id=digest.id, org_id=org_id, project_id=digest.project_id, project_name=project_name,
                week_start=digest.week_start, summary=digest.summary, link=f"{job.base_url}/runs",
            )  # fmt: skip
            try:
                await self._deliver(channel, message, notice_id, channels, to)
            except notifier.DeliveryError as exc:
                error = str(exc)
            except Exception as exc:  # a channel's failure must not stop the others
                error = f"{type(exc).__name__} (see the server log)"
                log.exception("run_digest.send_crashed", notice_id=notice_id, channel=channel)
        values: dict[str, Any]
        if error is None:
            values = {"status": outcome, "sent_at": now if outcome == "sent" else None, "last_error": None}
        elif attempts >= notifier.MAX_ATTEMPTS:
            outcome, values = "failed", {"status": "failed", "last_error": error}
            log.warning("run_digest.gave_up", notice_id=notice_id, channel=channel, attempts=attempts, error=error)
        else:
            outcome = "retry"
            delay = notifier.BACKOFF_S[min(attempts, len(notifier.BACKOFF_S)) - 1]
            values = {"next_at": now + timedelta(seconds=delay), "last_error": error}
            log.info("run_digest.retry", notice_id=notice_id, channel=channel, attempts=attempts, error=error)
        async with self.db.sessionmaker() as s:
            await s.execute(update(DigestNotice).where(DigestNotice.id == notice_id).values(**values))
            await s.commit()
        DIGESTS_SENT.labels(channel=channel, outcome=outcome).inc()
        return outcome == "sent"

    async def _deliver(
        self, channel: str, m: DigestMessage, notice_id: str, channels: notifier.Channels | None, to: list[str]
    ) -> None:
        job = self.notifier
        assert job is not None
        if channels is None or channel not in channels.enabled(notifier.smtp_ready(job.settings)):
            raise notifier.DeliveryError(f"the organisation no longer sends {channel} notifications")
        if channel == "email":
            if not to:
                raise notifier.DeliveryError("nobody to send it to: the organisation has no admins or builders")
            await asyncio.to_thread(job.mailer, job.settings, to, m.subject, m.text)
        elif channel == "slack":
            assert channels.slack_url
            body = json.dumps(m.slack()).encode()
            await notifier.post(channels.slack_url, body, {"Content-Type": "application/json"}, False, job.transport)
        else:
            assert channels.webhook_url and channels.webhook_secret
            body = json.dumps(m.payload(notice_id)).encode()
            stamp = str(int(time.time()))
            headers = {
                "Content-Type": "application/json", "User-Agent": "AgentCanvas-Notifier/1.0",
                "X-AgentCanvas-Event": "digest.weekly", "X-AgentCanvas-Delivery": notice_id,
                "X-AgentCanvas-Timestamp": stamp, "X-AgentCanvas-Signature": sign(channels.webhook_secret, body, stamp),
            }  # fmt: skip
            await notifier.post(channels.webhook_url, body, headers, job.settings.notify_allow_private, job.transport)

    async def _recipients(self, s: Any, org_id: str, channels: notifier.Channels | None) -> list[str]:
        """The organisation's team addresses when it set them, else every active admin and builder."""
        if channels is not None and channels.email_to:
            return channels.email_to[: notifier.MAX_RECIPIENTS]
        q = (
            select(AppUser.email)
            .join(Membership, Membership.user_id == AppUser.id)
            .where(
                Membership.org_id == org_id, Membership.accepted.is_(True), Membership.suspended.is_(False),
                Membership.role.in_(("admin", "builder")),
            )
        )  # fmt: skip
        return sorted({e for e in (await s.scalars(q)).all() if e})[: notifier.MAX_RECIPIENTS]
