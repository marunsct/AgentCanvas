# AgentCanvas — progress summary

Live status of the project. Update it with every commit that finishes or changes a piece of work.

- **Branch:** `claude/trusting-curie-n0i720`
- **Plan:** `docs/agent-canvas-spec/build-guide/13-sprint-plan.md`. Its execution log has the per-sprint detail.
- **Last updated:** end of Sprint 8.

## At a glance

| Area | Status |
|---|---|
| Specification (functional, technical, IR/compiler, node catalog, workspaces/events, reviews v0.5/v0.6, new features) | ✅ done (docs 01–15, ADRs 0001–0017) |
| Sprint plan S0–S12 | ✅ done |
| S0 Foundations | ✅ done |
| S1 IR v1.1 & API | ✅ done |
| S2 Runner & persistence | ✅ done |
| S3 Canvas editor I | ✅ done |
| S4 Canvas editor II & run UX | ✅ done |
| S5 Node catalog I | ✅ done |
| S6 Node catalog II & debug | ✅ done |
| S7 Workspaces & events | ✅ done |
| S8 Export & publish | ✅ done |
| **S9 MVP hardening → v0.1.0** | ⏳ **next** |
| S10 Accounts & access → v0.2.0 | ⬜ pending |
| S11 Production readiness → v0.3.0 | ⬜ pending |
| S12+ GA backlog | ⬜ pending |

**Test totals** at the end of S8:

| Suite | Result |
|---|---|
| API (pytest) | 196 passed, 1 skipped, 1 known upstream canary (xfail) |
| Web unit tests (vitest) | 39 passed |
| Browser journeys (Playwright) | 22 passed |

`make check` runs every CI gate except the browser journeys: lint, format, types, the import contract, schema/type drift, tests and the build.

## What has been done

### Specification & planning
- Reviewed the original research documents and re-verified them against current LangChain / LangGraph / Deep Agents / LangSmith releases (review v0.6, doc 14).
- Added the enhanced feature set (doc 15, FR-X-01…24).
- Wrote the full sprint plan with estimates, risks and spikes.

### S0–S2: foundations, IR, runner

**Monorepo and CI**
- `apps/api` (FastAPI) and `apps/web` (React + React Flow).
- CI jobs: api, web, e2e.

**IR v1.1 and validation**
- The IR is the single source of truth.
- Rule-based validator with diagnostic codes E0xx/W0xx.
- Invertible command API with optimistic locking.

**Runner**
- Framework-neutral runner behind an adapter SPI: the core never imports LangChain, which import-linter enforces.
- The interpreter and code generation share one execution plan.
- Durable runs:
  - resume after a restart
  - inbox for human decisions
  - state history, fork, breakpoints and cancel
- Persistence on SQLite (local edition) and Postgres.

### S3–S4: canvas editor & run UX
- **Editing:** typed ports and connection checks, JSON-Schema inspector, state designer, problems dock, autosave, undo/redo.
- **Canvas tools:** tidy layout, copy/paste, command palette.
- **Running:** test runs with JSON or chat input, decision cards, and a timeline for time travel.
- **Understanding runs:** Explain mode and the Live Code Mirror (generated code with highlighting).

### S5–S6: node catalog & debugging
- **Agents:** middleware widgets, agents with structured output, Deep Agent with subagents and file backends.
- **Flow control and data:** human input, map/join (parallel fan-out), subgraphs (convert / inline / drill-in).
- **Tools and memory:** MCP servers, HTTP tool, memory nodes and the Memory inspector.
- **Debug aids:** pins and mock models, cost and latency per node, budgets, channel lineage, trace policy.

### S7: workspaces & events
- **Workspaces:** a per-project git workspace with a code editor and ruff diagnostics.
- **Custom code:** script nodes, workspace tools, and custom widgets (`@canvas_node`).
- **Sandboxes:** in-process, subprocess, Docker, or LangSmith Sandbox. The LangSmith mode was spike SP-2, tested with a fake client.
- **Sandbox policy:** `sandbox.json` sets limits and an egress allow-list; headers are injected with secrets outside the sandbox.
- **Secrets:** encrypted at rest, write-only through the API, redacted from events.
- **Events as code:** `@on` handlers with filters, duplicate suppression, schemas, dry runs, lifecycle events and `sdk.emit`.

### S8: export & publish
- **Versions:** semver snapshots (IR + layout + workspace commit), structural diff, compare on the canvas, restore.
- **Saved test cases:** check final status, output values and route; carry mocks and pins; run against the draft or a version.
- **Export:** a standalone uv project. Its own `uv run pytest` passes, and it serves under `langgraph dev` through a `ServerRuntime` factory.
- **Publish:**
  - Checklist before going live: validation, tests and secrets block publishing; breaking contract changes are reported but don't block.
  - Each environment gets an API key, stored hashed; keys can be rotated and a deployment unpublished.
  - Public endpoints: `runs/wait`, `runs/stream`, and resume.
- **Triggers:** HMAC-signed webhooks and cron schedules.
- **Tracing:** LangSmith trace ids and metadata, plus an "Open trace" link.
- **Templates:** ticket triage, approval workflow, data extraction and RAG Q&A, plus the earlier three.
- **Record/replay:** a run's model replies become a regression test.
- **Contract view:** input/output fields, pause points and exposure surfaces, with breaking-change detection.
- **Spikes:**
  - SP-3: v3 streaming is not ready; keep v2.
  - SP-4: a `ServerRuntime` factory works on Agent Server.

## What is pending

### S9 — MVP hardening → v0.1.0 (next)
- [ ] M6-T8 hardening: error, empty and loading states on every screen; docs site from the build guide; release notes; tag `v0.1.0`.
- [ ] FR-X-02 blast-radius overlay (basic, from the nodes' declared `side_effects`).
- [ ] FR-X-24 guided build.
- [ ] Visual-only groups (layout frames), deferred from S6.
- [ ] Code-split the web bundle; it is currently 2.8 MB before compression, over the 500 kB warning.
- [ ] Design-partner feedback buffer. Exit criterion: 10 partners complete journey J1 unaided, which needs real users.

### S10 — accounts & access → v0.2.0
- [ ] OIDC login, organisations and projects, roles, audit log (M7-T1…T4). Until then every request acts as a seeded dev admin.
- [ ] FR-X-19 policy-as-code lint profiles.

### S11 — production readiness → v0.3.0
- [ ] Containers, Helm, observability, backups, load test and security review (M8-T1…T6).
- [ ] FR-X-10 safe redeploy.
- [ ] Leader lock for the cron scheduler when running more than one API instance (Postgres advisory lock).
- [ ] KMS envelope encryption for secrets; today the key is derived from `AGENTCANVAS_SECRET_KEY`.

### S12+ — GA backlog
- [ ] Evals studio, multiplayer editing, Data Studio, learning loops, MCP/A2A/AG-UI exposure, Copilot.
- [ ] The remaining FR-X items: 03, 04, 05, 06, 09, 11, 12, 16, 20, 21, 23.

### Open items carried forward
- [ ] **SP-2 follow-up:** one session with a real LangSmith key to measure sandbox latency. Open questions:
  - is an empty allow-list accepted as "no network"?
  - can one sandbox run several commands at once?
  - which snapshot to use (see `build-guide/spikes/SP-2-langsmith-sandbox.md`)?
- [ ] **Record/replay:** tool I/O is not captured yet; tool calls re-run during replay.
- [ ] **Agent-sandbox backend widget** (`backend.sandbox`, where W074 applies), P1.

## Commit log by sprint (latest first)

| Commit | Content |
|---|---|
| S8 part 7 | Spike reports SP-3 and SP-4; export uses a `ServerRuntime` factory; S8 docs; this file |
| `899ca6b` | Record/replay; contract view & API diff |
| `17754c2` | LangSmith trace links; MVP template set |
| `d275a93` | Webhook and cron triggers |
| `ecd2152` | Publish: deployments, API keys, checklist |
| `dfa54e9` | Export as a standalone Python project |
| `6939671` | Versions & diff; saved test cases |
| `b4f41f6` | Sandbox policy; LangSmith Sandbox runner (SP-2); S7 done |
| `7e39301` | Events as code v0 |
| earlier | S0–S7 parts; see `git log` |
