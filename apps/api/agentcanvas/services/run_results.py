"""A deployment run's result, for the callers that read it later (E-2): the deployment API's GET, A2A `GetTask` and
MCP tasks.

The server that ran the run answers from its memory while it still holds the run; any other server (or the same one
once the run left its memory) answers from the run's row, which keeps the answer and the pauses whole
(`Run.output`, `Run.interrupts`). A run stopped by a server restart continues as a new run on the same conversation
(services/redeploy.py): its result is the one of the run that continues it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import InterruptTask, Run, Thread

Status = Literal["success", "interrupted", "error", "cancelled", "running"]
# a run that a server restart (or a stalled server) stopped: another server continues it, so it is still running
CONTINUED = ("redeploy", "superseded")


@dataclass
class RunResult:
    run_id: str
    thread_id: str
    status: Status
    output: dict[str, Any] | None = None
    interrupts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def from_handle(h: Any) -> RunResult:
    """The result of a run that ended on this server, from its events."""
    res = RunResult(run_id=h.run_id, thread_id=h.thread_id, status="error")
    for ev in h.events:
        if ev["type"] == "RUN_FINISHED":
            res.status, res.output = ev.get("status") or "success", ev.get("output")
            res.interrupts = ev.get("interrupts") or []
        elif ev["type"] == "RUN_ERROR":
            code = ev.get("code")
            res.status = "running" if code in CONTINUED else "cancelled" if code == "cancelled" else "error"
            res.error = None if code in CONTINUED else ev.get("message")
    return res


class ResultRepo:
    def __init__(self, session: AsyncSession, runner: Any):
        self.s, self.runner = session, runner

    async def of(self, run_id: str, deployment_id: str | None = None) -> RunResult | None:
        """The run's result; None when there is no such run (or, with `deployment_id`, it is not one of that
        deployment's: the same None, so a caller cannot tell another deployment's run from one that does not exist)."""
        q = select(Run.id, Run.thread_id, Run.status, Run.error, Run.handover, Run.queue_key, Run.started_at)
        if deployment_id is not None:
            q = q.join(Thread, Thread.id == Run.thread_id).where(Thread.deployment_id == deployment_id)
        row = (await self.s.execute(q.where(Run.id == run_id))).first()
        if row is None:
            return None
        if row.status == "interrupted" and row.error is not None:  # stopped by a restart, not paused for a person
            row = await self._continuation(row) or row
        h = self.runner.handles.get(row.id)
        if h is not None:
            if not h.done.is_set():
                return RunResult(run_id=row.id, thread_id=row.thread_id, status="running")
            res = from_handle(h)
            if res.status != "running":
                return res
        if row.status == "running" or (row.status == "interrupted" and row.error is not None):
            return RunResult(run_id=row.id, thread_id=row.thread_id, status="running")  # here or another server
        saved = (await self.s.execute(select(Run.output, Run.interrupts).where(Run.id == row.id))).first()
        output, interrupts = (saved.output, saved.interrupts) if saved else (None, None)
        if row.status == "interrupted" and interrupts is None:  # ended before results were kept: its open pauses
            asked = select(InterruptTask).where(InterruptTask.run_id == row.id, InterruptTask.status == "pending")
            interrupts = [{"id": t.id, "kind": t.kind, "payload": t.payload, "canvas_node_id": t.canvas_node_id}
                          for t in (await self.s.scalars(asked)).all()]  # fmt: skip
        return RunResult(
            run_id=row.id, thread_id=row.thread_id, status=row.status, output=output, interrupts=interrupts or [],
            error=row.error,
        )  # fmt: skip

    async def _continuation(self, row: Any) -> Any:
        """The newest run that continues `row` after a hand-over (they share its place in the run queue)."""
        q = select(Run.id, Run.thread_id, Run.status, Run.error, Run.handover, Run.queue_key, Run.started_at).where(
            Run.thread_id == row.thread_id, Run.queue_key == (row.queue_key or row.id), Run.id != row.id
        )
        return (await self.s.execute(q.order_by(Run.started_at.desc(), Run.id.desc()).limit(1))).first()
