# AgentCanvas

**Visual agentic workflow builder** — a ComfyUI-style canvas where you drag widgets, wire them up and add scripts, while the backend compiles the picture into a real, idiomatic **LangGraph / LangChain / Deep Agents** Python project that you can run, debug, test, export and deploy.

| | |
|---|---|
| **User guide** (run it, use it) | [docs/user-guide/index.md](docs/user-guide/index.md) — start with [Run AgentCanvas on your computer](docs/user-guide/run-locally.md) |
| Specification (v0.6) | [docs/agent-canvas-spec/README.md](docs/agent-canvas-spec/README.md) |
| Competitive roadmap (approved sprint order) | [docs/agent-canvas-spec/16-competitive-roadmap.md](docs/agent-canvas-spec/16-competitive-roadmap.md) |
| Concept & new features | [docs/agent-canvas-spec/15-concept-evolution-and-new-features.md](docs/agent-canvas-spec/15-concept-evolution-and-new-features.md) |
| Latest review | [docs/agent-canvas-spec/14-deep-review-v0.6.md](docs/agent-canvas-spec/14-deep-review-v0.6.md) |
| Build guide | [docs/agent-canvas-spec/build-guide/README.md](docs/agent-canvas-spec/build-guide/README.md) |
| Sprint plan & execution log | [docs/agent-canvas-spec/build-guide/13-sprint-plan.md](docs/agent-canvas-spec/build-guide/13-sprint-plan.md) |
| Architecture decisions | [docs/adr/](docs/adr/README.md) |
| Release notes | [CHANGELOG.md](CHANGELOG.md) — current release **v0.1.0 (MVP)**; **v0.2.0** (accounts & access) merged, tag pending |
| Progress (done / pending) | [PROGRESS.md](PROGRESS.md) |
| Handover (continue in a new session or as a new person) | [HANDOVER.md](HANDOVER.md) |

## Repository layout

```
apps/api/        Python 3.12 — FastAPI control plane + runner, IR, validator, node library, compiler
apps/web/        React 19 + TypeScript — canvas (React Flow), inspector, run panel
packages/        shared artifacts (IR JSON Schema for the web app)
infra/           Dockerfiles, nginx, compose.prod.yml, Helm chart (helm/agentcanvas), dev docker-compose, reference DDL
docs/            specification, build guide, ADRs
```

## Quick start with Docker

```bash
make docker-up    # Postgres (pgvector) + migrations + API + web app on http://localhost:7777
```

Open http://localhost:7777 and choose your model on the first-run screen: Claude, GPT, Gemini, Mistral, Groq,
DeepSeek, Grok, any OpenAI-compatible API, or a local model with Ollama, LM Studio, vLLM or llama.cpp. See
[Models and API keys](docs/user-guide/models.md).

## Quick start from source (no API keys needed)

Prerequisites: Python tooling [`uv`](https://docs.astral.sh/uv/), Node 22 + pnpm 10.

```bash
make setup        # uv sync + pnpm install
make dev-fake     # API with scripted fake models on :8000
make dev-web      # web app on http://localhost:5173 (in another terminal)
```

Step-by-step version with prerequisites, a smoke test and troubleshooting:
[Run AgentCanvas on your computer](docs/user-guide/run-locally.md).

Open the web app and click **Start the guided build** for a six-step tour, or pick a template, press **▶ Run**,
approve the tool call and watch the nodes light up.

```bash
make test         # backend + frontend tests
make lint         # ruff, pyright, tsc
make e2e          # Playwright end-to-end (starts both servers)
make check        # every CI gate except e2e and the strict docs build
make docs         # documentation site on http://localhost:8001
```

Use real models with `make dev-api`, then choose the model and enter its key under **Model** in the web app (or export
provider keys such as `OPENAI_API_KEY` before starting it).

## Deploy

Container images, a production compose file (`infra/compose.prod.yml`, two API replicas behind nginx) and a Helm
chart (`infra/helm/agentcanvas`) are in `infra/`; the
[deployment guide](docs/agent-canvas-spec/build-guide/09-deployment-and-operations.md) walks through both.

## What you can do in v0.1.0

Build on the canvas (agents, Deep Agents, routers, map/join, subgraphs, MCP/HTTP tools, memory, workspace
scripts and custom widgets) → run and debug (breakpoints, time travel, Explain mode, pins/mocks, cost per step,
blast radius and dry runs) → test (saved test cases, record/replay) → ship (versions & diffs, export as a plain
Python project, publish behind an API key, webhooks and cron). Details in the [changelog](CHANGELOG.md).

## Added since v0.1.0

- **v0.2.0, accounts & access:** sign-in with your identity provider (OIDC), organisations that cannot see each
  other's work, roles with email invitations, an audit log, and organisation policies written as CEL.
- **v0.3.0, production readiness:** container images, compose prod and Helm, metrics and traces, retention and
  backups, safe redeploys and encrypted secrets.
- **v0.4.0, Evaluation Studio:** datasets, evaluators, experiments with a results grid, and release gates.
- **v0.5.0, evaluation in production:** online evaluators on deployment traffic with drift alerts, annotation
  queues, pairwise and composite evaluators, rubric self-grading on agents, simulated users, LangSmith dataset
  sync, and record/replay of tool calls.
- **v0.6.0, AI Copilot:** briefs, plans and builds workflows as proposals you accept on the canvas.
- **v0.7.0, open doors:** every deployment is an MCP server and an A2A agent; one-click app connectors; trace export
  to Langfuse, Phoenix, Datadog or any OTLP endpoint.
- **v0.8.0, Copilot II:** the Copilot tests, debugs and fixes workflows.
- **v0.9.0, export deploy kit:** an export is a deployable service for your computer, a server, Kubernetes, Cloud
  Run, AWS or Azure, with guides and `make doctor` / `make smoke`.
- **v0.10.0, any framework as a node:** agents built with nine other frameworks, remote A2A agents and MCP tools run
  as canvas steps; Open Agent Spec import and export; light, medium and dark themes.

See the [changelog](CHANGELOG.md) for details.

## Status

The project is executed sprint by sprint: see [PROGRESS.md](PROGRESS.md) for what is done and pending, and the
[execution log](docs/agent-canvas-spec/build-guide/13-sprint-plan.md#5-execution-log) for per-sprint detail.

| Sprint | State |
|---|---|
| S0–S9: foundations to MVP (v0.1.0) | ✅ merged |
| S10: accounts & access (v0.2.0) | ✅ merged ([#3](https://github.com/marunsct/AgentCanvas/pull/3)) |
| S11: production readiness (v0.3.0) | ✅ done (merged in #4; tag pending): containers, compose prod and Helm, observability, retention and backups, safe redeploy, load test, security review, KMS |
| S12: Evaluation Studio (v0.4.0) | ✅ done: datasets, evaluators, experiments, results grid, release gate (merged in #5) |
| S13: Evaluation in production (v0.5.0) | ✅ done (merged in #8): online evaluators and drift alerts, annotation queues, pairwise and composite evaluators, rubric self-grading, simulated users, LangSmith sync, tool record/replay |
| S14: AI Copilot I (v0.6.0) | ✅ done (merged in #15) |
| S15: Open doors (v0.7.0) | ✅ done (merged in #17): every deployment is an MCP server and an A2A agent, 30-connector gallery, trace export |
| S18: AI Copilot II and debug assistant (v0.8.0) | ✅ done (#19, built ahead of S16–S17) |
| S19: Export deploy kit (v0.9.0) | ✅ done (#19, built ahead of S16–S17) |
| UI refresh: light, medium and dark themes | ✅ done (#20) |
| S16: Any framework as a node (v0.10.0) | ✅ done |
| S17: Workspace and editor fixes (v0.11.0) | ✅ done: a workspace per workflow, exports bundle its code, Copilot in the code editor, a way back to the canvas, labelled buttons |
| S20: Framework foundation (v0.12.0) | ✅ done: a framework per workflow, badges for where it can run, adapter interface v2, conformance kit |
| S21: Design any agent (v0.13.0) | ✅ done: an AI agent block designed as a native agent in nine frameworks, with its code and a native export |
| S22: Whole flows, preview (v0.14.0) | ✅ done: export a whole workflow to LangGraph, WayFlow or OpenAI Agents through Agent Spec, with a gaps report |
| Milestone review after S20–S22 | ✅ done (#27): 19 fixes |
| S23: Ship to users (v0.15.0) | ✅ done: share a deployment as a public chat page or a website widget, four safety blocks with eval cases, a gallery of 25 templates |
| S24: Knowledge Studio (v0.16.0) | ✅ done: knowledge bases from files, web pages and text; hybrid search with citations; the Search knowledge block; retrieval evals |
| S25: OpenAI Agents SDK flows (v0.17.0) | ✅ done: workflows run and export as OpenAI Agents SDK code (agents, tools, approvals, typed answers); convert and compare in one experiment |
| S26: Model gateway and cost controls (v0.18.0) | ✅ done: backup models when a provider fails, an answer cache, day and month budgets with cost alerts, spending in the editor |
| S27: CrewAI flows (v0.19.0) | ✅ done: workflows as native CrewAI flows, Crew and Agent task blocks, pauses for people, CrewAI project export |
| S28: Mixed-flow export (v0.20.0) | ✅ done: frameworks that clash with LangGraph run as parts in their own environment, typed contracts between parts, one deployable project per mixed flow |
| S29–S30: Google ADK flows (v0.21.0) | ✅ done: workflows as native ADK graph workflows, ADK agents with tool approvals, pauses for people, ADK project export |
| S31: Onboarding and switching (v0.22.0) | ✅ done: Langflow, Flowise and n8n importers, a Playground, Simple mode, Push to GitHub |
| S32: Pydantic AI and Agno flows (v0.23.0) | ✅ done: workflows run and export as Pydantic AI or Agno flows, with the frameworks' own agents and tool approvals |
| S33–S36: framework flows | ⏸ next (S33–S34 Microsoft Agent Framework flows); order in the [competitive roadmap](docs/agent-canvas-spec/16-competitive-roadmap.md) |
