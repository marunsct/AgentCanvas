# 05 — Backend Guide

The backend is one Python service (`apps/api`) for the whole MVP. It contains the control plane (workflows, versions, users) **and** the runner (executes LangGraph graphs). Splitting into services is a post-MVP scaling step (spec doc02 §4); keep module boundaries clean so that split is easy.

## 1. Module map and dependency rules

```
api/  (HTTP only: parse, auth, call services, format responses)
 └── services/  (use-cases: save workflow, start run, decide interrupt, publish)
       ├── db/        (SQLAlchemy models + repositories; no business logic)
       ├── ir/        (models, commands, validator — pure, no I/O, no LangChain)
       ├── adapters/langchain/  (node library, interpreter, codegen, event translator)
       ├── runtime/   (runner: run registry, streaming loop, resources, persistence bindings)
       ├── sandbox/   (script execution)
       └── security/  (auth, roles, secrets)
```
Rules: `api` never touches SQLAlchemy directly; `ir` imports nothing from other packages; only `adapters/` imports LangChain/LangGraph (checked by import-linter from M3-T1).

## 2. Settings (`agentcanvas/settings.py`)

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    env: str = "dev"                                   # AGENTCANVAS_ENV
    database_url: str | None = None                    # DATABASE_URL
    redis_url: str | None = None
    secret_key: str = "dev-only-change-me"             # AGENTCANVAS_SECRET_KEY (Fernet key derivation)
    script_execution: str = "inprocess"                # inprocess | docker
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    recursion_limit: int = 50
    cors_origins: list[str] = ["http://localhost:5173"]

settings = Settings()
```
Install: `uv add pydantic-settings`.

## 3. Database schema (MVP)

The authoritative DDL is [`reference/infra/schema.sql`](./reference/infra/schema.sql) (verified on Postgres 16: 14 tables). Implement it as SQLAlchemy models and generate the Alembic migration from them (M1-T1); then compare the generated migration to `schema.sql`.

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `organization`, `app_user`, `membership`, `project` | tenancy & access | `membership.role ∈ admin/builder/reviewer/viewer` |
| `workflow` | editable draft | `draft_ir jsonb`, `layout jsonb`, `version int` (optimistic lock), `ir_hash` |
| `workflow_version` | immutable snapshot | `semver`, `ir`, `layout`, `workspace_commit` |
| `deployment` | published version + API key | `version_id`, `api_key_hash` |
| `thread` | conversation / run history owner | `workflow_id`, `status` (id = LangGraph `thread_id`) |
| `run` | one execution | `status`, `ir_hash`, tokens, cost, `trace_url` |
| `interrupt_task` | Inbox item | `kind`, `payload`, `status`, `decision` |
| `secret` | encrypted secret values | `ciphertext` only |
| `connection` | model providers, MCP servers… | `config` (non-secret) |
| `trigger` | webhook / cron | `config` |
| `audit_log` | append-only log | `action`, `target`, `detail` |

LangGraph creates its own tables when you call `await saver.setup()` / `await store.setup()`: `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations`, `store`, `store_migrations`. Do not edit them by hand.

**SQLAlchemy model example:**
```python
from datetime import datetime
from sqlalchemy import ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase): ...

class WorkflowRow(Base):
    __tablename__ = "workflow"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    name: Mapped[str]
    draft_ir: Mapped[dict] = mapped_column(JSONB)
    layout: Mapped[dict] = mapped_column(JSONB, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    ir_hash: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
```

**Optimistic locking in the repository:**
```python
async def save_ir(session, wf_id: str, ir: dict, expected_version: int) -> int:
    result = await session.execute(
        update(WorkflowRow)
        .where(WorkflowRow.id == wf_id, WorkflowRow.version == expected_version)
        .values(draft_ir=ir, ir_hash=ir_hash(ir), version=WorkflowRow.version + 1)
        .returning(WorkflowRow.version))
    new_version = result.scalar_one_or_none()
    if new_version is None:
        raise Conflict("workflow was modified by someone else")   # -> HTTP 409
    return new_version
```

**Canonical hash:** `ir_hash = sha256(json.dumps(ir, sort_keys=True, separators=(",", ":")).encode()).hexdigest()`.

## 4. API contract (v1, MVP)

All paths are prefixed with `/v1`. Auth: `Authorization: Bearer <OIDC JWT>` (M7; dev mode injects a dev user). Deployments use API keys.

### Workflows
| Method & path | Body → Response |
|---------------|-----------------|
| `POST /projects/{project_id}/workflows` | `{"name": "Support triage", "template": "support_triage"?}` → `201 {"id","version","ir","layout","diagnostics"}` |
| `GET /workflows/{id}` | → `{"id","name","version","ir","layout","diagnostics"}` (header `ETag: "<version>"`) |
| `PUT /workflows/{id}` + `If-Match` | `{"ir": {...}}` → `{"version","diagnostics"}` · `409` on version mismatch |
| `POST /workflows/{id}/commands` + `If-Match` | `{"commands": [{"op": "AddNode", "node": {...}}, {"op": "Connect", "edge": {...}}]}` → `{"version","ir","diagnostics"}` |
| `PUT /workflows/{id}/layout` | `{"positions": {"n_cls": {"x": 120, "y": 40}}}` → `204` |
| `GET /workflows/{id}/code` | → `text/x-python` · `422` problem if invalid |
| `POST /workflows/{id}/versions` | `{"message": "first release"}` → `{"id","semver"}` |
| `GET /workflows/{id}/versions/{a}/diff/{b}` | → `{"nodes": {"added": [...], "removed": [...], "changed": [{"id", "fields": {...}}]}, "edges": {...}}` |
| `GET /node-types` | → `[manifest, ...]` |
| `POST /expressions/check` | `{"kind": "cel", "source": "state.x > 1"}` → `{"ok": true}` or `{"ok": false, "error": "..."}` |

### Runs & threads
| Method & path | Body → Response |
|---------------|-----------------|
| `POST /workflows/{id}/runs/stream` | `{"input": {...}, "thread_id"?: "th_…", "mode": "normal"|"debug", "breakpoints"?: {"before": ["name"], "after": []}}` → SSE (§5) |
| `POST /threads/{id}/resume` | `{"interrupt_id": "…", "value": <decision>}` → SSE |
| `POST /runs/{id}/cancel` | → `202` |
| `GET /threads?workflow_id=&status=&cursor=` | → `{"items": [...], "next_cursor"}` |
| `GET /threads/{id}` | → `{"values", "next", "interrupts": [{"id","payload"}], "checkpoint_id"}` |
| `GET /threads/{id}/history` | → `[{"checkpoint_id","step","next","created_at"}]` |
| `POST /threads/{id}/state` | `{"values": {...}, "checkpoint_id"?: "…"}` → `{"checkpoint_id"}` |
| `POST /threads/{id}/fork` | `{"checkpoint_id": "…", "values"?: {...}}` → SSE of the continued branch |
| `DELETE /threads/{id}` | → `204` (also deletes checkpoints) |

### Inbox, secrets, triggers, deployments
| Method & path | Notes |
|---------------|-------|
| `GET /inbox?status=pending` | pending `interrupt_task` rows the caller may decide |
| `POST /inbox/{id}/decision` | `{"value": ...}` → resumes; SSE optional via `?stream=true` |
| `GET/POST/DELETE /projects/{id}/secrets` | POST `{"name","value"}`; GET returns names only |
| `CRUD /workflows/{id}/triggers` | webhook/cron config |
| `POST /hooks/{trigger_id}` | public; HMAC header `X-AgentCanvas-Signature: sha256=<hex>` |
| `POST /workflows/{id}/deployments` | `{"version_id"}` → `{"id","api_key"}` (key shown once) |
| `POST /deployments/{id}/runs/stream` | API-key auth; same body as draft runs |

## 5. The runner (`runtime/runner.py`)

Responsibilities: build the graph for a thread, stream canonical events, record run status, register interrupts, support cancel.

> This is the **target design** for tickets M1-T6, M4-T4, M4-T8, M4-T10 (repository and adapter calls are placeholders). The tested, runnable starting point is `reference/backend/agentcanvas/api/app.py` (`_stream`).

```python
class RunRegistry:
    """In-process registry of active runs (per API instance)."""
    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task] = {}

async def stream_run(ctx: RunContext) -> AsyncIterator[str]:
    run = await runs_repo.create(ctx.thread_id, ctx.ir_hash, mode=ctx.mode)
    graph = await adapter.build(ctx.workflow, ctx.resources, checkpointer=ctx.checkpointer, store=ctx.store)
    config = {"configurable": {"thread_id": ctx.thread_id}, "recursion_limit": settings.recursion_limit,
              "metadata": {"workflow_id": ctx.workflow.id, "run_id": run.id}}
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def pump():
        try:
            async for part in graph.astream(ctx.graph_input, config,
                                            stream_mode=["tasks", "updates", "messages", "custom"],
                                            subgraphs=True, version="v2",
                                            interrupt_before=ctx.breakpoints_before or None,
                                            interrupt_after=ctx.breakpoints_after or None):
                for ev in adapter.translate(part, ctx.names):
                    await queue.put(ev)
            snap = await graph.aget_state(config)
            await record_interrupts(run, snap)                 # -> interrupt_task rows (Inbox)
            status = "interrupted" if snap.interrupts or snap.next else "success"
            await runs_repo.finish(run.id, status)
            await queue.put({"type": "RUN_FINISHED", "runId": run.id, "outcome": status})
        except asyncio.CancelledError:
            await runs_repo.finish(run.id, "cancelled")
            await queue.put({"type": "RUN_ERROR", "message": "cancelled"})
        except Exception as exc:  # noqa: BLE001
            await runs_repo.finish(run.id, "error", error=str(exc))
            await queue.put({"type": "RUN_ERROR", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            await queue.put(None)

    registry.tasks[run.id] = asyncio.create_task(pump())
    yield sse({"type": "RUN_STARTED", "runId": run.id, "threadId": ctx.thread_id})
    while (ev := await queue.get()) is not None:
        yield sse(ev)
    registry.tasks.pop(run.id, None)
```
Why a queue + background task: the run keeps going (and can be cancelled or observed) even if the browser disconnects. Add `GET /runs/{id}/events` later to re-attach (post-MVP: buffer events in Redis Streams).

**Note:** with breakpoints the graph stops with `snap.next` non-empty but no `interrupts` — treat that as `interrupted` too (verified).

## 6. Resources, secrets and models

- `Resources(thread_id)` resolves `model.chat` resources via `init_chat_model(model, **params)`; `api_key` params come from secret refs: config `{"api_key": {"$secret": "OPENAI_API_KEY"}}` → decrypt from `secret` table → pass as `api_key=` (never set process-wide env vars per user).
- Cache resolved `Resources` per `(thread_id, ir_hash)` for the life of a run; real models are stateless, fakes are not (FAQ P-03).
- Secrets encryption (M5): `cryptography.fernet.Fernet(base64.urlsafe_b64encode(sha256(settings.secret_key).digest()))`; M8 replaces this with cloud KMS envelope encryption.

## 7. Persistence wiring (MVP)

```python
@asynccontextmanager
async def lifespan(app):
    async with AsyncPostgresSaver.from_conn_string(settings.database_url) as saver, \
               AsyncPostgresStore.from_conn_string(settings.database_url) as store:
        await saver.setup(); await store.setup()
        app.state.checkpointer, app.state.store = saver, store
        yield
```
- Durability: default `async`; for workflows marked "side effects" use `durability="sync"` in `astream(...)` (verified parameter).
- Deleting a thread: `await saver.adelete_thread(thread_id)` (verified) + delete `thread` row.
- Redis/Mongo checkpointers and the RoutedStore are post-MVP (doc08); Redis requires Redis 8/Stack (FAQ P-04).

## 8. Script execution protocol (sandbox)

Request (stdin, JSON): `{"import_path": "scripts.score:run", "state": {...}}` · Response (stdout, JSON): `{"update": {...}}` or `{"error": {"type": "...", "message": "..."}}`. Exit code 0 always; the runner converts `error` into an exception so LangGraph retry policies apply. Timeout enforced by the runner (`asyncio.wait_for`), container killed on timeout.

## 9. Triggers & scheduling

- Webhooks: verify `hmac.compare_digest(expected, received)` where `expected = "sha256=" + hmac.new(secret, raw_body, sha256).hexdigest()`; reject if older than 5 minutes when a timestamp header is present.
- Cron: `apscheduler.schedulers.asyncio.AsyncIOScheduler` with `CronTrigger.from_crontab(expr, timezone=tz)`; on multiple API replicas, only the instance holding Postgres advisory lock `pg_try_advisory_lock(42)` runs the scheduler.

## 10. Errors and logging

- Domain exceptions → problem+json in `api/errors.py` (02 §5).
- structlog processors: timestamp, level, `request_id`, bound context (`workflow_id`, `thread_id`, `run_id`); JSON renderer in non-dev.
- Never log: secret values, full prompts/outputs in prod (log lengths and hashes instead).
