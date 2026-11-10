"""Semantic search over runs (G-10): "find conversations where …".

After a run finishes its input and answer are embedded, with the project's embedding model (Settings > Models,
purpose `embedding`; the built-in offline embedder when none is chosen), and kept in `run_search`: one row per run.
Indexing is incremental (one run at a time, in the background, never slowing the run) and `reindex` fills in runs
from before and rows made with another embedding model. A search embeds the question with the project's current
model and ranks the project's rows with the same model by meaning, keywords or both (`knowledge_search`, shared with
Search knowledge), best run per conversation.

A row lives and dies with its run: the retention job deletes conversations and `delete_thread_rows` their rows, and
the table has the tenant policy (through its workflow), so another organisation never sees it. The text is masked
with the project's secrets before it is embedded or kept.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Select, delete, func, or_, select

from agentcanvas import knowledge_search as ks
from agentcanvas.db.models import Project, Run, RunSearch, Thread, WorkflowRow
from agentcanvas.errors import InvalidInput
from agentcanvas.ids import new_id
from agentcanvas.log import get_logger
from agentcanvas.services import model_defaults
from agentcanvas.services.knowledge import EmbedderFactory, builtin_only, pack, unpack
from agentcanvas.services.secrets import hide_secrets

log = get_logger(__name__)

MAX_TEXT = 4000  # characters kept (and embedded) per run
CANDIDATES = 5000  # newest rows of a project a search ranks
PEOPLES = or_(Run.origin.is_(None), Run.origin != "test")  # experiment and test runs are not conversations
BATCH = 64
INDEXING = 4  # finished runs indexed at once per process; the rest wait their turn
SNIPPET = 220
REINDEX_LIMIT = 2000  # runs one reindex fills in
FIND_LIMIT = 50  # runs one structured search returns
ERROR_CHARS = 300


class RunHit(BaseModel):
    thread_id: str
    run_id: str
    workflow_id: str
    workflow_name: str | None = None
    score: float = Field(description="0-1, best first")
    snippet: str
    started_at: datetime | None = None


class RunSearchResult(BaseModel):
    hits: list[RunHit]
    model: str = Field(description="the embedding model that ranked them")
    indexed: int = Field(description="runs of the project this search could rank")
    stale: int = Field(description="runs indexed with another model or not yet: reindex to include them")


RunStatus = Literal["success", "error", "interrupted", "cancelled", "running"]
SPAN = re.compile(r"^\s*(\d{1,4})\s*(h|d|w)\s*$", re.I)


def when(value: str | datetime | date | None, now: datetime) -> datetime | None:
    """A moment from an ISO date or time (UTC when it names no zone) or a span back from `now` ("24h", "7d", "4w")."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if m := SPAN.match(value):
        hours = int(m.group(1)) * {"h": 1, "d": 24, "w": 168}[m.group(2).lower()]
        return now - timedelta(hours=hours)
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise InvalidInput(f"'{value}' is not a date: use an ISO date or time (2026-11-03, 2026-11-03T09:00) or a span "
                           "back from now (24h, 7d, 4w)") from None  # fmt: skip
    return t if t.tzinfo else t.replace(tzinfo=UTC)


class RunFilters(BaseModel):
    """What a structured run search keeps: every filter given must hold. `text` ranks what is left by meaning."""

    status: RunStatus | None = None
    since: datetime | None = Field(default=None, description="runs that started at or after this moment")
    until: datetime | None = Field(default=None, description="runs that started before this moment")
    min_cost_usd: float | None = Field(default=None, ge=0)
    max_cost_usd: float | None = Field(default=None, ge=0)
    workflow: str | None = Field(default=None, max_length=200, description="a workflow's id or name, in the project")
    label: str | None = Field(default=None, max_length=200, description="key=value, or key alone: has that label")
    text: str | None = Field(default=None, max_length=500, description="what the conversation was about")
    limit: int = Field(default=20, ge=1, le=FIND_LIMIT)

    @field_validator("workflow", "label", "text")
    @classmethod
    def _blank(cls, v: str | None) -> str | None:
        return v.strip() or None if isinstance(v, str) else v


class FoundRun(BaseModel):
    run_id: str
    thread_id: str
    workflow_id: str
    workflow_name: str | None = None
    status: str
    error: str | None = None
    cost_usd: float
    tokens: int
    started_at: datetime | None = None
    duration_ms: int | None = None
    labels: dict[str, Any] = Field(default_factory=dict)
    score: float | None = Field(None, description="with `text`: how well it matches, 0-1")
    snippet: str | None = Field(None, description="with `text`: the start of what was asked and answered")


class RunFindResult(BaseModel):
    runs: list[FoundRun]
    matched: int = Field(description="runs that pass the filters (before `text` ranks them and `limit` cuts)")
    cost_usd: float = Field(description="what the runs that pass the filters cost together")
    stale: int = Field(0, description="with `text`: runs not in the search index yet")


def strings(value: Any, out: list[str] | None = None) -> list[str]:
    """The readable text in a run's input or output: its strings, chat messages by their content, in order."""
    out = [] if out is None else out
    if isinstance(value, str):
        if value.strip():
            out.append(value.strip())
    elif isinstance(value, dict):
        if "content" in value and ("type" in value or "role" in value):  # a chat message
            strings(value["content"], out)
        else:
            for k, v in value.items():
                if k not in ("type", "role", "id"):  # message and content-block labels are not what was said
                    strings(v, out)
    elif isinstance(value, list):
        for v in value:
            strings(v, out)
    elif isinstance(value, bool | int | float):
        pass
    return out


def text_of(input: Any, output: Any, secrets: dict[str, str] | None = None) -> str:
    """What a run is indexed by: "User: <input>\\nAnswer: <output>", cut to MAX_TEXT, secrets masked."""
    asked, answered = " ".join(strings(input)), " ".join(strings(output))
    text = "\n".join(p for p in (f"User: {asked}" if asked else "", f"Answer: {answered}" if answered else "") if p)
    return hide_secrets(text, secrets or {})[:MAX_TEXT]


def final_output(events: list[dict[str, Any]] | None) -> dict[str, Any]:
    for ev in reversed(events or []):
        if ev.get("type") == "RUN_FINISHED":
            return ev.get("output") or {}
    return {}


class RunSearchService:
    def __init__(
        self,
        db: Any,
        embedder: EmbedderFactory = builtin_only,
        secrets: Callable[[str], dict[str, str]] = lambda _: {},
    ):
        self.db, self.embedder, self.secrets = db, embedder, secrets
        self.tasks: set[asyncio.Task] = set()
        self._indexing = asyncio.Semaphore(INDEXING)

    # --------------------------------------------------------------- model ----
    async def _model(self, s: Any, project_id: str) -> tuple[str, dict[str, Any]]:
        """(model name, the embedding spec the embedder factory takes) of a project."""
        project = await s.get(Project, project_id)
        name = model_defaults.resolve(project.settings if project else None, "embedding").model or ks.BUILTIN
        return name, {"model": name}

    async def _embed(self, project_id: str, spec: dict[str, Any], texts: list[str]) -> list[list[float]]:
        embed = self.embedder(spec, self.secrets(project_id))
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            out.extend(await embed(texts[i : i + BATCH]))
        return out

    # ------------------------------------------------------------ indexing ----
    async def on_run_finished(self, h: Any, status: str, error: str | None, _interrupts: list[dict[str, Any]]) -> None:
        """Runner finish hook: index the run in the background and return at once."""
        if h.drained or h.superseded or h.origin == "test":
            return  # a hand-over is indexed when its continuation finishes; experiment runs are not conversations
        task = asyncio.create_task(self._index(h), name=f"run-search:{h.run_id}")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def drain(self, timeout: float = 10.0) -> None:
        if self.tasks:
            await asyncio.wait(list(self.tasks), timeout=timeout)

    async def _index(self, h: Any) -> None:
        try:
            async with self._indexing:  # a burst of finished runs indexes a few at a time
                async with self.db.sessionmaker() as s:  # what to index, read in a short session
                    project_id = await s.scalar(select(WorkflowRow.project_id).where(WorkflowRow.id == h.workflow_id))
                    if project_id is None:
                        return
                    input = h.input
                    if input is None:  # a resume continues the thread's last fresh run: it has the input
                        input = await s.scalar(
                            select(Run.recording["input"])
                            .where(Run.thread_id == h.thread_id, Run.id != h.run_id, Run.recording.is_not(None))
                            .order_by(Run.started_at.desc()).limit(1)
                        )  # fmt: skip
                    model, spec = await self._model(s, project_id)
                output = final_output(h.events + h.held)
                await self.index_runs(project_id, model, spec, [(h.run_id, h.thread_id, h.workflow_id, input, output)])
        except Exception:
            log.exception("run_search.index_failed", run_id=h.run_id)

    async def index_runs(
        self, project_id: str, model: str, spec: dict[str, Any], runs: list[tuple[str, str, str, Any, Any]]
    ) -> int:
        """Index runs given as (run id, thread id, workflow id, input, output); returns how many had text. Embedded
        with no database connection held (a hosted model takes a while), then written in one short transaction."""
        secrets = self.secrets(project_id)
        texts = {run_id: text_of(input, output, secrets) for run_id, _, _, input, output in runs}
        kept = [r for r in runs if texts[r[0]]]
        vectors = await self._embed(project_id, spec, [texts[r[0]] for r in kept]) if kept else []
        async with self.db.sessionmaker() as s:
            await s.execute(delete(RunSearch).where(RunSearch.run_id.in_(list(texts))))
            for (run_id, thread_id, workflow_id, _, _), vector in zip(kept, vectors, strict=True):
                s.add(
                    RunSearch(
                        id=new_id("rs"), run_id=run_id, thread_id=thread_id, workflow_id=workflow_id,
                        project_id=project_id, text=texts[run_id], vector=pack(vector), model=model,
                    )
                )  # fmt: skip
            await s.commit()
        return len(kept)

    async def reindex(self, project_id: str, limit: int = REINDEX_LIMIT) -> dict[str, int]:
        """Index the project's newest runs that are not indexed (or were with another model), from their recording
        (runs from before the index) or kept events. Returns {indexed, skipped}."""
        async with self.db.sessionmaker() as s:
            model, spec = await self._model(s, project_id)
            done = select(RunSearch.run_id).where(RunSearch.project_id == project_id, RunSearch.model == model)
            q = (
                select(Run.id)
                .join(Thread, Thread.id == Run.thread_id)
                .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
                .where(WorkflowRow.project_id == project_id, Run.status != "running", Run.id.not_in(done), PEOPLES)
                .order_by(Run.started_at.desc())
                .limit(limit)
            )
            ids = list((await s.scalars(q)).all())
        indexed = 0
        for i in range(0, len(ids), BATCH):  # a batch read, embedded with no session open, then written
            async with self.db.sessionmaker() as s:
                rows = (
                    await s.execute(
                        select(Run.id, Run.thread_id, Thread.workflow_id, Run.recording, Run.events)
                        .join(Thread, Thread.id == Run.thread_id)
                        .where(Run.id.in_(ids[i : i + BATCH]))
                    )
                ).all()
            runs = []
            for run_id, thread_id, workflow_id, recording, events in rows:
                rec = recording or {}
                runs.append(
                    (run_id, thread_id, workflow_id, rec.get("input"), rec.get("output") or final_output(events))
                )
            indexed += await self.index_runs(project_id, model, spec, runs)
        return {"indexed": indexed, "skipped": len(ids) - indexed}

    # -------------------------------------------------------------- search ----
    async def search(
        self,
        project_id: str,
        query: str,
        *,
        workflow_id: str | None = None,
        limit: int = 20,
        mode: str = "hybrid",
        within: Select[Any] | None = None,
    ) -> RunSearchResult:
        """`within`: a query of run ids; only those runs are ranked (a structured search's filters)."""
        async with self.db.sessionmaker() as s:
            model, spec = await self._model(s, project_id)
            rows_q = select(RunSearch).where(RunSearch.project_id == project_id, RunSearch.model == model)
            if within is not None:
                rows_q = rows_q.where(RunSearch.run_id.in_(within))
            total_q = (
                select(func.count())
                .select_from(Run)
                .join(Thread, Thread.id == Run.thread_id)
                .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
                .where(WorkflowRow.project_id == project_id, Run.status != "running", PEOPLES)
            )
            if workflow_id:
                rows_q = rows_q.where(RunSearch.workflow_id == workflow_id)
                total_q = total_q.where(WorkflowRow.id == workflow_id)
            rows = list((await s.scalars(rows_q.order_by(RunSearch.created_at.desc()).limit(CANDIDATES))).all())
            total = int(await s.scalar(total_q) or 0)
            # every row with this model, not only the CANDIDATES ranked: else a big project is "stale" for good
            in_index = int(await s.scalar(rows_q.with_only_columns(func.count()).order_by(None)) or 0)
            names: dict[str, str] = {}
            started: dict[str, Any] = {}
            if rows:  # the names of the workflows found only, not every workflow of the installation
                found_wfs = select(WorkflowRow.id, WorkflowRow.name).where(
                    WorkflowRow.id.in_({r.workflow_id for r in rows})
                )
                names = dict((await s.execute(found_wfs)).tuples().all())
                found = await s.execute(select(Run.id, Run.started_at).where(Run.id.in_([r.run_id for r in rows])))
                started = dict(found.all())
        chunks = [
            {"id": r.run_id, "doc_id": r.thread_id, "title": r.workflow_id, "text": r.text, "vector": unpack(r.vector)}
            for r in rows
        ]
        qv = (await self._embed(project_id, spec, [query]))[0] if query.strip() and rows else None
        index = await asyncio.to_thread(ks.Index, chunks)
        seen: set[str] = set()
        hits = []
        for f in ks.search(index, query, mode=mode, top_k=len(chunks), query_vector=qv):
            if f["doc_id"] in seen:  # the best run of each conversation
                continue
            seen.add(f["doc_id"])
            hits.append(
                RunHit(
                    thread_id=f["doc_id"], run_id=f["id"], workflow_id=f["title"], workflow_name=names.get(f["title"]),
                    score=f["score"], snippet=f["text"][:SNIPPET], started_at=started.get(f["id"]),
                )
            )  # fmt: skip
            if len(hits) >= limit:
                break
        return RunSearchResult(hits=hits, model=model, indexed=len(rows), stale=max(0, total - in_index))

    # ---------------------------------------------------------- structured ----
    async def find(self, project_id: str, f: RunFilters) -> RunFindResult:
        """The project's runs that pass every filter, newest first; with `text`, ranked by meaning among them (the
        best run of each conversation). Test and experiment runs are left out, as in `search`."""
        where = [WorkflowRow.project_id == project_id, PEOPLES]
        async with self.db.sessionmaker() as s:
            if f.workflow:
                wf_id = await s.scalar(
                    select(WorkflowRow.id)
                    .where(WorkflowRow.project_id == project_id)
                    .where(or_(WorkflowRow.id == f.workflow, func.lower(WorkflowRow.name) == f.workflow.lower()))
                    .order_by(WorkflowRow.id)
                    .limit(1)
                )
                if wf_id is None:
                    raise InvalidInput(f"no workflow '{f.workflow}' in this project")
                where.append(WorkflowRow.id == wf_id)
            if f.status:
                where.append(Run.status == f.status)
            if f.since:
                where.append(Run.started_at >= f.since)
            if f.until:
                where.append(Run.started_at < f.until)
            if f.min_cost_usd is not None:
                where.append(Run.cost_usd >= f.min_cost_usd)
            if f.max_cost_usd is not None:
                where.append(Run.cost_usd <= f.max_cost_usd)
            if f.label:
                key, eq, value = f.label.partition("=")
                found = Run.labels[key.strip()].as_string()
                where.append(found == value.strip() if eq else found.is_not(None))
            base = select(Run.id).join(Thread, Thread.id == Run.thread_id)
            base = base.join(WorkflowRow, WorkflowRow.id == Thread.workflow_id).where(*where)
            totals = base.with_only_columns(func.count(), func.coalesce(func.sum(Run.cost_usd), 0))
            matched, cost = (await s.execute(totals)).one()
            ranked: dict[str, tuple[float, str]] = {}
            ids: list[str] = []
            stale = 0
            if not f.text:
                ids = list((await s.scalars(base.order_by(Run.started_at.desc(), Run.id.desc()).limit(f.limit))).all())
        if f.text:
            found_runs = await self.search(project_id, f.text, limit=f.limit, within=base)
            ranked = {h.run_id: (h.score, h.snippet) for h in found_runs.hits}
            ids, stale = list(ranked), found_runs.stale
        secrets = self.secrets(project_id)
        async with self.db.sessionmaker() as s:
            q = (
                select(Run, Thread.workflow_id, WorkflowRow.name)
                .join(Thread, Thread.id == Run.thread_id)
                .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
                .where(Run.id.in_(ids), WorkflowRow.project_id == project_id)
            )
            rows = {r.id: (r, wf_id, name) for r, wf_id, name in (await s.execute(q)).all()}
        runs = []
        for run_id in ids:
            if run_id not in rows:  # deleted meanwhile
                continue
            r, wf_id, name = rows[run_id]
            took = int((r.ended_at - r.started_at).total_seconds() * 1000) if r.ended_at and r.started_at else None
            score, snippet = ranked.get(run_id, (None, None))
            runs.append(
                FoundRun(
                    run_id=r.id, thread_id=r.thread_id, workflow_id=wf_id, workflow_name=name, status=r.status,
                    error=hide_secrets(r.error[:ERROR_CHARS], secrets) if r.error else None,
                    cost_usd=float(r.cost_usd or 0), tokens=int(r.tokens_in or 0) + int(r.tokens_out or 0),
                    started_at=r.started_at, duration_ms=took, labels=dict(r.labels or {}), score=score,
                    snippet=snippet,
                )
            )  # fmt: skip
        return RunFindResult(runs=runs, matched=int(matched or 0), cost_usd=round(float(cost or 0), 6), stale=stale)
