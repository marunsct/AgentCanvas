# Changelog

All notable changes to AgentCanvas. Versions follow [semver](https://semver.org); dates are UTC.

## Unreleased

### Added
- **Any model provider, including local models.** Chat models can use Anthropic, OpenAI, Azure OpenAI, Google Gemini, Mistral, Groq, DeepSeek, xAI, any OpenAI-compatible API (`openai_compatible:` with a Base URL), and models on your own computer: Ollama (`ollama:`), LM Studio (`lmstudio:`), vLLM (`vllm:`) and llama.cpp (`llamacpp:`). Every integration is installed with the API. `GET /v1/model-providers` lists them, with each one's key variable, default address and whether it is installed.
- **API keys per provider, from the canvas.** A project secret named after the provider (e.g. `MISTRAL_API_KEY`) is used by that provider's models when their config names no key; the Chat model's **API key** field picks any project secret by name. The model field shows where the key comes from.
- **First-run setup.** Home asks for the model to use: pick a provider and model, enter the key or a local server's address, **Test connection**, and **Save**. The key becomes a project secret and the model the project's default, which new workflows from templates use. `GET/PUT /v1/projects/{id}/setup`, `POST …/setup/test`, `POST …/setup/skip`.
- **Run everything in Docker.** `make docker-up` (`infra/compose.local.yml`) builds and starts Postgres, the API and the web app on http://localhost:7777 without sign-in; local model servers on the host are reached through `host.docker.internal` (`AGENTCANVAS_LOCAL_MODELS_HOST`).
- **User guide** in `docs/user-guide/`: running AgentCanvas on your computer, and product documentation for every area.
- **Help for beginners on the canvas.** Hover any dot on a block to see what it carries and what to do with it. Click a dot to see everything that can connect there (steps that can come next, or the add-ons that fit an input) and add it already connected, in one click. Every problem in the Problems list and the Inspector says *what happened* and *how to fix it* in plain English, refused connections explain what to do instead, and failed runs explain the likely cause (API key, local model not running, misspelt field…). A beginner's guide opens the first time you open an empty workflow and is always one click away (**❓ Help**); `docs/user-guide/beginner-guide.md` has the same guide.

### Changed
- LLM judges, pairwise judges and simulated users accept any known provider's `provider:model`, not only catalog models.
- **Plain block names.** The palette uses everyday names and descriptions: *Ask AI* (was LLM call), *AI Agent*, *Planning Agent* (Deep Agent), *Helper Agent* (Subagent), *AI model* (Chat model), *Choose a path* (Router), *Repeat for each item* (Map), *Wait for all* (Join), *Group of steps* (Subgraph), *Ask for approval* (Approval), *Ask a person* (Human input), *Run Python code* (Python script), *Web API tool*, *App connector (MCP)*, and plain names for memory blocks and agent add-ons (middleware). Categories are ordered for beginners and every port has a description. Type ids (`core.llm`, …) are unchanged, so saved workflows and exported code are unaffected; new blocks get names from the new labels (e.g. `ask_ai`). Start and END show as *Start* and *Finish*. A new AI model block starts as the project's default model.
- New diagnostics: `W011` (a provider's integration is not installed on this server) and `E017` (an OpenAI-compatible model without a Base URL).

## v0.5.0 — Evaluation in production (not merged yet)

### Added
- **Online evaluators.** Attach any of a workflow's evaluators to a deployment with a sampling rate (0-100 %) and a filter (run status, the metadata the API caller sent). Sampled runs are scored in the background after the response is sent, so callers never wait. Scores show in the run list and on each run's timeline, with hourly and daily averages on the Evaluation page's *Production* tab. The judge's own cost is counted apart from the run's. `GET/POST /v1/deployments/{id}/online-evaluators`, `GET /v1/deployments/{id}/scores`, `/scored-runs`, `GET /v1/threads/{id}/scores`.
- **Drift alerts.** An online evaluator can alert when its mean over a recent window falls below a value, or drops more than a set amount below its trailing 7-day mean. Open alerts show in the app (`GET /v1/workflows/{id}/alerts`) and on `/metrics` as `agentcanvas_eval_alert_open`; `infra/observability/alerts.yaml` ships the `AgentCanvasEvaluationDrift` rule.
- **Annotation queues.** A queue per workflow is fed by rules (an online score below a threshold, a failed run, a thumbs-down) or by hand from any run. Reviewers see the input, output and timeline, score each rubric criterion, comment, and can add the run to a dataset with a corrected reference in one step. Queue progress and agreement between reviewers are shown on the *Queues* tab.
- **Feedback from API callers.** `POST /v1/deployments/{id}/runs/{run_id}/feedback` with `{"rating": "up" | "down", "comment"}`; a thumbs-down goes to the queues that ask for it.
- **Pairwise and composite evaluators.** A pairwise LLM judge compares two experiment targets on each example, asking twice with the answers swapped, and reports a win rate with a confidence interval. A composite evaluator combines others by weights or all/any rules. Both work in experiments and release gates (a pairwise gate compares the candidate with the live version).
- **Rubric self-grading.** A Rubric widget on agent and Deep Agent steps lets a grader model check the answer against your rubric and ask for revisions, up to a set number of rounds. Each grading round shows live in the run panel and is kept on the timeline; exported code carries it.
- **Simulated users.** For chat workflows, an experiment can hold a whole conversation per example with a simulated user (a catalog model given a persona, a goal and a turn limit). Each conversation is one result with its full transcript, which evaluators see.
- **LangSmith dataset sync.** Push a dataset version to LangSmith, or pull its current examples back as a new version, from the dataset page (`POST /v1/datasets/{id}/langsmith/push`, `/pull`). It uses the project secret `LANGSMITH_API_KEY` (or the server's) and only syncs when you ask.
- **Record/replay covers tools.** A recorded run now keeps what each HTTP, MCP and workspace tool was asked and answered. A regression test made from it replays those answers instead of calling the tools, so it runs without network access or side effects.

### Changed
- Fields listed in `settings.trace_redact` are also redacted in the root run and model runs sent to LangSmith and OpenTelemetry.
- A release gate keeps the dataset version and evaluator settings it was saved with, so later edits do not change what it measures until an admin saves it again.

### Fixed
- An API instance that stalls long enough for another to take over its runs or experiments now stops its copy instead of finishing it twice.
- Dry runs also stand in for workspace tool functions with a risky effect; they used to call them.

## v0.4.0 — Evaluation Studio (merged 2026-10-06, not tagged yet)

### Added
- **Datasets** (API) to evaluate a workflow on: build them from test cases, past runs, CSV or JSONL files; every change is kept as a version. `GET/POST /v1/workflows/{id}/datasets`, `/v1/datasets/{id}/…`.
- **Evaluators** (API) that score a run: exact match, regex, JSON Schema, an LLM judge with your rubric, tool-call trajectory, or your own function in `scripts/`. Each gives a 0-1 score, pass or fail at a threshold, and a comment. `GET/POST /v1/workflows/{id}/evaluators`, `POST /v1/evaluators/{id}/try`.
- **Experiments** (API): run the draft and published versions, or variants with another model or prompt, over a dataset version in the background, and compare mean scores, pass rates, latency, tokens and cost. An experiment can be cancelled and carries on after a redeploy. `POST /v1/workflows/{id}/experiments`, `/v1/experiments/{id}`, `/v1/experiments/{id}/results`.
- **Evaluation page** (editor → *Evaluate*): build datasets and evaluators, run experiments, read the results grid with each run's timeline one click away, and compare two experiments to see what improved or regressed.
- **Release gates.** An admin can require an evaluation before publishing to an environment: minimum mean scores on a dataset and how far the pass rate may drop against the live version. Publishing there evaluates the exact workflow (or reuses an evaluation that already did) and is refused on a miss; admins can publish anyway with a reason that the audit log keeps.
- **Rate limits per caller.** The public API limits each deployment key (120/min), webhook (120/min) and project (600/min) across all API instances, and answers 429 with `Retry-After` beyond that. See `AGENTCANVAS_RATE_LIMIT_*`.

### Changed
- The daily retention job deletes audit-log rows after 400 days (`AGENTCANVAS_AUDIT_RETENTION_DAYS`, 0 keeps them) and, if you set `AGENTCANVAS_MEMORY_RETENTION_DAYS`, long-term memories that were not updated for that long.

### Security
- The web app no longer needs `'unsafe-eval'`: forms validate without generating code, and the Content-Security-Policy is `script-src 'self'`.
- Base images are pinned by digest; workspace files that are symlinks out of the workspace are not listed.
- Release gates hold against builders: changing or deleting a gating dataset, or an evaluator of a gated workflow, needs an admin (403 otherwise), like the gate itself.
- Publishing pins exactly what the gate evaluated: if the draft or the workspace changes during the evaluation, the publish is refused (409). Evaluations are reused only for the same workspace commit.
- Regex evaluators run in a worker process with a 2 s limit, so a pathological pattern cannot freeze the API; code evaluators time out after 60 s.
- Masking also covers run errors sent to LangSmith, span status messages, and secrets with quotes, backslashes or non-ASCII characters.

### Fixed
- Secret values no longer reach LangSmith traces or OpenTelemetry spans; they are masked as `***`. A workflow can also name fields (`settings.trace_redact`) whose values are never traced.
- An example whose run never started now fails every evaluator instead of being left out of averages and gates, and a gate fails when not every example finished. A non-finite score (NaN) no longer counts as a pass.
- Trying an evaluator uses the workflow's own project for model keys and workspace code.
- The Evaluation page no longer crashes when switching experiments with an output diff open; forms accept Python-style regular expressions (for example in the PII middleware) again.
- Runs of an API instance that stopped without a clean shutdown (out of memory, a lost node) no longer stay "running": another instance picks them up after 60 s (`AGENTCANVAS_ORPHAN_AFTER_S`).

## v0.3.0 — Production readiness (merged 2026-10-06, not tagged yet)

### Production readiness
- **Containers, compose and Helm.** Non-root API and web images, `infra/compose.prod.yml` (two API replicas, a one-shot migration) and a Helm chart in `infra/helm/agentcanvas`. `/readyz` checks the database and the run store.
- **Observability.** OpenTelemetry traces for requests, database calls and outgoing HTTP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; Prometheus metrics on `/metrics`; a Grafana dashboard and alert rules in `infra/observability/`.
- **Retention and backups.** Conversations older than the retention period are deleted daily; backup and restore scripts with a runbook.
- **Safe redeploy.** On shutdown an instance stops taking runs, lets running ones reach a step boundary and hands them to another instance, which resumes them. Threads move to a newly published version only when it is resume-compatible.
- **Load tested.** 200 concurrent streaming runs on compose prod: 5,875 runs, none failed, first event p95 89 ms.
- **Secrets under a master key.** Each organisation's secrets use their own data key, wrapped by a key file, AWS KMS or Google Cloud KMS (`AGENTCANVAS_KMS_KEY`). Rotate with `python -m agentcanvas.security.kms rewrap`.
- **Encrypted checkpoints** when `LANGGRAPH_AES_KEY` is set.
- **Security review** completed (`10-security-checklist.md` §7). Dependency and image scanning run in CI; the web server sends a Content Security Policy and rate-limits deployment and webhook calls.

### Changed
- **Webhook signatures include a timestamp.** Senders send `X-AgentCanvas-Timestamp` and sign `"<timestamp>." + body`; deliveries older than five minutes are refused. Update existing senders before upgrading.
- Invitations to someone who already belongs to another organisation wait until they accept them (Home shows a banner). Members who have not accepted show no profile details.
- You start in the organisation you used last.
- The web app asks for `offline_access` and renews sessions silently; enable refresh tokens at your identity provider.
- The HTTP tool refuses private, loopback and link-local addresses unless the tool allows private networks.
- The docker sandbox runs as an unprivileged user and is killed on timeout.
- Production refuses the development secret key, and the hosted edition refuses to start without a sandbox. API docs are off in production.

## v0.2.0 — Accounts & access (merged 2026-10-05, not tagged yet)

### Accounts & access
- **Sign-in with your identity provider (OIDC).** Set `AGENTCANVAS_AUTH_MODE=oidc`; the web app signs in with authorization code + PKCE and the API checks every token. Deployment API keys and signed webhooks work as before.
- **Organisations.** Everything belongs to an organisation, and nobody sees another organisation's workflows, runs, secrets or deployments. Create projects with `POST /v1/projects`.
- **Roles:** viewer, reviewer (answers approvals), builder and admin. Admins invite people by email from the new Members page.
- **Audit log.** Every change is recorded with who made it, what it touched and from where; approvals include a hash of what was approved. Admins read it on the Audit log page.
- **Policies.** Admins write organisation rules as CEL on the new Policies page, for example "every external write needs approval" or "only approved models in prod". The editor flags workflows that break them (`E095`), and publishing refuses them in a new "policies" checklist item. Rules for one environment show as warnings in the editor (`W095`) and block publishing there. Steps inside subgraphs are checked too.
- **Memory per organisation.** Long-term memory is kept per organisation, and the Memory inspector shows only your organisation's. Memories saved before this release move to the default organisation the first time the API starts.

### Changed
- Downloads and the "Open trace" link now go through the signed-in API (`GET /v1/runs/{id}/trace?format=json`).
- Requests that name an id from another organisation, or one that doesn't exist, as a query parameter (`?workflow_id=…`) now answer 404 instead of an empty list.
- Policies are enforced when publishing. Draft runs, debug runs and test-case runs are no longer blocked by a broken policy.
- Deployment API end users are kept apart from organisation members: a `user_id` passed to a deployment becomes `api:<deployment>:<user_id>` in the run context and in user-scoped memory.
- Memories from before organisations are moved once; the API records that it has done so and never moves memories again.

### Fixed
- Checking policies took seconds on large workflows (about 6 s for 120 steps); it now takes a fraction of a second.
- A workflow with a malformed embedded subgraph no longer fails to open (500) once the organisation has a policy.
- Reviewers can resume runs but can no longer add debug pins, mocks or dry runs to them.
- An organisation can no longer be left without an admin who has signed in, including through concurrent role changes.
- The web app renews an expired sign-in in the background, has Sign out and "Sign in with a different account", and hides actions your role cannot take.

## v0.1.0 — MVP (2026-10-05)

The first usable release. You can build an agentic workflow on a canvas, run and debug it, test it, export it as plain Python and publish it behind an API.

### Build
- **Canvas editor** (React Flow):
  - typed, colour-coded ports
  - connections are checked before they are made, and a refusal says why
  - autosave with conflict detection, undo/redo
  - copy/paste, auto-layout (Tidy), ⌘K command palette, multi-select
- **Workflow format.** The workflow IR (v1.1) is the single source of truth: one JSON document, validated by about 70 diagnostic rules shown in the Problems dock.
- **Widgets:**

  | Group | Widgets |
  |---|---|
  | Steps | LLM, Router, Set, Script, Approval, Human input |
  | Agents | Agent (with middleware and structured output), Deep Agent with sub-agents and file backends |
  | Structure | Map/Join (parallel fan-out), Subgraph |
  | Memory | recall / remember / forget |
  | Tools | HTTP tool, MCP server, memory tools, workspace tools |
  | Middleware | PII, call limits, retry, fallback, summarisation, to-dos, tool selection |

- **Workspaces.** Each project has its own git-backed workspace with a code editor and lint markers. It holds:
  - script nodes and custom tools
  - custom widgets (`@canvas_node`)
  - event handlers (`@on`)
- **Sandboxes.** Workspace code runs in-process, in a subprocess, in Docker, or (experimental) in a LangSmith Sandbox. The policy file `sandbox.json` sets limits, an egress allow-list and header injection.
- **Secrets.** Encrypted at rest, write-only through the API, and masked in every run event.
- **Templates:** Ticket triage, Approval workflow, Data extraction, RAG Q&A, Research brief, Classify & route, Chat assistant.
- **Guided build.** A six-step first-run tutorial that advances as the real canvas takes shape.

### Run & debug
- **Durable runs.** Runs survive a restart.
- **Human decisions.** Approvals and inputs appear in the Run panel and in a cross-workflow Inbox.
- **Run panel:**
  - breakpoints
  - a timeline with state edits, fork and replay
  - Explain mode, which narrates each step
  - live token streaming
- **Live Code Mirror.** The generated LangGraph code updates as you edit.
- **Debug aids:**
  - pinned outputs and mock models
  - cost, tokens and latency per step
  - budgets per run and per conversation
  - channel lineage
- **Blast-radius overlay.** Shows which steps can write, send, execute or spend, and whether a human approves first. A **dry run** stubs every risky tool with a recorder.
- **LangSmith tracing** with an "Open trace" link.

### Ship
- **Versions.** Semver snapshots of the workflow and its workspace code, a field-level diff shown on the canvas, and restore.
- **Saved test cases:**
  - assert on final status, output values and route
  - run against the draft or any version
- **Record/replay.** Turn any recorded run, including production runs, into a deterministic regression test.
- **Export** as a standalone Python project:
  - generated graph, vendored node library, workspace code
  - pinned dependencies, Dockerfile, `langgraph.json`
  - the project's own tests pass with `uv run pytest`
  - it serves under `langgraph dev`
- **Publish:**
  - a pre-publish checklist: validation, tests and secrets; breaking contract changes are reported
  - one deployment per environment behind a rotatable API key
  - endpoints `runs/wait`, `runs/stream` and resume, always pinned to the published version
- **Triggers.** HMAC-signed webhooks and cron schedules.
- **Contract view** of a workflow's API, with breaking-change detection between versions.

### Quality
- **Tests:**

  | Suite | Count |
  |---|---|
  | API, on SQLite and Postgres | about 200 |
  | Web unit tests | 47 |
  | Playwright journeys | 27 |

- **`make check`** runs every CI gate locally except the Playwright journeys (`make e2e`) and the strict docs build.
- **UI states.** Every screen has loading, error-with-retry and empty states. Error boundaries keep one broken panel from blanking the app.
- **Bundle size.** The web entry bundle is 0.3 MB: pages and the layout engine load on demand.

### Known limitations
- **No real authentication yet.** Every request acts as a built-in development admin. OIDC, organisations and roles arrive in v0.2.0.
- **Cron runs on one API instance only.** Multi-instance deployments need the leader lock planned for v0.3.0.
- **Record/replay replays model replies only.** Tools run again during replay.
- **The LangSmith Sandbox mode is unmeasured.** It has not run against the live service (see spike SP-2).
