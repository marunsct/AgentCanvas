# AgentCanvas

**Visual agentic workflow builder** — a ComfyUI-style canvas where you drag widgets, wire them up and add scripts, while the backend compiles the picture into a real, idiomatic **LangGraph / LangChain / Deep Agents** Python project that you can run, debug, test, export and deploy.

| | |
|---|---|
| Specification (v0.6) | [docs/agent-canvas-spec/README.md](docs/agent-canvas-spec/README.md) |
| Concept & new features | [docs/agent-canvas-spec/15-concept-evolution-and-new-features.md](docs/agent-canvas-spec/15-concept-evolution-and-new-features.md) |
| Latest review | [docs/agent-canvas-spec/14-deep-review-v0.6.md](docs/agent-canvas-spec/14-deep-review-v0.6.md) |
| Build guide | [docs/agent-canvas-spec/build-guide/README.md](docs/agent-canvas-spec/build-guide/README.md) |
| Sprint plan & execution log | [docs/agent-canvas-spec/build-guide/13-sprint-plan.md](docs/agent-canvas-spec/build-guide/13-sprint-plan.md) |
| Architecture decisions | [docs/adr/](docs/adr/README.md) |

## Repository layout

```
apps/api/        Python 3.12 — FastAPI control plane + runner, IR, validator, node library, compiler
apps/web/        React 19 + TypeScript — canvas (React Flow), inspector, run panel
packages/        shared artifacts (IR JSON Schema for the web app)
infra/           docker-compose (Postgres + pgvector, Redis 8, Keycloak), Dockerfiles, reference DDL
docs/            specification, build guide, ADRs
```

## Quick start (no API keys needed)

Prerequisites: Python tooling [`uv`](https://docs.astral.sh/uv/), Node 22 + pnpm 10.

```bash
make setup        # uv sync + pnpm install
make dev-fake     # API with scripted fake models on :8000
make dev-web      # web app on http://localhost:5173 (in another terminal)
```

Open the web app, press **▶ Run**, approve the tool call, and watch the nodes light up.

```bash
make test         # backend + frontend tests
make lint         # ruff, pyright, tsc
make e2e          # Playwright end-to-end (starts both servers)
```

Use real models with `make dev-api` after exporting provider keys (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).

## Status

The project is executed sprint by sprint; see the [execution log](docs/agent-canvas-spec/build-guide/13-sprint-plan.md#5-execution-log).
