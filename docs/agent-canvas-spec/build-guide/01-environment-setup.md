# 01 — Environment Setup

Follow top to bottom. Every command has an **expected result** — if you don't see it, check [11-troubleshooting-faq.md](./11-troubleshooting-faq.md).

## 1. Versions (verified together on 2026-09-28)

| Tool / library | Version | Why this version |
|----------------|---------|------------------|
| Python | **3.12** | LangGraph node timeouts/error handlers and Deep Agents interpreters need ≥ 3.11; 3.12 is our standard |
| uv (Python package manager) | ≥ 0.5 | fast, reproducible installs with `uv.lock` |
| Node.js | **22 LTS** | frontend tooling |
| pnpm | **10** | frontend package manager |
| Docker + Docker Compose | recent | local Postgres/Redis/Keycloak |
| langgraph | 1.2.12 | runtime |
| langchain / langchain-core | 1.4.2 / 1.6.5 | agents, models, tools, middleware |
| deepagents | 0.7.19 | Deep Agent widget |
| langgraph-checkpoint-postgres | 3.1.2 | durable checkpoints + store |
| langgraph-checkpoint-redis | 0.5.2 | optional Redis checkpointer (needs Redis 8 / Redis Stack) |
| fastapi | 0.141 | HTTP API |
| cel-python | 0.5.0 | safe router conditions |
| React / @xyflow/react / zustand | 19 / 12.12 / 5 | UI and canvas |
| Vite / TypeScript / Vitest | 8 / 7 / 5 | frontend build and tests |
| Postgres | 16 + pgvector | persistence |
| Redis | 8 | cache, pub/sub, optional checkpointer |

The exact resolved versions are pinned in `reference/backend/uv.lock` and `reference/web/pnpm-lock.yaml`.

## 2. Install the toolchain

### macOS
```bash
brew install uv node@22 pnpm git
brew install --cask docker          # then open Docker Desktop once
```
### Ubuntu / Debian / WSL2
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs
sudo corepack enable && corepack prepare pnpm@10 --activate
# Docker Engine: follow docs.docker.com/engine/install for your distro, then:
sudo usermod -aG docker $USER && newgrp docker
```
### Windows
Use **WSL2 (Ubuntu)** and follow the Ubuntu steps inside WSL. Install Docker Desktop with the WSL2 backend.

**Check:**
```bash
uv --version        # uv 0.x
uv python install 3.12 && uv python list | grep 3.12
node --version      # v22.x
pnpm --version      # 10.x
docker compose version
```

## 3. Start local infrastructure

```bash
cd docs/agent-canvas-spec/build-guide/reference/infra
docker compose up -d postgres redis
docker compose ps                    # both "healthy" after ~10 s
```
**Check Postgres:** `docker compose exec postgres psql -U agentcanvas -c "select 1"` → prints `1`.
**Check Redis modules:** `docker compose exec redis redis-cli MODULE LIST` → lists `search` and `ReJSON` (needed only if you use the Redis checkpointer/store).

Connection strings for `.env`:
```
DATABASE_URL=postgresql://agentcanvas:agentcanvas@localhost:5432/agentcanvas
REDIS_URL=redis://localhost:6379
```

> **No Docker?** Install Postgres 16 natively and create the user/database, e.g. `createuser -s agentcanvas; createdb -O agentcanvas agentcanvas`. The reference tests also pass against a native Postgres.

## 4. Run the reference backend

```bash
cd docs/agent-canvas-spec/build-guide/reference/backend
uv sync                              # creates .venv from uv.lock (Python 3.12)
uv run pytest -q                     # expected: 20 passed
uv run ruff check .                  # expected: All checks passed!
uv run pyright                       # expected: 0 errors, 0 warnings
```

Start the API with **fake models** (no API keys needed):
```bash
uv run uvicorn agentcanvas.dev.fake_server:app --reload --port 8000
```
In another terminal, load the example workflow and run it:
```bash
curl -s -X POST localhost:8000/workflows -H 'content-type: application/json' \
     -d @examples/support_triage.ir.json
# {"id":"wf_support_triage","diagnostics":[]}

curl -sN -X POST localhost:8000/workflows/wf_support_triage/runs/stream \
     -H 'content-type: application/json' -d '{"input":{"ticket":"Invoice?"},"thread_id":"demo"}'
# data: {"type": "RUN_STARTED", ...}
# data: {"type": "STEP_STARTED", "stepName": "classify", "canvas_node_id": "n_cls"}
# ...
# data: {"type": "RUN_FINISHED", "threadId": "demo", "outcome": "interrupt"}

curl -sN -X POST localhost:8000/threads/demo/resume -H 'content-type: application/json' \
     -d '{"value":{"decisions":[{"type":"approve"}]}}'
# ... data: {"type": "RUN_FINISHED", "threadId": "demo", "outcome": "success"}
```

Use **Postgres** instead of memory for checkpoints:
```bash
DATABASE_URL=postgresql://agentcanvas:agentcanvas@localhost:5432/agentcanvas \
  uv run uvicorn agentcanvas.dev.fake_server:app --port 8000
```
The checkpoints of thread `demo` are now stored in Postgres (table `checkpoints`; inspect with `psql`). Note: the M0 reference still keeps *workflows* and the *thread → workflow* mapping in memory, so after a restart the API answers `404 thread not found` even though the checkpoints exist. Ticket M1-T6 moves both into Postgres, after which threads fully survive restarts.

Use **real models**: set `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` and run `uv run uvicorn agentcanvas.api.app:app --port 8000`.

## 5. Run the reference frontend

```bash
cd docs/agent-canvas-spec/build-guide/reference/web
pnpm install --frozen-lockfile
pnpm exec tsc -p tsconfig.json       # no output = OK
pnpm test                            # expected: Tests 2 passed
pnpm dev                             # http://localhost:5173 (proxies /api -> :8000)
```
Open http://localhost:5173 (backend must be running and the workflow loaded — see §4). Click **▶ Run**: nodes turn blue → green, the *Reply drafter* turns amber and an **Approval needed** card appears. Click **Approve**: the run completes and the output shows *Reply sent.*

Optional browser test: `pnpm add -D playwright && npx playwright install chromium && node e2e.mjs` → `E2E OK`.

## 6. Editor setup

- **VS Code** extensions: Python, Pylance, Ruff, ESLint, Prettier, Even Better TOML, Docker.
- Settings: format on save; Python interpreter = `reference/backend/.venv` (or the monorepo venv later).
- **PyCharm/WebStorm** work equally well; enable Ruff and point to the `.venv`.

## 7. Environment variables (full list)

| Variable | Used by | Example | Required |
|----------|---------|---------|----------|
| `DATABASE_URL` | API/runner | `postgresql://agentcanvas:agentcanvas@localhost:5432/agentcanvas` | M1+ (optional in M0) |
| `REDIS_URL` | pub/sub, cache (M5+) | `redis://localhost:6379` | M5+ |
| `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY` | model providers | secret | only for real models |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | tracing | `true`, secret, `agentcanvas-dev` | optional |
| `LANGGRAPH_AES_KEY` | checkpoint encryption | 32-byte key | prod |
| `AGENTCANVAS_SECRET_KEY` | session/JWT signing (dev auth) | random 64 chars | M7 |
| `OIDC_ISSUER`, `OIDC_AUDIENCE` | login | `http://localhost:8080/realms/agentcanvas` | M7 |
| `AGENTCANVAS_ENV` | behaviour switches | `dev` / `staging` / `prod` | always |

Put them in `.env` (never commit it); commit `.env.example` with names only.
