# AgentCanvas — handover

Everything an agent or a developer needs to pick this project up in a new session, or as a different person.
**Keep it current: rewrite the "Current state" section and tick the checklist at the end of every sprint** (see
[Sprint-end checklist](#11-sprint-end-checklist)). If this file and another document disagree, the more recently
updated one wins; fix the other in the same PR.

- **Last updated:** 2026-10-09, after Sprint 22 (Whole flows, preview → v0.14.0). Previous update: after Sprint 21 (Design any agent → v0.13.0, [#25](https://github.com/marunsct/AgentCanvas/pull/25)).
- **Repository:** https://github.com/marunsct/AgentCanvas (default branch `main`).

---

## 1. Current state

| | |
|---|---|
| Last sprint done | **S22 Whole flows, preview → v0.14.0** (the S22 PR): a whole workflow exports to LangGraph (through Agent Spec), WayFlow or the OpenAI Agents SDK as a project around its Agent Spec flow (`agentcanvas/bridges/`, runner `bridges/kit/flowkit.py`), with a gaps report in the Export box and `GAPS.md`; `GET /v1/workflows/{id}/bridges`, `…/bridges/{target}/export.zip`; Agent Spec export fixes; CI jobs `bridge_<target>` |
| Before that | S21 Design any agent → v0.13.0 ([#25](https://github.com/marunsct/AgentCanvas/pull/25), merged `f308699`); S20 Framework foundation → v0.12.0 (#24, merged `e92f164`) |
| Version in code | API `0.14.0` (`apps/api/agentcanvas/api/main.py`), CHANGELOG top entry v0.14.0 |
| Git tags | **none pushed.** v0.1.0 … v0.14.0 are all left to the owner (see §2) |
| Next sprint | **S23 Ship to users → v0.15.0** (was S17; [roadmap §7](docs/agent-canvas-spec/16-competitive-roadmap.md#7-sprint-plan)), then S24 onward |
| Work status | S22 is in its PR; under the owner's standing grant of 2026-10-09 (see §2) it is merged on local checks |
| Open PRs | the S22 PR (`claude/project-thread-nz5vdj`) until it is merged |
| CI | **GitHub Actions assigned no runners from 2026-10-08 05:51Z** (every job fails in about 2 seconds with no logs, `main` included), most likely the account's Actions minutes or spending limit. The owner must fix it in GitHub billing settings. #20 was merged on local checks at the owner's choice. Until Actions runs again, run the suites below locally before merging |

**Test totals at the end of S22** (all green locally):

| Suite | Count | Command |
|---|---|---|
| API (pytest, Postgres) | 534 passed, 1 skipped, 1 xfail (known upstream canary) | see §5 |
| Web unit (vitest) | 82 | `cd apps/web && pnpm exec vitest run` |
| Browser journeys (Playwright, prod build under the prod CSP) | 47 | see §5 |
| Real frameworks (S16, S21, S22) | 9 frameworks: wrapped agent, designed agent (plain, typed, approval), its export's tests; Agent Spec check; whole-flow exports (`bridge_langgraph`, `bridge_wayflow`, `bridge_openai_agents`): every template's export tests pass, 4/4/2 run end to end | `cd apps/api && uv run --with <package> python scripts/smoke_frameworks.py <framework>` (packages in `ci.yml` job `frameworks`) |
| Exported templates, each export's own tests | 7 templates × 5 passed | `uv run python scripts/export_templates.py <dir>`, then `uv sync && uv run pytest` in each `<dir>/local/*/*` |
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
  bridges/                 S22: whole workflows to LangGraph/WayFlow/OpenAI Agents through Agent Spec; kit/flowkit.py
  services/                triggers, projects, trace_export.py (OTel spans), ...
  registry/                node manifests, providers.json, mcp_gallery.json
  ir/, validation/         Graph IR v1.1 and the validator
  db/                      SQLAlchemy models; migrations in apps/api/migrations (Alembic)
apps/api/tests/            pytest; test_auth.py lists public/own-credential routes (update it for new routes)
apps/api/scripts/          export_ir_schema.py, smoke_mcp_gallery.py, smoke_frameworks.py (S16), ...
apps/web/src/              React 19 + TS, React Flow, Zustand, TanStack Query
  api/generated.ts         generated from OpenAPI (`pnpm gen`), never edit by hand
  release/                 publish, deployments, ConnectSection (MCP/A2A snippets), AgentSpec.tsx (S16)
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
- k6 lives at `~/go/bin/k6`; helm via `go install helm.sh/helm/v3/cmd/helm@v3.16.2`.

## 9. Known gaps and deferred items

- **Release tags** v0.1.0 to v0.14.0 not created (owner).
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
  (resumed in another process, the call is refused and the agent says so);
  structured output uses each framework's own mechanism, so typed answers can differ slightly. In this cloud sandbox
  the Claude Agent SDK must be tested under `env -i` (the session's own Claude env vars leak into its CLI).
- **S20 limits:** model and tool resolution (`Resources`) is still the LangChain adapter's object handed to every
  adapter, and long-term memory, MCP browsing and judges stay on the host adapter; a block's `engine` is recorded
  and checked; S21 made an Agent block designed for an agent framework run as that framework; the conformance kit has no
  tool-calling agent, subgraph or cross-process resume cases yet; Echo is a demo (in-memory pauses, no deploy kit).
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
| S22 Whole flows, preview | the S22 PR | (after merge) | v0.14.0 |

## 11. Sprint-end checklist

Do these before the sprint PR is merged, in the same PR:

1. Pre-sprint review done and its fixes listed in the PR; post-sprint end-to-end review (API + browser) done and fixed.
2. All suites green locally and in CI (numbers into §1).
3. CHANGELOG, PROGRESS, sprint plan execution log and user guide updated; schema regenerated.
4. **This file:** update "Last updated", §1 Current state, §2 if the owner decided anything new, §3 if unmerged
   work changed, §9 known gaps, §10 history row (fill the merge commit after the merge in the next PR or a follow-up).
5. Reply in the project thread with the PR link and what the owner needs to do (merge, tags).
