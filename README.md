# AgentCanvas

**Visual agentic workflow builder** — a ComfyUI-style canvas where you drag widgets, wire them up and add scripts, while the backend compiles the picture into a real, idiomatic project for the framework you pick — **LangGraph / LangChain / Deep Agents**, the OpenAI Agents SDK, CrewAI, Google ADK, Pydantic AI, Agno, Microsoft Agent Framework, Strands, LlamaIndex or Mastra (TypeScript) — that you can run, debug, test, export and deploy.

| Documentation | For | Start with |
|---|---|---|
| [User guide](docs/user-guide/index.md) | People who build workflows, and whoever runs AgentCanvas for a team | [Run AgentCanvas on your computer](docs/user-guide/run-locally.md), then [Getting started](docs/user-guide/getting-started.md); operators: [Deploy and operate](docs/user-guide/deploy.md) |
| [Developer documentation](docs/developer/README.md) | People who change AgentCanvas | [Handover](docs/developer/handover.md) (setup, conventions), [status and roadmap](docs/developer/hardening.md), [build guide](docs/developer/build-guide/README.md), [spec](docs/developer/spec/README.md), [ADRs](docs/developer/adr/README.md) |
| [Changelog](CHANGELOG.md) | Everyone | Code at **v0.32.3** plus the unreleased changes at the top; no release has been tagged yet (tags are the owner's call) |

## What you can do

Build on the canvas (agents, Deep Agents, routers, map/join, subgraphs, MCP/HTTP tools, memory, knowledge bases,
workspace scripts and custom widgets), or let the AI Copilot build and fix a workflow from a description → run and
debug (breakpoints, time travel, Explain mode, pins/mocks, cost per step, blast radius and dry runs) → test and
evaluate (test cases, datasets, evaluators, experiments, release gates, online evaluation) → ship (versions and
diffs, export as a plain project for the framework you chose, publish behind an API key, as an MCP server or an A2A
agent, as a chat page or a website widget, webhooks and cron) → govern (sign-in with your identity provider,
organisations, roles, policies, an audit log). The [user guide](docs/user-guide/index.md) covers each.

## Quick start with Docker

```bash
make docker-up    # Postgres (pgvector) + migrations + API + web app on http://localhost:7777
```

The image carries every framework (several GB); `FRAMEWORKS=none WITH_MASTRA=false make docker-up` builds a small one with LangGraph and
the OpenAI Agents SDK only.

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

Use real models with `make dev-api`, then choose the model and enter its key under **Model** in the web app (or export
provider keys such as `OPENAI_API_KEY` before starting it).

## Deploy

Container images, a production compose file (`infra/compose.prod.yml`, two API replicas behind nginx) and a Helm
chart (`infra/helm/agentcanvas`) are in `infra/`; [Deploy and operate](docs/user-guide/deploy.md) walks through both,
and the [Operations reference](docs/user-guide/operations.md) has the metrics and runbooks.

## Develop

```bash
make test         # backend + frontend tests
make lint         # ruff, pyright, tsc
make e2e          # Playwright end-to-end (starts both servers)
make check        # lint, types, schema drift, tests, build
make ci           # local CI: check + strict docs + audit + Helm (make ci-full: everything incl. Postgres, exports, e2e; make hooks: run lint before push)
make docs         # documentation site on http://localhost:8001
```

The repository map, conventions and CI are in the [handover](docs/developer/handover.md); the
[developer documentation](docs/developer/README.md) maps the rest.
