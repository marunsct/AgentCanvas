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
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select

from agentcanvas import knowledge_search as ks
from agentcanvas.db.models import Project, Run, RunSearch, Thread, WorkflowRow
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
SNIPPET = 220
REINDEX_LIMIT = 2000  # runs one reindex fills in


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
            async with self.db.sessionmaker() as s:
                row = await s.get(WorkflowRow, h.workflow_id)
                if row is None:
                    return
                input = h.input
                if input is None:  # a resume continues the thread's last fresh run: it has the input
                    input = await s.scalar(
                        select(Run.recording["input"]).where(Run.thread_id == h.thread_id, Run.recording.is_not(None))
                        .order_by(Run.started_at.desc()).limit(1)
                    )  # fmt: skip
                await self.index_run(s, row.project_id, h.run_id, h.thread_id, h.workflow_id, input,
                                     final_output(h.events + h.held))  # fmt: skip
        except Exception:
            log.exception("run_search.index_failed", run_id=h.run_id)

    async def index_run(
        self, s: Any, project_id: str, run_id: str, thread_id: str, workflow_id: str, input: Any, output: Any
    ) -> bool:
        text = text_of(input, output, self.secrets(project_id))
        if not text:
            await s.execute(delete(RunSearch).where(RunSearch.run_id == run_id))
            await s.commit()
            return False
        model, spec = await self._model(s, project_id)
        [vector] = await self._embed(project_id, spec, [text])
        await s.execute(delete(RunSearch).where(RunSearch.run_id == run_id))
        s.add(
            RunSearch(
                id=new_id("rs"), run_id=run_id, thread_id=thread_id, workflow_id=workflow_id, project_id=project_id,
                text=text, vector=pack(vector), model=model,
            )
        )  # fmt: skip
        await s.commit()
        return True

    async def reindex(self, project_id: str, limit: int = REINDEX_LIMIT) -> dict[str, int]:
        """Index the project's newest runs that are not indexed (or were with another model), from their recording
        (runs from before the index) or kept events. Returns {indexed, skipped}."""
        indexed = skipped = 0
        async with self.db.sessionmaker() as s:
            model, _ = await self._model(s, project_id)
            done = select(RunSearch.run_id).where(RunSearch.project_id == project_id, RunSearch.model == model)
            q = (
                select(Run.id, Run.thread_id, Thread.workflow_id, Run.recording, Run.events)
                .join(Thread, Thread.id == Run.thread_id)
                .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
                .where(WorkflowRow.project_id == project_id, Run.status != "running", Run.id.not_in(done), PEOPLES)
                .order_by(Run.started_at.desc())
                .limit(limit)
            )
            rows = (await s.execute(q)).all()
            for run_id, thread_id, workflow_id, recording, events in rows:
                rec = recording or {}
                out = rec.get("output") or final_output(events)
                if await self.index_run(s, project_id, run_id, thread_id, workflow_id, rec.get("input"), out):
                    indexed += 1
                else:
                    skipped += 1
        return {"indexed": indexed, "skipped": skipped}

    # -------------------------------------------------------------- search ----
    async def search(
        self, project_id: str, query: str, *, workflow_id: str | None = None, limit: int = 20, mode: str = "hybrid"
    ) -> RunSearchResult:
        async with self.db.sessionmaker() as s:
            model, spec = await self._model(s, project_id)
            rows_q = select(RunSearch).where(RunSearch.project_id == project_id, RunSearch.model == model)
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
