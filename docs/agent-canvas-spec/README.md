# AgentCanvas — Visual Agentic Workflow Builder

**Functional & Technical Specification — v0.5 (Draft)** — **v0.5 adds a deep review ([13](./13-deep-review-report.md)) and a beginner [Build Guide](./build-guide/README.md) with a tested reference implementation.** v0.2 adds persistence, multi-database data modelling and self-improving agents (docs 08–10); v0.3 makes the architecture framework-agnostic (doc 11); v0.4 adds source-verified multi-framework research and the full adapter technical specification (doc 12 + adapters/).
Working name: **AgentCanvas** (rename freely).
Target frameworks: **LangChain 1.4+, LangGraph 1.2+, Deep Agents 0.7+, LangSmith** (researched against the official `langchain-ai/docs`, `langchain-ai/deepagents`, `langchain-ai/langchain` and `langchain-ai/langgraph` repositories, September 2026).

---

## 0. How to read this spec

| # | Document | What it covers |
|---|----------|----------------|
| 0 | **README.md** (this file) | Vision, core concept, key design decisions, glossary |
| 1 | [01-functional-spec.md](./01-functional-spec.md) | Personas, user journeys, every functional module and its requirements (FR-xxx) |
| 2 | [02-technical-architecture.md](./02-technical-architecture.md) | System architecture, services, tech stack, data model, APIs, runtime, streaming, deployment, security, NFRs |
| 3 | [03-graph-ir-and-compiler.md](./03-graph-ir-and-compiler.md) | The Graph Intermediate Representation (IR), type system, validation, code generation to LangGraph/LangChain/Deep Agents, round-tripping |
| 4 | [04-node-catalog.md](./04-node-catalog.md) | Every built-in widget/node, its ports, config, and the exact LangChain construct it compiles to |
| 5 | [05-workspaces-scripting-events.md](./05-workspaces-scripting-events.md) | Code workspaces, script nodes, custom nodes SDK, custom events/triggers/hooks |
| 6 | [06-roadmap-risks.md](./06-roadmap-risks.md) | Phased delivery plan, MVP cut, risks, open questions, success metrics |
| 7 | [07-worked-example.md](./07-worked-example.md) | End-to-end example: canvas → IR → generated Python → deployment |
| 8 | [08-persistence-memory-data-modeling.md](./08-persistence-memory-data-modeling.md) | Persistence Map (6 layers), checkpointer bindings (Postgres, Redis, MongoDB, …), long-term memory spaces, RoutedStore over multiple DBs, caching, **Data Studio** (visual polyglot data modelling), data nodes & tools, side-effect correctness |
| 9 | [09-self-improving-agents.md](./09-self-improving-agents.md) | Learning ladder (memory → examples → skills → prompts → routing → fine-tuning), feedback capture, Learning Loop nodes, gates, canary & rollback |
| 10 | [10-improvement-plan.md](./10-improvement-plan.md) | Research-driven improvement plan v0.1 → v0.2 and beyond |
| 11 | [11-framework-agnostic-plan.md](./11-framework-agnostic-plan.md) | Framework-agnostic architecture (Core IR + dialects, Adapter SPI, canonical events, hosting modes) and phased plan: LangChain in R1, then interop, OpenAI Agents SDK, Google ADK, Microsoft Agent Framework, CrewAI, Pydantic AI, … |
| 12 | [12-multi-framework-technical-spec.md](./12-multi-framework-technical-spec.md) | **Source-verified** research on 10 frameworks + protocols (versions, APIs, licences), verified concept matrix, adapter SPI (normative), AG-UI-based canonical events, canonical HITL/resume envelope, persistence/tool/model bridges, hosting wrappers, conformance suite, tiers, detailed delivery plan; per-framework specs in [adapters/](./adapters/) |
| 13 | [13-deep-review-report.md](./13-deep-review-report.md) | Deep review v0.4 → v0.5: verified API errors, runtime pitfalls, contradictions, decisions (ADRs), scope changes |
| **BG** | [**build-guide/**](./build-guide/README.md) | **Start here to build the product:** concepts primer, environment setup, walking-skeleton tutorial with tested reference code (backend + canvas), milestone/ticket plan M0–M9, backend/frontend/compiler guides, testing, deployment, security checklist, FAQ, traceability |

Requirement IDs: `FR-` functional, `NFR-` non-functional, `TR-` technical. Priority: **P0** (MVP), **P1** (GA), **P2** (later).

---

## 1. The idea in one paragraph

ComfyUI proved that a node graph is the right UX for composing complex generative pipelines: people who never write code build sophisticated workflows by dragging widgets and wiring them together, and power users extend it with custom nodes. **AgentCanvas applies that idea to agentic systems.** Users compose agents, tools, models, memory, sub-agents, human approvals, routers and scripts on a canvas. Behind the canvas, a compiler turns the picture into a **real, idiomatic, production-grade LangGraph / LangChain / Deep Agents Python project** — the same code a senior engineer would write by hand — that can be run live on the canvas, debugged step-by-step, evaluated in LangSmith, exported to Git, and deployed to LangSmith Deployment (Agent Server), Managed Deep Agents, or your own infrastructure.

## 2. What "WYSIWYG for agents" really means (the enhanced concept)

A literal "what you see is what you get" for agents has to solve something ComfyUI never had to: agents are **stateful, looping, non-deterministic, long-running, and interruptible**, not a one-shot DAG. We therefore define WYSIWYG along five axes, and every design decision in this spec serves one of them:

1. **Structural fidelity** — Every box on the canvas corresponds 1:1 to a named construct in the generated code (a graph node, an agent, a tool, a middleware, a subagent). Nothing hidden, nothing invented. Each generated block carries a source-map comment (`# @canvas-node: n_7f3a`) so the mapping is navigable in both directions.
2. **Behavioral fidelity** — What runs when you press ▶ on the canvas is *the same code you would deploy*. We guarantee this with two execution modes (interpreted & compiled) that are continuously differential-tested against each other (see TR-COMP-40).
3. **Runtime visibility** — While running, the canvas *is* the debugger: nodes light up as LangGraph super-steps execute, tokens stream into the node that produced them, tool calls appear on the edges, sub-agents expand live, interrupts turn a node amber and open an approval card, and every run is a LangSmith trace you can overlay on the graph.
4. **Temporal fidelity** — Agents have history. The canvas has a **timeline scrubber** backed by LangGraph checkpoints: scrub back to any super-step, inspect state, edit it, and fork a new run from there (LangGraph time travel).
5. **Escape-hatch fidelity** — When widgets are not enough, users drop into code *without leaving the model*: script nodes, custom tools, custom middleware and custom event handlers are edited in a full in-browser IDE (the **Workspace**) and remain first-class canvas citizens. Users can also **eject** an entire workflow to code and (within limits) **re-import** code back to the canvas.

## 3. Key design decisions (summary — rationale in the linked docs)

| # | Decision | Why |
|---|----------|-----|
| D1 | **The Graph IR (JSON) is the single source of truth**, not the code and not the canvas layout. Canvas and code are projections of the IR. | Enables versioning, diffing, validation, multiple code targets, AI editing, and round-tripping. |
| D2 | **Primary code target: Python** (LangGraph 1.2 + LangChain 1.4 + deepagents 0.7). **Secondary: TypeScript** (LangGraph.js / deepagents-js) in P2. | Python has the most complete feature set (per-node timeouts, node error handlers, `CodeInterpreterMiddleware`, `RubricMiddleware`, interpreters/PTC are Python-first or Python-only), the richest integration ecosystem, and data-science friendliness for script nodes. TS is useful for edge/Next.js deploys. |
| D3 | **Three abstraction tiers on one canvas**: *Agent tier* (a Deep Agent or `create_agent` as one rich widget with slots), *Graph tier* (explicit `StateGraph` with routers, fan-out, interrupts), *Code tier* (script nodes & custom components). Users zoom between tiers. | Beginners stay in the Agent tier; architects build explicit graphs; engineers write code — all in the same artifact. |
| D4 | **Three kinds of edges**: *Flow* edges (runtime control transitions), *Wiring* edges (compile-time dependency injection: model→agent, tool→agent, backend→agent — ComfyUI style), *Data* edges (explicit state-channel mapping between nodes/subgraphs). Visually distinct. | ComfyUI is pure dataflow; LangGraph is a state machine over shared state. Unifying both is the core UX innovation. |
| D5 | **Typed ports with a structural type system** (e.g. `ChatModel`, `Tool[]`, `Messages`, `JSON<Schema>`, `Backend`, `Subagent`) and live type-checking while wiring. | Invalid wiring is prevented at edit time, not at run time. |
| D6 | **LangGraph is the runtime; we do not build our own orchestration engine.** Durable execution, checkpoints, interrupts, streaming, stores are all LangGraph's. **v0.5:** the MVP hosts graphs in its own open-source runner (FastAPI + LangGraph library + Postgres); LangSmith Agent Server / Deployment is a publish target (review D-01). | Maximum leverage, zero divergence from the ecosystem, the generated code is portable, no runtime licence dependency. |
| D7 | **LangSmith is the observability, evaluation and (optional) deployment plane.** We embed it, we don't replace it. Self-hosted/OTel path supported. | Tracing, evals, datasets, prompt hub/Context Hub, Agent Server, sandboxes already exist. |
| D8 | **All user code runs in sandboxes**, never inside the control plane. | Security; multi-tenancy. |
| D9 | **Deep Agents is the default "Agent" widget**; plain `create_agent` is the "Lite Agent". | Deep Agents gives planning, virtual filesystem, subagents, skills, memory, summarization and HITL out of the box — the batteries non-coders need. |
| D10 | **Canvas-native AI Copilot** that edits the IR through the same tools a human uses (add node, wire, configure), is itself a Deep Agent, and must pass the validator before changes apply. | "Describe your workflow" → graph; also lint/auto-fix/explain. |
| D11 | **Persistence is explicit and layered** (execution state / long-term memory / agent files / business data / caches / learning artifacts), bound per environment to real databases, with a visual **Data Studio** for business data. | Agents that remember, learn and act on data need clear ownership, retention and safety per layer (doc 08). |
| D12 | **Self-improvement is a governed ladder**, not magic: every learned artifact is versioned, evaluated, reviewable and revertible. | Learning without gates degrades production silently (doc 09). |
| D13 | **Framework-agnostic core, LangChain first.** The IR is split into a portable core plus namespaced framework dialects; all framework code lives behind a Framework Adapter SPI; the platform consumes canonical run events. R1 ships only the LangChain adapter. | Lets us add OpenAI Agents SDK, Google ADK, Microsoft Agent Framework, CrewAI, etc. as adapters without rewriting the product (doc 11). |

## 4. How AgentCanvas maps to the LangChain ecosystem

```
                ┌──────────────────────── AgentCanvas ────────────────────────┐
  Canvas (UI) ─►│ Graph IR ─► Validator ─► Compiler ─► Python project          │
                │                    │                    │                   │
                │                    └─► Interpreter ─────┤ (same semantics)  │
                └────────────────────────────────────────┬┴───────────────────┘
                                                         ▼
   LangChain 1.4  : create_agent, middleware, tools, structured output, langchain.mcp (MCPAdapter)
   Deep Agents 0.7: create_deep_agent, subagents (sync/async/forked/dynamic), backends, skills,
                    memory, permissions, interpreters (QuickJS/PTC), RubricMiddleware, profiles
   LangGraph 1.2  : StateGraph / Functional API, Send, Command, interrupt, checkpointers, stores,
                    DeltaChannel, RetryPolicy/CachePolicy, per-node timeouts & error handlers,
                    RunControl (graceful drain, `langgraph.runtime`), stream_events v3 (experimental), time travel
   LangSmith      : tracing, datasets, experiments, online evals, annotation queues, Studio,
                    Agent Server (assistants/threads/runs/crons/webhooks/MCP/A2A),
                    Managed Deep Agents, Context Hub, LLM Gateway, Sandboxes, Fleet
```

## 5. Additional features proposed beyond the original brief

(Each is specified in the functional spec; listed here so they are not missed.)

1. **Live execution overlay & step debugger** with breakpoints (compiled to `interrupt_before`/`interrupt_after`), watch expressions on state channels, and time-travel forking.
2. **Pinned outputs / mocks** — pin a node's output (like n8n) or swap a model with `LLMToolEmulator`/a fake model to iterate cheaply and deterministically.
3. **Partial re-execution with node caching** (ComfyUI's killer feature) using LangGraph `CachePolicy` — re-run from the changed node only.
4. **AI Copilot** for NL→graph, explain, lint, auto-fix, and "generate a custom node from a description".
5. **Evaluation Studio** — datasets, test suites per workflow, trajectory evals, LLM-as-judge, pairwise comparisons, rubric-driven self-correction, and a **release gate** that blocks publishing on regression.
6. **Versioning & visual diff** — Git-backed; see node-level diffs between versions; branch/merge workflows; environments (dev/staging/prod).
7. **Real-time multiplayer** (CRDT) with presence, comments pinned to nodes, and review/approve flows.
8. **Triggers & channels** — webhooks, cron, queues, email, Slack, file drop, DB change events, and inbound MCP/A2A calls start runs.
9. **Exposure surfaces** — every published workflow is automatically a REST API, an MCP server, an A2A agent, an AG-UI endpoint, and an embeddable chat widget.
10. **Custom node SDK & marketplace** — `@canvas_node` decorator, typed ports, packaged as pip wheels; community/org registries with signing and review (the ComfyUI-Manager equivalent, done securely).
11. **Cost & latency estimator** — static estimate on the canvas before running, actuals from LangSmith after.
12. **Guardrails pack** — PII redaction, prompt-injection screening, tool-call limits, model-call limits, content filters, destructive-tool approval (via MCP `destructive_hint`).
13. **Human task inbox** — interrupts from all running workflows land in a shared inbox with assignment, SLA and escalation.
14. **Templates gallery** — research agent, RAG, SQL agent, support triage, coding agent, data analyst, content pipeline, etc.
15. **Governance** — RBAC/ABAC, audit log, secrets vault, model allow-lists (via LLM Gateway policies), spend limits, data residency.
16. **Eject & import** — export clean code; import an existing LangGraph project and get a (read-mostly) canvas view.
17. **Pluggable persistence** — choose checkpoint DB per environment (Postgres, Redis, MongoDB, DynamoDB, …), TTL/retention, encryption, thread browser (doc 08).
18. **Memory Spaces & Memory Inspector** — typed long-term memory across multiple databases via a routed store (doc 08).
19. **Data Studio** — draw your data model once, map it to Postgres/Redis/Mongo/Neo4j/Elastic/vector DBs, generate models, migrations, repositories and safe agent tools (doc 08).
20. **Self-improving agents** — feedback capture, example banks, prompt optimisation, skill learning, bandits and fine-tuning behind eval gates (doc 09).

## 6. Glossary

| Term | Meaning |
|------|---------|
| **Workflow** | A top-level canvas document. Compiles to one LangGraph graph (one Agent Server *assistant blueprint*). |
| **Node / Widget** | A box on the canvas. Either a *runtime node* (becomes a LangGraph node) or a *resource node* (becomes a constructor argument: model, tool, backend…). |
| **Port** | A typed input/output socket on a node. |
| **Flow edge** | Runtime transition between runtime nodes (`add_edge`, conditional edges, `Command(goto=…)`, `Send`). |
| **Wiring edge** | Compile-time injection of a resource into a consumer (e.g. `ChatModel` → Agent.model). |
| **Data edge** | Explicit mapping of state channels between a node/subgraph and the parent state. |
| **State schema** | The typed set of channels (with reducers) shared by the graph. |
| **Group / Subgraph** | A collapsed set of nodes; compiles to a LangGraph subgraph or a Deep Agents `CompiledSubAgent`. |
| **Script node** | A runtime node whose body is user code edited in the Workspace. |
| **Workspace** | A per-project, Git-backed code environment (editor, venv, terminal, sandbox) holding scripts, custom nodes, tools, middleware, event handlers, skills and tests. |
| **Graph IR** | The canonical JSON representation of a workflow (see doc 03). |
| **Run** | One execution of a workflow on a thread (LangGraph run / LangSmith trace). |
| **Thread** | A persistent conversation/execution context (checkpoint lineage). |
| **Trigger** | Something that starts a run: manual, API, webhook, cron, event, channel message. |
| **Persistence binding** | Mapping of a logical persistence need (checkpointer, store, cache, connection) to a physical database in an environment. |
| **Memory Space** | A typed, namespaced region of long-term memory (store) with index, TTL, writers and PII policy. |
| **Data Model** | A Data Studio ER model; entities map to one or more physical stores. |
| **Learning Loop** | A workflow that turns signals (feedback, outcomes, evals) into versioned artifacts (memories, examples, prompts, skills, models) behind gates. |
| **Adapter** | A package implementing the Framework Adapter SPI for one agent framework (LangChain is adapter #1). |
| **Dialect** | Framework-specific node types/config (e.g. `langchain.deep_agent`) layered on the portable Core IR. |
| **Target** | A code-generation/deployment target (Python-LangGraph, Managed Deep Agents project, TS, etc.). |
