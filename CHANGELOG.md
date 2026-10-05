# Changelog

All notable changes to AgentCanvas. Versions follow [semver](https://semver.org); dates are UTC.

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
