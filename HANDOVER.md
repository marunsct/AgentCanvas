# AgentCanvas — handover

Everything an agent or a developer needs to pick this project up in a new session, or as a different person.
**Keep it current: rewrite the "Current state" section and tick the checklist at the end of every sprint** (see
[Sprint-end checklist](#11-sprint-end-checklist)). If this file and another document disagree, the more recently
updated one wins; fix the other in the same PR.

- **Last updated:** 2026-10-18, v0.29.0 (the rest of the deep review: paused runs in the database, engine fixes, server and worker hardening; #52). Before that: 2026-10-18, v0.28.1 (first round of review fixes, #51). Previous update: 2026-10-17, v0.28.0 (AgentCanvas on Matura: workspaces, row-level security, portal links, audit sink; the rest of Matura sprint TS26 that fits today), merged in #50. Before that: 2026-10-17, v0.27.0 (sign-in through Matura, the owner's identity and tenancy platform, marunsct/Platform sprint TS9a), after the owner asked Matura to start with AgentCanvas. Previous update: 2026-10-14, v0.26.3 (Docker app builds every framework by default).
- **Repository:** https://github.com/marunsct/AgentCanvas (default branch `main`).

---

## 1. Current state

| | |
|---|---|
| Last work done | **Deep review, round 2 → v0.29.0** (#52, branch `claude/review-followups`, rebased on v0.28.2 (#53: keys for local model servers, Find models on the server, the sandbox child's stand-in `agentcanvas.sdk`) and merged on 2026-10-19 at the owner's request to review and merge all pending branches): paused runs of non-LangGraph frameworks in `thread.paused_state` (migration `b8d2f6a0c4e7`, `runtime/paused.py`); the OpenAI Agents SDK and Echo engines get the supersteps join/resume fixes; a run claims its thread in the database; `TenantSession` narrows every session of a Matura request; workspace saves off the event loop; regex pool retries innocent searches; 413 over 20 MB; E999 and P007; IR migration registry; worker env allowlist, robust reads, async writes, 10-minute idle limit, Mastra restart after 500 flows; non-dumpable API with subprocess sandboxes; CI permissions, concurrency, timeouts and a database-role check. Tested on 2026-10-19 after the rebase, against Postgres: API suite 1131 passed, 286 skipped, 1 xfail; the only failures (96) are the conformance kit on frameworks whose environments were not installed in that session (`make crewai`, `make framework FW=…`, `make mastra`); `test_review_followups.py` 45 passed; ruff clean; the migration upgrades, downgrades and upgrades again |
| v0.28.1 | **Deep review, round 1** (#51, merged `43b4f3d`): workspace code always in the sandbox, a run gate for E041/E042/E043/E080, the API's own database role (`db/app_role.py`), run-scoped pause ids, safe hand-over, web edits and streams. Review and plan: Claude doc "AgentCanvas — Deep Review and Improvement Plan" |
| v0.28.0 | **AgentCanvas on Matura → v0.28.0** (#50, merged `2f571a2`): projects can belong to a Matura workspace (`project.workspace_id`, `GET /v1/workspaces`, `?workspace_id=` filter, `PUT /v1/projects/{id}/workspace`, picker on the home page); forced Postgres row-level security on the 35 organisation-owned tables (`db/rls.py`, migration `a3c7e9b1d5f2`); `/v1/me` `portal_url`; `AuditSink`; tokens with `pmode` other than `shared` get 503 |
| Before that | **Sign-in through Matura → v0.27.0** (#49, merged `1b2e186`): `AGENTCANVAS_AUTH_MODE=matura`, permissions to roles, contract tables, webhooks and change feed, organisation picker |
| Earlier | **Every framework in the Docker app by default → v0.26.3**; Review after the competitive roadmap → v0.26.1 ([#41](https://github.com/marunsct/AgentCanvas/pull/41), merged `f5786d1`, 40 fixes); S36 Mastra → v0.26.0 ([#40](https://github.com/marunsct/AgentCanvas/pull/40), merged `0334885`); S35 Strands and LlamaIndex Workflows flows → v0.25.0 ([#39](https://github.com/marunsct/AgentCanvas/pull/39), merged `f813972`); S33–S34 Microsoft Agent Framework flows → v0.24.0 (#38, `8e307ad`); S32 Pydantic AI and Agno flows → v0.23.0 (#37, `63be5ca`) |
| Version in code | API `0.32.1` (`apps/api/agentcanvas/api/main.py`; `pyproject.toml`, `uv.lock`, `package.json`, `openapi.json` and Helm `appVersion` follow it), CHANGELOG top entry v0.32.1 |
| Git tags | **none pushed.** v0.1.0 … v0.29.0 are all left to the owner (see §2) |
| Next sprint | **Proposed (owner to decide):** restore CI runners and run every suite on v0.29.0 first (nothing since v0.27.0 has run in CI). Then the review plan's Phase 3 (consolidation: engine and packager duplication, `ResourceResolver`, split `nodes.py`) and the web items in §9, then real users before the GA backlog |
| Work status | #51 (v0.28.1), #53 (v0.28.2) and #52 (v0.29.0) merged; the API suite passes (see above). Not run yet: pyright, the web unit tests, the e2e suite and the framework conformance kit (no npm or framework environments in the cloud session). **Run `make check`, `make e2e` and the kit with every framework before building on them**, and try `infra/compose.prod.yml` with a fresh and an existing volume (the new API database role) |
| Open PRs | #36 (the identity and tenancy plan, doc 18 and ADR 0019, already built as Matura: a draft, docs only, for the owner to merge); Dependabot #48 (Node 26: not mergeable as is, Node 25+ has no corepack, which `Dockerfile.web` uses) and #45 (Python 3.14 in the API image: needs an image build with every framework first). #44, #46, #47 merged on 2026-10-19 |
| CI | **GitHub Actions assigned no runners from 2026-10-08 05:51Z** (every job fails in about 2 seconds with no logs, `main` included), most likely the account's Actions minutes or spending limit. The owner must fix it in GitHub billing settings. #20 was merged on local checks at the owner's choice. Until Actions runs again, run the suites below locally before merging |

**Test totals after v0.26.2** (all green locally):

| Suite | Count | Command |
|---|---|---|
| API (pytest, Postgres) | 1467 passed, 1 xfail (known upstream canary), 0–1 skipped (the Docker sandbox test needs a running dockerd); the CrewAI tests, the kit on CrewAI and the CrewAI parts (S28) need `make crewai`, the ADK tests and the kit on ADK `make framework FW=google_adk`, the Pydantic AI, Agno, Agent Framework, Strands and LlamaIndex tests and the kit on them `make framework FW=<id>` for each, the Mastra tests and the kit on Mastra `make mastra` (Node.js 22.18+) | see §5 |
| Web unit (vitest) | 103 | `cd apps/web && pnpm exec vitest run` |
| Browser journeys (Playwright, prod build under the prod CSP) | 71 (the S27 and S28 journeys need `make crewai`, the S29 journey `make framework FW=google_adk`, the S32 journeys `FW=pydantic_ai` and `FW=agno`, the S33 journey `FW=microsoft_agent_framework`, the S35 journeys `FW=strands` and `FW=llamaindex`, the S36 journey `make mastra`) | see §5 |
| Real frameworks (S16, S21, S22) | 9 frameworks: wrapped agent, designed agent (plain, typed, approval), its export's tests; Agent Spec check; whole-flow exports (`bridge_langgraph`, `bridge_wayflow`, `bridge_openai_agents`): every template's export tests pass, 4/4/2 run end to end | `cd apps/api && uv run --with <package> python scripts/smoke_frameworks.py <framework>` (packages in `ci.yml` job `frameworks`) |
| Exported templates, each export's own tests | 27 templates on LangGraph (the Mixed-framework team export also deployed with Docker Compose and passed `make smoke`, S28), and the 22 each that the OpenAI Agents SDK (S25), CrewAI (S27), Google ADK (S29), Pydantic AI and Agno (S32) the Microsoft Agent Framework (S33–S34), Strands and LlamaIndex (S35) can run, and the 22 Mastra (S36) can run as TypeScript projects (`npm test` in each), in `<dir>/<framework>/*/*` (their tests pass; CI runs them) | `uv run python scripts/export_templates.py <dir>`, then `make test` in each `<dir>/local/*/*` (it installs the parts) |
| MCP gallery smoke | 30/30 | `cd apps/api && uv run python scripts/smoke_mcp_gallery.py` |

## 2. Owner decisions and working agreements

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
- **Sprint order** is the approved value-first roadmap: [docs/agent-canvas-spec/16-competitive-roadmap.md §7](docs/agent-canvas-spec/16-competitive-roadmap.md#7-sprint-plan).
  Multi-framework support (not just LangGraph) is the core promise of the product.
- **Before each sprint:** review the whole codebase for errors and fix them first. **After each sprint:** an
  end-to-end review (API and browser flows), fix, then merge.
- **Handover** (2026-10-08): keep this file updated at the end of every sprint.
- **Re-plan** (2026-10-09): the owner expects the canvas to *design* flows in any widely used framework and mix
  frameworks in one flow, not only run other frameworks' agents. They adopted the [framework coverage
  review](docs/agent-canvas-spec/17-framework-coverage-review.md): S17 = their workspace and editor findings, then
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

## 3. Unmerged work

None. The S18/S19 drafts that used to live only in a git bundle
(`/mnt/project-files/agentcanvas-handover/unmerged-sprint-work.bundle`) were finished and merged in #19; the bundle
is kept only as a record.

## 4. Repository map

```
apps/api/agentcanvas/      FastAPI control plane + runner (Python 3.12, uv)
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
docs/agent-canvas-spec/    spec 01–16, build guide (13-sprint-plan.md = plan + execution log), adapter specs
docs/user-guide/           end-user docs (MkDocs); docs/adr/ = ADRs 0001–0017
PROGRESS.md, CHANGELOG.md  live status and release notes
```

Product docs to read first: [README](README.md), [roadmap](docs/agent-canvas-spec/16-competitive-roadmap.md),
[sprint plan](docs/agent-canvas-spec/build-guide/13-sprint-plan.md), [PROGRESS](PROGRESS.md), [ADRs](docs/adr/README.md).

## 5. Setup and commands

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

## 6. CI

**CI runs locally.** `make ci` (check, strict docs, audit, Helm), `make ci-full` (also framework environments, the whole API
suite **on a throwaway Postgres container** when Docker is there (SQLite otherwise, with a warning), the `frameworks`
and `export` stages, images, e2e), `make hooks` (pre-push lint + web tests); all in `scripts/ci-local.sh`, which mirrors the
workflow. The GitHub workflow is `workflow_dispatch` only (Actions has had no runners since 2026-10-08). The `api` stage
includes the old `connectors` job (MCP gallery smoke, part environments). Only the Trivy scan and the prod-compose probes
of `images` exist just in the workflow. Helm runs from `alpine/helm` when it is not installed. Playwright: if its
browser build is not installed set `PW_CHROMIUM_PATH` to another Chromium; if port 8000 is taken (a running Docker app)
set `E2E_API_PORT`; if the dev server runs out of file watchers set `E2E_PROD=1` (the production build, as in CI).

`.github/workflows/ci.yml` jobs: `api` (lint, pyright, import contract, schema drift, pytest on Postgres+pgvector),
`web` (generated-type drift, typecheck, vitest, build), `e2e` (Playwright on the prod build under the strict CSP via
`vite preview`), `docs` (MkDocs build), `connectors` (gallery smoke, S15), `audit` (pip-audit, pnpm audit),
`export` (S19: every template to every target, deploy files linted, each export's own tests, Docker Compose and
kind deployed for real), `frameworks` (S16: a real agent of each framework as a step against a fake model server;
Agent Spec exports loaded with pyagentspec), `images` (Docker builds, Trivy, prod and local compose stacks answer through nginx, Helm lint/render). Playwright traces are uploaded on failure.

## 7. Conventions

- **One PR per sprint**, title `Sprint N: <theme> -> vX.Y.0`, merged with a merge commit. PR body: Before/After
  paragraphs, then How, then local test results.
- **Docs in the same PR:** CHANGELOG (new version section), PROGRESS (table row + sprint section), the sprint plan's
  execution log and §S15-onward entry, user guide pages for user-visible features (+ `mkdocs.yml` nav), and this file.
- Bump the API version in `api/main.py` for each release; regenerate the schema afterwards.
- New public or own-credential routes must be added to the allowlists in `apps/api/tests/test_auth.py`.
  Admin-only routes go in the role exceptions in `api/deps.py`.
- LangChain/LangGraph imports only under `agentcanvas/adapters/` (`lint-imports` enforces it).
- Commit messages describe the change in plain words; no model names in commits, PRs or code.

## 8. Cloud-session environment quirks

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

## 9. Known gaps and deferred items

The hardening checklist (done and open, with versions) is `HARDENING.md`; update it in every hardening PR.

- **Matura TS26 left out (v0.28.0):** schema- or database-per-tenant placement (AgentCanvas has one engine and
  background jobs that work across organisations; shared mode only, `pmode` other than `shared` → 503); workspaces
  only group projects (no per-workspace roles: grants in non-default workspaces still do not count, and the token's
  `wid` is not used); Matura has no audit ingest API, so `MaturaWebhookSink` (`AGENTCANVAS_MATURA_AUDIT_URL`) is an
  untested placeholder; the `matura_*` contract tables have no RLS in AgentCanvas (its one API role writes them for
  every tenant and the organisation picker reads all of a user's memberships); `organization`, `app_user`,
  `membership` and `rate_window` are exempt from RLS (reasons in `db/rls.py`), LangGraph's checkpoint/store tables
  are not covered; portal links follow Matura's portal routes (`<public URL>/portal/t/<tenant id>/members`, from
  Platform `web/portal/src/app/router.tsx`). Members are not migrated from `oidc` mode; agent identity, backup, privacy and the
  AI readiness pack of TS26 are not started.
- **Open from the 2026-10-18 review** (details in the review doc; fixed items are in CHANGELOG v0.28.1 and v0.29.0):
  the `subprocess` sandbox is limits only (same user, same files; v0.29.0 hides the API's memory and environ), so
  (none: the production compose file and Helm chart now default to the sandbox service, `script_execution=remote`, v0.31.0; `subprocess` still warns at start-up; the service needs an internal network, so egress allow-lists stay LangSmith-only; run `make` prod compose and `helm template` to check it, I could not here); RLS policies on child tables nest 3–4 EXISTS levels (EXPLAIN
  the hot list queries with a tenant set); the RLS migration locks 35 tables at once; duplicated engine, worker and packager code (about 1,000–1,300 lines,
  Phase 3); `Resources` is still LangChain's object for every adapter; web: session and project in module variables
  (`ME` defaults to admin until `/me` answers), hand-written response types, keyboard access is done for the palette and ports but not yet for creating edges by drag or the inspector forms, no ESLint (`@rjsf/validator-ajv8` looks unused but `@rjsf/core` imports it: keep it);
  CI has no framework-environment cache and no Mastra `tsc`; framework pins live in four places; the Mastra npm set
  has no lockfile.
- **Release tags** v0.1.0 to v0.29.0 not created (owner).
- **Left from the review after the roadmap (v0.26.1):** Home opens an imported file straight away when nothing needs checking, so its "Everything carried over." note never shows; node policies (retry, timeout, error route) are ignored without
  a warning on the flow-engine frameworks, CrewAI and ADK; personal-data patterns stop after 1,000 matches; Mastra
  templates still differ from Jinja on negative indexes, slices and Python string methods; tools wrapped for dry runs
  on LangChain designed agents are matched by tool name only in *Ask before using*.
- **S36 limits:** Mastra flows run on `flow.ts`, the TypeScript twin of the flow engine, not as Mastra's own
  `createWorkflow` chain (the "Full" tier, if users ask). There is no JSON-RPC Node Adapter Host with
  `lower/generate/start` methods as the adapter spec sketched: the server generates the TypeScript and the Node.js
  worker speaks the flow workers' JSON lines. Approvals use Mastra's `requireApproval`; the pause keeps every workflow
  snapshot of an in-memory store (about 26 KB) and writes them into a fresh store to continue. Mastra suspends at
  each call that needs approval, so calls are asked about one at a time. `flow.ts` must stay in step with
  `supersteps.py` (the conformance kit and `tests/test_mastra.py` check both). Prompts render with Nunjucks (no
  negative list indexes; objects print as JSON where Jinja prints Python reprs). Workspace code stays Python (a helper
  runs it in exports). The API image has no Node.js. Typed checks: `tsc` (5.9, with `@types/node`) passes on the
  exported project; it is not part of CI.
- **S35 limits:** Strands and LlamaIndex flows run on the S32 flow engine, not a Strands `GraphBuilder` graph or one
  LlamaIndex `Workflow` with an event per edge. Strands' resume sets the agent's private `_interrupt_state` from the
  saved dict (what its session managers do). LlamaIndex does not serialize an agent's chat memory with its `Context`,
  so the runtime saves it beside and sets it again after `agent.run(ctx=...)`; its approvals come one at a time, keyed
  by tool name and arguments (two identical calls in one turn share an answer). A typed answer written as JSON text
  (scripted replies) is read from the text when the framework's answer tool is not used.
- **S33–S34 limits:** Agent Framework flows run on the S32 flow engine, not as the framework's own `WorkflowBuilder`
  graph with `request_info` and checkpoints (the roadmap's "Full" tier, if users ask). The framework asks about every
  call in a model turn once one tool needs approval, so the runtime approves the other calls itself. It binds an
  approval to the call as the model made it (a function middleware that rewrites the arguments makes it drop the
  approval), so edited arguments are applied by a wrapper around the tool. A rejected call reaches the agent as the
  framework's fixed "rejected by user" message, not the person's reason.
- **S32 limits:** Pydantic AI and Agno flows run like the CrewAI and ADK ones (a worker from `.frameworks/<id>`, models
  through the model bridge, a paused run in the API's memory). The flow shape is AgentCanvas's own engine
  (`supersteps`), not `pydantic_graph` or Agno `Workflow` steps: neither maps the canvas's cycles and joins one to one.
  An agent is asked the conversation as one text (earlier turns quoted before the latest message), not as a message
  history. Agno has no model call limit of its own, so the runtime counts the agent's model calls (`model.ainvoke`).
  The OpenAI Agents SDK runtime (S25) still has its own copy of the superstep engine; it could move onto
  `supersteps.py`.
- **S31 limits:** the importers read each builder's export format as documented and as the hand-written samples in
  `apps/api/tests/importers/` show; real exports from newer builder versions may name components differently (unknown
  ones become placeholders, never errors). Langflow custom components, loops and multi-output components, n8n
  expressions other than `$json.<field>` / `$('Node').item.json.<field>`, n8n webhook and schedule triggers (input
  fields instead), and tools other than HTTP requests are listed in the report, not converted. Push to GitHub needs a
  token in a project secret (no GitHub App sign-in) and pushes at most 500 files. Simple mode is remembered per
  browser (localStorage), not per user.
- **S29–S30 limits:** ADK workflows run like CrewAI flows: one worker process from `apps/api/.frameworks/google_adk`,
  models through LiteLLM and the model bridge, a paused run in the API's memory continued by replay (a parallel branch
  that had not finished when another paused runs again). Tool approvals re-run the agent's turn up to the call (with
  a real model the model is asked again; with scripted replies the same calls come back), and are the runtime's
  wrapper rather than ADK's `require_confirmation`/`ToolConfirmation`. A step reached from two parallel branches
  without Wait for all runs once per branch (ADK's semantics; LangGraph merges branches that finish in the same
  step). Not yet: `adk.*` dialect blocks (sequential/parallel/loop agents, code executor, Google Search), YAML
  `AgentConfig` import, ADK's resumable sessions (`DatabaseSessionService`), `adk web`/`adk deploy` on the export
  (its steps need `rt.run`), backup models and the answer cache in exports, maps, subflows and the other
  LangGraph-only blocks. `--build-arg WITH_GOOGLE_ADK=true` is untested here (image builds fail in this environment).
- **S28 limits:** contracts cover what crosses into a designed agent (the conversation, as text) and back (its
  structured output or text), not arbitrary data edges between parts. One worker process per framework environment
  serves all runs of a server (started on first use, restarted if it died), with the server's own trust (no extra
  sandbox). Wrapped black-box agents (`agent.framework`) of clashing frameworks still install in the host and so
  conflict in an export. A2A parts are called and listed, not generated (no TypeScript sidecar). `own_env` is a fixed
  flag per framework, proved by `scripts/check_part_envs.py` against the current pins (CI api job): re-run it when a
  pin changes. The API image includes part environments only on request (`WITH_CREWAI`, `WITH_GOOGLE_ADK`).
- **S27 limits:** CrewAI cannot be installed next to the API (it pins `openai<3`; the API needs `openai>=3` for the
  OpenAI Agents SDK), so CrewAI flows run in one long-lived worker process started from `apps/api/.frameworks/crewai`
  (`make crewai`, or `AGENTCANVAS_CREWAI_PYTHON`; the API image only with `--build-arg WITH_CREWAI=true`, untested
  here because image builds fail in this environment). The worker starts on the first CrewAI run (about 4 s) and is
  restarted if it dies. A paused run lives in the API process's memory (as on the SDK); continuing it starts the flow
  again from the saved fields and replays the finished steps from the record, so a parallel branch that had not
  finished when another branch paused runs again. Not on CrewAI yet: asking before a tool call (refused with P001),
  `FlowDefinition` import, `@human_feedback` outcomes, CrewAI memory, knowledge and `@persist` blocks, backup models
  and the answer cache in exports (inside AgentCanvas they work through the bridge), and the same blocks the SDK lacks.
  Mock replies are per conversation, so a crew's tasks (each a fresh conversation) all get the first scripted reply.
  pyright skips `runtime.py` and `worker.py` (they import CrewAI); ruff checks them. Validation reports P003 on even
  more workflows now (three frameworks installed).
- **S26 limits:** the answer cache lives in each API instance's memory (per project, 1,000 answers): replicas do not
  share it and a restart empties it; OpenAI Agents SDK exports keep backup models but not the cache. Day and month
  spend counts finished runs plus the spend so far of runs still going on the same API instance (v0.26.1); runs on
  other replicas are counted only once they finish, so with several replicas a budget can still be overshot a little;
  periods are UTC. A backup model takes the block's temperature, output limit and timeout but not its address or key (its
  provider's own key is used). The older `mw.model_fallback` add-on stays for LangGraph agents.
- **S25 limits:** the OpenAI Agents SDK adapter declares no optional abilities: no saved history, state editing,
  replay, breakpoints or long-term memory, and a paused run lives in the API process's memory as JSON (a restart or
  a second API replica loses it; exports keep it in `.runs/`). An edited tool call is sent back to the agent to make
  again (the SDK runs calls as the model made them); the re-made call with exactly the edited arguments is approved
  automatically. Not yet on the SDK: Planning Agents, maps, subflows, memory blocks, HTTP and MCP tools, guard
  blocks, add-ons other than the model call limit, and the spec's `openai.*` dialect nodes (handoffs, guardrails,
  hosted tools, sessions). Exports search a knowledge base's file with the built-in embedder (a provider-embedded
  one falls back to keyword matching). Validation now reports P003 on most workflows (two frameworks installed).
- **S24 limits:** search runs in memory per API instance (up to 50,000 chunks per knowledge base, cached by
  `version`); pgvector is not used yet (vectors are float32 bytes in `knowledge_chunk.vector`), so a very large
  corpus needs that follow-up. The built-in embedder matches word forms and misspellings, not synonyms: hybrid
  beats keyword on the sample by a small margin (recall@3 0.95 vs 0.93); provider embedding models are wired
  (`knowledge_embedder` in nodes.py, via `init_embeddings`) but not exercised in CI. No Google Drive or other
  connector ingestion (upload the files, or use an MCP server on an agent); no OCR for scanned PDFs; web pages are
  fetched once (no re-crawl); a document's text is stored in `knowledge_document.content`. The Search knowledge
  block names its knowledge base as free text (no picker; a missing one fails the run with a clear message). The
  embedding provider's API key must be a `{"$secret": …}` reference or the provider's usual project secret.
- **S23 limits:** a share link is the credential (stored in plain text so builders can copy it again; regenerate
  it if logs leak, since it appears in URL paths); `/s/` pages can be framed by any site until the builder lists sites
  (`deployment_share.frame_origins`; nginx asks `GET /v1/share/{token}/frame` through `auth_request` and sets
  frame-ancestors per link; unlisted = `*` so existing widgets keep working); abuse protection is the deployment's and project's rate limits plus nginx's per-IP limit, so
  a busy public link can use up the limit API-key callers share; visitors see streamed text from every AI step
  while a run is going, then the extracted answer; no history after a reload; the page sends one text field, so
  workflows with other required inputs fail with a generic error; the widget panel has a fixed size and no
  postMessage. Safety blocks: the fixed checks are pattern lists that new wording or other languages beat (the
  moderation phrase list is a fallback, not a moderator) and the model path's quality is not measured in CI; PII
  misses 7-digit local phone numbers, SSNs without separators and spelled-out emails, counts `1.2.3.4` as an IP,
  hashes without salt, and searches custom patterns in the first 100,000 characters; redacting in place is wrong
  for list fields. Bridges and Agent Spec treat safety blocks as gaps; Echo does not run them. Templates: keep them
  single-path (pyagentspec cannot load parallel branches); a new template needs an INPUTS entry in
  `tests/test_templates.py`, a category from `services/workflows.GALLERY_CATEGORIES`, a line in
  `docs/user-guide/templates.md` and a pass of the bridge smoke checks; web_research is the only template that
  calls out (Wikipedia), behind an approval. Playwright uses fixed ports 8000/5173 and `apps/api/agentcanvas.db`,
  so only one e2e run at a time.
- **S22 limits (preview):** whole-flow exports go through Agent Spec, so routers with rules beyond `state.x == "v"`,
  steps side by side, subflows, and steps only AgentCanvas runs (memory, map, connectors; written by hand in
  `steps.py`) block running there; agents lose their tools; approvals become text questions; nothing reports back
  to AgentCanvas. CrewAI (no loader), AutoGen and Microsoft Agent Framework (agents only) are not offered. The
  OpenAI Agents SDK path uses code pyagentspec generates at start-up, which takes AI steps, agents and simple
  paths only, with one model client for all models. `pyagentspec[langgraph]` and `wayflowcore` cannot be installed
  together (anyio pins), so each export installs one.
- **S21 limits:** designed agents run only on the LangGraph host (ability `agent_designs`); their model calls go
  through the local bridge, so a framework's provider-specific features (hosted tools, built-in web search, prompt
  caching knobs) are not used; add-ons (middleware) and tool kinds other than workspace Python, HTTP and MCP are
  P005; on a tool approval the agent step reruns from its start after resume (tools without approval run twice), and the
  decision only applies if the rerun asks for exactly the same call, which the server remembers in its own process
  (resumed in another process, the call is refused and the agent says so; the memory holds at most 10,000 pauses);
  structured output uses each framework's own mechanism, so typed answers can differ slightly. In this cloud sandbox
  the Claude Agent SDK must be tested under `env -i` (the session's own Claude env vars leak into its CLI).
- **S20 limits:** model and tool resolution (`Resources`) is still the LangChain adapter's object handed to every
  adapter, and long-term memory, MCP browsing and judges stay on the host adapter; a block's `engine` is recorded
  and checked; S21 made an Agent block designed for an agent framework run as that framework; the conformance kit has no
  tool-calling agent, subgraph or cross-process resume cases yet; Echo is a demo (no deploy kit).
- **S17 limits:** event handlers and secrets stay per project; custom block types written in a workflow's folder are
  registered for the whole project; editing a helper module does not reload the files that import it until they
  change too; a workflow's folder is named after its id (`flows/wf_…/`), and code a workflow imports from another
  workflow's folder is bundled but not shown in its Workspace. The per-workflow folder is `flows/`, not
  `workflows/`, because LlamaIndex Workflows installs a top-level `workflows` package; keep new workspace root
  folders clear of installed package names.
- **GitHub Actions** had no runners from 2026-10-08 05:51Z (billing or spending limit, owner to fix); #20 and the
  S16 work were verified locally only.
- **S16 limits:** a wrapped agent is a black box (its own tools are not gated one by one; declare its side effects);
  Agent Spec files are JSON only; routers export as branches only for `state.x == "v"` rules on one field; agents'
  tools and middleware are not exported to Agent Spec; the framework CI job uses a fake model server. The A2A step
  remembers remote replies in the server process only: after a restart, a step paused on a remote question stops
  with a clear error instead of re-sending (it reads LangGraph's private `__pregel_scratchpad` to notice; the
  `test_a2a_stops_instead_of_resending_after_a_restart` test catches an upstream change).
- **S9 exit criterion** (10 design partners finish J1 unaided) needs real users.
- **MCP endpoint nits left as is:** no `Origin` header validation (the endpoint needs a bearer key anyway) and the
  `MCP-Protocol-Version` header is not checked. A2A supports 0.3 method names only (`message/send`, `tasks/get`); no streaming.
- **S18 limits:** Copilot test runs are dry runs only; the debug eval suite has only run with a scripted model
  (no provider keys here); explanations of past runs use the run's pinned version.
- **S19 limits:** the exported service has the deployment API (runs, resume, feedback) but not the MCP and A2A
  addresses; feedback is logged (and written to `FEEDBACK_FILE`), not stored; a run stops at `RUN_TIMEOUT_SECONDS`
  (default 300) where AgentCanvas keeps it running; cloud targets (Cloud Run, AWS, Azure) are syntax-checked in CI
  only, since CI has no cloud accounts.
- **Phone layout:** the editor stacks into one column below 700px (S15 review); it is usable but not designed.
- **MCP gallery:** key-request and some docs links were not opened from the sandbox; Google Drive needs a browser
  sign-in on first run.
- **GA backlog** and remaining FR-X items: PROGRESS.md "GA backlog".

## 10. History

| Sprint / work | PR | Merge commit | Release |
|---|---|---|---|
| S0–S8 | #1 | — | — |
| S9 MVP hardening | #2 | `5650f01` | v0.1.0 |
| S10 Accounts & access | #3 | `03ccb7e` | v0.2.0 |
| S11 Production readiness | #4 | `d7fcd8d` | v0.3.0 |
| S12 Evaluation Studio | #5 | `f6049eb` | v0.4.0 |
| S13 Evaluation in production | #8 | `d6af4da` | v0.5.0 |
| User guide, any-provider models, setup page, Docker app | #9, #12, #13 | `6dcce81`, `e2954e3`, `90e97f4` | — |
| Beginner-friendly canvas | #14 | `6d0c442` | — |
| Competitive roadmap (sprint order S15–S32) | #16 | `6df2886` | — |
| S14 AI Copilot I | #15 | `958f9cd` | v0.6.0 |
| S15 Open doors + end-to-end review fixes | #17 | `19de2dc` | v0.7.0 |
| Handover document | #18 | `9c9ef6e` | — |
| S18 AI Copilot II and debug assistant + S19 Export deploy kit | #19 | `7cca0c6` | v0.8.0, v0.9.0 |
| UI refresh: themes, restyle, Copilot chat layout | #20 | `24a63fe` | (in v0.10.0) |
| S16 Any framework as a node | #21 | `25be73e` | v0.10.0 |
| Framework coverage review and re-plan (S17, S20–S36) | #22 | `9140ef9` | — |
| S17 Workspace and editor fixes | #23 | `cd8c770` | v0.11.0 |
| S20 Framework foundation | #24 | `e92f164` | v0.12.0 |
| S21 Design any agent | #25 | `f308699` | v0.13.0 |
| S22 Whole flows, preview | #26 | `9e741e3` | v0.14.0 |
| Milestone review after S20–S22 (19 fixes) | #27 | `54d2673` | (in v0.15.0) |
| S23 Ship to users | #28 | `820312e` | v0.15.0 |
| S24 Knowledge Studio | #29 | `2db7871` | v0.16.0 |
| S25 OpenAI Agents SDK flows | #30 | `2e2eeb5` | v0.17.0 |
| S26 Model gateway and cost controls | #31 | `5832c26` | v0.18.0 |
| S27 CrewAI flows | #32 | `43d4a71` | v0.19.0 |
| S28 Mixed-flow export | #33 | `b8b2e01` | v0.20.0 |
| S29–S30 Google ADK flows | #34 | `c038d0e` | v0.21.0 |
| S31 Onboarding and switching | #35 | `2e10fff` | v0.22.0 |
| S32 Pydantic AI and Agno flows | #37 | `63be5ca` | v0.23.0 |
| S33–S34 Microsoft Agent Framework flows | #38 | `8e307ad` | v0.24.0 |
| S35 Strands and LlamaIndex Workflows flows | #39 | `f813972` | v0.25.0 |
| S36 Mastra | #40 | `0334885` | v0.26.0 |
| Review after the competitive roadmap (40 fixes) | [#41](https://github.com/marunsct/AgentCanvas/pull/41) | `f5786d1` | v0.26.1 |
| Framework setup, export to any framework, Full mode fits | [#42](https://github.com/marunsct/AgentCanvas/pull/42) | `df98d9d` | v0.26.2 |
| Every framework in the Docker app by default | #43 | `f511959` | v0.26.3 |
| Sign-in through Matura (Matura sprint TS9a) | #49 | `1b2e186` | v0.27.0 |
| AgentCanvas on Matura (Matura sprint TS26) | #50 | `2f571a2` | v0.28.0 |
| Deep review fixes, round 1 | #51 | `43b4f3d` | v0.28.1 |
| Deep review fixes, round 2 | #52 | (after merge) | v0.29.0 |

## 11. Sprint-end checklist

Do these before the sprint PR is merged, in the same PR:

1. Pre-sprint review done and its fixes listed in the PR; post-sprint end-to-end review (API + browser) done and fixed.
2. All suites green locally and in CI (numbers into §1).
3. CHANGELOG, PROGRESS, sprint plan execution log and user guide updated; schema regenerated.
4. **This file:** update "Last updated", §1 Current state, §2 if the owner decided anything new, §3 if unmerged
   work changed, §9 known gaps, §10 history row (fill the merge commit after the merge in the next PR or a follow-up).
5. Reply in the project thread with the PR link and what the owner needs to do (merge, tags).
