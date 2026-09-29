# 13 — Sprint Plan (S0–S12) and Execution Log

The milestone plan ([04-implementation-plan.md](./04-implementation-plan.md)) says **what** to build and in which order; this document time-boxes it into **two-week sprints**, slots in the v0.6 requirements (doc [15](../15-concept-evolution-and-new-features.md), FR-X) and the review fixes (doc [14](../14-deep-review-v0.6.md)), and records **execution status**.

## 1. Planning assumptions

| Item | Value |
|------|-------|
| Team | 4 developers (backend, compiler/runtime, frontend, full-stack/DevOps) + part-time design & PM |
| Sprint length | 2 weeks (S0 is 1 week) |
| Capacity | 4 devs × 10 days × 0.7 focus ≈ **28 ideal days** per sprint; plan to ≤ 24 (buffer for bugs/reviews) |
| Estimates | ticket estimates from build-guide/04 (ideal days for one developer) |
| Repository | this repository (ADR 0012): `apps/api`, `apps/web`, `packages/ir-schema`, `infra/`, `docs/` |
| Branching | trunk-based, short-lived branches, Conventional Commits, CI green before merge (02 §7) |
| Definition of Done | build-guide/README §4 + "docs updated in the same PR" + "execution log below updated at sprint end" |

**Ceremonies:** planning (day 1, 2 h), daily 15-min sync, backlog refinement (mid-sprint, 1 h), demo + retro (last day). Demo = the sprint's *demo script* below, run live against `main`.

**Release trains:** `v0.1.0` (MVP) at end of S9; `v0.2.0` (accounts) S10; `v0.3.0` (production-ready) S11; GA features from S12.

## 2. Sprint overview

| Sprint | Weeks | Theme | Milestone tickets | v0.6 items | Load (d) | Outcome |
|--------|------:|-------|-------------------|-----------|---------:|---------|
| **S0** | 1 | Inception & foundations | M0-T1…T4 | ADRs 0012–0017, stack pins, async node library (V-03) | 5 | Monorepo, CI, ADRs; skeleton runs from `main` |
| **S1** | 2 | IR v1.1 & API foundation | M1-T1…T5, T7, T8 | IR storage mode (V-06), context schema, policies, E041/E042/E080 dev-only | 22 | Workflows stored in Postgres, edited via commands, validated with rules & manifests |
| **S2** | 2 | Runner & persistence | M1-T6; M3-T1, T10, T11, T13; M4-T4…T8, T10 (backend) | durability, context/user scoping, queue-based runner (V-10) | 22 | Durable runs: resume after restart, inbox API, history/state/fork/breakpoints/cancel |
| **S3** | 2 | Canvas editor I | M2-T1…T9 | spike SP-1 (500-node perf) | 22 | Drag, wire (type-checked), configure, validate, autosave |
| **S4** | 2 | Canvas editor II & run UX | M2-T10…T12; M4-T1…T3; M4-T4…T8 (UI) | FR-X-01 Live Code Mirror, FR-X-17 Explain mode | 24 | Build → run → approve → time-travel entirely in the UI |
| **S5** | 2 | Node catalog I | M3-T2, T3, T4, T7, T8, T9, T12 | stable defaults of limit middleware (V-07 #9) | 19 | Agents, Deep Agent, map/parallel, human input, middleware widgets |
| **S6** | 2 | Node catalog II & debug | M3-T5, T6, T14; M2-T13; M4-T9, T11, T12 | FR-X-07 trace policy, FR-X-15 budgets, FR-X-18 channel lineage, E042 | 24 | MCP/HTTP tools, memory, subgraphs, pins/mocks, cost per node |
| **S7** | 2 | Workspaces & events | M5-T1…T7 | FR-X-22 events-as-code v0, FR-X-08 sandbox policy (basic), spike SP-2 (LangSmith sandbox runner) | 24 | Code editor, script nodes, sandbox, secrets, custom nodes/tools, event handlers |
| **S8** | 2 | Export & publish | M6-T1…T7, T9 | FR-X-13 record/replay, FR-X-14 contract view; spikes SP-3 (v3 readiness), SP-4 (`ServerRuntime` hosting) | 24 | Versions/diff, export project, publish endpoint, triggers, tracing, templates, test cases |
| **S9** | 2 | **MVP hardening → v0.1.0** | M6-T8 | FR-X-02 blast radius (basic), FR-X-24 guided build | 16 + buffer | 10 design partners complete J1 unaided |
| **S10** | 2 | Accounts & access → v0.2.0 | M7-T1…T4 | FR-X-19 policy-as-code lint profiles | 13 + buffer | OIDC, orgs/projects, roles, audit |
| **S11** | 2 | Production readiness → v0.3.0 | M8-T1…T6 | FR-X-10 safe redeploy | 18 | Containers, Helm, observability, backups, load & security review |
| **S12+** | — | GA backlog (M9) | evals studio, multiplayer, Data Studio, learning loops, MCP/A2A/AG-UI exposure, Copilot | FR-X-03/04/05/06/09/11/12/16/20/21/23 | — | Prioritised per quarter |

Total to MVP ≈ 19 weeks; to production-ready ≈ 23 weeks with four developers. One developer: multiply by ~3.5.

## 3. Sprint backlogs

Each backlog item lists: ticket, owner role (B backend, R runtime/compiler, F frontend, D DevOps), estimate. Acceptance criteria are the ticket's AC in build-guide/04 unless stated.

### S0 — Inception & foundations (1 week)
**Goal:** a monorepo on `main` where `make test` and `make lint` are green and the walking skeleton runs.

| Item | Role | Est |
|------|------|----:|
| M0-T1 seed monorepo from `reference/` (ADR 0012 layout) | D | 0.5 |
| M0-T2 CI workflow (api + web jobs) | D | 0.5 |
| M0-T3 ADRs 0001–0017 in `docs/adr/` | all | 1 |
| Stack pins `stack-2026.09` (ADR 0017): `langchain[mcp]==1.4.3`, `langsmith` | R | 0.5 |
| Async node library: every builder returns `async` callables; sync scripts via `asyncio.to_thread` (ADR 0015) + regression test "timeout policy on every node type" | R | 1 |
| M0-T4 skeleton demo + Playwright spec | F | 1 |

**Demo:** clone → `make setup test lint` → start API + web → run support triage → approve.
**Exit:** CI green on `main`; ADRs merged.

### S1 — IR v1.1 & API foundation
**Goal:** workflows live in Postgres and are edited through validated commands.

| Item | Role | Est |
|------|------|----:|
| IR v1.1 models: channel `storage` (full/delta), `io` visibility, workflow `context` fields, node `policies`, `settings` (recursion limit, durability, node defaults), resource & node type registry keys | B | 3 |
| M1-T2 IDs (prefixed ULIDs), settings, structlog, problem+json errors | B | 1.5 |
| M1-T1 SQLAlchemy models (14 tables) + Alembic initial migration | B | 2 |
| M1-T3 Workflow CRUD with `If-Match`/409 and pagination | B | 3 |
| M1-T4 Command API with inverse commands + Hypothesis round-trip | B | 4 |
| M1-T5 Layout document (moves never change `ir_hash`) | B | 0.5 |
| M1-T7 Node-type manifests (`registry/nodes/*.json`), `GET /v1/node-types`, IR JSON Schema export to `packages/ir-schema` | R | 2 |
| M1-T8 Validator rule framework + P0 rules (E002, E004, E020, E030, E031, E033, E050, E070, W003, W010) + v0.6 rules E041, E042, E080 (dev-only `examples.`) | R | 4 |
| `POST /v1/expressions/check` | B | 0.5 |

**Demo:** create a workflow via API, apply command batches, see diagnostics; two concurrent writers → 409.
**Exit:** migrations up/down; 200-node validation < 150 ms benchmark.

### S2 — Runner & persistence
**Goal:** durable, observable runs with a DB-backed thread model.

| Item | Role | Est |
|------|------|----:|
| M3-T1 Adapter boundary `adapters/langchain/` + import-linter contract | R | 2 |
| Runner v1: queue + background task (survives disconnect), run registry, run/thread rows, durability (`exit/async/sync`), recursion limit, run `context` (user scoping) | B | 4 |
| M1-T6 threads & runs endpoints (list, get, history, state, delete) | B | 2 |
| M4-T4 (backend) interrupt tasks → Inbox API; decisions resume threads | B | 2 |
| M4-T5…T7 (backend) history, state edit, fork (replay / edit & branch / copy) | R | 3 |
| M4-T8 (backend) breakpoints (`interrupt_before/after`, debug runs only) | R | 1 |
| M4-T10 cancel run | B | 1.5 |
| M3-T13 node policies (retry, timeout, cache) incl. graph `node_defaults` | R | 2 |
| M3-T10 loop guard / recursion error mapping; M3-T11 output schema | R | 2.5 |
| Integration test: pause → restart app → resume (Postgres) | B | 1 |

**Demo:** start run, kill API, restart, approve from Inbox API, run completes; fork from a past checkpoint takes the other branch.
**Exit:** all runner endpoints covered by API tests with fake models.

### S3 — Canvas editor I
| Item | Role | Est |
|------|------|----:|
| M2-T1 app shell & routes | F | 1.5 |
| M2-T2 generated types (OpenAPI + IR schema) & API client | F | 1 |
| M2-T3 load, edit, debounced command queue, 409 handling | F | 3 |
| M2-T4 palette & drag-and-drop | F | 2 |
| M2-T5 manifest-driven node renderer with typed handles | F | 3 |
| M2-T6 type-checked connections | F | 2 |
| M2-T7 inspector (RJSF) with prompt/channel/CEL widgets | F | 4 |
| M2-T8 state designer | F | 2 |
| M2-T9 problems panel & badges | F | 1.5 |
| SP-1 spike: React Flow with 500 nodes/1,000 edges at 60 fps (LOD) | F | 2 |

**Demo:** build the support-triage workflow from an empty canvas; broken wiring shows E001 within 1 s.

### S4 — Canvas editor II & run UX
| Item | Role | Est |
|------|------|----:|
| M2-T10 undo/redo, copy/paste, delete | F | 3 |
| M2-T11 auto-layout (ELK) · M2-T12 command palette | F | 3 |
| M4-T1 test-run panel (chat + form) · M4-T2 live overlay · M4-T3 token preview | F | 5.5 |
| M4-T4…T8 UI: inbox, timeline, state inspector/edit, fork, breakpoints | F | 6 |
| FR-X-01 Live Code Mirror (split view, selection sync, changed-lines preview) | F+R | 3 |
| FR-X-17 Explain mode (super-step walkthrough from `tasks`/`updates`) | F | 2 |

**Demo:** journey J2-lite: breakpoints, scrub timeline, edit state, fork.

### S5 — Node catalog I
M3-T2 model catalog (2) · M3-T3 Lite Agent full options (3) · M3-T4 Deep Agent P0 (4) · M3-T7 human input (1.5) · M3-T8 map / `Send` (3) · M3-T9 parallel & join (2) · M3-T12 middleware widgets with upstream defaults (3). **Demo:** research template with map-reduce and a Deep Agent writing `/memories/`.

### S6 — Node catalog II & debug
M3-T5 MCP (`langchain[mcp]`, E042) (3) · M3-T6 HTTP tool (2) · M3-T14 memory nodes (3) · M2-T13 groups & subgraphs (4) · M4-T9 pins & mock mode (2) · M4-T11 cost & latency (2) · M4-T12 memory inspector (2) · FR-X-07 trace policy (1) · FR-X-15 budgets (2) · FR-X-18 channel lineage (2). **Demo:** MCP-backed agent with budget, pinned classifier, cost per node.

### S7 — Workspaces & events
M5-T1 storage (2) · M5-T2 editor (3) · M5-T3 script nodes (2) · M5-T4 Docker sandbox runner (4) · M5-T5 custom tools (2) · M5-T6 secrets (3) · M5-T7 custom node SDK (4) · FR-X-22 events-as-code v0: event schemas + `@on` trigger handlers + replay dry-run (3) · SP-2 LangSmith sandbox runner with auth proxy (1). **Demo:** journey J3 (custom node + event handler starting a workflow).

### S8 — Export & publish
M6-T1 versions & diff (3) · M6-T2 packager (4) · M6-T3 export (1) · M6-T4 publish (3) · M6-T5 triggers (3) · M6-T6 tracing (2) · M6-T7 templates (3) · M6-T9 saved test cases (3) · FR-X-13 record/replay (1, basic) · FR-X-14 contract view (1, basic) · SP-3 v3 readiness, SP-4 `ServerRuntime` hosting (spikes, time-boxed 0.5 d each). **Demo:** publish v1, call the endpoint with an API key, webhook trigger, export zip passes `uv run pytest`.

### S9 — MVP hardening → v0.1.0
M6-T8 hardening & release (5) · FR-X-02 blast-radius overlay (basic, from manifests' `side_effects`) (3) · FR-X-24 guided build (3) · design-partner feedback buffer (5). **Exit:** 10 design partners complete J1 unaided; tag `v0.1.0`.

### S10 — Accounts & access → v0.2.0
M7-T1 OIDC (3) · M7-T2 orgs/projects (3) · M7-T3 roles (2) · M7-T4 audit log (2) · FR-X-19 policy-as-code lint profiles (3).

### S11 — Production readiness → v0.3.0
M8-T1 containers (2) · M8-T2 Helm/compose prod (3) · M8-T3 observability (3) · M8-T4 backups & retention (2) · M8-T5 load test (2) · M8-T6 security review (3) · FR-X-10 safe redeploy (3).

### S12+ — GA backlog (prioritised quarterly)
Evaluation Studio (FR-EVAL) · multiplayer (Yjs) · persistence bindings & RoutedStore · Data Studio · learning loops (ADR 0014) · MCP/A2A/AG-UI exposure & chat widget · Copilot · FR-X-03 headless tools · FR-X-04 generative UI · FR-X-16 multimodal · FR-X-21 per-user connections · FR-X-05/06 decision models & Engine bridge · FR-X-09 stream projections · FR-X-11 ACP · FR-X-12 trace import · FR-X-20 PR visual diffs · FR-X-23 budget-aware routing · second framework adapter (doc 12).

## 4. Risks to the schedule

| Risk | Signal | Response |
|------|--------|----------|
| Upstream API churn (beta namespaces) | CI job against latest upstream turns red | Pin stack; capability flags; fix in the next sprint's buffer |
| Canvas performance | SP-1 fails 60 fps target | LOD rendering, collapse subgraphs by default, WebGL fallback in S12+ |
| Sandbox complexity | M5-T4 slips | Ship in-process execution for the local edition first (D-10); hosted edition waits for sandbox |
| Scope creep from FR-X | sprint load > 24 d | FR-X items are the first to move to S12+ |

## 5. Execution log

Updated at the end of each sprint (and as work lands). Commit references are on branch `claude/trusting-curie-n0i720`.

| Sprint | Status | What landed | Notes |
|--------|--------|-------------|-------|
| Spec v0.6 | ✅ done | docs 14, 15, v0.6 corrections, this plan | review verified against PyPI + official docs (2026-09-29) |
| S0 | ✅ done | monorepo seeded from `reference/` (`apps/api`, `apps/web`, `infra/`), Makefile, CI (api/web/e2e jobs), ADRs 0001–0017, root README, stack pins `stack-2026.09`, async node library (ADR 0015) with timeout regression tests, `examples.` imports dev-only, Playwright spec `e2e/skeleton.spec.ts` | 32 API tests, 2 web tests, 1 e2e pass; ruff/pyright/tsc clean |
| S1 | ⏳ in progress | — | — |
| S2 | ⏳ planned | — | — |
