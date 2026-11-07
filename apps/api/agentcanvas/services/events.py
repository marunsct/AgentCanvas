"""Events as code v0 (FR-X-22, doc 15 §6): deliver an event to the workspace handlers that subscribe to it.

Handlers are `@on("type", filter=<CEL>, dedup_key=<CEL>)` functions in `events/*.py`. They only *describe*
actions (start a workflow, resume a thread, emit an event); this service performs them afterwards — or not, in a
dry run — so handler code never holds platform credentials and can run in the script sandbox.
"""

from __future__ import annotations

import asyncio
import fnmatch
import inspect
import json
from dataclasses import dataclass, field
from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from agentcanvas.db.models import EventDedup, EventLog, WorkflowRow
from agentcanvas.errors import InvalidInput
from agentcanvas.expr import ExpressionError, eval_expression
from agentcanvas.ids import new_id
from agentcanvas.ir.models import Workflow
from agentcanvas.log import get_logger
from agentcanvas.runtime.types import ResumeSpec

log = get_logger(__name__)
MAX_DEPTH = 5  # emit -> handler -> emit (or a run it starts, which emits) … stops here
HANDLER_TIMEOUT_S = 30.0


@dataclass
class HandlerResult:
    handler: str
    matched: bool
    actions: list[dict[str, Any]] = field(default_factory=list)
    skipped: str | None = None  # "filter" | "duplicate"
    error: str | None = None
    runs: list[str] = field(default_factory=list)  # run ids started by its actions


@dataclass
class EventResult:
    event_id: str | None
    type: str
    status: str  # handled | ignored | error | dry_run
    handlers: list[HandlerResult]


class EventService:
    def __init__(self, db: Any, runner: Any, projects: Any):
        self.db = db
        self.runner = runner
        self.projects = projects

    # ------------------------------------------------------------------ schemas ----
    def schema(self, project_id: str, event_type: str) -> dict[str, Any] | None:
        path = self.projects.workspace(project_id).root / "events" / "schemas" / f"{event_type}.json"
        return json.loads(path.read_text()) if path.is_file() else None

    # ----------------------------------------------------------------- dispatch ----
    async def dispatch(
        self,
        project_id: str,
        event_type: str,
        data: dict[str, Any],
        *,
        source: str = "api",
        dry_run: bool = False,
        user_id: str | None = None,
        depth: int = 0,
        quiet: bool = False,
    ) -> EventResult:
        """Deliver an event. `quiet` (lifecycle events) keeps events no handler subscribes to out of the log."""
        schema = self.schema(project_id, event_type)
        if schema is not None:
            problems = sorted(Draft202012Validator(schema).iter_errors(data), key=str)
            if problems:
                raise InvalidInput(f"event '{event_type}' does not match its schema: {problems[0].message}")
        event_id = new_id("evt")
        event = {"id": event_id, "type": event_type, "data": data, "source": source}
        catalog = self.projects.catalog(project_id)
        results: list[HandlerResult] = []
        for h in catalog.handlers:
            if not fnmatch.fnmatchcase(event_type, h["type"]):
                continue
            results.append(await self._run_handler(project_id, h, event, dry_run))
        for r in results:
            if dry_run or not r.actions:
                continue
            for action in r.actions:
                try:
                    r.runs += await self._perform(project_id, action, user_id, depth)
                except Exception as exc:  # noqa: BLE001 - one failing action must not lose the others
                    r.error = f"{action.get('action')}: {exc}"
        if quiet and not results:
            return EventResult(event_id=None, type=event_type, status="ignored", handlers=[])
        matched = [r for r in results if r.matched and not r.skipped]
        status = (
            "dry_run" if dry_run else "error" if any(r.error for r in results) else "handled" if matched else "ignored"
        )
        if not dry_run:
            async with self.db.sessionmaker() as s:
                s.add(
                    EventLog(
                        id=event_id,
                        project_id=project_id,
                        type=event_type,
                        source=source,
                        data=data,
                        status=status,
                        results=[r.__dict__ for r in results],
                    )
                )
                await s.commit()
        return EventResult(event_id=None if dry_run else event_id, type=event_type, status=status, handlers=results)

    async def _run_handler(
        self, project_id: str, h: dict[str, Any], event: dict[str, Any], dry_run: bool
    ) -> HandlerResult:
        res = HandlerResult(handler=h["import_path"], matched=True)
        variables = {"event": event}
        claimed: str | None = None
        try:
            if h.get("filter") and not bool(eval_expression(h["filter"], variables)):
                return HandlerResult(handler=h["import_path"], matched=False, skipped="filter")
            if h.get("dedup_key") and not dry_run:
                value = str(eval_expression(h["dedup_key"], variables))
                if not await self._first_delivery(project_id, h["import_path"], value, event["id"]):
                    res.skipped = "duplicate"
                    return res
                claimed = value
            res.actions = await asyncio.wait_for(self._call(project_id, h["import_path"], event), HANDLER_TIMEOUT_S)
        except (ExpressionError, TimeoutError, Exception) as exc:  # noqa: BLE001 - report, never crash delivery
            res.error = f"{type(exc).__name__}: {exc}"
            if claimed is not None:  # it did not run to the end: the same event delivered again may (R-10)
                await self._release(project_id, h["import_path"], claimed)
        return res

    async def _call(self, project_id: str, import_path: str, event: dict[str, Any]) -> list[dict[str, Any]]:
        resources = self.projects.resources(project_id)
        if getattr(resources, "script_runner", None) is not None:  # sandboxed: the child records the actions
            return await resources.script_runner.handle(import_path, event)
        from agentcanvas.sdk import Actions, Event
        from agentcanvas.workspace import load_attr

        fn = load_attr(import_path, resources.workspace)
        act = Actions()
        out = fn(Event(type=event["type"], data=event["data"], id=event["id"], source=event["source"]), act)
        if inspect.isawaitable(out):
            await out
        return act.recorded

    async def _first_delivery(self, project_id: str, handler: str, value: str, event_id: str) -> bool:
        async with self.db.sessionmaker() as s:
            s.add(EventDedup(id=new_id("dd"), project_id=project_id, handler=handler, value=value, event_id=event_id))
            try:
                await s.commit()
                return True
            except IntegrityError:
                return False

    async def _release(self, project_id: str, handler: str, value: str) -> None:
        async with self.db.sessionmaker() as s:
            same = (EventDedup.project_id == project_id, EventDedup.handler == handler, EventDedup.value == value)
            await s.execute(delete(EventDedup).where(*same))
            await s.commit()

    # ------------------------------------------------------------------ actions ----
    async def _perform(self, project_id: str, action: dict[str, Any], user_id: str | None, depth: int) -> list[str]:
        kind = action.get("action")
        if kind in ("start_workflow", "resume") and depth >= MAX_DEPTH:  # its run's events would be one level deeper
            raise InvalidInput(f"events led to runs more than {MAX_DEPTH} levels deep")
        if kind == "start_workflow":
            wf = await self._workflow(project_id, str(action["workflow"]))
            context = {f.name: user_id for f in wf.context.fields if f.source == "user" and user_id}
            h = await self.runner.start(
                wf,
                thread_id=action.get("thread_id"),
                input=action.get("input") or {},
                user_id=user_id,
                context=context,
                origin="event",
                event_depth=depth + 1,
            )
            return [h.run_id]
        if kind == "resume":
            thread_id = str(action["thread_id"])
            from agentcanvas.db.models import Deployment, Thread, WorkflowVersion
            from agentcanvas.services.projects import PROJECT_OF
            from agentcanvas.services.redeploy import thread_version

            target: dict[str, Any] = {}
            async with self.db.sessionmaker() as s:
                t = await s.get(Thread, thread_id)
                row = await s.get(WorkflowRow, t.workflow_id) if t is not None else None
                # a handler acts only inside its own project: another project's thread is as good as missing
                if t is None or row is None or row.project_id != project_id:
                    raise InvalidInput(f"thread '{thread_id}' not found")
                ir = row.draft_ir
                if t.deployment_id:  # a published conversation continues on its deployment's version (M6-T4)
                    dep = await s.get(Deployment, t.deployment_id)
                    ver = await s.get(WorkflowVersion, dep.version_id) if dep else None
                    if dep is None or ver is None:
                        raise InvalidInput(f"the deployment of thread '{thread_id}' no longer exists")
                    ver = await thread_version(s, thread_id, ver)
                    ir, target = ver.ir, {"deployment_id": dep.id, "version_id": ver.id}
            PROJECT_OF[row.id] = project_id  # the run's lifecycle events reach this project's handlers
            h = await self.runner.start(
                Workflow.model_validate(ir), thread_id=thread_id, resume=ResumeSpec(action.get("value")),
                user_id=user_id, origin="event", event_depth=depth + 1, **target,
            )  # fmt: skip
            return [h.run_id]
        if kind == "emit":
            if depth >= MAX_DEPTH:
                raise InvalidInput(f"events emitted more than {MAX_DEPTH} levels deep")
            await self.dispatch(
                project_id,
                str(action["type"]),
                action.get("data") or {},
                source="emit",
                user_id=user_id,
                depth=depth + 1,
            )
            return []
        raise InvalidInput(f"unknown action '{kind}'")

    async def _workflow(self, project_id: str, ref: str) -> Workflow:
        async with self.db.sessionmaker() as s:
            q = select(WorkflowRow).where(
                WorkflowRow.project_id == project_id, or_(WorkflowRow.id == ref, WorkflowRow.name == ref)
            )
            rows = list((await s.scalars(q)).all())
        if len(rows) != 1:
            raise InvalidInput(f"{'no' if not rows else 'more than one'} workflow named '{ref}' in this project")
        from agentcanvas.services.projects import PROJECT_OF

        PROJECT_OF[rows[0].id] = project_id
        return Workflow.model_validate(rows[0].draft_ir)

    # -------------------------------------------------------------- lifecycle ----
    async def on_run_finished(self, h: Any, status: str, error: str | None, interrupts: list[dict[str, Any]]) -> None:
        """run.completed / run.failed / interrupt.raised, and events emitted inside the run (sdk.emit)."""
        from agentcanvas.services.projects import PROJECT_OF

        if h.origin == "test":
            return  # saved test cases run without side effects on the project's handlers
        project_id = PROJECT_OF.get(h.workflow_id)
        if project_id is None or not self.projects.has_handlers(project_id):
            return  # cheap: no workspace (or no events/ code) -> nothing can subscribe
        base = {
            "workflow_id": h.workflow_id,
            "workflow_name": h.workflow_name,
            "thread_id": h.thread_id,
            "run_id": h.run_id,
        }
        depth = getattr(h, "event_depth", 0)  # a run a handler started: its events count as deeper (RT-6)
        for e in h.emitted:  # domain events are delivered whoever started the run
            await self.dispatch(
                project_id, str(e["ac_event"]), dict(e.get("data") or {}), source="run", quiet=True, depth=depth
            )
        if h.origin == "event":
            return  # a handler reacting to "its own" run would loop forever
        lifecycle = {"success": "run.completed", "error": "run.failed", "cancelled": "run.cancelled"}.get(status)
        if status == "interrupted":
            for intr in interrupts:
                data = {
                    **base,
                    "interrupt_id": intr.get("id"),
                    "kind": intr.get("kind"),
                    "node_id": intr.get("canvas_node_id"),
                }
                await self.dispatch(project_id, "interrupt.raised", data, source="lifecycle", quiet=True)
        elif lifecycle:
            await self.dispatch(
                project_id, lifecycle, {**base, "status": status, "error": error}, source="lifecycle", quiet=True
            )
