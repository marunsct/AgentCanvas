# AgentCanvas — progress summary

Live status of the project. Update it with every commit that finishes or changes a piece of work.

- **Branch:** the user guide, any-provider models, first-run setup and the Docker app are in [#9](https://github.com/marunsct/AgentCanvas/pull/9). S13 was merged via [#8](https://github.com/marunsct/AgentCanvas/pull/8). S0–S8 were merged via [#1](https://github.com/marunsct/AgentCanvas/pull/1), S9 via [#2](https://github.com/marunsct/AgentCanvas/pull/2), S10 via [#3](https://github.com/marunsct/AgentCanvas/pull/3), S11 via [#4](https://github.com/marunsct/AgentCanvas/pull/4) and S12 via [#5](https://github.com/marunsct/AgentCanvas/pull/5).
- **Plan:** `docs/agent-canvas-spec/build-guide/13-sprint-plan.md`. Its execution log has the per-sprint detail.
- **Last updated:** 2026-10-07. Sprint 14 (AI Copilot I → v0.6.0) is done: the Copilot briefs, plans and builds workflows as proposals previewed on the canvas. Before it, a whole-codebase review's 11 findings were fixed.
- **Previously:** 2026-10-06. Sprint 13 is merged (#8; tag `v0.5.0` is the maintainer's call). #9 adds the user guide, models from any provider (local ones included) with per-provider keys, a first-run model setup screen, and `make docker-up`.

## At a glance

| Area | Status |
|---|---|
| Specification (functional, technical, IR/compiler, node catalog, workspaces/events, reviews v0.5/v0.6, new features) | ✅ done (docs 01–15, ADRs 0001–0017) |
| Sprint plan S0–S13 | ✅ done (S13 backlog planned 2026-10-06) |
| S0 Foundations | ✅ done |
| S1 IR v1.1 & API | ✅ done |
| S2 Runner & persistence | ✅ done |
| S3 Canvas editor I | ✅ done |
| S4 Canvas editor II & run UX | ✅ done |
| S5 Node catalog I | ✅ done |
| S6 Node catalog II & debug | ✅ done |
| S7 Workspaces & events | ✅ done |
| S8 Export & publish | ✅ done |
| S9 MVP hardening → v0.1.0 | ✅ done (git tag pending) |
| S10 Accounts & access → v0.2.0 | ✅ done (merged in #3; tag `v0.2.0` pending) |
| S11 Production readiness → v0.3.0 | ✅ done (merged in #4; tag `v0.3.0` pending) |
| S12 Evaluation Studio → v0.4.0 | ✅ done (merged in #5; tag `v0.4.0` pending) |
| S13 Evaluation in production → v0.5.0 | ✅ done (merged in #8; tag `v0.5.0` pending) |
| **User guide, any-provider and local models, first-run setup, Docker app** | ✅ **done** (#9) |
| S14 AI Copilot I → v0.6.0 | ✅ done (merged in #15; tag `v0.6.0` pending) |
| S15 Open doors (MCP/A2A endpoints, connectors, trace export) → v0.7.0 | ✅ done |
| S16–S32 (any framework as a node, ship to users, Copilot II, deploy kit, Knowledge Studio, framework adapters) | ⏸ paused by the owner after S15, see [competitive roadmap](docs/agent-canvas-spec/16-competitive-roadmap.md) |
| Later: GA backlog | ⬜ pending |

**Test totals** at the end of S13:

| Suite | Result |
|---|---|
| API (pytest) | 343 passed, 1 known upstream canary (xfail) |
| Web unit tests (vitest) | 59 passed |
| Playwright journeys | 33 passed (production build under the production CSP) |
| Browser journeys (Playwright) | 31 passed |

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

### S9: MVP hardening → v0.1.0
- **Blast radius (FR-X-02):** per step, what it can do (write externally, send messages, execute code, spend, read personal data) and whether a human approves it first; a canvas overlay. **Dry runs** replace risky tools with recorders.
- **Guided build (FR-X-24):** a six-step first-run tutor that advances when the real canvas shows each step done.
- **UI states:** loading, error-with-retry and empty states on every screen; error boundaries per page and panel.
- **Visual groups:** labelled frames around steps (layout only; deferred from S6).
- **Performance:** pages and the layout engine load on demand; the entry bundle went from 2.8 MB to 0.3 MB.
- **Release prep:** version 0.1.0, `CHANGELOG.md`, MkDocs documentation site (`make docs`, CI job).

### S10: accounts & access → v0.2.0
- **Sign-in (M7-T1):** `AGENTCANVAS_AUTH_MODE=oidc` requires a bearer JWT on every `/v1` route except deployment endpoints, signed webhooks and `/v1/auth/config`. Users are created on first sign-in; `AGENTCANVAS_BOOTSTRAP_ADMIN_EMAILS` seeds the first admins. The web app signs in with authorization code + PKCE. Dev mode is unchanged and refused in staging/prod.
- **Organisations (M7-T2):** every id a request names must belong to the caller's organisation (404 otherwise); `X-Org-Id` picks among several; `POST /v1/projects`.
- **Roles (M7-T3):** viewer, reviewer, builder, admin; member invitations by email (`/v1/org/members`) and a Members page; the editor is view-only below builder.
- **Audit log (M7-T4):** every successful change with actor, target and IP; approvals carry the payload's SHA-256; `GET /v1/org/audit` and an Audit log page.
- **Policies (FR-X-19):** organisation rules as CEL predicates over steps, resources or the whole workflow (`/v1/org/policies`, Policies page). The validator reports breaches (`E095`/`W095`, `E096` when a rule cannot be evaluated, which fails closed); publishing checks the rules for its environment in a "policies" checklist item. Steps and resources inside subgraphs are checked too, and their breaches point at the subgraph step.
- **Memory per organisation (M7-T2):** runs keep long-term memory under the workflow's organisation (`org:<id>` root), and the Memory inspector only sees the caller's organisation. Memories saved before Sprint 10 move to the default organisation at startup.
- **End-of-sprint review:** three parallel reviews (web, auth and roles, policies and memory). Fixed:
  - policy checks were quadratic in workflow size (6 s for 120 steps, now under 0.4 s) and returned 500 on a malformed embedded subgraph;
  - policies blocked draft and test runs; they are now enforced at publish only, as designed;
  - reviewers could resume with debug pins and mocks; an organisation could lose its last signed-in admin through concurrent changes;
  - deployment API callers could pass a member's user id and reach that member's memories (now `api:<deployment>:<user_id>`);
  - pre-S10 memory adoption now runs once (marker) and pages through namespaces;
  - web: silent token renewal and retry, sign out and account switch, actions hidden for roles that cannot use them.

## What is pending

### Still open from S9
- [ ] Tag `v0.1.0`. A tag is outward-facing, so it needs the maintainer's go-ahead.
- [ ] Exit criterion: 10 design partners complete journey J1 unaided. This needs real users.

### S10 — accounts & access → v0.2.0
- [x] OIDC login, organisations and projects, roles, audit log (M7-T1…T4), in #3.
- [x] FR-X-19 policy-as-code lint profiles, in #3.
- [x] Long-term memory separated per organisation, and policies checked inside subgraphs, in #3.
- [x] End-of-sprint review and fixes, in #3.
- [x] Merged in #3 (2026-10-05).
- [ ] Tag `v0.2.0` (maintainer's call).

### S11 — production readiness → v0.3.0 (detail in the sprint plan §3)
- [x] M8-T1 containers: non-root images built in CI, SSE-safe nginx.
- [x] M8-T2 compose prod and Helm chart; `/readyz` pings the database and the store.
- [x] Cron across replicas: each minute is claimed with a conditional update, so it fires once (works on SQLite too, no advisory lock needed).
- [x] M8-T3 observability: OpenTelemetry traces (FastAPI, SQLAlchemy, httpx), Prometheus `/metrics`, a Grafana dashboard and alert rules.
- [x] M8-T4 conversation retention job, backup and restore scripts with a runbook.
- [x] FR-X-10 safe redeploy: runs drain on shutdown and another instance resumes them, on a new version only when it is resume-compatible.
- [x] M8-T5 k6 load test on compose prod: 200 concurrent streams, 5,875 runs, 0 failed, first-event p95 89 ms (target 250 ms).
- [x] M8-T6 security review: checklist completed with evidence, no open P0; pip-audit, pnpm audit and Trivy in CI; Dependabot.
- [x] KMS envelope encryption: per-organisation data keys wrapped by a file, AWS KMS or GCP KMS master key, with a re-wrap command.
- [x] S10 review carry-overs: invitations need acceptance, last-used organisation is the default, silent token refresh, project read per request, `FORWARDED_ALLOW_IPS` documented.
- [x] Merged in #4 (2026-10-06).
- [ ] Tag `v0.3.0` (maintainer's call).

**Review (2026-10-06).** Every S11 item met its acceptance. Things to know before deploying:
- Webhook senders must now sign `timestamp.body` and send the timestamp header; old-format signatures are refused.
- `offline_access` needs refresh tokens enabled at the identity provider, otherwise sessions end when the access token does.
- The CSP keeps `'unsafe-eval'` because form validation (Ajv) compiles schemas at runtime (dropped in S12).
- Rate limits exist only in nginx, per client address.
- The AWS and GCP KMS adapters are tested with fakes only; long-term memory (the store) is not encrypted.
- A pod killed without a graceful shutdown leaves its runs marked running.
- Throughput on the load test was CPU-bound (about 37 runs/s on two replicas).

The follow-ups are in the S12 backlog.

### S12 — Evaluation Studio → v0.4.0 (detail in the sprint plan §3)
- [x] FR-EVAL-02 datasets API: examples from test cases, past runs and CSV/JSONL (LangSmith-style JSONL too), checked against the input contract; every change is an immutable version; splits; JSONL export. The page comes with the results UI.
- [x] FR-EVAL-03 evaluators API: exact/regex/JSON Schema, LLM-as-judge rubric (catalog model, 0-1 score with reasoning), trajectory match (strict/unordered/subset), workspace code evaluators in the sandbox; thresholds, and a Try endpoint.
- [x] FR-EVAL-04 experiments API: draft/versions × pinned dataset version × variants (config overrides on resources or steps), in the background with bounded concurrency; scores, tokens, cost and latency per example; aggregates per target; cancel; taken over by another instance after a redeploy.
- [x] Evaluation page: datasets, evaluators (try on a sample), experiments with variants and live progress, results grid with output diff and timeline links, comparison of two experiments (Playwright journey).
- [x] FR-EVAL-05 release gate: per workflow and environment (dataset, minimum mean per evaluator, max pass-rate drop vs live); publish runs or reuses an evaluation of the exact workflow and refuses on a miss; admins override with an audited reason.
- [x] Orphaned-run sweeper: runs of an instance killed without draining are handed over after `AGENTCANVAS_ORPHAN_AFTER_S` (60 s) without a heartbeat.
- [x] API rate limits per deployment key, webhook and project, counted in the database across replicas (429 with `Retry-After`).
- [x] Trace masking: secrets masked in LangSmith runs and OTel spans; `settings.trace_redact` fields redacted in every step's trace.
- [x] Audit-log retention (400 days by default) and opt-in long-term memory retention in the daily job.
- [x] CSP without `'unsafe-eval'`: interpreting form validator; CI runs the e2e suite on the production build under the production policy.
- [x] Base images pinned by digest; workspace symlink escape test.
- [x] End-of-sprint review fixes (gate hardening, regex time limit, masking gaps, web crashes); merged in #5 (2026-10-06).
- [ ] Tag `v0.4.0` (maintainer's call).

**Review (2026-10-06).** Every S12 item met its acceptance. The review's fixes are listed in the sprint plan. Things
to know before deploying:
- Changing a dataset or evaluator that a release gate uses now needs an admin.
- A publish that races with an edit of the draft or the workspace is refused with 409; publish again.
- `trace_redact` does not yet cover the graph's root run or model runs (S13).

### S13 — Evaluation in production → v0.5.0 (detail in the sprint plan §3)
- [x] S12 follow-ups: `trace_redact` on root and model runs, lease owners, gates snapshot their dataset version and evaluators.
- [x] FR-EVAL-06 online evaluators on deployment traffic (sampling, filters, aggregates, drift alerts with a Prometheus rule).
- [x] Annotation queues fed by rules or by hand; corrected answers go to a dataset.
- [x] Pairwise (win rate) and composite evaluators, in experiments and gates.
- [x] FR-EVAL-08 rubric self-grading widget with live grading events.
- [x] FR-EVAL-07 simulated users for multi-turn chat experiments.
- [x] LangSmith dataset push and pull.
- [x] Record/replay captures tool I/O (HTTP, MCP, workspace tools).
- [x] End-of-sprint review fixes (drift alerts, annotation races, online scoring of handed-over runs, pairwise pass rate).
- [x] Demo on compose prod and the deployment latency check (scoring every run: p95 42 → 52 ms at 30 requests/s; details in the sprint plan).
- [ ] Merge #8 (owner's call) and tag `v0.5.0` (maintainer's call).

**Review (2026-10-06).** Every S13 item met its acceptance in the tests. Things to know before deploying:
- Migrations add the online-evaluator, alert, annotation, simulation and LangSmith tables and columns; run them first.
- Online scoring runs inside the API pods after the response. An LLM judge uses the project's model keys.
- LangSmith sync needs `LANGSMITH_API_KEY` as a project secret or server variable.

### S15 — Open doors (v0.7.0)
Order changed on 2026-10-07 to the value-first roadmap: S15 is Open doors; Copilot II moves to S18, the deploy kit to
S19. The owner asked to stop after S15 and its end-to-end review.
- [x] Every deployment is an MCP server (`/v1/deployments/{id}/mcp`, Streamable HTTP, a tool per workflow plus `answer`)
  and an A2A agent (card at `/a2a/.well-known/agent-card.json`, `message/send`, `tasks/get`, input-required for pauses).
- [x] One-click MCP connector gallery (`registry/mcp_gallery.json`, `GET /v1/mcp-connectors`, palette section);
  `apps/api/scripts/smoke_mcp_gallery.py` checks every entry; stdio connectors gain `env` (values can be secrets).
- [x] Trace export per project to Langfuse, Phoenix, Datadog (Agent OTLP) or any OTLP/HTTP endpoint, with GenAI and
  OpenInference attributes, secret masking and `trace_redact` (`/v1/projects/{id}/trace-export`, admin only).
- [x] Review fixes before the sprint: chat models cached per config (an edited model, key or address took effect
  only after a restart), overlapping Copilot turns leave one open proposal, a webhook for a deleted workflow gets
  404 instead of "already received".

### GA backlog
- [ ] Multiplayer editing, Data Studio, learning loops, AG-UI exposure, Git sync, hosted trial (MCP/A2A exposure moved to S15, Copilot shipped in S14).
- [ ] The remaining FR-X items: 03, 04, 05, 06, 09, 11, 12, 16, 20, 21, 23.

### Open items carried forward
- [x] **Branch housekeeping:** the S9 commits were rebased onto `main` and opened as #2.
- [ ] **SP-2 follow-up:** one session with a real LangSmith key to measure sandbox latency. Open questions:
  - is an empty allow-list accepted as "no network"?
  - can one sandbox run several commands at once?
  - which snapshot to use (see `build-guide/spikes/SP-2-langsmith-sandbox.md`)?
- [x] **Record/replay:** tool I/O is captured and replayed since S13.
- [ ] **Agent-sandbox backend widget** (`backend.sandbox`, where W074 applies), P1.

## Commit log by sprint (latest first)

| Commit | Content |
|---|---|
| S9 part 6 | v0.1.0: version, changelog, docs site; S9 docs |
| `7882394` | Visual groups (frames) |
| `5b5a01c` | Guided build |
| `12cbd13` | Loading, error and empty states; error boundaries |
| `7c35eb4` | Lazy-loaded pages and ELK |
| `be62d92` | Blast radius and dry runs |
| `2d5f759` | Spike reports SP-3 and SP-4; export uses a `ServerRuntime` factory; S8 docs; this file (merged in #1) |
| `899ca6b` | Record/replay; contract view & API diff |
| `17754c2` | LangSmith trace links; MVP template set |
| `d275a93` | Webhook and cron triggers |
| `ecd2152` | Publish: deployments, API keys, checklist |
| `dfa54e9` | Export as a standalone Python project |
| `6939671` | Versions & diff; saved test cases |
| `b4f41f6` | Sandbox policy; LangSmith Sandbox runner (SP-2); S7 done |
| `7e39301` | Events as code v0 |
| earlier | S0–S7 parts; see `git log` |
