# 01 — Functional Specification

## 1. Personas

| Persona | Goals | Primary tier | Must-have capabilities |
|---------|-------|--------------|-----------------------|
| **P1 — Citizen builder** (ops analyst, PM, support lead) | Automate a process with an agent without coding | Agent tier | Templates, Copilot, Agent widget with sensible defaults, one-click run/publish, chat test panel |
| **P2 — Agent architect** (ML/AI engineer) | Design reliable multi-agent systems, control flow, HITL | Graph tier | Explicit state schema, routers, fan-out, subgraphs, interrupts, evals, time-travel debugging |
| **P3 — Platform engineer** | Extend the platform, integrate enterprise systems | Code tier | Workspaces, custom nodes SDK, custom middleware, event handlers, CI/CD, Git, eject |
| **P4 — Reviewer / approver** (compliance, domain expert) | Approve agent actions; approve releases | Inbox / Review | Human task inbox, version diffs, eval reports, audit log |
| **P5 — Admin** | Govern cost, security, access | Admin console | RBAC, SSO, secrets, model policies, spend limits, audit, data retention |
| **P6 — End user of a published agent** | Use the agent | Chat widget / API / Slack | Embeddable chat, streaming, file upload, approvals |

## 2. Core user journeys

**J1 — From idea to running agent in 5 minutes (P1)**
1. User clicks *New workflow* → chooses "Describe it" and types: *"Every morning, read new Zendesk tickets, classify urgency, draft replies for low-urgency ones, and ask me before sending anything."*
2. Copilot proposes a graph (Cron Trigger → Fetch Tickets (MCP tool) → Classifier (structured output) → Router → Deep Agent "Reply drafter" → Human Approval → Send Reply tool). The diff is shown as ghost nodes; user accepts.
3. Validator flags *"Zendesk connection missing"*; user clicks the badge → connects via OAuth.
4. User presses ▶ **Test run** with a sample payload. Nodes light up; the approval card appears in the right panel; user approves; run finishes. Trace link to LangSmith shown.
5. User presses **Publish** → chooses *LangSmith Deployment* → gets an endpoint, a cron schedule, and a Slack channel hook.

**J2 — Architect builds a supervised multi-agent research system (P2)**
Designs a state schema (`messages`, `plan`, `findings[]` with append reducer, `report`); adds a Planner node, a `Send` fan-out node that spawns N Researcher subgraphs in parallel, a Reducer/Synthesizer, a Rubric critic loop (max 3 iterations), and an Interrupt before publishing. Sets breakpoints, runs, scrubs the timeline back to the planner step, edits the plan in state, forks a new run. Creates a dataset from 10 traces, runs an experiment comparing two models, and sets the experiment as a release gate.

**J3 — Engineer adds a custom event and a custom node (P3)**
Opens the Workspace, creates `nodes/salesforce_upsert.py` using the `@canvas_node` decorator with typed ports; it appears in the palette immediately (hot reload). Writes an event handler `events/on_high_value_lead.py` subscribed to the `lead.created` webhook with a filter; the handler starts the workflow with a mapped payload. Writes unit tests; CI runs them on push; eject to GitHub.

**J4 — Reviewer handles approvals (P4)**
Gets a Slack/email notification; opens the Inbox; sees the pending tool call (`send_email` with args), the agent's reasoning excerpt, and the trace; chooses **Edit** (fixes the subject line) → **Approve**. The run resumes (`Command(resume=…)`).

**J6 — Persistent, self-improving assistant with company data (P2/P3)**
The architect opens **Data Studio**, imports the existing CRM Postgres schema (reverse engineering), adds a Redis `SessionState` entity with a 1-hour TTL, marks `email` as PII and `tenant_id` as the tenant scope, and generates data tools (`search_customers`, `get_ticket`, `update_ticket_status` — writes require approval). In the workflow's **Persistence panel** they bind the checkpointer to the company Postgres in prod (SQLite in dev), set a 30-day thread TTL and enable encryption. They add Memory Spaces `user_profile` (Postgres store) and `episodes` (MongoDB vector search) behind one routed store, drop a *Recall* node before the agent and enable memory tools. Finally they add the *Nightly self-improvement loop* template: HITL edits and 👎 feedback become examples and prompt-optimisation input; candidates pass an eval gate and a human review before a 10 % canary.

**J5 — Production incident (P3/P5)**
An alert fires (LangSmith online eval score drop). The engineer opens the failing trace; the canvas overlays the trace; the failing tool node is red; engineer pins the failing input, fixes the prompt, re-runs from that node only (cache), adds the case to the regression dataset, publishes v1.4.1 through the eval gate, and rolls back instantly if needed.

## 3. Application layout (UI)

```
┌─────────────────────────────────────────────────────────────────────────────────────┐
│ Top bar: project ▸ workflow ▸ branch/version │ env: dev ▾ │ ▶ Run ▾ │ ⏸ │ Publish │ 👥 │
├──────────┬──────────────────────────────────────────────────────────┬───────────────┤
│ Left     │                    CANVAS                                │ Right panel   │
│ rail     │  (infinite, zoomable; minimap; groups; comments)         │ (contextual)  │
│ ─────────│                                                          │ ─ Inspector   │
│ Palette  │   [Trigger]──▶[Agent]──▶[Router]──▶[Tool]                │ ─ Chat/Test   │
│ Outline  │        ▲ model  ▲ tools                                  │ ─ Run/State   │
│ State    │   [ChatModel]  [MCP Server]                              │ ─ Approvals   │
│ Files    │                                                          │ ─ Copilot     │
│ Data     │                                                          │ ─ Code view   │
│ Memory   │                                                          │ ─ Persistence │
│ Evals    │                                                          │               │
│ Learning │                                                          │               │
│ Versions │                                                          │               │
├──────────┴──────────────────────────────────────────────────────────┴───────────────┤
│ Bottom dock: Timeline scrubber │ Logs │ Problems │ Terminal │ Traces │ Cost/Latency    │
└─────────────────────────────────────────────────────────────────────────────────────┘
```

Three switchable main views (same IR): **Canvas**, **Code** (read-only generated code with source-map highlighting; editable only in code islands), **Split** (canvas + code side-by-side, selection-synced).

---

## 4. Functional modules & requirements

### 4.1 Canvas editor (FR-CAN)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-CAN-01 | Infinite pan/zoom canvas with minimap, grid snapping, auto-layout (ELK layered, left→right and top→bottom), align/distribute tools. | P0 |
| FR-CAN-02 | Drag nodes from palette; quick-add by double-clicking empty canvas (fuzzy search, like ComfyUI's search box); drag from a port to empty space opens a filtered search showing only type-compatible nodes. | P0 |
| FR-CAN-03 | Three edge types rendered distinctly: **Flow** (solid, animated during runs), **Wiring** (thin, colored by port type), **Data** (dashed, labeled with channel names). | P0 |
| FR-CAN-04 | Conditional edges show their branch labels; router nodes show one output port per declared route plus optional `default`. | P0 |
| FR-CAN-05 | Groups: select nodes → *Group* (visual only) or *Convert to Subgraph* (semantic; creates a reusable component with its own input/output schema). Double-click to enter; breadcrumb to exit. | P0 |
| FR-CAN-06 | Reroute/elbow points, edge bundling, and "wireless" named links (send/receive pairs) to reduce spaghetti (ComfyUI "Anything Everywhere" equivalent, but typed). | P1 |
| FR-CAN-07 | Node states rendered with badges: *invalid* (red), *warning* (yellow), *running* (pulsing blue), *interrupted* (amber), *succeeded* (green), *cached* (grey tick), *pinned* (pin icon), *disabled/bypassed* (faded). | P0 |
| FR-CAN-08 | Bypass/disable a node (compile skips it and passes state through) for quick experiments. | P1 |
| FR-CAN-09 | Undo/redo (unbounded within session), copy/paste across workflows (including as IR JSON in clipboard), duplicate with wiring. | P0 |
| FR-CAN-10 | Sticky notes and Markdown comments pinned to nodes; threaded discussions (see collaboration). | P1 |
| FR-CAN-11 | Keyboard-first operation: command palette (⌘K), shortcuts for run, run-from-here, toggle breakpoint, group, bypass, focus, search. | P0 |
| FR-CAN-12 | Accessibility: full keyboard navigation of graph (tab through nodes/ports, arrow keys along edges), screen-reader outline view, high-contrast theme, WCAG 2.2 AA. | P1 |
| FR-CAN-13 | Canvas scales to ≥ 500 nodes / 1,000 edges at 60 fps (virtualized rendering, LOD: collapsed rendering at low zoom). | P1 |
| FR-CAN-14 | "Outline" tree view of the workflow (nodes, groups, subgraphs, resources) for navigation and bulk editing. | P1 |

### 4.2 Node palette & node model (FR-NODE)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-NODE-01 | Palette organized by category: Triggers, Agents, Models, Tools, MCP, Knowledge/RAG, Memory, Logic & Control, Human-in-the-loop, Data & Transform, Scripts, Middleware/Guardrails, Subgraphs, Outputs & Channels, Evaluation, Custom (workspace), Marketplace. Full catalog in doc 04. | P0 |
| FR-NODE-02 | Each node has: title, icon, description, typed input/output ports, a config form (generated from a JSON Schema), advanced settings, docs link, and a "View code" action showing the compiled snippet. | P0 |
| FR-NODE-03 | Config fields can be **literal**, **expression** (sandboxed expression language over state/context, e.g. `{{ state.customer.tier }}`), **secret reference**, or **promoted to input port** (like ComfyUI "convert widget to input"). | P0 |
| FR-NODE-04 | Prompt fields use a rich prompt editor: Mustache/f-string variables with autocomplete from the state schema, token counter, version history, link to LangSmith prompt/Context Hub commits, test-in-playground button. | P0 |
| FR-NODE-05 | Node versioning: each node type is semver'd; workflows pin node versions; upgrade assistant shows breaking changes. | P1 |
| FR-NODE-06 | Custom nodes from the Workspace and the marketplace appear in the palette with a provenance badge (built-in / org / community / local). | P0 |
| FR-NODE-07 | Per-node runtime policies (Graph-tier runtime nodes): retry policy, cache policy, timeout (run/idle), error handler route, concurrency limit, tags/metadata for tracing. | P0 |

### 4.3 Type system & live validation (FR-TYPE)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-TYPE-01 | Ports are typed. Connecting incompatible ports is refused with an explanation; compatible-with-adapter connections offer an auto-inserted adapter (e.g. `Text → Messages`, `JSON → Text`). | P0 |
| FR-TYPE-02 | Continuous validation on every IR change (< 150 ms for 200 nodes) with a *Problems* panel: errors (block run/publish), warnings, info; click to focus. | P0 |
| FR-TYPE-03 | Graph-level checks: START reachable to END; no dangling required ports; unbounded cycles must have a guard (loop counter, recursion limit, or router exit) — warn otherwise; interrupts require a checkpointer (always present on our runtime; flagged for export targets); `Send` targets must accept the sent payload type; subgraph I/O schema compatibility; state-channel write conflicts in the same super-step without a reducer → error (mirrors LangGraph `InvalidUpdateError`). | P0 |
| FR-TYPE-04 | Agent-level checks: model supports tool calling if tools wired; structured output schema valid; `response_format` strategy supported by model; tool name collisions; subagent names unique with descriptions present; permissions paths absolute and without `..`; HITL configured only for tools that exist. | P0 |
| FR-TYPE-05 | Security checks: secrets never inlined; shell/`execute` tools only with a sandbox backend; destructive MCP tools without approval produce a warning; network egress for scripts limited by policy. | P0 |
| FR-TYPE-06 | Script nodes are type-checked (pyright) against their declared ports; errors surface as node badges. | P1 |

### 4.4 State designer (FR-STATE)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-STATE-01 | Visual editor for the graph's state schema: channels with name, type (JSON Schema / Pydantic-like), default, reducer (`replace`, `append`, `add_messages`, `merge_dict`, `sum`, `max`, `DeltaChannel` (append-optimized), `custom` (script)), visibility (input/output/private). | P0 |
| FR-STATE-02 | Separate **Input schema**, **Output schema**, and **Context schema** (run-scoped, immutable config such as `user_id`, `tenant`, feature flags — compiles to `context_schema`/`Runtime.context`). | P0 |
| FR-STATE-03 | Presets: *Chat* (`messages` with `add_messages`), *Agent* (Deep Agents state incl. `files`, optional `todos`), *Pipeline* (typed fields). | P0 |
| FR-STATE-04 | Each node declares which channels it reads and writes; the canvas shows read/write badges; the validator warns about channels never written or never read. | P1 |
| FR-STATE-05 | Schema migrations: renaming/removing channels on a published workflow triggers a migration wizard (because persisted threads contain old checkpoints); publish runs a **resume-compatibility check** (can threads created by the previous version resume on the new one?). Promoted to P1 in v0.2. | P1 |
| FR-STATE-06 | Channels can be typed with **Data Studio entities** (`Customer`, `Ticket`) and marked as `DeltaChannel` for append-heavy data; the designer shows estimated checkpoint growth per turn. | P1 |

### 4.5 Agent composer (FR-AGENT)

The **Deep Agent** widget is the flagship. It exposes every `create_deep_agent` capability as slots and panels; the **Lite Agent** widget exposes `create_agent`.

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-AGENT-01 | Slots (wiring ports): `model` (ChatModel), `tools` (Tool[] — multi-connect), `middleware` (Middleware[] — ordered), `subagents` (Subagent[]), `skills` (SkillSet), `memory` (MemorySource[]), `backend` (Backend), `store` (Store), `response_format` (Schema). | P0 |
| FR-AGENT-02 | Config panel: name, system prompt (rich editor), `interrupt_on` table (per tool: approve / edit / reject / respond allowed decisions), permissions rule table (`operations`, `paths`, `mode` = allow/deny/interrupt; first-match-wins with reorder handles), debug flag, recursion limit. | P0 |
| FR-AGENT-03 | Built-in capabilities toggles with explanations: Planning (adds `TodoListMiddleware` — opt-in since deepagents 0.7), Filesystem tools, General-purpose subagent (on/off/override), Summarization (trigger thresholds), Prompt caching (auto), Code interpreter (QuickJS; PTC tool allow-list), Dynamic subagents, Rubric self-grading. | P0 |
| FR-AGENT-04 | Subagents editor: add *Declarative subagent* (name, description, system prompt, model, tools, middleware, skills, permissions, interrupt_on, response_format), *Compiled subagent* (link any sub-workflow on the canvas), *Forked subagent* (inherits parent context), *Async subagent* (remote/ASGI deployment; launch/check/update/cancel/list tools). Each subagent is also visible as a nested canvas. | P0 (sync), P1 (async/forked/dynamic) |
| FR-AGENT-05 | Backend chooser: *State* (ephemeral per thread), *Store* (persistent; namespace per user / assistant / thread / org), *Local filesystem* (dev only), *Sandbox* (Daytona, Modal, Runloop, E2B, Vercel, AWS AgentCore, LangSmith Sandboxes, Docker; thread- or assistant-scoped), *Context Hub* (versioned in LangSmith), *Composite* (route path prefixes to backends, e.g. `/memories/` → Store, `/workspace/` → Sandbox). | P0 (State/Store/Composite), P1 (sandboxes, Context Hub) |
| FR-AGENT-06 | Skills manager: attach skills (folders with `SKILL.md` + `scripts/`, `references/`, `assets/`) from the Workspace, Context Hub or marketplace; per-subagent skill selection; read-only vs writable; write-approval. | P1 |
| FR-AGENT-07 | Memory manager: `AGENTS.md`-style memory files; scope (agent / user / org); read-only vs writable; episodic memory search toggle; background consolidation schedule. Memory is configured through **Memory Spaces** (doc 08 §3) so the same spaces are shared by agent file routes, memory tools and Recall/Remember nodes. | P1 |
| FR-AGENT-08 | Harness/Provider profile selection (deepagents `HarnessProfile` / `ProviderProfile`) with an override editor. | P2 |
| FR-AGENT-09 | "Explode to graph": convert a Lite Agent into an explicit Graph-tier ReAct loop (model node + tool node + conditional edge) for full control; and "Collapse to agent" inverse where pattern matches. | P1 |
| FR-AGENT-10 | Structured output designer: define the response schema visually (fields, types, enums, descriptions), choose strategy (provider-native vs tool-calling), preview JSON Schema/Pydantic. | P0 |

### 4.6 Execution, testing & debugging (FR-RUN)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-RUN-01 | **Run** a workflow from the canvas with: chat input (for message-based workflows), JSON input form generated from Input schema, or a saved test case. | P0 |
| FR-RUN-02 | **Live overlay**: node status transitions per super-step; token streaming into the active node's preview; tool calls & results shown on the tool node; subagent activity streamed into nested badges (Deep Agents subagent streaming); custom events (`get_stream_writer`) shown as toasts or node logs. | P0 |
| FR-RUN-03 | **Run modes**: *Run all*, *Run from here* (reuse cached upstream checkpoint), *Run to here*, *Step* (one super-step), *Re-run node* (with same inputs). | P0 |
| FR-RUN-04 | **Breakpoints** on nodes (before/after) → compiled into dynamic `interrupt_before` / `interrupt_after` for debug runs only; conditional breakpoints (expression over state). | P0 |
| FR-RUN-05 | **State inspector**: view the full state at any checkpoint, diff between checkpoints, **edit state** and continue (`update_state`), **fork** from any checkpoint (time travel). | P0 |
| FR-RUN-06 | **Timeline scrubber** in the bottom dock: one tick per super-step; hover shows nodes executed; click restores canvas overlay to that point. | P0 |
| FR-RUN-07 | **Pinned outputs**: pin a node's last output so downstream iterations don't re-execute it; pinned data editable as JSON. | P0 |
| FR-RUN-08 | **Mock mode**: replace a model with a fake/scripted model or `LLMToolEmulator`, replace tools with recorded responses (cassettes) — deterministic, zero-cost test runs. | P1 |
| FR-RUN-09 | **Node caching**: nodes flagged cacheable are memoized by input hash (`CachePolicy`, TTL); changing a node invalidates only downstream caches. | P1 |
| FR-RUN-10 | Multiple concurrent test threads; thread list with titles; resume any thread. | P0 |
| FR-RUN-11 | **Double-texting policy** selection for published agents (reject / enqueue / interrupt / rollback). | P1 |
| FR-RUN-12 | Cancel and **graceful drain** (`langgraph.runtime.RunControl.request_drain()`; the run raises `langgraph.errors.GraphDrained`), resume later from the saved checkpoint. | P1 |
| FR-RUN-13 | Run cost & latency displayed per node and per run (tokens, $, ms) from trace data. | P0 |

### 4.7 Human-in-the-loop & Inbox (FR-HITL)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-HITL-01 | **Human Approval node** (Graph tier) and **tool-level approvals** (Agent tier via `interrupt_on` / `HumanInTheLoopMiddleware`) with decision types *approve*, *edit* (args), *reject* (with feedback message), *respond* (answer instead of executing). | P0 |
| FR-HITL-02 | **Human Input node**: pause and ask a question with a generated form (from a JSON Schema); resume with the answer. | P0 |
| FR-HITL-03 | **Inbox**: all pending interrupts across workflows/threads; filters (workflow, assignee, age, priority); bulk approve; SLA timers; escalation rules; assignment by role/group; audit of every decision. | P1 |
| FR-HITL-04 | Notifications via email, Slack, Teams, webhook, mobile push; deep link to the approval card. | P1 |
| FR-HITL-05 | MCP elicitation requests (from `langchain.mcp` `MCPAdapter`, surfaced as interrupts) appear in the same inbox. | P1 |
| FR-HITL-06 | Filesystem permission rules with `mode="interrupt"` surface as approvals with a file diff preview. | P1 |

### 4.8 Workspaces & scripting (FR-WS) — summary; full spec in doc 05

Git-backed per-project code workspace with Monaco editor, Python LSP, package management (`uv`), terminal into a sandbox, test runner, and hot reload into the palette. Holds script nodes, custom nodes, custom tools, custom middleware, state reducers, event handlers, skills, prompts, and tests.

### 4.9 Triggers, events & channels (FR-TRIG) — summary; full spec in doc 05

Manual, API, Webhook (signed), Cron, Event bus (internal & external: Kafka, SQS, Pub/Sub, Postgres CDC), Email inbound, Slack/Teams/Discord/WhatsApp channels, File drop (S3/GCS), Form submission, MCP/A2A inbound calls, Workflow-completed (chaining). Each trigger has a payload schema and a mapping to the workflow Input schema; filters and dedup keys; custom event handlers in code.

### 4.10 Knowledge & RAG (FR-KB)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-KB-01 | Knowledge Base resource: upload files / connect sources (S3, GDrive, Confluence, Notion, SharePoint, web crawl, DB), choose splitter, embedding model, vector store (pgvector default; Pinecone, Qdrant, Weaviate, Elastic, OpenSearch, Chroma adapters). | P1 |
| FR-KB-02 | Retriever node / Retriever-as-tool node (compiles to retriever tool); hybrid search, metadata filters from state expressions, reranker option. | P1 |
| FR-KB-03 | Agentic RAG template (grade documents → rewrite query → re-retrieve loop). | P1 |
| FR-KB-04 | Ingestion pipelines are themselves workflows (scheduled re-index). | P2 |

### 4.11 Evaluation studio (FR-EVAL)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-EVAL-01 | Test cases per workflow (input, expected output/criteria, optional reference trajectory); "Save run as test case" from any run. | P0 |
| FR-EVAL-02 | Datasets synced to LangSmith datasets; import from CSV/JSONL/traces; dataset splits and versions. | P1 |
| FR-EVAL-03 | Evaluators: exact/regex/JSON-schema match, LLM-as-judge (custom rubric), trajectory match (tool sequence strict/unordered/subset), code evaluators (workspace scripts), pairwise comparison, composite evaluators, human annotation queues. Use `openevals` / `agentevals` where applicable. | P1 |
| FR-EVAL-04 | Experiments: run workflow version(s) × dataset × config matrix (models, prompts, temperatures); results grid; per-example trace; statistical comparison. | P1 |
| FR-EVAL-05 | **Release gate**: publishing to *prod* requires the gating experiment to meet thresholds (e.g. correctness ≥ 0.85, no regression > 3 %). | P1 |
| FR-EVAL-06 | Online evaluators on production traffic (sampling rate, LLM-judge/code/composite/multi-turn), alerts on drift. | P2 |
| FR-EVAL-07 | Multi-turn simulation: simulated user persona against chat workflows. | P2 |
| FR-EVAL-08 | Runtime self-evaluation via the **Rubric** capability (deepagents `RubricMiddleware`) with rubric authored in the UI and grading events shown live. | P1 |

### 4.12 Observability (FR-OBS)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-OBS-01 | Every run is traced to LangSmith (project per workflow per environment) with metadata: `canvas_workflow_id`, `canvas_version`, `canvas_node_id`, `env`, `tenant`, `user`. | P0 |
| FR-OBS-02 | Trace overlay: open any LangSmith trace on the canvas; spans mapped back to nodes via `canvas_node_id` metadata. | P0 |
| FR-OBS-03 | Dashboards: runs, error rate, p50/p95 latency, tokens, cost, per-node hot spots, interrupt wait time, eval scores. | P1 |
| FR-OBS-04 | Alerts: thresholds on error rate, latency, cost, eval scores; destinations Slack/PagerDuty/email/webhook. | P1 |
| FR-OBS-05 | PII-safe tracing: input/output masking rules, trace sampling, conditional tracing; OpenTelemetry export for customers with their own stack. | P1 |
| FR-OBS-06 | Insights: clustering of production conversations to find failure modes (LangSmith Insights). | P2 |

### 4.13 Versioning & collaboration (FR-VER)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-VER-01 | Auto-save drafts; explicit **commit** with message creates an immutable version (IR + workspace files snapshot). | P0 |
| FR-VER-02 | Git backing: each project is a Git repo (internal Gitea-compatible or customer GitHub/GitLab); IR stored as canonical, stably-ordered JSON/YAML for readable diffs. | P1 |
| FR-VER-03 | **Visual diff**: added/removed/changed nodes & edges highlighted; per-field config diff; code diff for scripts. | P1 |
| FR-VER-04 | Branches, merge with 3-way IR merge (node-level), conflict resolution UI. | P2 |
| FR-VER-05 | Real-time multiplayer editing (CRDT), live cursors/presence, follow mode, node-level soft locks during edits. | P1 |
| FR-VER-06 | Review requests: request review on a version; reviewers comment on nodes; approval required to publish to prod (configurable). | P1 |
| FR-VER-07 | Environments: dev / staging / prod with per-env config overrides (models, secrets, endpoints) and promotion flow. | P1 |

### 4.14 Publish, deploy & expose (FR-DEP / FR-EXP)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-DEP-01 | **Publish targets**: (a) AgentCanvas Cloud runtime (our managed Agent Server fleet), (b) customer's **LangSmith Deployment** (cloud/hybrid/self-hosted), (c) **Managed Deep Agents** project (`mda deploy`) for Agent-tier-only workflows, (d) **Docker image** (standalone Agent Server), (e) **Export to Git** (clean project), (f) P2: TypeScript target. | P0 (a,e), P1 (b,c,d), P2 (f) |
| FR-DEP-02 | Pre-publish checklist: validation clean, tests pass, eval gate, secrets bound for target env, cost estimate reviewed. | P0 |
| FR-DEP-03 | Blue/green & canary (percentage traffic to new revision), instant rollback to previous revision. | P1 |
| FR-DEP-04 | Assistants: multiple configured *assistants* per published workflow (same graph, different config — e.g. model, prompt, tool subset). | P1 |
| FR-EXP-01 | Every published workflow automatically exposes: REST/SSE (Agent Server API: threads, runs, stream, state, history), **MCP endpoint** (the workflow as an MCP tool), **A2A** endpoint, **AG-UI** event stream, and OpenAPI spec. | P0 (REST), P1 (MCP, A2A, AG-UI) |
| FR-EXP-02 | **Embeddable chat widget** (script tag / React component) with theming, streaming, file upload, interrupt/approval UI, generative UI components; plus a hosted shareable chat page. | P1 |
| FR-EXP-03 | API keys per consumer, rate limits, quotas, custom auth (JWT/OIDC) mapped to `Runtime` user identity for per-user memory and credentials. | P1 |
| FR-EXP-04 | Crons and schedules with timezone and payload; webhooks on run completion. | P0 |

### 4.15 AI Copilot (FR-COP)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-COP-01 | NL → workflow: generate a full graph from a description; shown as ghost diff; accept/partial accept/reject. | P1 |
| FR-COP-02 | NL edits: "add an approval before sending emails", "make the researchers run in parallel", "switch all models to the cheaper tier". | P1 |
| FR-COP-03 | Explain: explain a node, a run, a failure ("why did it loop 12 times?") using trace + IR. | P1 |
| FR-COP-04 | Lint & auto-fix: best-practice rules (missing subagent descriptions, overly broad tool sets, no loop guard, missing approval on destructive tools, prompt anti-patterns). | P1 |
| FR-COP-05 | Code assist inside the Workspace: generate custom nodes/tools/handlers from description; tests generation. | P1 |
| FR-COP-06 | Eval assist: synthesize test cases & rubrics from the workflow's purpose and traces. | P2 |
| FR-COP-07 | Copilot operates only through the IR mutation API and must pass validation; every copilot change is an undoable, attributed commit. | P1 |

### 4.16 Templates, marketplace & reuse (FR-TPL)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-TPL-01 | Templates gallery (≥ 15 at GA): Deep Research, Agentic RAG, SQL Analyst, Customer Support Triage, Coding Agent (sandbox), Data Analysis (interpreter), Content Pipeline, Email Assistant with approvals, Meeting-notes-to-tasks, Lead Enrichment, Document Extraction (structured output), Supervisor multi-agent, Handoffs/Swarm, Router, Evaluator-Optimizer loop. | P0 (5), P1 (15) |
| FR-TPL-02 | Reusable components: publish a subgraph as an org component with versioned I/O contract. | P1 |
| FR-TPL-03 | Marketplace for custom nodes, components, skills, templates; publisher verification, code signing, security scanning, ratings; org-private registries. | P2 |

### 4.17 Connections, secrets & integrations (FR-CON)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-CON-01 | Connections manager: model providers (OpenAI, Anthropic, Google, Bedrock, Azure, Mistral, Fireworks, Groq, Ollama/vLLM local, NVIDIA NIM, LangSmith LLM Gateway), MCP servers (URL/stdio/in-process with OAuth 2.1 / bearer / per-user auth), SaaS OAuth apps, databases, vector stores. | P0 |
| FR-CON-02 | Secrets stored in a vault, referenced by name, injected at runtime only; never shown after creation; scoped per env; rotation. | P0 |
| FR-CON-03 | Per-end-user credentials (delegated OAuth) so an agent acts as the calling user. | P2 |
| FR-CON-04 | MCP server browser: list tools/resources/prompts of a connected server; import tools selectively as tool nodes; tool metadata (`destructive_hint`, `read_only_hint`) displayed. | P0 |

### 4.18 Governance & admin (FR-GOV)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-GOV-01 | Orgs → Workspaces(tenancy) → Projects → Workflows hierarchy; RBAC roles: Owner, Admin, Builder, Developer, Reviewer, Operator, Viewer; custom roles; ABAC on tags. | P0 (basic RBAC), P1 |
| FR-GOV-02 | SSO (SAML/OIDC), SCIM provisioning, MFA. | P1 |
| FR-GOV-03 | Model policies: allow-list providers/models per env; spend limits per project/user; rate limits (via LLM Gateway policies or our proxy). | P1 |
| FR-GOV-04 | Audit log of all actions (edits, publishes, approvals, secret access) exportable to SIEM. | P1 |
| FR-GOV-05 | Data retention policies for threads/checkpoints/traces; right-to-erasure per end user. | P1 |
| FR-GOV-06 | Node allow-list: admins can disable node types (e.g. shell, arbitrary HTTP) per project. | P1 |

### 4.19 Cost & performance estimation (FR-COST)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-COST-01 | Static estimate per run: model calls × expected tokens (from prompt sizes + historical averages) × price table; ranges for loops. | P1 |
| FR-COST-02 | Actual cost per run/node from traces; budgets and alerts. | P0 (actuals) |
| FR-COST-03 | Suggestions: cheaper model for subagents, prompt caching opportunities, summarization thresholds, tool pruning (`LLMToolSelectorMiddleware`, provider tool search). | P2 |

### 4.20 Persistence (FR-PER) — summary; full spec in doc 08

Persistence panel per workflow: checkpointer binding per environment (platform / Postgres / Redis / MongoDB / DynamoDB-Valkey / Cosmos / CockroachDB / SQLite / memory / custom), durability mode (`exit`/`async`/`sync`), TTL & pruning, `DeltaChannel` optimisation, encryption, subgraph persistence modes, conformance gate for non-default backends, thread browser (search, inspect, copy, export, delete). Requirements FR-PER-01…10.

### 4.21 Long-term memory & caching (FR-MEM, FR-CACHE) — summary; full spec in doc 08

Memory Spaces (typed, namespaced, indexed, TTL, writers, PII policy), Recall / Remember / Forget nodes, generated memory tools, Memory Inspector, episodic memory, background consolidation, memory safety; **RoutedStore** to spread memory across several databases; cache bindings, node caches, semantic LLM cache. Requirements FR-MEM-01…08, FR-CACHE-01…05.

### 4.22 Data Studio — visual data modelling (FR-DM) — summary; full spec in doc 08

ER-style polyglot modelling (entities, relations, indexes, vector fields, PII & tenant classifications), physical mappings to Postgres/MySQL/SQL Server, Redis, MongoDB, DynamoDB, Neo4j, Elasticsearch, vector DBs and warehouses; reverse engineering; migrations with approval; data browser & fixtures; DB branching; data nodes (Get, Query, Upsert, Vector search, Graph traverse, Cache, Transaction scope), generated repositories and scoped agent data tools; CDC triggers. Requirements FR-DM-01…11.

### 4.23 Self-improving agents (FR-LRN) — summary; full spec in doc 09

Feedback schema & capture (incl. automatic capture of HITL edits), Learning Center, Learning Loop nodes (Trace Query, Memory Extractor, Example Curator, Few-shot Selector, Prompt Optimizer, Skill Writer, Variant Router, Fine-tune Job, Eval Gate, Human Review, Promote/Canary/Rollback), versioned & revertible learned artifacts, learning budgets and safety. Requirements FR-LRN-01…10.

---

## 5. Non-functional requirements (product-level)

| ID | Requirement |
|----|-------------|
| NFR-01 | Editor interactions < 50 ms; validation < 150 ms (200 nodes); compile-to-code < 2 s (200 nodes); first token of a test run < 1.5 s + model latency. |
| NFR-02 | Runtime availability 99.9 % (GA), 99.95 % enterprise; no loss of committed checkpoints. |
| NFR-03 | Horizontal scalability: 10k concurrent active runs per region; 1M threads per tenant. |
| NFR-04 | Security: SOC 2 Type II, ISO 27001 readiness; tenant isolation; encryption at rest (AES-256) and in transit (TLS 1.3); sandboxed user code. |
| NFR-05 | Portability: exported code runs with only open-source dependencies (`langgraph`, `langchain`, `deepagents`, provider packages) + an optional thin `agentcanvas-runtime` helper package (Apache-2.0). No lock-in. |
| NFR-06 | Browser support: latest 2 versions of Chrome, Edge, Firefox, Safari. Desktop-first; read-only/approvals on mobile. |
| NFR-07 | Internationalization-ready UI; initial English. |
| NFR-09 | Persistence: no committed checkpoint lost (RPO ≤ 1 min in data plane); right-to-erasure across all persistence layers < 72 h; zero business-data queries without a resolved tenant/user scope. |
| NFR-08 | Observability of the platform itself: OpenTelemetry everywhere. |
