# 13 — Sprint Plan (S0–S36) and Execution Log

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
| Definition of Done | build-guide/README §4 + "docs updated in the same PR" + "execution log below updated at sprint end" + "[HANDOVER.md](../../../HANDOVER.md) updated at sprint end" |

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
| **S11** | 2 | Production readiness → v0.3.0 | M8-T1…T6 + S10 carry-overs | FR-X-10 safe redeploy | 23 | Containers, Helm, observability, backups, load & security review |
| **S12** | 2 | Evaluation Studio → v0.4.0 | FR-EVAL-02…05 + S11 follow-ups | — | 23 | Datasets, evaluators, experiments, results grid, release gate |
| **S13** | 2 | Evaluation in production → v0.5.0 | FR-EVAL-06…08, FR-EVAL-03 rest + S12 follow-ups | — | 22 | Online evaluators and drift alerts, annotation queues, pairwise and composite evaluators, rubric self-grading, simulated users |
| **S14** | 2 | AI Copilot I → v0.6.0 | Copilot plan (Claude Doc "Copilot, export and beginner features", plan v2) | — | 24 | Copilot briefs, plans and builds workflows as proposals you accept on the canvas |
| **S15** | 2 | Open doors → v0.7.0 | [doc 16 §7](../16-competitive-roadmap.md#7-sprint-plan) | — | — | Deployments as MCP tools and A2A agents, MCP connector gallery (~30), OTel trace export |
| **S16** | 2 | Any framework as a node → v0.10.0 | doc 16, doc 11 F1 | — | — | Wrapped, A2A and MCP agent nodes for 9 frameworks; Agent Spec import/export (✅ done) |
| **S18** | 2 | AI Copilot II → v0.8.0 | configure, test, debug and fix | — | 23 | The Copilot tests, debugs and fixes workflows (✅ done, built ahead of S16–S17) |
| **S19** | 2 | Export deploy kit → v0.9.0 | export as a service | — | 24 | Exports are deployable codebases with prerequisites, setup and deploy guides (✅ done, built ahead of S16–S17) |
| **S17** | 2 | Workspace and editor fixes → v0.11.0 | owner's findings 2026-10-09, §3 below | — | 14 | A workspace per workflow, exports bundle its code, a way back to the canvas, Copilot in the code editor, every button labelled (✅ done) |
| **S20** | 2 | Framework foundation → v0.12.0 | [doc 17](../17-framework-coverage-review.md), doc 12 WS0 | — | 20 | Adapter interface v2, adapter per workflow, `engine` per node, framework picker, portability check, conformance kit (✅ done) |
| **S21** | 2 | Design any agent → v0.13.0 | doc 17 | — | 22 | The canvas Agent block builds a native agent in nine frameworks; mixed flows run and export |
| **S22** | 2 | Whole flows, preview → v0.14.0 | doc 17, [adapters/agent-spec-bridge.md](../adapters/agent-spec-bridge.md) | — | 16 | Portable flows export to CrewAI, OpenAI Agents, AutoGen and LangGraph through Agent Spec (preview) |
| **S23** | 2 | Ship to users → v0.15.0 (was S17) | doc 16 | — | — | Chat widget and share page, guardrails pack, 25 templates |
| **S24** | 2 | Knowledge Studio → v0.16.0 (was S20) | doc 16 | — | — | Ingestion, hybrid search, citations, retrieval evals |
| **S25** | 2 | OpenAI Agents SDK flows → v0.17.0 (was S21) | [adapters/openai-agents-sdk.md](../adapters/openai-agents-sdk.md) | — | — | Flow tier and Full agent tier; convert-and-compare |
| **S26** | 2 | Model gateway & cost → v0.18.0 (was S22) | doc 16 | — | — | Fallbacks, budgets, caching, cost alerts |
| **S27** | 2 | CrewAI flows → v0.19.0 (was S25–S26) | [adapters/crewai.md](../adapters/crewai.md) | — | — | Full for Flows, Authoring for Crews |
| **S28** | 2 | Mixed-flow export → v0.20.0 (new) | doc 12 WS7, doc 17 | — | — | One deployable project with several frameworks and typed boundaries |
| **S29–S30** | 4 | Google ADK flows → v0.21.0 (was S27–S28) | [adapters/google-adk.md](../adapters/google-adk.md) | — | — | Full |
| **S31** | 2 | Onboarding & switching → v0.22.0 (was S23) | doc 16 | — | — | Beginner items (old S18–S19) + Langflow/Flowise/n8n importers |
| **S32** | 2 | Pydantic AI & Agno flows → v0.23.0 (was S24) | [adapters/pydantic-ai.md](../adapters/pydantic-ai.md) | — | — | Authoring tier |
| **S33–S34** | 4 | Microsoft Agent Framework flows → v0.24.0 (was S30–S31) | [adapters/microsoft-agent-framework.md](../adapters/microsoft-agent-framework.md) | — | — | Authoring tier; Full later if users ask |
| **S35** | 2 | Strands & LlamaIndex flows → v0.25.0 (was S29) | [adapters/strands.md](../adapters/strands.md), [adapters/llamaindex-workflows.md](../adapters/llamaindex-workflows.md) | — | — | Authoring tier |
| **S36** | 2 | Mastra → v0.26.0 (was S32) | [adapters/mastra.md](../adapters/mastra.md) | — | — | Authoring tier with a Node.js sidecar |
| **Later** | — | GA backlog (M9) | multiplayer, Data Studio, learning loops, Git sync, hosted trial | FR-X-03/04/05/06/09/11/12/16/20/21/23 | — | Prioritised per quarter |

From S15 the order follows the value-first [competitive roadmap](../16-competitive-roadmap.md), approved 2026-10-07. S18 and S19 were built first (their drafts predated the reorder) and finished on 2026-10-08 at the owner's request, so release numbers follow merge order: S18 v0.8.0, S19 v0.9.0, S16 v0.10.0. On 2026-10-09 the owner re-planned from S17 on after the [framework coverage review](../17-framework-coverage-review.md): S17 is now the owner's workspace and editor fixes, then S20–S22 bring framework design forward; the old S17–S32 moved to S23–S36 ("was" in the table).

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

**Goal:** one team can run AgentCanvas for real users on their own infrastructure: several API replicas behind
nginx, a managed Postgres, metrics and traces, tested backups, and redeploys that do not break running work.

**Backlog (planned at the end of S10, 2026-10-05).** Branch `claude/sprint-11-production-readiness`, in
[#4](https://github.com/marunsct/AgentCanvas/pull/4).

| # | Item | Acceptance | Est. |
|---|------|-----------|-----:|
| 1 | **M8-T1 Containers** | `docker build` of `infra/Dockerfile.api` and `Dockerfile.web` succeeds in CI; images run as a non-root user; nginx streams SSE unbuffered (`/api/v1/**/stream`, `/runs/*/events`) and serves the SPA with long-lived caching for hashed assets only. | 2 |
| 2 | **M8-T2 Compose prod & Helm** | `infra/compose.prod.yml` (api ×2, web, Postgres, Redis, migrations as a one-shot job) comes up healthy; Helm chart in `infra/helm/agentcanvas` (api Deployment with N replicas, web, migration Job, Ingress, Secret refs, probes) passes `helm lint` and `helm template` in CI. `/readyz` pings the database and the persistence store; `/healthz` stays a cheap liveness check. | 3 |
| 3 | **Leader lock for schedulers** (carried over) | With two API replicas each cron schedule fires once: the scheduler loop takes a Postgres advisory lock (no-op on SQLite) and hands over within one tick when the leader stops. | 1 |
| 4 | **M8-T3 Observability** | OpenTelemetry traces for FastAPI, SQLAlchemy and httpx when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; `/metrics` (Prometheus) with runs started/finished by status, run duration, node errors, tokens and cost, SSE subscribers; a Grafana dashboard JSON in `infra/observability/`. Request ids appear in logs and spans. | 3 |
| 5 | **M8-T4 Backups & retention** | Retention setting per organisation (default 90 days) and a daily job deleting older threads with `adelete_thread` plus their rows; restore runbook (Postgres PITR) in the docs, rehearsed once against compose prod. | 2 |
| 6 | **FR-X-10 Safe redeploy** | On SIGTERM the API stops taking runs, lets running ones reach a superstep boundary, marks them `interrupted` and exits; another replica resumes them. A publish that changes a deployment's version resumes pinned threads on the new version only when the resume-compatibility check passes (same pause points and state channels); otherwise they finish on the version they started on. | 3 |
| 7 | **M8-T5 Load test** | `k6` script in `infra/load/`: 200 concurrent streaming runs on fake models against compose prod; p95 event latency < 250 ms; the result recorded in this log. | 2 |
| 8 | **M8-T6 Security review** | [10-security-checklist.md](./10-security-checklist.md) completed with evidence links; dependency and container scanning (pip-audit, pnpm audit, Trivy) in CI; findings fixed or ticketed. | 3 |
| 9 | **KMS envelope encryption for secrets** (carried over) | Secrets encrypted with a per-organisation data key wrapped by a KMS key (AWS KMS / GCP KMS / local key file for dev); a re-wrap command rotates the master key without downtime. | 2 |
| 10 | **S10 review carry-overs** | Invitations need the invitee's acceptance when they already belong to another organisation, and member lists show no profile details before that; the default organisation is the one used last (not the first by id); the web app refreshes tokens silently (`offline_access`) and reads its project per request instead of at module load; `FORWARDED_ALLOW_IPS` documented for the proxy in front of the API. | 2 |

**Load:** 23 days (≤ 24). If the sprint runs long, KMS (item 9) moves to S12 first, then the Grafana dashboard.

**Order:** containers and compose prod first (everything else is tested against them), then the leader lock,
observability and safe redeploy, then retention, load test and the security review at the end.

**Demo script:** `docker compose -f infra/compose.prod.yml up`; sign in; start a long run; restart one API replica
mid-run and watch it finish on the other; open the Grafana dashboard; show the k6 report and the completed security
checklist.

**Exit:** compose prod and the Helm chart deploy cleanly; the load test meets its target; no open high-severity
security finding; tag `v0.3.0` (maintainer's call).

**Review (2026-10-06).** All ten items met their acceptance; the evidence is in the execution log below and in
[10-security-checklist.md](./10-security-checklist.md) §7. Item 3 needed no advisory lock: claiming each minute
with a conditional update of `trigger.last_fired` works on SQLite and Postgres alike. The load test found that every
open stream held a database connection; requests now release their sessions once the response starts. Deviations and
gaps, all carried into S12 unless noted:
- `script-src` keeps `'unsafe-eval'` (Ajv compiles schemas at runtime).
- Rate limits are per client address in nginx only, not per API key or project in the API (S30 partial).
- Trace payloads to LangSmith/OTel are not masked (S9 partial); audit-log and memory retention are missing (S29 partial).
- An instance killed without a graceful shutdown leaves its runs `running`; nothing sweeps them.
- Base images are not pinned by digest; the workspace path test has no symlink case.
- Webhook signatures changed format (timestamped), a breaking change called out in the changelog.
- Not carried: AWS/GCP KMS adapters are tested with fakes only (needs cloud accounts); the store is not encrypted
  (upstream); memory provenance tags (S10, P2) stay in the GA backlog.

### S12 — Evaluation Studio → v0.4.0
FR-EVAL-02 datasets (3) · FR-EVAL-03 evaluators (4) · FR-EVAL-04 experiments (4) · results UI (3) · FR-EVAL-05 release
gate (2) · S11 follow-ups (7).

**Goal:** a builder can show, with numbers, that a new version of a workflow is better than the last one before it
reaches prod: build a dataset from test cases and real runs, score versions with evaluators, compare them side by
side, and have publishing to prod refuse a version that regresses.

**Backlog (planned at the end of S11, 2026-10-06).** Branch `claude/sprint-12-evaluation-studio`, in
[#5](https://github.com/marunsct/AgentCanvas/pull/5).

| # | Item | Acceptance | Est. |
|---|------|-----------|-----:|
| 1 | **FR-EVAL-02 Datasets** | A dataset belongs to a workflow and holds examples (input, optional context, reference output, optional reference tool trajectory, split, metadata). Create from saved test cases, from selected past runs ("Add run to dataset"), or by importing CSV/JSONL (column mapping, validated against the workflow's input schema). Every change creates a new dataset version; experiments pin a version. Export as JSONL. Org-scoped, audited, role-checked like other resources. | 3 |
| 2 | **FR-EVAL-03 Evaluators** | Evaluators per workflow: exact match, regex and JSON Schema on a channel; **LLM-as-judge** with a rubric, a model from the catalog and a 0–1 score with reasoning; **trajectory match** (tool sequence strict / unordered / subset); **code evaluators** from workspace `evals/*.py` run in the project's sandbox; each returns score, pass/fail at a threshold, and a comment. Uses `openevals`/`agentevals` where they fit. Unit-tested with fake models. | 4 |
| 3 | **FR-EVAL-04 Experiments** | Run one or more workflow versions (or the draft) × a dataset version × optional variants (model or prompt override per resource) in the background with bounded concurrency, through the normal runner (mocks honoured, no triggers or event handlers). Each example result keeps the thread id, output, scores, tokens, cost and latency; aggregates per variant (mean score, pass rate, p50/p95 latency, cost). Cancellable; survives a redeploy through the existing handover. | 4 |
| 4 | **Results UI** | Evaluation page per workflow: datasets (import, edit, versions), evaluators (configure, try on one example), experiments (start, progress, cancel). Results grid of examples × variants with scores, a diff of outputs, and a link to each run's timeline. Comparing two experiments shows per-evaluator deltas and which examples improved or regressed. | 3 |
| 5 | **FR-EVAL-05 Release gate** | An environment can require a gating dataset and thresholds (minimum mean score per evaluator, maximum regression vs. the live version). Publishing there adds an "evaluation" checklist item that runs or reuses an experiment on the exact version and refuses when a threshold fails; admins can override with a reason, recorded in the audit log. | 2 |
| 6 | **API rate limits per key and project** (S11, S30) | Deployment run creation and webhooks are limited per API key / trigger and per project, counted per minute in the control database so the limits hold across replicas (done instead of a Redis token bucket: compose prod and the chart have no Redis); 429 with `Retry-After`; limits configurable per environment. | 2 |
| 7 | **Orphaned-run sweeper** (S11) | Runs still `running` whose instance stopped heartbeating are marked `interrupted` and resumed by the handover job; a heartbeat per instance; test kills an instance without draining. | 1 |
| 8 | **Trace masking** (S11, S9) | Secret values and fields named in the workflow's trace policy are masked in LangSmith and OTel payloads; test with a known secret. | 1 |
| 9 | **Audit-log and memory retention** (S11, S29) | Retention settings for the audit log (default 400 days) and long-term memory (off by default); the daily job deletes older rows; documented. | 1 |
| 10 | **Drop `'unsafe-eval'`** (S11, S22) | Form validation works under `script-src 'self'` (an interpreting validator or precompiled schemas); CSP updated; the e2e suite passes under the stricter policy. | 1 |
| 11 | **Image digests and symlink test** (S11, S27/S3) | Base images in both Dockerfiles pinned by digest (Dependabot updates them); a workspace test proves a symlink cannot escape the root. | 1 |

**Load:** 23 days (≤ 24). If the sprint runs long, the comparison view in item 4 and variant overrides in item 3 move
to S13 first.

**Order:** S11 follow-ups that touch running systems first (sweeper, rate limits), then datasets → evaluators →
experiments → results UI → release gate, then the CSP and image follow-ups.

**Demo script:** import a CSV of 20 questions into a dataset; add an LLM-as-judge and a trajectory evaluator; run an
experiment comparing v1 and the draft with two models; open the grid and a regressed example's timeline; turn on the
prod gate and watch publishing refuse the weaker version.

**Exit:** the demo works end to end on compose prod; the security checklist has no partial P1 left from S11 except
the cloud-KMS live test; tag `v0.4.0` (maintainer's call).

**Review (2026-10-06).** All eleven items met their acceptance; CI ran green on every push, including the
digest-pinned images and the e2e suite on the production build under `script-src 'self'`. An end-of-sprint review
(backend, gates and masking, web) found and fixed, before merge:
- A builder could weaken an admin-only gate through what it measures with (delete the gating dataset, which
  cascaded to the gate, or lower an evaluator's threshold). Changing a gating dataset or an evaluator of a gated
  workflow now needs an admin.
- Publishing pinned the draft as it was *after* the evaluation; it now refuses (409) when the draft or the workspace
  moved meanwhile. Evaluation reuse also requires the same workspace commit (new `experiment.workspace_commit`).
- Examples whose run never started were left out of averages, so a gate could pass on the few that ran; they now
  score 0, and a gate fails when not every example finished. NaN scores counted as 1.0; they now count as unscored.
- A pathological regex (`(a+)+$`) froze the API worker, its heartbeats and so its runs' leases: regexes run in a
  worker process with a 2 s limit; code evaluators time out after 60 s; a failing experiment cancels its examples.
- Trying an evaluator used the default project's keys and code; masking missed run errors, span statuses and
  secrets that JSON escapes; the publish request held a database connection during the evaluation.
- Web: switching experiments with an output diff open crashed the page; the new validator rejected Python-style
  regexes in custom-format fields and mis-placed errors for keys containing dots.

Deviations and gaps, carried into S13 unless noted:
- `settings.trace_redact` covers each step's trace but not the graph's root run or model runs.
- Run and experiment leases have no owner: an instance stalled for over 60 s can be taken over while it keeps going.
- A gate measures with the dataset's latest version and the evaluators' current settings (protected by the admin
  rule above) rather than a snapshot.
- Datasets are not synced to LangSmith; pairwise, composite and human-annotation evaluators (FR-EVAL-03) are not built.
- Not carried: the experiment comparison uses each experiment's first target only (by design for now).

### S13 — Evaluation in production → v0.5.0
FR-EVAL-06 online evaluators (4) · annotation queues (3) · pairwise & composite evaluators (2) · FR-EVAL-08 rubric
(3) · FR-EVAL-07 simulated users (3) · LangSmith dataset sync (2) · S12 follow-ups (3) · record/replay tool I/O (2).

**Goal:** quality is measured after release too. Production runs are sampled and scored, drops raise an alert,
people review what the evaluators flag and turn it into dataset examples, and agents can grade and correct their own
work before answering.

**Backlog (planned at the end of S12, 2026-10-06).** Built on branch `claude/sprint-13-evaluation-in-production`
([#8](https://github.com/marunsct/AgentCanvas/pull/8)) from `main` after #5.

| # | Item | Acceptance | Est. |
|---|------|-----------|-----:|
| 1 | **FR-EVAL-06 Online evaluators** | Per deployment, attach evaluators with a sampling rate (0–100 %) and an optional filter (status, metadata); sampled finished runs are scored in the background after the response, never delaying it; scores stored per run and shown in the run list and timeline; daily and hourly aggregates per evaluator. A drift rule (mean below X, or Y points below the trailing 7-day mean) raises an alert in the app and on `/metrics` (Prometheus alert rule shipped). Judge cost counted separately from the run's cost. | 4 |
| 2 | **Annotation queues** | A queue per workflow fed by rules (online score below a threshold, a failed run, a thumbs-down from the deployment API) or by hand from any run; reviewers (role `reviewer`+) see input, output and the timeline, give a score per rubric criterion and a comment, and can add the run to a dataset with a corrected reference in one step; queue progress and inter-reviewer agreement shown. Org-scoped, audited. | 3 |
| 3 | **Pairwise & composite evaluators** (FR-EVAL-03) | A pairwise LLM judge compares two experiment targets per example (position-swapped to cancel order bias) and reports a win rate with a confidence interval in the comparison view; a composite evaluator combines others with weights or all/any rules. Both usable in experiments and release gates. | 2 |
| 4 | **FR-EVAL-08 Rubric self-grading** | A Rubric widget on Deep Agent and agent nodes (deepagents `RubricMiddleware`: rubric text, grader model, max iterations, tools) authored in the inspector; grading iterations stream as `ac.rubric` events and appear live in the run panel and timeline; codegen and export carry it. | 3 |
| 5 | **FR-EVAL-07 Simulated users** | For chat workflows, an experiment can drive a multi-turn conversation with a simulated user (persona, goal, max turns, stop condition) played by a catalog model; each conversation is one result with its full transcript, scored by evaluators that see every turn. | 3 |
| 6 | **LangSmith dataset sync** (FR-EVAL-02) | Push a dataset version to LangSmith and pull one back (examples, splits, metadata) when a LangSmith key is configured; the link is shown on the dataset; sync is explicit, never automatic. | 2 |
| 7 | **S12 follow-ups** | `trace_redact` also applied to the graph's root run and model runs (LangSmith and OTel); run and experiment leases get an owner, and a heartbeat that finds itself replaced stops its work; a release gate snapshots its dataset version and evaluator specs when an admin saves it. | 3 |
| 8 | **Record/replay tool I/O** (carried since S8) | Recorded runs capture tool inputs and outputs, and replays return them instead of calling the tools again; covered for HTTP, MCP and workspace tools. | 2 |

**Load:** 22 days (≤ 24). If the sprint runs long, simulated users (item 5) move to S14 first, then LangSmith sync.

**Order:** S12 follow-ups first (they touch the gate and leases), then online evaluators → annotation queues (fed by
them) → pairwise/composite → rubric → simulated users, with LangSmith sync and record/replay alongside.

**Demo script:** publish a support agent with an online LLM judge at 50 % sampling; send 40 requests through the
deployment API, a quarter of them off-topic; watch the hourly mean drop and the drift alert fire; open the
annotation queue, correct two answers and add them to the gating dataset; turn on a rubric on the agent and watch it
revise an answer live; run a pairwise comparison of the old and new prompt.

**Exit:** the demo works end to end on compose prod; online scoring adds no latency to deployment responses (p95
unchanged within noise on the load test); tag `v0.5.0` (maintainer's call).

**Review (2026-10-06).** All eight items met their acceptance in the test suite (343 API tests, 59 web unit tests).
An end-of-sprint review found and fixed, before merge:
- Drift alerts stayed open forever once their online evaluator was deleted, or a rule was turned off; they are now
  resolved then, and every pod resolves alerts no online evaluator watches any more.
- Scoring tasks or pods that saw a rule break at the same moment could each open an alert; a partial unique index
  (`eval_alert_one_open`) allows one open alert per deployment, evaluator and rule.
- Runs stopped for a hand-over on a redeploy were scored (as interrupted) and then scored again when continued;
  resumed and continued runs were scored without their input and labels. Hand-over stops are no longer scored, and
  continuations are scored with the thread's last fresh input and labels.
- A failed run sent to a queue by the "failed" rule showed reviewers no input; fresh failed runs now keep their
  input (not a full cassette: they still cannot become test cases or dataset examples directly).
- "Add to dataset" committed the example before the review was saved, so a refused review could leave one behind;
  the review is saved first. Two reviews of one item at once could leave it pending; items are locked while counted.
- Mirrored pairwise verdicts counted in each target's overall pass rate, which bent the gate's regression check; a
  pairwise verdict no longer decides whether an answer passed.

**Demo on compose prod (2026-10-06)**, two API replicas with demo models (`infra/load/compose.load.yml`; no provider
keys here, so a regex "stays on topic" evaluator stood in for the LLM judge). 40 requests through the deployment API
at 50 % sampling, the off-topic quarter in the second half: no alert after the first 20, then 20 scored runs, hourly
mean 0.65 and a `below` alert (`agentcanvas_eval_alert_open` = 1 on `/metrics`). Off-topic runs were queued (by hand
for those scored before the queue existed, by the rule for later ones); two corrected answers became a new version of
the gating dataset. The rubric ran a grading round live; the pairwise judge, the rubric grader and the simulated
user need a real model, so with the demo model they ended unscored or as `grader_error`. The demo found one defect,
fixed: a grader model that never returned a verdict looped for LangGraph's default ~10,000 steps and held the run;
the grader now gets 25 steps and ends the round as `grader_error`.

**Latency (exit criterion)**, `infra/load/deployment.js` against the API on the compose network, 30 requests/s for
60 s, every run scored (sampling 100 %), two rounds each: without online scoring p50 31/33 ms, p95 42/44 ms; with it
p50 33/33 ms, p95 52/51 ms. The first measurement was p95 +15-25 ms; per-run drift checks (now at most every 10 s
per evaluator and instance) and re-parsing the published workflow (now cached) were trimmed. Saturated (100 users back
to back, CPU-bound) throughput drops from 69 to 54 runs/s at 100 % sampling, since scoring shares the API pods' CPU.

Deviations and gaps, carried into S14 unless noted:
- Online scoring runs in the API pods (bounded to 4 tasks per pod), not in a separate worker: at 100 % sampling p95
  rises about 8 ms below saturation and throughput drops at saturation. A separate scoring worker is the S14 option.
- Simulated users run turn by turn on one thread; they do not branch or retry a turn.
- Replayed MCP tools take any arguments (their schemas are not recorded), and a replayed run's thread state cannot
  be viewed while the MCP server is unreachable.
- `test_validation_of_200_nodes_is_fast` (150 ms) failed once on a slow CI runner (473 ms) and passed on the next
  runs; watch it.

### S14 — AI Copilot I → v0.6.0

Before the sprint the whole codebase was reviewed (all gates green: API 364 passed; web 67 unit tests) and the
review's findings were fixed first (see the S14 review below).

| # | Item | Days | Acceptance |
|---|------|-----:|------------|
| 1 | Spike + eval harness | 3 | `agentcanvas.copilot.evals`: 10 plain-language build requests, each checked for a proposal that applies, validates without errors and uses the expected blocks; runs with any `provider:model`; CI runs it with a scripted model |
| 2 | Tool layer | 4 | `copilot/tools.py`: `get_workflow`, `find_blocks`, `get_block`, `get_template`, `set_brief`, `propose_changes` over the IR command API; proposals apply to a working copy only, failures come back to the model, secrets refused |
| 3 | Knowledge pack | 2 | `copilot/pack.py` generated from the registry (workspace blocks included) and templates plus a short guide; sent as a cacheable prefix |
| 4 | Copilot service | 5 | brief → plan → build loop with self-repair (`copilot/service.py`), SSE `POST /v1/workflows/{id}/copilot/messages`, conversations stored per person and workflow, prompt caching on Anthropic |
| 5 | Model setting | 2 | same model as the workflow or separate; tool-calling check; org model policies apply; token cap per conversation |
| 6 | Chat panel and brief card | 3 | Copilot tab, Build/Ask modes, live tool activity, brief card |
| 7 | Ghost proposals | 4 | proposal previewed read-only on the canvas (new green, changed amber); Accept = one undoable edit; Reject |
| 8 | Guardrails | 1 | Ask mode has read-only tools (viewers allowed); max 16 model calls per turn, 80 commands per proposal; workflow text marked as data; literal secrets refused; one turn at a time per conversation |

**Pre-sprint review (2026-10-07).** A review of the whole codebase found 11 defects, all fixed before S14 work:
two runs could start on one thread (double-clicked approvals; the thread slot is now taken before any await, and an
inbox item is decided once, 409 after); event-handler `resume` used the wrong project's resources and could not
resume deployment threads; an expired LangSmith sandbox was never replaced; secret masking missed values JSON escapes
and the live `ac.interrupt` event; the editor's `flush()` returned while a save was in flight (a run could use a stale
draft); the Memory page was 403 for non-admins; state edits and branch forks could change a thread with an active
run; deleting a thread did not wait for its cancelled run; `GET /runs/{id}/events` sent 200 before a 404; a label
with a newline could inject code into generated Python; signed webhooks could be replayed within the tolerance
window (now 409, recorded in the shared dedup table).

**Review (2026-10-07).** Every item met its acceptance in the tests (`test_copilot.py`, `e2e/copilot.spec.ts`).
Deviations and gaps:
- The eval harness has not been run against a hosted model here (no provider keys in this environment); CI runs it
  with a scripted model. Run `python -m agentcanvas.copilot.evals --model …` before relying on a model.
- Answers arrive per model turn, not token by token.
- "One turn at a time per conversation" is enforced per API instance; two replicas could each take one message.
- Removed elements are listed on the proposal card, not drawn as ghosts on the canvas.

### S16 — Any framework as a node → v0.10.0

| # | Item | Days | Acceptance |
|---|------|-----:|------------|
| 1 | Wrapped agent step (`agent.framework`) | 5 | an agent from the Workspace built with OpenAI Agents SDK, CrewAI, Google ADK, Pydantic AI, Agno, Strands, LlamaIndex Workflows, Microsoft Agent Framework or Claude Agent SDK (or a Python function) runs as one step in the interpreter and in generated code; prompt and output fields; factory per run; approval before it runs; `ac.agent` trace events; hosted edition refuses it (E041); exports install the pinned framework package |
| 2 | Remote agent (A2A) step | 3 | `message/send` to an Agent Card or endpoint; `input-required` pauses the run for a person and resumes the same task without sending twice; private addresses refused unless allowed; secret headers |
| 3 | MCP agent step | 1 | calls one tool of a wired `mcp.server` with templated arguments |
| 4 | Framework badge | 1 | each such block names its framework (or A2A, MCP) on the canvas |
| 5 | Open Agent Spec export and import | 4 | flows with LLM, agent, A2A, question and branching nodes, other steps as tools; an exact round trip of AgentCanvas files (layout included); import of any Agent Spec flow or agent with a report; Versions download and home-page import |
| 6 | Real-framework CI | 2 | a real agent of each framework at its pinned version runs as a step against a fake model server; exports load in pyagentspec |
| 7 | Docs | 1 | user guide section, changelog, handover |

**Review (2026-10-08).** Before the sprint the deploy kit (S19) was reviewed: Azure deployed "unset" for empty
settings, Cloud Run's service account could not read its secrets, and placeholder API keys counted as set; all fixed.
Deviations from the plan:
- The roadmap's "generated wrappers" are one generic step that calls each framework's own entry point (named in
  `interop.FRAMEWORKS`), not generated code per framework; the agent stays the user's own code in the Workspace.
- Agent Spec files are JSON (YAML is not read); routers export as branches only for rules like `state.x == "v"` on
  one field, and agents' tools and middleware are not exported (both listed in the report).
- The real-framework job uses a fake model server, so it proves the wiring, not model quality; the Claude Agent SDK
  runs its bundled CLI against the fake Anthropic API.

### S17 — Workspace and editor fixes → v0.11.0 (✅ done)

Reported by the owner on 2026-10-09. Causes marked *(checked)* were confirmed in the code; the others are the likely
cause and are confirmed at the pre-sprint review.

| # | Item | Role | Days | Acceptance |
|---|------|------|-----:|------------|
| 1 | **Way back from the Workspace** | F | 1 | Opening the Workspace from a block or the editor shows "Back to *workflow name*" and returns to the same workflow, selection and canvas position; the browser Back button does the same; the Workspace page shows which workflow it belongs to. *(checked: `/workspace` is one route with no workflow in it, `apps/web/src/app/App.tsx`)* |
| 2 | **A workspace per workflow** | B, F | 4 | Each workflow has its own code folder (`workflows/<workflow>/` with `scripts/`, `tools/`, `nodes/`, `events/`); a shared `lib/` holds code several workflows use; existing projects migrate (current files move to `lib/` so nothing breaks); import paths, the code editor's file tree, secrets and Copilot tools follow the workflow; versions pin the workflow's own code. *(checked: one workspace per project today, `workspace_commit` is the project's head)* |
| 3 | **Exports bundle the right code** | B | 2 | An export contains the workflow's own folder, every `lib/` module it imports (found by following imports, not by copying everything), data files under those folders and a `requirements` list the workflow's code declares; other workflows' code is left out; an export test fails if any `import_path` in the workflow cannot be imported inside the exported project. *(checked: `packager.py` copies only `scripts/ tools/ nodes/ events/` of the whole project, `WORKSPACE_DIRS`)* |
| 4 | **Copilot in the code editor** | B, F | 4 | In the Workspace editor: "Write with Copilot" turns a plain description into a function with the right signature for the block that uses it (script step, tool, custom block, event handler); "Explain" and "Fix" on the open file or a selected range, using the ruff markers; changes arrive as a diff to accept or reject, never written silently; starter snippets per file type for beginners; works with the workflow's Copilot model and its privacy mode |
| 5 | **Every button labelled** | F | 2 | Icon-only buttons get visible text or a tooltip plus an accessible name; the list fields in block settings ("Tools that need approval" in the AI agent block and every other list) show "Add", "Remove", "Move up" and "Move down"; a new list item starts empty instead of `null`, so it no longer shows "Instance type null is invalid"; a Playwright check with axe's `button-name` rule runs on the editor, inspector, Workspace, Versions, Copilot and settings pages and fails on any unlabelled button. *(likely cause: the settings form library's default list buttons use an icon font the app does not load)* |
| 6 | Docs | all | 1 | User guide Workspace page (per-workflow code, shared library, Copilot in the editor), changelog, handover |

**Demo:** open the AI agent block, add a tool that needs approval with labelled buttons, open its code in the
Workspace, write the tool with the Copilot, go back to the same spot on the canvas, export and run the export's tests.

**Review (2026-10-09).** The pre-sprint review confirmed every cause in the table, including the unlabelled list
buttons (the settings form library's default buttons draw glyphicons, a font the app does not load, and a new list
item started as `null`). It also found that workspace files could not import each other in the server process
(`from scripts.x import y` failed outside the sandbox); fixed, so code imports the same way in a run and in an export.
Deviations from the plan:
- Shared code stays where it was (`scripts/`, `tools/`, `nodes/`, `events/` at the workspace root) instead of moving
  to `lib/`: existing import paths keep working, so no migration is needed. A workflow's own code is in
  `flows/<workflow id>/{scripts,tools,nodes}/`, imported as `flows.<id>.scripts.<file>:<function>`.
- Event handlers and secrets stay per project (an event can start any workflow); custom block types written in a
  workflow's folder are still registered for the whole project.
- Editing a helper module does not reload the files that import it until they change too (the hot reload follows
  the file a step names).
- The unlabelled-button check is a Playwright check written for the app (any visible button whose name has no letter
  or digit fails), not axe, to avoid a new dependency; it runs on every editor tab, dock panel and page.
- Copilot code help uses the Copilot model and privacy settings of the workflow's project, like the chat.

### S20 — Framework foundation → v0.12.0 (✅ done)

| # | Item | Role | Days | Acceptance |
|---|------|------|-----:|------------|
| 1 | Adapter interface v2 | R | 5 | `FrameworkAdapter` split into a required core (generate, package, execute, canonical events) and optional abilities (thread state, time travel, memory store, MCP inspection) declared in a manifest; model calls for the Copilot and LLM judges move to a framework-neutral model service; the LangChain adapter passes unchanged |
| 2 | Adapter per workflow | R, B | 3 | The runtime, state, memory and export routes pick the adapter from `workflow.framework`; features an adapter lacks are hidden or refused with a plain message |
| 3 | IR v1.2: `engine` per node | R | 2 | Optional `engine` on agent blocks, validated against installed adapters; schema regenerated; old workflows load unchanged |
| 4 | Framework picker and portability check | F, R | 4 | A workflow framework picker and a per-block engine picker; P001–P004 diagnostics with help entries; a badge row showing which frameworks the workflow can target and what blocks each |
| 5 | Conformance kit | R | 3 | C-AGENT, C-FLOW, C-HITL, C-EVENTS suites with the fake model server, run against every installed adapter in CI |
| 6 | Toy second adapter | R | 2 | An "echo" adapter passes the kit, proving the seams |
| 7 | Docs | all | 1 | ADR for interface v2, user guide "Choosing a framework", changelog, handover |

**Review (2026-10-09).** The pre-sprint review found that S17's per-workflow Script steps (`flows.<wf>.scripts.`)
bypassed the project sandbox (the sandbox only matched `scripts.`), and that the Copilot's copy of the user guide had
missed the S17 pages; both fixed. Deviations from the plan:
- Model and tool resolution (`Resources`) is still the LangChain adapter's object, handed to every adapter; the
  model service uses LangChain's provider integrations. S21 gives adapters resources of their own.
- Long-term memory, MCP browsing and judges stay on the host adapter (LangChain); they are per project, not per
  workflow. A workflow whose framework lacks conversation state still shows its conversations, without values.
- The conformance kit covers C-FLOW, C-AGENT (model replies, no tool calls yet), C-HITL (approve, reject, edit,
  re-asked question) and C-EVENTS; tool-calling agents, subgraphs, resume in a new process and C-STATE/C-PERSIST join
  as the adapters that need them arrive. It runs inside the api CI job rather than a separate one.
- The badges and the engine picker compute portability in the browser from `GET /v1/frameworks` (the same rules as
  the server), so they update on every edit without a request.
- Echo is a demo engine: off unless `AGENTCANVAS_ADAPTERS` names it, paused runs in memory, export is the workflow
  file only, and the LangGraph deploy kit refuses its workflows with a plain message.

### S21 — Design any agent → v0.13.0 (backlog, not started)

| # | Item | Role | Days | Acceptance |
|---|------|------|-----:|------------|
| 1 | Agent codegen per framework | R | 8 | The canvas Agent block (instructions, model, tools, structured output) generates a native agent for OpenAI Agents SDK, CrewAI, Google ADK, Pydantic AI, Agno, Strands, LlamaIndex, Microsoft Agent Framework and Claude Agent SDK; golden-file tests per framework |
| 2 | Tools and models across frameworks | R | 4 | Python, HTTP and MCP tools and every catalog model (local ones included) reach each framework's agent; what a framework cannot take is a P-diagnostic, not a run error |
| 3 | Mixed flows | R | 3 | A drawn agent with a non-LangChain engine runs in the LangGraph host through the S16 step, keeping approvals, traces and the inbox; tool-level approvals where the framework has them |
| 4 | Single-agent export per framework | B | 4 | Export one agent as a native project for its framework (its own runner, pinned package, README, tests) |
| 5 | Copilot builds for a framework | B | 2 | The Copilot proposes engine choices and explains portability problems |
| 6 | Real-framework CI | D | 1 | The `frameworks` job runs every drawn agent, mixed flow and single-agent export against the fake model server |
| 7 | Docs | all | 1 | User guide "Mixing frameworks", templates, changelog, handover |

### S22 — Whole flows, preview → v0.14.0 (backlog, not started)

| # | Item | Role | Days | Acceptance |
|---|------|------|-----:|------------|
| 1 | Agent Spec bridge export | R | 6 | Portable workflows export as runnable projects for CrewAI, OpenAI Agents, AutoGen and LangGraph through pyagentspec loaders, marked preview; P004 "bridge used" diagnostic |
| 2 | Bridge CI | D | 3 | 5 templates export and run on 3 frameworks against the fake model server |
| 3 | Gaps report | R | 2 | What each bridge cannot express is listed in the export and in the portability panel |
| 4 | Export dialog | F | 2 | Framework choice in the export dialog with preview labels |
| 5 | Docs | all | 1 | User guide, changelog, handover |

### S18 — AI Copilot II and debug assistant → v0.8.0

| # | Item | Days | Acceptance |
|---|------|-----:|------------|
| 1 | Configurator | 3 | build prompt fills required settings; `list_models` (models, providers, secret names only) |
| 2 | Tester and fix loop | 3 | `run_test` runs the working copy as a dry run, optional scripted model replies and expectations |
| 3 | Brief to tests | 2 | `propose_tests`; tests are saved as test cases when the proposal is accepted |
| 4 | Debug assistant | 6 | `get_problems` (explained), `list_runs`, `get_run` (Explain-mode steps), `reproduce_run` replays a run's recorded model and tool I/O on the working copy; Ask-the-Copilot buttons in Problems and run errors |
| 5 | Error catalog | 1 | one catalog (`validation/help.json`) for the editor and the Copilot's pack |
| 6 | Debug eval set | 2 | `--suite debug`: 27 planted bugs across the starter templates |
| 7 | Docs search | 2 | BM25 over the user guide, packaged with the API |
| 8 | Project Copilot notes | 2 | notes in Copilot settings, read every turn; `save_note` proposals |
| 9 | Split/privacy modes | 1 | `ask_model` for questions; `privacy: local_only` refuses non-local models |
| 10 | Docs | 1 | user guide Testing, Debugging, Project notes sections |

**Review (2026-10-07).** Before the sprint the codebase was re-checked (all gates green after S14's merge). Every
item met its acceptance in `test_copilot_debug.py` and `e2e/copilot.spec.ts`. Deviations:
- Reproductions use the *current draft* to explain past runs; a deployment thread pinned to an older version is
  explained against the draft.
- The debug eval suite has only been run with a scripted model here (no provider keys).
- Test runs are dry runs only; the Copilot cannot run a workflow for real.

**Finished 2026-10-08** (owner's request) after S15, in #19. Review against S15, fixed: a follow-up turn starts from
the open proposal, so a tests-only proposal no longer drops its changes and test runs see the fix; a new or changed
local (stdio) app connector counts as unaccepted code; reproductions replay recorded tool output instead of the
dry-run stub; deployment runs are explained against their pinned version (the first deviation above no longer
applies); the question model is checked when settings are saved.

### S19 — Export deploy kit → v0.9.0

| # | Item | Days | Acceptance |
|---|------|-----:|------------|
| 1 | Service with the deployment API | 4 | `app.py`: runs/wait, runs/stream, threads/{id}/resume, runs/{id}/feedback, healthz, docs; API keys; same shapes as AgentCanvas deployments |
| 2 | Startup checks and checkpointer by environment | 1 | Postgres with `DATABASE_URL`, else SQLite; the service stops at startup with a plain message naming missing settings |
| 3 | Dockerfile and Compose | 2 | non-root, health check, pinned base images; service plus Postgres; Caddy with automatic HTTPS for servers |
| 4 | Target packs | 4 | Helm chart (Deployment, Service, Ingress, HPA), Cloud Run, ECS Fargate, Azure Container Apps; only the chosen target in the zip |
| 5 | Guide generator | 3 | 1-PREREQUISITES, 2-SETUP, 3-DEPLOY, 4-VERIFY-AND-OPERATE with this workflow's keys, hosts and settings |
| 6 | `make doctor` and `make smoke` | 3 | tools, settings, database, hosts, port, `--live` model calls, each problem pointing at its guide section; smoke runs the example input and answers approvals |
| 7 | Tests and gate in the export | 2 | saved test cases and the service's API tests (offline, `DEMO_MODE`); `make gate` |
| 8 | Export dialog | 2 | version and target picker; what the target needs before download (`GET …/export/plan`) |
| 9 | CI | 3 | every template to every target; deploy files linted; each export's own tests with its own pins; Compose and kind deployed for real with doctor and smoke |

**Review (2026-10-07).** Before the sprint the whole codebase was reviewed (7 findings, fixed in #17: Copilot
test runs refuse unaccepted code and secrets sent to a new address, late Copilot answers stay with their workflow,
Accept is recorded only when the edit lands, one Copilot turn at a time with merged saves, the question model keeps
its endpoint, policy codes treated as in the Run panel). Deviations from the plan:
- `make gate` runs the saved test cases and the service's API tests; LLM-judged evaluations stay in AgentCanvas.
- Feedback is logged (and written to `FEEDBACK_FILE`), not stored in a database.
- Cloud targets (Cloud Run, AWS, Azure) are syntax-checked in CI only, since CI holds no cloud accounts; the parity
  script against a live AgentCanvas deployment is replaced by the service's API tests using the same request and
  response shapes.

**Finished 2026-10-08** (owner's request) after S15, in #19. Review against S15, fixed: a resume runs with the
context of the run that paused (kept in the checkpoint metadata; user-scoped memory failed after an approval);
feedback finds a run's thread in the checkpoints, so it works across replicas and restarts; `make smoke`, `make gate`
and the generated curl send the required context fields; the MCP and A2A addresses (S15) are documented as not part
of the exported service.

### Later — GA backlog (prioritised quarterly)
Multiplayer (Yjs) · persistence bindings & RoutedStore · Data Studio · learning loops (ADR 0014) · A2A/AG-UI exposure & chat widget · FR-X-03 headless tools · FR-X-04 generative UI · FR-X-16 multimodal · FR-X-21 per-user connections · FR-X-05/06 decision models & Engine bridge · FR-X-09 stream projections · FR-X-11 ACP · FR-X-12 trace import · FR-X-20 PR visual diffs · FR-X-23 budget-aware routing · second framework adapter (doc 12).

### S15 onward

Scope, exit criteria and ordering for S15–S36 are in the [competitive roadmap §7](../16-competitive-roadmap.md#7-sprint-plan),
approved on 2026-10-07 and re-planned on 2026-10-09 after the [framework coverage review](../17-framework-coverage-review.md).
S17 and S20–S22 have backlogs above; the others get theirs when the sprint starts, after the pre-sprint codebase
review. Framework flow sprints (S25, S27, S29–S30, S32–S36) follow their [adapter specs](../adapters/README.md).

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
| S1 | ✅ done | IR v1.1 (storage mode, context, policies, settings, data edges); canonical hash; node registry (8 manifests) + model catalog; rule-based validator (26 codes incl. E041/E042/E080); invertible command API with Hypothesis property test; SQLAlchemy models for the 14 control tables + Alembic migration (Postgres & SQLite); settings, prefixed ULIDs, structlog request ids, problem+json; `/v1` projects, workflows CRUD with `If-Match`/409/428, commands, layout, validate, code, node-types, models, templates, IR schema, expression check; IR JSON Schema exported to `packages/ir-schema` | 89 API tests green on SQLite **and** Postgres 16; 200-node validation < 150 ms; e2e still green. Deviation: SQLite supported as the local-edition control DB (no Docker needed) |
| S2 | ✅ done | Adapter boundary (`adapters/spi.py`, `adapters/langchain/`: plan, interpreter, codegen, events, persistence, adapter) + import-linter contract; Execution Plan shared by interpreter and codegen; node policies (retry/timeout/cache/error route/trace), node defaults, bypassed nodes, input/output/context schemas, DeltaChannel storage; framework-neutral runner (background tasks, replayable event buffer, cancel, double-texting 409); persistence bindings memory/SQLite/Postgres; `/v1` runs (stream, attach, cancel), threads (list, state, history with per-step writes, state edit, fork branch/copy, continue after breakpoint, delete), inbox (list, decision); stateless demo model for key-less dev; web skeleton on `/v1` | 108 API tests (+1 strict xfail canary) green on SQLite **and** Postgres; restart-and-resume verified; e2e green. Found 3 upstream issues (FAQ P-13 checkpoint_ns on SQLite, P-14 `acopy_thread` unimplemented, P-15 error handlers bypassed when streaming messages/custom/subgraphs) with workarounds + canaries |
| S3 | ✅ done | Editor app: router (home, editor, 404), TanStack Query hooks, typed API client (problem+json), types generated from `ir.schema.json` and OpenAPI component schemas; Zustand editor store with optimistic local commands, debounced command queue with `If-Match`, 409 reload notice, undo/redo from server inverses; manifest-driven widget/resource/terminal renderers with typed, colour-coded ports and status/diagnostic badges; type-checked connections with refusal reasons; palette (search, drag & drop, double-click); RJSF inspector with prompt/channel/CEL/model widgets, node policies editor (retry, timeout, cache, error route, trace, bypass), state designer (type, reducer, storage, io, chat preset), workflow settings; problems dock (click to focus); run panel (input, approvals, streamed & final output); code tab with source-map highlighting of the selected node (FR-X-01 v0) | 19 web unit tests; 4 Playwright journeys (build from empty canvas → validate → run → reload; E001 + undo; two-tab conflict; template run with approval/rejection). **SP-1:** 500 nodes / 976 edges — save 83–132 ms, command 128–331 ms, editor load 0.8 s (prod build; 1.9 s dev), panning ~61 fps (headless Chromium) → React Flow is sufficient; no WebGL layer needed for R1 |
| S4 | ✅ done | Run & debug UX: breakpoints (per node, remembered locally) → debug runs → Continue; Stop (cancel); JSON or Chat input (chat when `messages` is the only input; toggle); decision cards for tool approvals (editable args, reject reason), approvals (editable value) and input requests (RJSF from `response_schema`); Inbox page across workflows with pending count in the top bar; bottom-dock Timeline (checkpoint ticks, state view, Save edit, Fork from here, Fork to new thread) and **Explain mode** (FR-X-17: server-side narrative per super-step with channel writes and router decisions re-evaluated with the runtime's CEL); node highlight from timeline/explain; token preview on nodes with rAF batching; ELK "Tidy" layout; copy/paste with re-IDs and unique names; ⌘K command palette and double-click quick-add; **Live Code Mirror** v1 (FR-X-01: changed generated lines highlighted after each edit) | 109 API tests (+1 canary); 22 web unit tests; 9 Playwright journeys green (adds breakpoint/continue, timeline fork into the other branch + explain, multi-turn chat, inbox approval, tidy/code-diff/copy-paste/palette) |
| S5 | ✅ done | **Node catalog I** — 16 new types: middleware widgets (PII, model/tool call limits, model/tool retry, model fallback with wired models, summarization, to-dos, tool selector) with upstream defaults; `core.agent` ordered middleware + structured output (`response_format` → `structured_response`); **Deep Agent** (`agent.deep`: planning toggle, subagents, approvals incl. built-in tools, file permissions, structured output) with `agent.subagent` and file backends (`backend.state`, `backend.store` mounted at a folder, shared per user / workflow / thread); `core.human_input` (validated answers, re-ask); `core.map` (Send fan-out with `each`/`empty` outputs, carry, max items); `core.join` (multi-source edge). Plan: resources in dependency order (resource→resource wiring), map/join edges. Validator: E016, W013, E022, E030 (structured output), E032, E033 (built-ins), E034, E035, E036, E071, E004 (embedded schemas). Runs store their context (resume/decide/fork reuse it — FAQ P-18). Editor: named flow outputs, input ports on resources, structured-output designer (fields ↔ JSON), middleware reorder, typed sample input. Template **Research brief** (map-reduce + Deep Agent writing `/memories/`) | 128 API tests (+1 canary) on SQLite **and** Postgres (checkpoint/store tables now truncated between tests); 32 web unit tests; 11 Playwright journeys. Found & fixed a latent S3 bug: nested merge patches (FAQ F-05). Upstream notes FAQ P-16…P-19 |
| S6 | ✅ done | **Node catalog II & debug** — `mcp.server` (langchain.mcp beta; http + secret headers, stdio admin-gated E042; async discovery per graph build; Browse tools with destructive/read-only hints cached for W002/E033; generated `abuild_graph()`), `tool.http` (URL template over args, JSON-Schema args, secret headers, domain allow-list, errors as tool errors); memory: `core.memory_recall/remember/forget` + `tool.memory` (namespace space/scope/owner shared with Deep Agent folders; keyword fallback without an index) and the **Memory inspector** (`/v1/memory`, page with search/delete/erase); **cost & latency** per node (usage callback → CUSTOM `ac.usage`, catalog pricing in the runner, `duration_ms` on steps, run totals on the run row); **budgets** (tokens/USD/seconds per run, tokens/USD per conversation; `budget_exceeded`; W015); **pins & mock models** for debug runs; **channel lineage** in the timeline; trace policy **summarize**; **subgraphs** (`core.subgraph`, convert/inline refactorings as undoable command batches, nested builders in exported code, drill-in view with nested activity, approvals inside subgraphs). Visual-only groups (layout frames) deferred to the S9 buffer | 152 API tests (+1 canary) on SQLite **and** Postgres; 36 web unit tests; 13 Playwright journeys. Fixed along the way: multi-selection lost on every store change (F-06), handles moving under the cursor + layout saves overtaking commands (F-07), inbox journey not isolated. `make check` runs every CI gate locally; CI uploads Playwright traces on failure |
| S7 | ✅ done | **Workspaces & events** — per-project workspace (git repo, every save a commit, safe file API) with a CodeMirror editor and ruff markers; `core.script` backed by `scripts/*.py`, hot-reloaded by workspace revision; **sandboxes** `inprocess | subprocess` (scrubbed env, rlimits, sockets blocked) `| docker` (`--network none`, read-only, caps dropped) `| langsmith` (spike **SP-2**: one warm LangSmith Sandbox per project, workspace uploaded as one archive on change, same stdlib child protocol); **sandbox policy** `sandbox.json` (FR-X-08 basic: limits, egress allow-list, header injection by the auth proxy with secrets as write-only values; E082/E083/W082/W074); workspace tools (`@tool`) and **custom nodes** (`@canvas_node` with Pydantic/JSON-Schema config) found by AST scan, in the palette, validated, run in both modes and exported; **secrets** (Fernet at rest, write-only API, `{"$secret"}` refs, `sdk.secret()`, masked in every run event); **events as code v0** (`@on(type, filter=, dedup_key=)`, recorded actions start/resume/emit, JSON-Schema payloads + E081, run lifecycle events, `sdk.emit`, loop guard, dry-run, Events page) | 170 API tests (+1 canary; docker sandbox test runs where a daemon exists) on SQLite **and** Postgres; 37 web unit tests; 18 Playwright journeys (J3: custom node + event handler starting a workflow). SP-2 report: `build-guide/spikes/SP-2-langsmith-sandbox.md` (open questions need a LangSmith key). CI caught a Playwright substring match after part 3 (FAQ F-08); test DB truncation now tolerates older revisions |
| S8 | ✅ done | **Export & publish** — **versions** (semver snapshots of IR + layout + workspace commit, structural diff with per-field changes, compare on canvas, restore); **saved test cases** (status / exact channels / route; pins and mock replies; run against draft or a version; never trigger handlers); **export** as a standalone uv project (generated graph, vendored node library, workspace code, pinned stack, .env.example, Dockerfile, `langgraph.json` → `ServerRuntime` factory, CLI, offline replay of mocked test cases); **publish** (checklist: validation, tests, secrets + informational contract diff; deployment per environment behind a hashed API key; `runs/wait|stream`, resume; threads pinned to their version; rotate/unpublish); **triggers** (HMAC-signed webhooks with input mapping, cron scheduler); **tracing** (deterministic LangSmith root trace ids, metadata, Open trace); **templates** (ticket triage, approval, data extraction, RAG Q&A + existing); **record/replay** (per-resource model-reply cassettes → regression tests); **contract view** & breaking-change diff | 196 API tests (+1 canary) on SQLite **and** Postgres; 39 web unit tests; 22 Playwright journeys. Demo path verified: publish v1 → call with the key; signed webhook; exported zip passes `uv sync && uv run pytest` and serves under `langgraph dev`. Spikes: SP-3 v3 streaming not ready (keep v2), SP-4 ServerRuntime factory works (`build-guide/spikes/`). Inbox now lists newest first |
| S9 | ✅ done (release tag pending) | **MVP hardening → v0.1.0** — **blast radius** (FR-X-02: capabilities per step from manifests, declarations, wired HTTP/MCP tools and subgraphs; gated by tool approvals or an approval on every path; canvas overlay) and **dry runs** (risky tools replaced by recorders, `ac.dry_run` events); **guided build** (FR-X-24: six-step tutor checked against the real canvas); **loading / error-with-retry / empty states** on every screen and error boundaries per page and panel; **visual groups** (labelled frames in `metadata.groups`, deferred from S6); **lazy-loaded pages and ELK** (entry bundle 2.8 MB → 0.3 MB); **v0.1.0** version, `CHANGELOG.md`, MkDocs documentation site (`make docs`, CI job) | 198 API tests (+1 canary); 44 web unit tests; 27 Playwright journeys. Not done here: the `v0.1.0` git tag (needs the maintainer) and the exit criterion — 10 design partners completing J1 unaided — which needs real users |
| S10 | ✅ done (merged in #3; tag pending) | **Accounts & access** ([#3](https://github.com/marunsct/AgentCanvas/pull/3), branch `claude/project-thread-sj4pqp`) — **OIDC sign-in** (M7-T1: bearer JWTs checked against the issuer's JWKS, users provisioned on first sign-in, bootstrap admins, `AUTH_MODE=dev` refused in prod; SPA authorization code + PKCE with oidc-client-ts as a lazy chunk); **organisations** (M7-T2: every id a request names is checked against the caller's organisation, 404 otherwise; org-filtered thread and inbox lists; `POST /v1/projects`; the web app picks its project at startup); **roles** (M7-T3: viewer / reviewer / builder / admin by method and route, member invitations claimed at first verified sign-in, view-only editor, Members page); **audit log** (M7-T4: every successful change with actor, target and IP; decision payloads hashed; admin page with filters); **policies** (FR-X-19: organisation rules as CEL over steps, resources or the workflow, reported by the validator as `E095`/`W095`/`E096` and enforced per environment at publish, subgraphs included; Policies page); **memory per organisation** (long-term memory under an `org:<id>` root, inspector limited to the caller's organisation, pre-S10 memories adopted by the default organisation) ; **end-of-sprint review** (policy checks converted once for CEL, 6 s → under 0.4 s for 120 steps; policies enforced at publish only, not on draft runs; reviewers resume without debug aids; an organisation always keeps a signed-in admin; deployment end users namespaced `api:<deployment>:<user_id>`; pre-S10 memories adopted once; web token renewal, sign out, role-gated actions) | 252 API tests (+1 canary); 51 web unit tests; 31 Playwright journeys |
| S11 | ✅ done (merged in #4; tag pending) | **Production readiness** ([#4](https://github.com/marunsct/AgentCanvas/pull/4), branch `claude/sprint-11-production-readiness`) — **containers** (M8-T1: multi-stage non-root API image with git, unprivileged nginx web image with an `API_UPSTREAM` template, unbuffered SSE, immutable caching for hashed assets only); **compose prod and Helm** (M8-T2: `infra/compose.prod.yml` with a one-shot migration and two API replicas; Helm chart with probes, migration hook Job, RWX workspaces volume, Ingress and NetworkPolicy; `/readyz` checks the database and the run store); **cron across replicas** (each minute claimed by a conditional update of `trigger.last_fired`); **observability** (M8-T3: OTel traces for FastAPI, SQLAlchemy and httpx, Prometheus `/metrics`, Grafana dashboard and alerts); **retention and backups** (M8-T4: daily conversation retention, backup/restore scripts and runbook); **safe redeploy** (FR-X-10: drain on shutdown, `Run.handover`, another instance resumes; resume-compatible version check); **load test** (M8-T5: k6 on compose prod, 200 concurrent streams, 5,875 runs, 0 failed, first-event p95 89 ms; requests release DB sessions once streaming starts; pool settings); **security review** (M8-T6: checklist §7 with evidence, no open P0; KMS envelope encryption with rotation; timestamped webhook HMAC; HTTP tool refuses internal addresses; sandbox user and kill on timeout; secret masking in state, history, explain and logs; encrypted checkpoints; CSP and nginx rate limits; pip-audit, pnpm audit, Trivy, Dependabot); **S10 carry-overs** (invitation acceptance, last-used organisation, silent refresh, project per request) | 281 API tests (+1 canary); 51 web unit tests; 31 Playwright journeys (also under the production CSP). Review and deviations in §3 S11 |
| S12 | ✅ done (merged in #5; tag pending) | **Evaluation Studio** ([#5](https://github.com/marunsct/AgentCanvas/pull/5), branch `claude/sprint-12-evaluation-studio`) — **orphaned-run sweeper** (item 7: `Run.heartbeat_at` refreshed each hand-over tick; runs without a heartbeat for 60 s are marked `interrupted` and handed over); **API rate limits** (item 6: per deployment key, webhook and project per minute, counted in the control database so they hold across replicas without Redis; 429 with `Retry-After`; per-environment override); **datasets** (item 1, API: `/v1/workflows/{id}/datasets`, `/v1/datasets/{id}` with examples, versions, export, import CSV/JSONL, from test cases and runs; every change an immutable `dataset_version`, so experiments can pin one); **evaluators** (item 2, API: exact, regex, JSON Schema, LLM judge with rubric and catalog model, trajectory strict/unordered/subset, workspace code; each scores 0-1 with pass/fail at a threshold and a comment, never raises; `POST /v1/evaluators/{id}/try` scores a hand-written sample. Deviations: code evaluators live in `scripts/` (the sandboxed import path) rather than `evals/`; the judge and matchers are written here, not with `openevals`/`agentevals`, to keep LangChain inside the adapter); **experiments** (item 3, API: draft and published versions × a pinned dataset version (optionally one split) × variants that replace config values on named resources or steps; each example an ordinary `origin="test"` run with bounded concurrency; results keep thread, output, tool calls, scores, tokens, cost and latency; per-target mean score and pass rate per evaluator, overall pass rate, p50/p95 latency, tokens and cost; cancellable from any instance; a heartbeat lets another instance take an experiment over after a redeploy or a lost instance and run the examples that have no result yet); **results UI** (item 4: Evaluation page per workflow behind the editor's *Evaluate* link: datasets (create, add examples, import CSV/JSONL, add saved test cases, older versions read-only, export), evaluators (add per kind with a starting config, try on a sample), experiments (draft, versions and override variants; live progress; cancel), results grid of examples × targets with scores, per-example output diff and a link that opens the run's timeline in the editor (`?thread=`), and a comparison of two experiments with per-evaluator deltas and improved/regressed examples); **release gate** (item 5: `PUT /v1/workflows/{id}/gates/{environment}` (admins) names a dataset, minimum mean scores per evaluator (default: each evaluator's threshold) and a maximum pass-rate drop against the live version; publishing there adds an `evaluation` checklist item that reuses an experiment on the exact IR, dataset version and evaluator configs or runs one (with the live version as a second target only when its evaluation is missing), and refuses when a threshold fails; admins publish over it with `override_reason`, kept in the audit row; gates are per workflow and environment); **trace masking** (item 8: every known secret value is masked as `***` in LangSmith run inputs, outputs and metadata (a masking `langsmith.Client` installed with `langsmith.configure`) and in exported OTel span and event attributes (`MaskingExporter`); a workflow's `settings.trace_redact` names fields whose values are `[redacted]` in every step's traced input and output, composed with the step's own trace policy, also in exported code); **audit and memory retention** (item 9: the daily retention job also deletes audit rows older than `AGENTCANVAS_AUDIT_RETENTION_DAYS` (400) and, when `AGENTCANVAS_MEMORY_RETENTION_DAYS` is set (off by default), memories not updated for that long; documented in 09 §7); **no `'unsafe-eval'`** (item 10: RJSF forms validate with `@cfworker/json-schema`, which interprets schemas; `script-src 'self'` in nginx; CI's e2e job runs the production build under the production CSP (`vite preview`) and every journey fails on a CSP violation); **image digests and symlink test** (item 11: base images pinned by digest in the FROM lines, where Dependabot can update them, mirrors via `--build-context`; symlinks out of a workspace are refused and not listed) | 313 API tests (+1 canary); 58 web unit tests; 33 Playwright journeys |
| S13 | ✅ done (in review in #8; tag pending) | **Evaluation in production** ([#8](https://github.com/marunsct/AgentCanvas/pull/8), branch `claude/sprint-13-evaluation-in-production`) — **S12 follow-ups** (item 7: `trace_redact` on the root run and model runs; run and experiment leases carry their owner and a heartbeat that finds itself replaced stops its work; release gates snapshot their dataset version and evaluator specs when saved); **online evaluators** (item 1: per deployment, an evaluator with sampling (deterministic per run) and a filter on status and caller metadata; a runner finish hook only schedules scoring; `RunScore` rows with judge tokens and cost; hourly and daily buckets, scored runs, per-thread scores; drift rules (window mean below a value, or a drop below the trailing 7-day mean) open and resolve `eval_alert` rows, exported as `agentcanvas_eval_alert_open` with an `AgentCanvasEvaluationDrift` rule; *Production* tab and score badges on the timeline); **annotation queues** (item 2: per workflow, fed by rules (failed run, low online score, thumbs-down through `POST /v1/deployments/{id}/runs/{run_id}/feedback`) or by hand; reviewers score each rubric criterion, comment and add the run to a dataset with a corrected reference in one step; reviews also stored as `RunScore` rows (`source="annotation"`); progress and pairwise agreement; *Queues* tab); **pairwise and composite evaluators** (item 3: a pairwise judge asked twice with positions swapped, win rate with a 95 % interval (normal approximation), experiments only and in gates against the live version; composite by weights or all/any); **rubric** (item 4: `mw.rubric` widget on deepagents `RubricMiddleware` with grader model and tools; `ac.rubric` events live in the run panel and kept as `RunScore` rows; codegen and export carry it); **simulated users** (item 5: an experiment option for chat workflows; a catalog model with persona, goal (or the example's `metadata.goal`), max turns and stop phrase drives the conversation on one thread; the transcript is the result's output); **LangSmith sync** (item 6: explicit push of a dataset version (creates or reuses the linked dataset, replaces its examples, tags `agentcanvas-v<N>`) and pull into a new version keeping example ids; context and trajectory ride in metadata); **record/replay tool I/O** (item 8: tools built by `Resources.tool` are tagged with their resource; `ac.tool_io` events become cassette entries `{tool, args, output \| error}` under the resource; replay answers from them (same arguments first) and never calls HTTP, MCP or workspace tools; MCP replays need no server); end-of-sprint review fixes, compose-prod demo and deployment latency check above | 343 API tests (+1 canary); 59 web unit tests |
| S16 | ✅ done (tag pending) | **Any framework as a node** — agents from nine frameworks, remote A2A agents and MCP tools as canvas steps with approvals and traces (`adapters/langchain/interop.py`, vendored into exports); framework badges; Open Agent Spec export and import (`agentcanvas/agentspec.py`); CI `frameworks` job running a real agent of each framework against a fake model server and checking exports with pyagentspec; deploy kit review fixes | API, web unit and Playwright suites green locally; GitHub Actions had no runners on 2026-10-08 (see HANDOVER) |
| S17 | ✅ done (tag pending) | **Workspace and editor fixes** — a workspace folder per workflow (`flows/<id>/{scripts,tools,nodes}/`, `workspace/store.py`) next to the shared root folders, with files, catalog and palette filtered per workflow; workspace code imports shared code in a run as in an export (`workspace/loader.py`, sandbox child); exports bundle only the code the workflow uses by following imports, its data files and `requirements.txt`, with a generated import test (`workspace/bundle.py`, `packager.py`); Copilot code help (Write, Explain, Fix as an accepted diff, `POST /workflows/{id}/copilot/code`) and starter files per kind; the Workspace at `/workflows/{id}/workspace` with "Back to *workflow*" and the canvas viewport and selection restored; labelled list buttons and empty new list items in block settings; a Playwright check for unlabelled buttons on every tab and page | deviations in the S17 review above |
| S20 | ✅ done (tag pending) | **Framework foundation** — adapter interface v2 (`adapters/spi.py`: required core plus `ThreadStateAbility`, `MemoryAbility`, `McpAbility`, `require()`; ADR 0018); adapter manifests as data (`registry/adapters/*.json`, `AGENTCANVAS_ADAPTERS`); the runner picks each workflow's adapter, and state, history, fork, code view and export follow it; a framework-neutral `ModelService` for the Copilot, judges and setup (`adapters/langchain/models.py`); IR 1.2 `engine` on AI blocks; P001–P004 (`validation/rules/portability.py`); `GET /v1/frameworks`, `GET /v1/workflows/{id}/portability`; framework picker, engine picker and badges in the editor; conformance kit (`agentcanvas/conformance`, 12 cases) passed by LangChain and the Echo demo adapter (`adapters/echo`) | deviations in the S20 review above |
