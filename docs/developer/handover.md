# Handover

Everything an agent or a developer needs to pick this project up in a new session, or as a different person: the
owner's standing decisions, where the code is, how to set it up, run the checks and CI, the conventions, and the
quirks of the cloud sessions. It does not keep a status: what is open and planned is in [hardening.md](hardening.md),
what changed is in the [changelog](https://github.com/marunsct/AgentCanvas/blob/main/CHANGELOG.md), and the judgment calls made without asking are in
[decisions.md](decisions.md). The [developer README](README.md) maps every other document.

- **Repository:** https://github.com/marunsct/AgentCanvas (default branch `main`).
- **The state on 2026-10-18** (v0.29.0, test totals, known gaps per sprint, the PR history) is kept as a snapshot in
  [history/handover-2026-10.md](history/handover-2026-10.md).

---

## 1. Owner decisions and working agreements

These come from the owner in the project chat; they override defaults.

- **Merging.** The owner normally wants to be asked before any merge. A standing grant let sprint PRs be merged
  without asking (with a whole-codebase review before each sprint) **only through S15**. That grant is used up.
  For later sprints, ask before merging unless the owner renews the grant. **Renewed 2026-10-09** for every sprint
  from S20 on: see "Standing merge grant" below.
- **Stop after S15** (2026-10-07): finish S15, run an end-to-end review, fix what it finds, then stop. Done.
- **Finish the S18/S19 drafts** (2026-10-08): the owner asked to finish the already-built Copilot II and deploy kit
  and mark them done in the docs. Done in #19. Release numbers follow merge order (S18 v0.8.0, S19 v0.9.0,
  so S16 becomes v0.10.0 and S17 v0.11.0).
- **Release tags** (`v0.x.0`) are always the owner's call. Never push tags.
- **Sprint order** is the approved value-first roadmap: [the competitive roadmap §7](history/16-competitive-roadmap.md#7-sprint-plan).
  Multi-framework support (not just LangGraph) is the core promise of the product.
- **Before each sprint:** review the whole codebase for errors and fix them first. **After each sprint:** an
  end-to-end review (API and browser flows), fix, then merge.
- **Handover** (2026-10-08): keep this file updated at the end of every sprint.
- **Re-plan** (2026-10-09): the owner expects the canvas to *design* flows in any widely used framework and mix
  frameworks in one flow, not only run other frameworks' agents. They adopted the [framework coverage
  review](spec/17-framework-coverage-review.md): S17 = their workspace and editor findings, then
  S20–S22 framework design, old S17–S32 moved to S23–S36. They said to merge the plan PR but **not to start
  implementation yet**.
- **UI refresh and S16** (2026-10-08): the owner asked for a modern UI with light, medium and dark themes (done in
  #20), then "merge the pr and start the next sprint" (S16). #20 was merged on local checks while Actions had no
  runners, on the owner's explicit choice. Beyond S16, ask before starting the next sprint and before merging.
- **Node versions:** stay on Node LTS. Node 25 dropped corepack, which `infra/Dockerfile.web` uses (Dependabot #10 was closed for this).
- **Dependabot:** `@dependabot` commands must be typed by the owner; ones posted by an agent are ignored.
- **Merged branches** are deleted by the owner from the PR page (the cloud git proxy refuses remote branch deletion).
- **Build the remaining sprints one by one** (owner, 2026-10-09): S20, S21, S22, then S23 onward in the re-planned
  order, one PR per sprint.
- **Standing merge grant** (owner, 2026-10-09 14:14Z): "merge and continue the sprint dont wait for my decisions keep
  implementing the sprints. this is your flow. Review the code -> fix bugs if any found -> implement the next sprint
  -> test -> complete the sprint -> merge. do the review of the entire code base only after a major milestone else
  just implement the sprint merge and move on to the next sprint". So: per sprint a quick review and fixes, build,
  test, finish the docs, merge (merge commit) and start the next; a whole-codebase review only after a major
  milestone. Tags stay the owner's.

## 2. Repository map

```
apps/api/agentcanvas/      FastAPI control plane + runner (uv; Python 3.14 as in the image, from .python-version)
  api/v1/                  REST routes; protocols.py = MCP + A2A per deployment, traces.py = trace export settings
  adapters/langchain/      the only place LangChain/LangGraph may be imported (import-linter contract, ADR 0011)
  runtime/                 framework-neutral runner, run handles, finish hooks, masking
  copilot/                 AI Copilot (S14) + tests, debugging, guide search, notes, debug evals (S18)
  adapters/langchain/deploykit.py, kit/   export deploy kit: service template, guides, doctor/smoke/gate (S19)
  adapters/langchain/interop.py   agents from other frameworks, remote A2A and MCP agents as steps (S16; vendored into exports)
  agentspec.py             Open Agent Spec export/import (S16), pure dict mapping, no pyagentspec at runtime
  designs/                 S21: an Agent block as native code for 9 agent frameworks; export.py + kit/ (per-agent project)
  guardrails.py            S23: decision logic of the safety blocks (shipped in exports); evals in registry/guardrails/
  knowledge_search.py      S24: chunking, built-in embedder, BM25/cosine/hybrid search, retrieval metrics (shipped in exports)
  adapters/langchain/gateway.py   S26: model gateway (backup models, answer cache) of a model block; vendored into exports
  adapters/openai_agents/  S25: workflows as OpenAI Agents SDK code (codegen.py), its runtime (runtime.py, vendored into exports), packager.py
  adapters/langchain/parts.py + part_worker.py   S28: agents of frameworks that clash with LangGraph run as parts in their own environment (.frameworks/<id>, make framework FW=<id>); typed contracts
  adapters/crewai/         S27: workflows as native CrewAI flows (codegen.py); runtime.py + worker.py run in .frameworks/crewai (make crewai); packager.py
  adapters/google_adk/     S29: workflows as native ADK graph workflows (codegen.py); runtime.py + worker.py run in .frameworks/google_adk (make framework FW=google_adk); packager.py; server side shared with adapters/crewai
  adapters/supersteps.py   S32: the flow engine (FLOW of steps run in supersteps, pauses as JSON) for frameworks without a graph; flowgen.py (codegen + Dialect), flowworker.py, flowadapter.py, flowpackager.py are shared by:
  adapters/pydantic_ai/, adapters/agno/   S32: codegen.py (a Dialect), runtime.py (Run with the framework's agents, native tool approvals), worker.py, __init__.py (adapter); run in .frameworks/<id>
  adapters/microsoft_agent_framework/   S33–S34: the same four files for the Microsoft Agent Framework (chat middleware for limits, settings and scripted test replies)
  adapters/strands/, adapters/llamaindex/   S35: the same four files for Strands (AskBefore hook on interrupts) and LlamaIndex (FunctionAgent, HumanResponseEvent waits)
  adapters/mastra/         S36: TypeScript. codegen.py writes TS; flow.ts (engine twin), runtime.ts, worker.ts run on Node.js from .frameworks/mastra (make mastra; package.json pins npm packages); packager.py (TS project + python/sidecar.py)
  importers/               S31: Langflow, Flowise and n8n JSON to workflows (Builder + one module per builder); samples in tests/importers/
  bridges/                 S22: whole workflows to LangGraph/WayFlow/OpenAI Agents through Agent Spec; kit/flowkit.py
  services/                triggers, projects, trace_export.py (OTel spans), ...
  registry/                node manifests, providers.json, mcp_gallery.json
  ir/, validation/         Graph IR v1.1 and the validator
  db/                      SQLAlchemy models; migrations in apps/api/migrations (Alembic)
apps/api/tests/            pytest; test_auth.py lists public/own-credential routes (update it for new routes)
apps/api/scripts/          export_ir_schema.py, smoke_mcp_gallery.py, smoke_frameworks.py (S16), ...
apps/web/src/              React 19 + TS, React Flow, Zustand, TanStack Query
  api/generated.ts         generated from OpenAPI (`pnpm gen`), never edit by hand
  release/                 publish, deployments, ConnectSection (MCP/A2A snippets), AgentSpec.tsx (S16), Import.tsx (S31)
  playground/              S31: Playground page; editor/simple.ts = Simple mode
  theme/                   light / medium / dark / system themes (#20); colours are CSS tokens in styles.css
  observability/           TraceExportSettings (Setup page)
apps/web/e2e/              Playwright journeys
packages/ir-schema/        generated ir.schema.json, node-types.json, openapi.json (CI fails on drift)
infra/                     Dockerfiles, nginx, compose (local/prod), Helm chart, k6 load test, Grafana
docs/user-guide/           user and operator docs (MkDocs); copied into the Copilot's knowledge by `make schema`
docs/developer/            developer docs: README (map), this file, hardening.md (status), decisions.md, spec/,
                           build-guide/, adr/, history/
CHANGELOG.md               release notes ("Unreleased" at the top)
```

Read first: the [README](https://github.com/marunsct/AgentCanvas/blob/main/README.md), [hardening.md](hardening.md) (what is open), the [spec README](spec/README.md)
and the [ADRs](adr/README.md).

## 3. Setup and commands

```bash
make setup                 # uv sync + pnpm install
make dev-fake & make dev-web   # API with scripted models (no keys) + web on :5173
make docker-up             # whole app in Docker on http://localhost:7777 (no sign-in); WEB_PORT overrides
make check                 # lint, formats, types, import contract, most drift checks, tests, build; NOT the connectors, audit, frameworks, export or images jobs, and framework tests skip without their environments
make schema                # regenerate packages/ir-schema (then `cd apps/web && pnpm gen`)
```

API tests need Postgres (`service postgresql start` in a cloud container if it stopped):

```bash
cd apps/api && AGENTCANVAS_ENV=test \
  TEST_DATABASE_URL=postgresql://agentcanvas:agentcanvas@localhost:5432/agentcanvas uv run pytest -q
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports
```

Browser journeys (starts its own API and a production build):

```bash
cd apps/web && CI=1 PW_CHROMIUM_PATH=$(ls -d /opt/pw-browsers/chromium-*/chrome-linux*/chrome | head -1) \
  pnpm exec playwright test
```

**Common CI failure:** any change to a route, a docstring on a route, a Pydantic model or the API version changes
`openapi.json`. Run `make schema` and `pnpm gen` and commit the result, or the `api` job fails on drift.

## 4. CI

**CI runs locally and on GitHub.** `make ci` (check, strict docs, audit, Helm), `make ci-full` (also framework environments, the whole API
suite **on a throwaway Postgres container** when Docker is there (SQLite otherwise, with a warning), the `frameworks`
and `export` stages, images, e2e), `make hooks` (pre-push lint + web tests); all in `scripts/ci-local.sh`, which mirrors the
workflow. The GitHub workflow runs on every pull request and push to main, and by hand (runners came back by 2026-11-12). The `api` stage
includes the old `connectors` job (MCP gallery smoke, part environments). Only the Trivy scan and the prod-compose probes
of `images` exist just in the workflow. Helm runs from `alpine/helm` when it is not installed. Playwright: if its
browser build is not installed set `PW_CHROMIUM_PATH` to another Chromium; if port 8000 is taken (a running Docker app)
set `E2E_API_PORT`; if the dev server runs out of file watchers set `E2E_PROD=1` (the production build, as in CI).

`.github/workflows/ci.yml` jobs: `api` (lint, pyright, import contract, schema drift, pytest on Postgres+pgvector),
`web` (generated-type drift, typecheck, vitest, build), `e2e` (Playwright on the prod build under the strict CSP via
`vite preview`), `docs` (MkDocs build), `connectors` (gallery smoke, S15), `audit` (`scripts/audit-deps.sh`: the API, each framework environment's lock, Mastra, the web app),
`export` (S19: every template to every target, deploy files linted, each export's own tests, Docker Compose and
kind deployed for real), `frameworks` (S16: a real agent of each framework as a step against a fake model server;
Agent Spec exports loaded with pyagentspec), `images` (Docker builds with every framework and Mastra, Trivy, prod and local compose stacks answer through nginx, Helm lint/render). Playwright traces are uploaded on failure.

## 5. Conventions

- **One PR per piece of work** (a sprint used to be one), merged with a merge commit by the owner. PR body:
  Before/After paragraphs, then How, then local test results. The sprints S0–S36 are done; work since then is
  hardening passes and the items in [hardening.md](hardening.md).
- **Docs in the same PR:** a CHANGELOG line under "Unreleased"; [hardening.md](hardening.md) (delete the item you
  closed, add what you found); [decisions.md](decisions.md) for a judgment call; user guide pages for anything
  people see (+ `mkdocs.yml` nav, then `make schema` to refresh the Copilot's copy); this file when a convention,
  command or owner decision changes. [README](README.md#keeping-the-docs-current) has the rules.
- Bump the version for each release in `apps/api/pyproject.toml` (the API reports it from there), `apps/web/package.json`,
  the chart's `appVersion` and the `VERSION` argument of both Dockerfiles (`test_deploy_files.py` keeps them equal);
  regenerate the schema afterwards. A migration must leave the previous release working on the migrated database
  (expand, migrate, contract): the deploy guide's rollback runs the previous images on it.
- New public or own-credential routes must be added to the allowlists in `apps/api/tests/test_auth.py`.
  Admin-only routes go in the role exceptions in `api/deps.py`.
- LangChain/LangGraph imports only under `agentcanvas/adapters/` (`lint-imports` enforces it).
- A migration that adds an index to a large table (`run`, `thread`, `audit_log`, `event_log`, …) builds it
  concurrently on Postgres, inside `op.get_context().autocommit_block()` with `postgresql_concurrently=True`, after the
  migration's other changes; a plain CREATE INDEX blocks every write to the table while the old servers still serve
  (`tests/test_migration_locks.py` checks). Prefer a partial index when only some rows are read.
- Commit messages describe the change in plain words; no model names in commits, PRs or code.
- **Releases.** Tags (`vX.Y.Z`) and version bumps are the owner's. The plan for releases, not yet in place (no image is
  published yet): merging to `main` builds images tagged with the commit SHA; staging
  gets them and the Playwright smoke tests run against it; a tag promotes the same images to production (no rebuild),
  migrations first. A rollback redeploys the previous tag, so a migration must stay backward compatible for one
  release (expand, migrate, contract).

## 6. Cloud-session environment quirks

- Never `pkill -f` a pattern that appears in your own command line (it kills the shell). `ss` is missing; use `ps aux`.
- Git pushes occasionally fail with HTTP 500 from GitHub; retry with backoff, they go through.
- Docker: Docker Hub is rate-limited and Debian/ghcr are blocked. Start `dockerd` by hand and use `mirror.gcr.io`
  images (e.g. `POSTGRES_IMAGE=mirror.gcr.io/pgvector/pgvector:pg16`). Image builds that need apt/apk fail; layer
  code onto existing images or build stand-ins from `mirror.gcr.io` Python images with `--network host` and the proxy CA.
- The egress proxy blocks most remote MCP hosts and many vendor doc sites, so `smoke_mcp_gallery.py --live` and some
  research can't run here.
- A fresh clone needs `make crewai` (CrewAI in `apps/api/.frameworks/crewai`, gitignored) before the CrewAI tests,
  the conformance kit on CrewAI and the S27 browser journey run; without it they skip or fail with an install hint.
- Google ADK workflows (S29): `make framework FW=google_adk` (ADK in `apps/api/.frameworks/google_adk`, also used by
  ADK agents in mixed flows; the target now adds `cel-python`, `jinja2` and `jsonschema`, which the ADK runtime needs)
  before `tests/test_google_adk.py`, the kit on ADK and the S29 browser journey; an environment made by S28's
  `make framework` needs it run again.
- Mixed flows (S28): an agent designed for CrewAI, Google ADK, Agno, Strands or LlamaIndex runs only where its
  framework has an environment: `make framework FW=<id>` (CrewAI's is `make crewai`'s). `tests/test_parts.py`'s CrewAI
  cases and the Mixed-framework team template test skip without it; the S28 browser journey needs it.
- To deploy an export with Docker here: start `dockerd`, tag `mirror.gcr.io/astral/uv:0.11` as
  `ghcr.io/astral-sh/uv:0.11`, build a local `python:3.12-slim` from `mirror.gcr.io/library/python:3.12-slim` with
  `/root/.ccr/ca-bundle.crt` as `SSL_CERT_FILE`, then `docker build --network host --build-arg HTTPS_PROXY=...` the
  export's own Dockerfile and `docker compose ... up -d --no-build --wait` its compose file (as `make deploy` would).
- After a container restart: `service postgresql start`; run pyright as `uv run pyright` (or `.venv/bin/pyright
  --pythonpath .venv/bin/python`) and pytest with `PATH=$PWD/.venv/bin:$PATH` (the LangSmith sandbox test calls `python`).
- k6 lives at `~/go/bin/k6`; helm via `go install helm.sh/helm/v3/cmd/helm@v3.16.2`.

## 7. Before a PR is merged

1. A review of the code the work touched, and its fixes; for a large piece, an end-to-end pass through the API and
   the browser.
2. All suites green locally (`make check`, and `scripts/ci-local.sh` stages for what the change touches: `api` on
   Postgres, `e2e`, `frameworks`, `export`, `images`), and the GitHub workflow green on the pull request.
3. CHANGELOG, [hardening.md](hardening.md), the user guide and `mkdocs.yml` updated; schema regenerated
   (`make schema`, then `pnpm gen`).
4. This file updated if the owner decided something new or a command or convention changed.
5. Reply in the project thread with the PR link and what the owner needs to do (merge, tags).
