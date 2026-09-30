# 15 — Concept Evolution: WYSIWYG for Agents, Revisited (v0.6)

> The original brief: *"A ComfyUI-like product where users create agentic workflows by dragging and dropping components. Users only use widgets, connections and scripts, but the backend creates a full-fledged agentic workflow on LangChain (Python or a better-suited language). Workspaces let users write code for custom events."*
>
> Docs 00–13 turned that into a buildable system. This document steps back and asks: **what does "what you see is what you get" really have to mean for agents, where does the concept break, and what would make the product decisively better?** It ends with 24 new requirements (FR-X-01…24) that are sequenced into the sprint plan.

---

## 1. The core idea, stated as three contracts

A visual builder is only trustworthy if it keeps promises that users can check. AgentCanvas makes three:

| Contract | Promise | How it is enforced |
|----------|---------|--------------------|
| **Fidelity** | *The picture is the program.* Every box is a named construct in real LangGraph/LangChain/Deep Agents code, and what runs on the canvas is what ships. | IR as single source of truth (D1); shared node library for interpreter and generated code (ADR 0004); differential tests (TR-COMP-40); source maps; `canvas_node_id` on every node and span. |
| **Ownership** | *You can always leave with your code.* Export is clean, idiomatic, runs with open-source dependencies, and your scripts are copied verbatim. | Export target is a standard LangGraph project (`langgraph dev` runnable); runtime helper is Apache-2.0; code islands never templated; drift detection. |
| **Evolution** | *Changing an agent is as safe as changing code.* Every change is versioned, diffable, testable, gated and reversible — including changes that *agents make to themselves*. | Versions & visual diff; saved test cases → eval gates; resume-compatibility checks; governed learning ladder (doc 09); rollback. |

Everything in this document either strengthens one of these contracts or removes friction from keeping them.

## 2. Deepening WYSIWYG: from five axes to a ladder

The README defines five fidelity axes (structural, behavioral, runtime, temporal, escape-hatch). Two more are needed for agents, which — unlike images — **act on the world and cost money**:

6. **Consequence fidelity** — *see what it could do before it does it.* The canvas shows each workflow's **blast radius**: which nodes and tools can write to external systems, send messages, spend money, execute code or read sensitive data, and which of those are behind approvals. A "dry run" stubs every side-effecting tool while keeping the rest real (FR-X-02).
7. **Contract fidelity** — *see the outside of the workflow.* A workflow is also an API (REST, MCP, A2A, AG-UI, chat widget). The canvas shows that outer contract — input/output schemas, auth, exposure surfaces — and flags breaking changes between versions (FR-X-14).

These seven axes form a **WYSIWYG ladder** that also serves as the product's maturity model and onboarding path:

| Level | The user can see… | Enabled by | Persona who needs it first |
|-------|------------------|------------|---------------------------|
| L1 Structure | the graph and its wiring | canvas, typed ports, validator | P1 citizen builder |
| L2 Code | the exact code each widget becomes | Live Code Mirror (FR-X-01), source maps | P2/P3 |
| L3 Execution | what is running now, token by token | live overlay, v2 stream → canonical events | all |
| L4 History | every past step, editable and forkable | checkpoints, timeline, time travel | P2 |
| L5 Consequences | what it can change in the world, what it costs | blast-radius map, budgets, cost estimator (FR-X-02, FR-X-15) | P4/P5 |
| L6 Contract | how the outside world calls it and what breaks | contract view & API diff (FR-X-14) | P3/P6 |
| L7 Production | how it behaves at scale and why it fails | traces overlay, Engine bridge, online evals (FR-X-06) | P3/P5 |

**Design rule:** a feature ships only if it makes one rung *visible on the canvas*, not only in a separate console.

## 3. Where the concept breaks — and the answers

| Tension | Why it bites | Answer in AgentCanvas |
|---------|--------------|----------------------|
| **Graphs vs. agents.** ComfyUI graphs are DAGs; agents loop, branch on model output and spawn subagents at run time. A static picture cannot show a dynamic trajectory. | Users misread a Deep Agent box as "one step". | Three tiers (D3). Runtime-expanding nested canvases for subagents (`ns` events), trajectory overlay per run, and "Explode to graph" (FR-AGENT-09) to make an agent's loop explicit. |
| **Non-determinism.** The same picture produces different runs. | "It worked yesterday." | Pins, mocks and cassettes (FR-RUN-07/08), saved test cases with repetitions, record/replay from production traces (FR-X-13), eval gates. |
| **Spaghetti at scale.** Visual programs become unreadable around 50–100 nodes. | Pros abandon the canvas. | Subgraphs/components, wireless links, outline view, script nodes, Copilot refactors, and a policy that **any node can be "code-backed"** without leaving the canvas. |
| **Two-way sync with code.** Users want to edit generated code. | Round-trip editors rot. | Code islands are the only editable regions; everything else is regenerated. Eject is explicit; import is best-effort with a fidelity score. We do not promise arbitrary round-tripping. |
| **Hidden prompts are hidden logic.** Prompts steer behaviour more than wiring does. | The picture can look correct while behaviour is wrong. | Prompts are first-class resources with versions, diffs and tests; prompt changes appear in visual diffs and trigger the same gates as wiring changes. |
| **Self-modifying agents.** Learning loops change prompts, memories and skills. | The canvas stops being the truth. | Learned artifacts are versioned IR-adjacent resources (doc 09) with provenance; the canvas shows "learned" badges and a diff of what was learned since the last human-approved version. |

## 4. Language decision, revisited

The brief allowed "Python or any other language that might be better suited". The v0.6 evidence strengthens the original decision:

| Criterion (verified 2026-09) | Python | TypeScript | Others (Go/Java/Rust/C#) |
|------------------------------|--------|-----------|--------------------------|
| LangGraph node timeouts & error handlers | ✅ (Python-only per LangGraph 1.2 changelog) | ❌ | ❌ no LangGraph runtime |
| Deep Agents interpreters/PTC, rubric, permissions `interrupt` mode, profiles | ✅ | partial | ❌ |
| `langchain.mcp` (FastMCP-based) | ✅ | separate JS package | community only |
| Data work in scripts (pandas, numpy, sklearn) | ✅ | weak | varies |
| Browser/edge deployment, React UI, `useStream`, headless tools | ❌ | ✅ | ❌ |

**Decision (unchanged, sharpened):**
- **Generated agent code: Python** (canonical target). The IR stays language-neutral; a **TypeScript target** (P2) is enabled per node through the capability matrix.
- **Product frontend and embeddable widgets: TypeScript/React** — this is where LangChain's frontend patterns (`useStream`, headless tools, generative UI) live.
- **Other languages** are served *through the published contract*, not codegen: every workflow is REST/MCP/A2A, so Java/Go/C# systems call agents rather than host them.
- **Polyglot scripts** run in sandboxes: JavaScript via QuickJS (pure transforms) or Node sandbox, SQL against connections — without changing the Python graph.

## 5. Positioning (as understood in September 2026 — verify before external use)

| Product family | What it does well | Where AgentCanvas differs |
|----------------|------------------|---------------------------|
| **ComfyUI** | Node graphs for generative media, huge custom-node ecosystem | Same UX DNA; AgentCanvas adds state machines, HITL, durable execution, evals, governance, and compiles to code instead of interpreting a JSON graph forever. |
| **n8n / Zapier-style automation** | Integrations, triggers, no-code automation | AgentCanvas is agent-native (Deep Agents, subagents, memory, middleware) and emits real LangGraph code you can own. |
| **Langflow / Flowise / Dify** | Visual LLM app builders | AgentCanvas's differentiators are the *compiler contract* (idiomatic, exportable, tested-equivalent code), graph-tier control over LangGraph primitives, workspaces with events-as-code, and eval-gated releases. |
| **LangSmith Fleet** (formerly Agent Builder) | No-code agents from templates, connections, approvals | Complementary: Fleet targets individual productivity; AgentCanvas targets teams building *systems* with explicit graphs, code islands and CI/CD. AgentCanvas can publish to LangSmith Deployment and Managed Deep Agents. |
| **LangSmith Studio** | Visualize/debug LangGraph apps that already exist as code | AgentCanvas is an *authoring* environment whose debugger works the same way; exported projects remain Studio-compatible. |

**Strategy:** integrate with the LangChain platform rather than compete — LangSmith for traces/evals/Engine/sandboxes/gateway, Agent Server and Managed Deep Agents as publish targets — and win on authoring, composition, governance and ownership.

## 6. Workspaces and custom events, deepened: "Events as Code"

The brief singles out *"workspaces where we can do code for any custom events"*. Doc 05 specifies inbound events, runtime events and UI events. v0.6 unifies them into one model that is **code-first but canvas-visible**.

### 6.1 One event model, four handler kinds

| Handler kind | Written as (workspace) | Runs | Typical use | LangChain primitive underneath |
|--------------|-----------------------|------|-------------|-------------------------------|
| **Trigger handler** | `@on("crm.lead.created", filter=…, dedup_key=…)` in `events/*.py` | Trigger service → sandbox | start a workflow, resume a thread waiting on an external event, fan out to topics | runs API; `Command(resume=…)` for waits |
| **Lifecycle hook** | `@on("node.failed", workflow="support_triage")` | Event service on canonical run events | alerting, compensation, audit, chaining | canonical events from the stream; Agent Server webhooks for deployed runs |
| **In-graph emitter** | `runtime.emit("invoice.flagged", {...})` / `get_stream_writer()` in scripts, tools, middleware | inside the run | progress, custom UI, domain events for other workflows | `custom` stream mode |
| **Stream projection** (P2) | class in `events/transformers/*.py` | inside the run (v3) | typed custom channels (`stream.extensions.*`) for UIs and SDK clients | `compile(transformers=[…])` |

### 6.2 Event catalog & schema registry
- Every event type (`crm.lead.created`, `custom.invoice.flagged`, `node.failed`, …) is declared once with a JSON Schema and version in `events/schemas/`; emitters and handlers are type-checked against it (validator rule **E081**: handler filter references a field not in the schema).
- The **Event Map** view (new canvas mode) shows the whole project as a system: event sources → handlers → workflows → emitted events → downstream workflows. It is generated from code (AST scan) and IR — nothing is drawn by hand.

### 6.3 Developer loop
1. Write a handler in the Workspace editor (typed `Event` payload autocompletes from the schema).
2. **Replay** a captured event (from the event log) or a sample against the handler in the sandbox; see the actions it *would* take (dry-run) before enabling it.
3. Enable; delivery is at-least-once with dedup keys, retries, DLQ with replay (FR-EVT-01…08).
4. Handler runs appear in the same run timeline as workflows, so an incident can be followed from webhook → handler → run → approval → side effect.

### 6.4 Custom widgets from code
Custom events are one half; the other half is **custom nodes** (`@canvas_node`), custom tools, middleware, reducers, evaluators and stream transformers — all discovered from the Workspace by AST scan and appearing in the palette with a *workspace* provenance badge (doc 05 §3). v0.6 adds: custom nodes can declare `side_effects=["external_write"]` so they participate in the blast-radius map (FR-X-02) and E070 checks.

## 7. New requirements (FR-X)

Priorities follow the spec convention (P0 MVP, P1 GA, P2 later). "Sprint" refers to [build-guide/13-sprint-plan.md](./build-guide/13-sprint-plan.md).

| ID | Feature | Description | Upstream basis | Pri | Sprint |
|----|---------|-------------|----------------|-----|--------|
| FR-X-01 | **Live Code Mirror** | Split view with bidirectional selection sync; selecting a node shows its generated snippet; editing config highlights the changed lines before save ("this edit changes 3 lines"). | source maps (doc 03 §8) | P0 | S4 |
| FR-X-02 | **Blast-radius map & dry run** | Static analysis marks nodes/tools by capability: `external_write`, `message_send`, `code_exec`, `spend`, `pii_read`; overlay shows which are behind approvals. *Dry run* replaces side-effecting tools with recorders. | MCP `destructive_hint`/`read_only_hint`, node manifests | P1 | S8 |
| FR-X-03 | **Headless (client-side) tools** | Tool widget whose implementation runs in the end user's browser (clipboard, geolocation, local files). Compiles to a server tool that calls `interrupt()`; the chat widget executes and resumes. | `useStream` headless tools | P1 | GA |
| FR-X-04 | **Generative UI designer** | Register a component catalog (declarative: json-render / A2UI style); a *UI Output* node lets agents emit UI specs validated against the catalog; preview in the test panel and chat widget. | LangChain declarative generative UI | P1 | GA |
| FR-X-05 | **Decision-model nodes** | *Decide* node and evaluator type using typed decision models (probability / choice / score) through the LLM Gateway — cheaper, typed classifiers and guardrails. Falls back to an LLM with structured output when unavailable. | LangSmith decision models (SemIf, Jev) | P2 | GA+ |
| FR-X-06 | **Engine bridge** | Show LangSmith Engine issues as badges on the nodes their traces map to; "turn issue into test case" adds the failing trace inputs to saved tests; fix proposals can be applied as IR command batches and re-gated. | LangSmith Engine | P2 | GA+ |
| FR-X-07 | **Per-node trace policy** | Inspector toggle: trace inputs/outputs fully, omit, or summarise, per node. Compiles to `add_node(trace_policy=TracePolicy(...))`. Secret redaction stays in LangSmith client anonymizers. | `TracePolicy`, `omit_payload` | P1 | S6 |
| FR-X-08 | **Sandbox policy designer** | For workspace scripts and agent sandboxes: egress allow-list, secret injection via auth proxy (secrets never enter the sandbox), mounts (S3/GCS/Git/Context Hub), snapshots. | `langsmith.sandbox` auth proxy & mounts | P1 | S7 (basic), GA |
| FR-X-09 | **Custom stream projections** | Workspace stream transformers become typed custom channels visible on canvas and in SDK clients. | `compile(transformers=…)`, v3 | P2 | after SP-3 |
| FR-X-10 | **Safe redeploy** | Revisions drain in-flight runs at a superstep boundary (`RunControl.request_drain()`), then resume on the new revision if the resume-compatibility check passes; otherwise threads stay pinned to the old revision. | `RunControl`, `GraphDrained` | P1 | S11 |
| FR-X-11 | **IDE exposure (ACP)** | Agent-tier workflows can be exposed over the Agent Client Protocol so editors (ACP clients) can use them. | `deepagents-acp` | P2 | GA+ |
| FR-X-12 | **Trace-to-canvas import** | Create a draft workflow from a LangSmith trace of an existing agent (spans → nodes, tool calls → tool widgets, fidelity score). | LangSmith runs API | P2 | GA+ |
| FR-X-13 | **Record/replay from production** | Capture model and tool I/O of a production run as a cassette; replay deterministically on a new version; add as regression test. | fake models + cassettes, traces | P1 | S8 |
| FR-X-14 | **Contract view & API diff** | Canvas mode showing the workflow's external contract (input/output/context schemas, exposure surfaces, auth); version diff flags breaking changes (removed output field, narrowed enum, new required input). | IR schemas | P1 | S8 |
| FR-X-15 | **Budgets** | Per-run and per-thread budgets (tokens, $, wall-clock) configured on the workflow; enforced by call-limit middleware and a budget guard in the runner; estimator shows expected vs. budget. | `ModelCallLimitMiddleware`, usage metadata | P1 | S6 |
| FR-X-16 | **Multimodal I/O** | Typed `File`, `Image`, `Audio`, `Video`, `PDF` ports; test panel uploads; Deep Agents multimodal `read_file`. | Deep Agents multimodal | P1 | GA |
| FR-X-17 | **Explain mode** | Step-through visualisation of super-steps (which nodes run in parallel, which channels are written, why a router chose a branch) for learning and reviews. | `tasks`/`updates` stream | P1 | S4 |
| FR-X-18 | **Channel lineage** | State inspector shows, per channel value, which node/step wrote it (from checkpoint task writes), with links to the producing span. | checkpoint metadata & writes | P1 | S6 |
| FR-X-19 | **Policy-as-code lint profiles** | Org rules as CEL predicates over the IR ("every `external_write` requires approval", "no model outside allow-list in prod"), evaluated by the validator and at publish. | validator plugin | P1 | S10 |
| FR-X-20 | **Visual diff in PRs** | For projects synced to GitHub/GitLab, a bot comments the rendered IR diff (added/removed/changed nodes, prompt diffs) on pull requests. | Git sync | P2 | GA+ |
| FR-X-21 | **Per-user runtime connections** | On Agent Server targets, tools and MCP servers are loaded per authenticated user through a `ServerRuntime` graph factory (delegated OAuth). | `ServerRuntime.ensure_user()` | P2 | GA |
| FR-X-22 | **Events-as-code** | Event catalog & schema registry, four handler kinds, Event Map view, replay dry-run (§6). | custom stream mode, runs API | P1 | S7 (handlers v0), GA |
| FR-X-23 | **Budget-aware model routing** | "Cheapest capable model" option on Chat Model widgets: pick from an allow-list by capability profile and budget; overridable per environment. | model profiles, LLM Gateway | P2 | GA+ |
| FR-X-24 | **Guided build (tutor)** | First-run tutorial that builds a real workflow with the user, explaining each concept (state, reducer, interrupt) in context — tuned for citizen builders. | templates + Copilot | P1 | S9 |

### 7.1 New diagnostics introduced by FR-X and the v0.6 review

| Code | Severity | Rule |
|------|----------|------|
| `E041` | error | Host-executing backend (`LocalShellBackend`, `FilesystemBackend`) bound to a hosted environment |
| `E042` | error | Local/stdio MCP target without `allow_local_mcp` and sandboxed execution |
| `E074` | error | Channel storage mode changed (delta ↔ full) or delta channel removed while the previous version has live threads |
| `E081` | error | Event handler filter/mapping references a field missing from the event schema |
| `E090` | error | Budget below the static minimum for one successful path |
| `W074` | warning | Sandbox without an egress allow-list |
| `W080` | warning | Side-effecting node (`external_write`/`message_send`/`spend`) reachable without an approval or dry-run stub |
| `W081` | warning | Breaking change to the external contract without a major version bump |
| `I010` | info | Workflow uses an upstream beta/experimental API (listed) |

## 8. What we deliberately do *not* do

- **No proprietary runtime.** LangGraph executes; we assemble, observe and govern (D6).
- **No arbitrary code↔canvas round-trip.** Code islands plus explicit eject/import only.
- **No silent self-modification.** Every learned change is a versioned, gated artifact.
- **No lowest-common-denominator multi-framework UI.** Core IR + native dialects (doc 11/12); LangChain is and stays the flagship adapter.
- **No beta upstream API on the critical path without a stable fallback** (see doc 14 §7).
