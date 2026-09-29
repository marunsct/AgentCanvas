# 02 — Repository Layout, Conventions & Decisions

## 1. Create the product repository (ticket M0-T1)

Create a **new** repository `agentcanvas` (the spec lives in the current repo; the product gets its own). Start from the reference implementation:

```bash
mkdir agentcanvas && cd agentcanvas && git init
mkdir -p apps packages infra docs/adr
cp -r <spec-repo>/docs/agent-canvas-spec/build-guide/reference/backend  apps/api
cp -r <spec-repo>/docs/agent-canvas-spec/build-guide/reference/web      apps/web
cp -r <spec-repo>/docs/agent-canvas-spec/build-guide/reference/infra/*  infra/
cp <spec-repo>/docs/agent-canvas-spec/build-guide/reference/.gitignore  .gitignore
```

## 2. Target monorepo layout (grow into it milestone by milestone)

```
agentcanvas/
├── apps/
│   ├── api/                       # Python: FastAPI control plane + runner (M0–M6 in one process)
│   │   ├── agentcanvas/
│   │   │   ├── api/               # HTTP routers: workflows, runs, threads, inbox, connections, secrets, auth
│   │   │   ├── db/                # SQLAlchemy models, session, repositories (M1)
│   │   │   ├── ir/                # IR models, commands, validator, JSON schema export
│   │   │   ├── runtime/           # node library, interpreter, events, resources, persistence bindings
│   │   │   ├── compiler/          # code generator + templates + project packager (M4)
│   │   │   ├── adapters/          # framework adapters; langchain first (M3 boundary)
│   │   │   ├── registry/          # node-type manifests (JSON schema of each node's config)
│   │   │   ├── sandbox/           # script execution (in-process dev / docker runner) (M5)
│   │   │   ├── security/          # authn/z, secrets vault client (M7)
│   │   │   ├── dev/               # fake servers and seed data
│   │   │   └── testing.py         # fakes & helpers
│   │   ├── migrations/            # Alembic (M1)
│   │   ├── tests/                 # unit/, integration/, conformance/
│   │   └── pyproject.toml, uv.lock
│   └── web/                       # React + Vite
│       ├── src/
│       │   ├── app/               # routing, providers, layout shell
│       │   ├── api/               # typed API client (generated from OpenAPI, M2)
│       │   ├── canvas/            # React Flow canvas, node renderers, edges, palette
│       │   ├── inspector/         # config forms (RJSF), prompt editor
│       │   ├── run/               # run store, SSE client, overlay, timeline, chat panel
│       │   ├── inbox/             # approvals UI
│       │   ├── workspace/         # Monaco editor, file tree (M5)
│       │   └── shared/            # UI kit, hooks, utils
│       ├── e2e/                   # Playwright tests
│       └── package.json, pnpm-lock.yaml
├── packages/
│   └── ir-schema/                 # JSON Schema of the IR, generated from Pydantic, consumed by web
├── infra/                         # docker-compose.yml, Dockerfiles, helm/ (M8)
├── docs/adr/                      # architecture decision records
├── .github/workflows/ci.yml
└── Makefile                       # make dev / test / lint / fmt
```

## 3. Makefile (copy this)

```makefile
.PHONY: infra dev-api dev-fake dev-web test lint fmt
infra:      ; docker compose -f infra/docker-compose.yml up -d postgres redis
dev-api:    ; cd apps/api && uv run uvicorn agentcanvas.api.app:app --reload --port 8000
dev-fake:   ; cd apps/api && uv run uvicorn agentcanvas.dev.fake_server:app --reload --port 8000
dev-web:    ; cd apps/web && pnpm dev
test:       ; cd apps/api && uv run pytest -q && cd ../web && pnpm test
lint:       ; cd apps/api && uv run ruff check . && uv run pyright && cd ../web && pnpm exec tsc -p tsconfig.json
fmt:        ; cd apps/api && uv run ruff format . && uv run ruff check --fix .
```

## 4. Coding conventions

### Python
- Python 3.12, `from __future__ import annotations` in every module.
- **ruff** (lint + format, line length 120, rules `E,F,I,UP,B,RUF,BLE,PLW`) and **pyright** standard mode must pass.
- Pydantic v2 models for all external data (IR, API bodies); `extra="forbid"` on IR models so typos fail.
- Async everywhere on request paths (`async def`, `ainvoke`, `astream`, `AsyncPostgresSaver`).
- No framework imports in `ir/` (framework-neutral core); LangChain/LangGraph only in `runtime/`, `compiler/`, `adapters/`. Enforce with `import-linter` in M3.
- Never `eval`/`exec` user input. Conditions use CEL (`agentcanvas/expr.py`); templates use sandboxed Jinja.
- Errors: raise domain exceptions (`CompileError`, `ExpressionError`, …); API layer converts them to problem+json (§5).
- Logging: `structlog` JSON logs with `request_id`, `workflow_id`, `thread_id`, `run_id` fields; never log secrets or full prompts in prod.
- Docstrings on public functions: one sentence *what* + *why* if non-obvious.

### TypeScript / React
- `strict: true`; no `any` without a comment.
- State: **Zustand** for client state (canvas, run), **TanStack Query** for server data.
- Components: function components + hooks; one component per file; file name = component name.
- IR types are **generated** from the Pydantic JSON Schema (`packages/ir-schema`) — do not hand-edit after M2-T2.
- Tests: Vitest for logic (reducers, converters), Playwright for flows.

### Naming
| Thing | Convention | Example |
|-------|-----------|---------|
| IR node `name` / channel names | snake_case, `^[a-z][a-z0-9_]{0,62}$` | `reply_drafter` |
| IDs | prefixed ULID | `wf_01J9…`, `n_01J9…`, `th_…`, `run_…`, `ver_…` |
| Node types | `namespace.kind` | `core.agent`, `langchain.deep_agent` |
| Diagnostics | letter + 3 digits | `E001`, `W070`, `P002` |
| Canonical events | AG-UI names; extensions `ac.*` | `STEP_STARTED`, `CUSTOM ac.interrupt` |
| API paths | plural nouns, kebab-case | `/workflows/{id}/runs/stream` |

## 5. API conventions

- JSON bodies, `snake_case` fields; timestamps ISO-8601 UTC.
- **Errors** — RFC 9457 `application/problem+json`:
  ```json
  {"type": "https://agentcanvas.dev/errors/validation", "title": "Workflow has errors", "status": 422,
   "detail": "2 errors", "errors": [{"code": "E001", "message": "...", "node_id": "n_cls"}]}
  ```
- **Pagination** — cursor based: `?limit=50&cursor=<opaque>` → `{"items": [...], "next_cursor": "..."}`.
- **Concurrency** — every mutable resource has `version`; send `If-Match: <version>`; mismatch → `409 Conflict`.
- **Streaming** — `text/event-stream`; each message `data: <json>\n\n`; set header `X-Accel-Buffering: no` so proxies do not buffer.
- **Idempotency** — `Idempotency-Key` header on POSTs that create runs.
- Versioned under `/v1` from M2.

## 6. Architecture Decision Records

Each ADR is a short file in `docs/adr/NNNN-title.md` with *Context / Decision / Consequences*. Create these on day 1 (text from [13-deep-review-report.md §3.4](../13-deep-review-report.md#34-undecided-choices--decisions-recorded-as-adrs-in-build-guide02-6)):

| ADR | Decision |
|-----|----------|
| 0001 | IR is the single source of truth (spec D1) |
| 0002 | Python 3.12 backend, generated code in Python (spec D2) |
| 0003 | MVP runtime = own open-source runner on LangGraph + Postgres (review D-01) |
| 0004 | Generated code calls the shared node library; equivalence tested (review C-03) |
| 0005 | CEL for conditions, sandboxed Jinja for templates (D-02) |
| 0006 | React Flow canvas, Zustand + TanStack Query (D-03, D-04) |
| 0007 | SSE over POST for run streams (D-05) |
| 0008 | Canonical events = AG-UI + `ac.*` extensions (spec 12 §C.3) |
| 0009 | Alembic migrations; prefixed ULIDs; problem+json errors (D-06, D-08, D-09) |
| 0010 | OIDC auth, single org in MVP (D-07) |
| 0011 | Framework-neutral core with adapter boundary (spec 11/12) |

Template:
```markdown
# 0003 — MVP runtime is our own runner
Status: accepted · Date: 2026-10-01
## Context
Self-hosting LangGraph Agent Server in production requires a LangSmith licence; we need a free local/dev runtime.
## Decision
Implement a FastAPI runner on the open-source LangGraph library with Postgres checkpoints; mirror Agent Server's API shape.
## Consequences
+ No licence dependency; full control of events. − We implement threads/runs/stream ourselves; LangSmith Deployment stays a publish target.
```

## 7. Git workflow

- Trunk-based: `main` is always releasable. Short-lived branches `feat/M2-T3-inspector`, `fix/…`.
- **Conventional Commits**: `feat(canvas): add wiring edges`, `fix(runtime): resume uses same thread resources`.
- PR template: *What / Why / How tested / Screenshots / Checklist (DoD)*. One reviewer minimum; CI green required.
- Tag releases `vMAJOR.MINOR.PATCH`; changelog generated from commits.

## 8. CI (GitHub Actions) — `.github/workflows/ci.yml`

```yaml
name: ci
on: [push, pull_request]
jobs:
  api:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        env: { POSTGRES_USER: agentcanvas, POSTGRES_PASSWORD: agentcanvas, POSTGRES_DB: agentcanvas }
        ports: ["5432:5432"]
        options: --health-cmd "pg_isready -U agentcanvas" --health-interval 5s --health-retries 10
    defaults: { run: { working-directory: apps/api } }
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync
      - run: uv run ruff check . && uv run ruff format --check .
      - run: uv run pyright
      - run: uv run pytest -q
        env: { TEST_DATABASE_URL: "postgresql://agentcanvas:agentcanvas@localhost:5432/agentcanvas" }
  web:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: apps/web } }
    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v4
        with: { version: 10 }
      - uses: actions/setup-node@v4
        with: { node-version: 22, cache: pnpm, cache-dependency-path: apps/web/pnpm-lock.yaml }
      - run: pnpm install --frozen-lockfile
      - run: pnpm exec tsc -p tsconfig.json
      - run: pnpm test
      - run: pnpm build
```
