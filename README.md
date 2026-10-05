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
| Release notes | [CHANGELOG.md](CHANGELOG.md) — current release **v0.1.0 (MVP)**; **v0.2.0** (accounts & access) in review |
| Progress (done / pending) | [PROGRESS.md](PROGRESS.md) |

## Repository layout

```
apps/api/        Python 3.12 — FastAPI control plane + runner, IR, validator, node library, compiler
apps/web/        React 19 + TypeScript — canvas (React Flow), inspector, run panel
packages/        shared artifacts (IR JSON Schema for the web app)
infra/           Dockerfiles, nginx, compose.prod.yml, Helm chart (helm/agentcanvas), dev docker-compose, reference DDL
docs/            specification, build guide, ADRs
```

## Quick start (no API keys needed)

Prerequisites: Python tooling [`uv`](https://docs.astral.sh/uv/), Node 22 + pnpm 10.

```bash
make setup        # uv sync + pnpm install
make dev-fake     # API with scripted fake models on :8000
make dev-web      # web app on http://localhost:5173 (in another terminal)
```

Open the web app and click **Start the guided build** for a six-step tour, or pick a template, press **▶ Run**,
approve the tool call and watch the nodes light up.

```bash
make test         # backend + frontend tests
make lint         # ruff, pyright, tsc
make e2e          # Playwright end-to-end (starts both servers)
make check        # every CI gate except e2e and the strict docs build
make docs         # documentation site on http://localhost:8001
```

Use real models with `make dev-api` after exporting provider keys (e.g. `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).

## Deploy

Container images, a production compose file (`infra/compose.prod.yml`, two API replicas behind nginx) and a Helm
chart (`infra/helm/agentcanvas`) are in `infra/`; the
[deployment guide](docs/agent-canvas-spec/build-guide/09-deployment-and-operations.md) walks through both.

## What you can do in v0.1.0

Build on the canvas (agents, Deep Agents, routers, map/join, subgraphs, MCP/HTTP tools, memory, workspace
scripts and custom widgets) → run and debug (breakpoints, time travel, Explain mode, pins/mocks, cost per step,
blast radius and dry runs) → test (saved test cases, record/replay) → ship (versions & diffs, export as a plain
Python project, publish behind an API key, webhooks and cron). Details in the [changelog](CHANGELOG.md).

## Coming in v0.2.0 (accounts & access)

Sign in with your identity provider (OIDC, `AGENTCANVAS_AUTH_MODE=oidc`), organisations that cannot see each
other's work, roles (viewer, reviewer, builder, admin) with email invitations, an audit log of every change, and
organisation **policies** written as CEL ("every external write needs approval", "only approved models in prod")
that the editor reports and publishing enforces. Long-term memory is kept per organisation. See the
[Unreleased section of the changelog](CHANGELOG.md#unreleased-v020).

## Status

The project is executed sprint by sprint: see [PROGRESS.md](PROGRESS.md) for what is done and pending, and the
[execution log](docs/agent-canvas-spec/build-guide/13-sprint-plan.md#5-execution-log) for per-sprint detail.

| Sprint | State |
|---|---|
| S0–S9: foundations to MVP (v0.1.0) | ✅ merged |
| S10: accounts & access (v0.2.0) | ✅ done, in review ([#3](https://github.com/marunsct/AgentCanvas/pull/3)) |
| S11: production readiness (v0.3.0) | 🚧 in progress: containers, compose prod and Helm, observability, backups and retention, safe redeploy, load test, security review ([backlog](docs/agent-canvas-spec/build-guide/13-sprint-plan.md#s11--production-readiness--v030)) |
