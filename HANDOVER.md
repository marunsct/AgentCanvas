# AgentCanvas — handover

Everything an agent or a developer needs to pick this project up in a new session, or as a different person.
**Keep it current: rewrite the "Current state" section and tick the checklist at the end of every sprint** (see
[Sprint-end checklist](#11-sprint-end-checklist)). If this file and another document disagree, the more recently
updated one wins; fix the other in the same PR.

- **Last updated:** 2026-10-08, after Sprint 15 (merged in [#17](https://github.com/marunsct/AgentCanvas/pull/17), merge commit `19de2dc`).
- **Repository:** https://github.com/marunsct/AgentCanvas (default branch `main`).

---

## 1. Current state

| | |
|---|---|
| Last sprint done | **S15 Open doors → v0.7.0**: deployments as MCP servers and A2A agents, 30-connector MCP gallery, OpenTelemetry trace export (Langfuse, Phoenix, Datadog, any OTLP), plus the post-sprint end-to-end review fixes |
| Version in code | API `0.7.0` (`apps/api/agentcanvas/api/main.py`), CHANGELOG top entry v0.7.0 |
| Git tags | **none pushed.** v0.1.0 … v0.7.0 are all left to the owner (see §2) |
| Next sprint | **S16 Any framework as a node → v0.8.0** ([roadmap §7](docs/agent-canvas-spec/16-competitive-roadmap.md#7-sprint-plan)) |
| Work status | **STOPPED by the owner after S15.** Do not start S16 or later until the owner says so in the project chat |
| Open PRs | none from this workstream |
| CI on `main` | green at `19de2dc` (api, web, e2e, docs, connectors, audit, images) |

**Test totals at the end of S15** (all green):

| Suite | Count | Command |
|---|---|---|
| API (pytest, Postgres) | 396 passed, 1 skipped, 1 xfail (known upstream canary) | see §5 |
| Web unit (vitest) | 70 | `cd apps/web && pnpm exec vitest run` |
| Browser journeys (Playwright, prod build under the prod CSP) | 38 | see §5 |
| MCP gallery smoke | 30/30 | `cd apps/api && uv run python scripts/smoke_mcp_gallery.py` |

## 2. Owner decisions and working agreements

These come from the owner in the project chat; they override defaults.

- **Merging.** The owner normally wants to be asked before any merge. A standing grant let sprint PRs be merged
  without asking (with a whole-codebase review before each sprint) **only through S15**. That grant is used up.
  For S16+, ask before merging unless the owner renews the grant.
- **Stop after S15** (2026-10-07): finish S15, run an end-to-end review, fix what it finds, then stop. Done.
- **Release tags** (`v0.x.0`) are always the owner's call. Never push tags.
- **Sprint order** is the approved value-first roadmap: [docs/agent-canvas-spec/16-competitive-roadmap.md §7](docs/agent-canvas-spec/16-competitive-roadmap.md#7-sprint-plan).
  Multi-framework support (not just LangGraph) is the core promise of the product.
- **Before each sprint:** review the whole codebase for errors and fix them first. **After each sprint:** an
  end-to-end review (API and browser flows), fix, then merge.
- **Handover** (2026-10-08): keep this file updated at the end of every sprint.
- **Node versions:** stay on Node LTS. Node 25 dropped corepack, which `infra/Dockerfile.web` uses (Dependabot #10 was closed for this).
- **Dependabot:** `@dependabot` commands must be typed by the owner; ones posted by an agent are ignored.
- **Merged branches** are deleted by the owner from the PR page (the cloud git proxy refuses remote branch deletion).

## 3. Unmerged work you can reuse (S18 and S19 drafts)

Under the old sprint order, Copilot II and the deploy kit were built **before** the roadmap was reordered. They now
belong to **S18** and **S19**. The code was never pushed or reviewed against S15. It is saved as a git bundle in
the project's shared files:

`/mnt/project-files/agentcanvas-handover/unmerged-sprint-work.bundle`

| Bundle branch | Contents | Base |
|---|---|---|
| `wip/s18-copilot-ii` | Copilot II: tests, debugging, error catalog, guide search, notes, split/private model modes, debug evals, plus review fixes (3 commits, 50 files) | `531a7ba` on `main` (before S15) |
| `wip/s19-deploy-kit` | the above + export deploy kit: service with the deployment API, deploy files for six targets, four generated guides, `make doctor`/`make smoke`, tests in the export, export dialog, CI (4 commits, 70 files) | same |

Restore: `git fetch /mnt/project-files/agentcanvas-handover/unmerged-sprint-work.bundle 'refs/heads/wip/*:refs/heads/wip/*'`,
then merge `main` into the branch when its sprint starts. Expect conflicts in `copilot/`, `api/v1/copilot.py`,
CHANGELOG, PROGRESS and the version numbers (they say v0.7.0/v0.8.0; under the new order they become v0.10.0/v0.11.0).
Re-run the pre-sprint review on it; it was written before S15's changes to `adapters/langchain/nodes.py` and the
Copilot service.

## 4. Repository map

```
apps/api/agentcanvas/      FastAPI control plane + runner (Python 3.12, uv)
  api/v1/                  REST routes; protocols.py = MCP + A2A per deployment, traces.py = trace export settings
  adapters/langchain/      the only place LangChain/LangGraph may be imported (import-linter contract, ADR 0011)
  runtime/                 framework-neutral runner, run handles, finish hooks, masking
  copilot/                 AI Copilot (S14)
  services/                triggers, projects, trace_export.py (OTel spans), ...
  registry/                node manifests, providers.json, mcp_gallery.json
  ir/, validation/         Graph IR v1.1 and the validator
  db/                      SQLAlchemy models; migrations in apps/api/migrations (Alembic)
apps/api/tests/            pytest; test_auth.py lists public/own-credential routes (update it for new routes)
apps/api/scripts/          export_ir_schema.py, smoke_mcp_gallery.py, ...
apps/web/src/              React 19 + TS, React Flow, Zustand, TanStack Query
  api/generated.ts         generated from OpenAPI (`pnpm gen`), never edit by hand
  release/                 publish, deployments, ConnectSection (MCP/A2A snippets)
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
make check                 # every CI gate except e2e and docs: lint, formats, types, import contract, drift, tests, build
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

`.github/workflows/ci.yml` jobs: `api` (lint, pyright, import contract, schema drift, pytest on Postgres+pgvector),
`web` (generated-type drift, typecheck, vitest, build), `e2e` (Playwright on the prod build under the strict CSP via
`vite preview`), `docs` (MkDocs build), `connectors` (gallery smoke, S15), `audit` (pip-audit, pnpm audit),
`images` (Docker builds, Trivy, prod and local compose stacks answer through nginx, Helm lint/render). Playwright traces are uploaded on failure.

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
- k6 lives at `~/go/bin/k6`; helm via `go install helm.sh/helm/v3/cmd/helm@v3.16.2`.

## 9. Known gaps and deferred items

- **Release tags** v0.1.0 to v0.7.0 not created (owner).
- **S9 exit criterion** (10 design partners finish J1 unaided) needs real users.
- **MCP endpoint nits left as is:** no `Origin` header validation (the endpoint needs a bearer key anyway) and the
  `MCP-Protocol-Version` header is not checked. A2A supports 0.3 method names only (`message/send`, `tasks/get`); no streaming.
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

## 11. Sprint-end checklist

Do these before the sprint PR is merged, in the same PR:

1. Pre-sprint review done and its fixes listed in the PR; post-sprint end-to-end review (API + browser) done and fixed.
2. All suites green locally and in CI (numbers into §1).
3. CHANGELOG, PROGRESS, sprint plan execution log and user guide updated; schema regenerated.
4. **This file:** update "Last updated", §1 Current state, §2 if the owner decided anything new, §3 if unmerged
   work changed, §9 known gaps, §10 history row (fill the merge commit after the merge in the next PR or a follow-up).
5. Reply in the project thread with the PR link and what the owner needs to do (merge, tags).
