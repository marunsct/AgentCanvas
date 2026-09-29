# 11 — Framework-Agnostic Architecture & Multi-Framework Roadmap

> Question: *Can AgentCanvas build workflows for multiple agentic frameworks, not only LangChain?*
> **Answer: Yes — if we design for it now.** Release 1 ships with **LangChain / LangGraph / Deep Agents only**, but the IR, compiler, runtime, event protocol and UI are built around a **Framework Adapter SPI** so that further frameworks (OpenAI Agents SDK, Google ADK, Microsoft Agent Framework, CrewAI, Pydantic AI, LlamaIndex Workflows, Mastra, Claude Agent SDK, Strands, …) can be added as adapters without rewriting the product.

---

## 1. Feasibility analysis

### 1.1 Why it is possible
1. **Agent frameworks share a common conceptual core.** Every major framework has models, tools, instructions/prompts, an agent loop (reason → call tools → observe), structured output, some form of multi-agent composition (handoffs, sub-agents, crews, teams), and increasingly explicit workflows (graphs, flows, sequential/parallel/loop agents).
2. **Interop standards now exist for the edges:** **MCP** (tools/resources), **A2A** (agent-to-agent calls), **AG-UI** (agent ↔ UI event stream), **OpenTelemetry GenAI semantic conventions** (tracing), and **Open Agent Specification (Agent Spec)** — a framework-agnostic declarative language for agents and flows with runtimes demonstrated on LangGraph, CrewAI, AutoGen and WayFlow.
3. **A universal host runtime exists.** LangSmith Agent Server can already run agents from other frameworks (Claude Agent SDK, Strands, CrewAI, AutoGen via the LangGraph Functional API `@entrypoint`/`@task` pattern; Google ADK via `deployments-wrap-sdk`), giving them threads, checkpoints, streaming and tracing. That means *our platform services* (runs, inbox, triggers, persistence, observability) can stay the same while the agent logic comes from another framework.

### 1.2 Why it is hard (and how we avoid the traps)

| Trap | Why it bites | Our answer |
|------|--------------|-----------|
| **Lowest-common-denominator** | If the canvas only offers what every framework supports, it's worse than each framework. | **Portable core + native dialects**: a portable node set that compiles everywhere, plus framework-specific nodes that unlock native power. The validator tells users what is portable. |
| **Semantic mismatch** | State, persistence, HITL, streaming and retries behave differently per framework (e.g. LangGraph shared state + super-steps vs. CrewAI task outputs vs. OpenAI handoffs vs. ADK sessions). | Explicit **capability matrix** per adapter with levels *native / emulated / unsupported*; emulation documented; conformance tests prove equivalence where claimed. |
| **Leaky platform** | If LangGraph types leak into the IR, UI and services, adding a framework means a rewrite. | Architectural rule: **no framework imports outside adapter packages**; fitness tests in CI enforce it. |
| **Moving targets** | Every framework ships breaking changes. | Per-adapter **stacks** (pinned versions), nightly upstream compatibility CI, adapter semver independent of the core. |
| **Cost explosion** | N frameworks × M features. | Tiered support (see §7), prioritisation by demand, community adapters via SDK. |

### 1.3 Positioning
AgentCanvas becomes **"the visual IDE and control plane for agents — bring your framework"**. LangChain is the first-class, deepest integration; other frameworks progressively reach parity where they have the concepts.

---

## 2. Target architecture

```
                         ┌──────────────── Framework-neutral core ────────────────┐
 Canvas / Copilot / API ─► Core IR (portable)  +  Dialect extensions (namespaced)  │
                         │        │                                               │
                         │  Validator (core rules + adapter capability rules)     │
                         │        │                                               │
                         │  Adapter Registry ──► selects adapter per workflow/node │
                         └────────┼───────────────────────────────────────────────┘
                                  ▼
        ┌──────────────── Framework Adapter SPI (one package per framework) ───────────────┐
        │ manifest & capabilities │ node library │ lowering │ codegen │ interpreter/shim   │
        │ event translator → Canonical Run Events │ persistence mapping │ HITL mapping     │
        │ tool/model bridges (MCP, model gateway) │ packaging & deploy targets │ importer  │
        └───────┬──────────────┬──────────────┬──────────────┬──────────────┬──────────────┘
                ▼              ▼              ▼              ▼              ▼
          LangChain/LG    OpenAI Agents    Google ADK    MS Agent Fw     CrewAI  …
          (R1, Tier 1)    (planned)        (planned)     (planned)       (planned)
                │              │              │              │              │
                └──────── Hosting modes: (A) Agent Server host (B) native runtime (C) A2A/MCP remote ───┘
                                  ▼
             Platform services (unchanged): runs, inbox, triggers, persistence bindings,
             Data Studio, learning loops, evals, tracing (OTel GenAI → LangSmith / others)
```

### 2.1 Core IR vs dialects

- **Core IR (portable)** — the concepts every adapter must at least *understand* (even if it rejects some):
  `Workflow`, `State` (typed channels), `Model`, `Tool` (function / HTTP / MCP), `Prompt`, `Schema`, `Agent` (tool-using loop with instructions), `SubAgent`/`Delegate`, `Handoff`, control flow (`Sequence`, `Router`, `Parallel`, `Join`, `Map`, `Loop`), `HumanApproval`, `HumanInput`, `MemoryRecall`/`MemoryWrite`, `Script`, `Trigger`, `Output`, node policies (retry, timeout).
- **Dialect extensions** — framework-specific node types and config, namespaced: `langchain.deep_agent`, `langchain.middleware.*`, `langgraph.send`, `openai.guardrail`, `adk.loop_agent`, `crewai.crew`, `crewai.flow.router`, `msaf.workflow.executor`, `pydantic_ai.capability`, … Each dialect node declares which adapter(s) can compile it.
- **Portable config with dialect overrides**: a core node may carry `"x-langchain": {...}` / `"x-openai": {...}` blocks for adapter-specific tuning without breaking portability.

```jsonc
{ "id": "n_ag", "type": "core.agent@1.0", "name": "reply_drafter",
  "config": { "instructions": "…", "output_schema": {"$ref": "#/types/Reply"} },
  "x-langchain": { "harness": "deep_agent", "planning": false, "summarization": { "trigger_tokens": 60000 } },
  "x-openai":    { "model_settings": { "tool_choice": "auto" } } }
```

- **Framework selection**: `workflow.framework = "langchain"` (primary adapter) and optional per-node `engine` override for mixed graphs (P3+).
- **Portability score** shown in the top bar: % of nodes that compile on each installed adapter + list of blockers.

### 2.2 The Framework Adapter SPI

```python
class FrameworkAdapter(Protocol):
    manifest: AdapterManifest            # id, version, languages, supported stacks, hosting modes
    capabilities: CapabilityMatrix       # per core concept: native | emulated | unsupported (+ notes)

    def node_library(self) -> list[NodeTypeManifest]: ...          # dialect nodes + support flags for core nodes
    def validate(self, plan: ExecutionPlan) -> list[Diagnostic]: ... # adapter-specific rules
    def lower(self, plan: ExecutionPlan) -> AdapterPlan: ...        # core → framework constructs (+ emulations)
    def generate(self, plan: AdapterPlan, target: Target) -> BuildArtifact: ...   # project files + source map
    def interpret(self, plan: AdapterPlan, debug: DebugOptions) -> RunnableHandle: ...  # dev runs
    def event_translator(self) -> EventTranslator: ...              # native events → Canonical Run Events
    def persistence(self) -> PersistenceMapping: ...                # how threads/state/memory map to bindings
    def hitl(self) -> HitlMapping: ...                               # approvals / input requests / resume
    def hosting(self) -> list[HostingMode]: ...                      # agent_server_wrapped | native | a2a_remote
    def importer(self) -> Importer | None: ...                       # code → IR (optional)
    def conformance(self) -> ConformanceSuite: ...                   # tests the adapter must pass
```

Adapters are Python packages (`agentcanvas-adapter-<fw>`) loaded by the compiler workers and runtime; TS-only frameworks (e.g. Mastra) ship a Node sidecar implementing the same SPI over JSON-RPC.

### 2.3 Canonical Run Event protocol (framework-neutral)

The run overlay, inbox, triggers, cost tracking and learning loops consume **only** canonical events:

| Canonical event | LangGraph source | Typical other-framework source |
|-----------------|------------------|--------------------------------|
| `run.start/end/error` | run lifecycle | runner start/finish/exception |
| `node.start/end/error` | tasks/debug stream (node name ↔ canvas id) | step/executor/agent/task callbacks |
| `llm.token`, `llm.call` | `messages` stream | model streaming events |
| `tool.call`, `tool.result` | tool messages / events | tool hooks |
| `agent.handoff`, `agent.delegate` | subgraph ns / `task` tool | handoff / sub-agent / delegation events |
| `state.update`, `checkpoint` | `updates` / `checkpoints` stream | session state deltas / flow state (may be *emulated*) |
| `interrupt.raised/resolved` | `interrupt()` / `Command(resume)` | approval requests / input requests (native or emulated) |
| `custom.*` | `custom` stream | custom events / callbacks |

Wire format aligns with **AG-UI** for UI consumers and **OpenTelemetry GenAI semantic conventions** for traces, so any framework already emitting OTel/AG-UI needs a thin translator.

### 2.4 Hosting modes

| Mode | Description | When |
|------|-------------|------|
| **A. Agent Server host** (default for all adapters) | Generated code wraps the framework's agent/workflow in a LangGraph Functional API `@entrypoint` (or `deployments-wrap-sdk` for ADK); Agent Server provides threads, checkpoints (session state saved via `entrypoint.final(save=…)`), streaming, crons, webhooks, MCP/A2A endpoints; our platform features work unchanged. | Uniform ops, fastest path for new adapters |
| **B. Native runtime** | Deploy to the framework's own platform (e.g. Vertex AI Agent Engine for ADK, Azure AI Foundry for Microsoft Agent Framework, CrewAI Enterprise) with our event translator & tracing attached. | Customer mandates their cloud/framework runtime |
| **C. Remote agent** | Agent runs anywhere; AgentCanvas calls it via **A2A** or **MCP** as a black-box node. | Existing agents owned by other teams |

### 2.5 Cross-framework building blocks (bridges)
- **Tools**: MCP is the universal tool contract — every tool node can be exposed as an MCP tool and consumed by any adapter; native function tools are generated per framework from the same JSON-Schema signature.
- **Models**: model resources compile to each framework's model client; a shared **model gateway** (LangSmith LLM Gateway or OpenAI-compatible proxy) gives uniform policies, fallbacks and cost accounting.
- **Memory & persistence**: platform-level Memory Spaces (doc 08) are exposed to non-LangChain adapters through generated memory tools / service clients, so memory is portable even where the framework has no store concept.
- **HITL**: where a framework lacks native pause/resume, the adapter emulates approvals by (a) tool wrappers that raise an interrupt in the hosting `@entrypoint`, or (b) ending the run with a pending decision and resuming a new run with the decision injected (documented as *emulated*).
- **Interchange**: import/export **Open Agent Spec** documents (Agents + Flows) to/from Core IR (P3), enabling round-trips with other Agent Spec runtimes.

---

## 3. Concept mapping across frameworks (planning reference)

> **Superseded (2026-09-28):** this indicative table has been replaced by the **source-verified** matrix in [doc 12 §A.4](./12-multi-framework-technical-spec.md#a4-verified-concept-matrix-core-ir--framework) and the per-framework specs in [adapters/](./adapters/). Notable corrections: OpenAI Agents SDK, Google ADK, MAF and Pydantic AI have **native** tool-approval HITL; Google ADK 2.x has a native **graph `Workflow`** engine; MAF workflows are Pregel-style with checkpoint storage; CrewAI Flows have a declarative **`FlowDefinition`**. Kept below for history.

| Core concept | LangChain / LangGraph / Deep Agents (R1) | OpenAI Agents SDK | Google ADK | Microsoft Agent Framework | CrewAI | Pydantic AI |
|--------------|------------------------------------------|-------------------|------------|---------------------------|--------|-------------|
| Agent loop | `create_agent`, `create_deep_agent` | `Agent` + `Runner` | `LlmAgent` + `Runner` | agents (AutoGen/SK lineage) | `Agent` (role/goal/backstory) + `Task` | `Agent` |
| Tools / MCP | tools, `langchain.mcp` | function tools, hosted tools, MCP | tools, MCP toolsets | tools, MCP | tools, MCP | tools, toolsets, MCP |
| Structured output | `response_format` | `output_type` | output schema | structured output | task `output_pydantic`/`output_json` | `output_type` |
| Multi-agent | subagents, supervisor, handoffs (`Command`) | handoffs, agents-as-tools | sub-agents, `transfer_to_agent`, A2A | group chat / orchestration patterns, A2A | crews (sequential / hierarchical) | agent delegation |
| Explicit workflow | `StateGraph`, Functional API | code orchestration (emulated graph) | `SequentialAgent`, `ParallelAgent`, `LoopAgent`, graph workflows | Workflows (executors + edges, checkpointing) | Flows (`@start`, `@listen`, `@router`) | graphs (pydantic-graph) |
| State & persistence | checkpointers, stores | sessions | `SessionService`, `MemoryService` | threads, workflow checkpoints | flow state / persistence, memory | message history, durable execution integrations |
| HITL | `interrupt()`, `HumanInTheLoopMiddleware` | tool approval / emulated | callbacks / tool confirmation / emulated | workflow request/response | human input on tasks / emulated | deferred tools / emulated |
| Guardrails / middleware | middleware | input/output guardrails | callbacks, plugins | middleware / filters | guardrails, callbacks | capabilities, hooks |
| Streaming | `stream_events` v3 / stream modes | streamed run events | event stream | streaming updates | event listeners | streamed runs / events |
| Tracing | LangSmith | built-in tracing / OTel | OTel / Cloud Trace | OTel | OTel / integrations | Logfire / OTel |
| Languages | Python (TS P2) | Python, TS | Python, TS, Java, Go | Python, .NET | Python | Python |
| Native hosting | Agent Server / MDA | — (self-host) | Vertex AI Agent Engine | Azure AI Foundry | CrewAI Enterprise | self-host |

Also targeted as **black-box agents (Tier 3)** early: Claude Agent SDK, Strands Agents, LlamaIndex, smolagents, Agno, and any A2A/MCP-exposed agent. **Mastra** (TypeScript) is the reference for the Node sidecar SPI.

---

## 4. What we must build *now* (Release 1, LangChain-only) to stay agnostic

These are cheap now and very expensive later. Estimated overhead: **~15 % of R1 engineering**.

| # | Decision / task | Detail | Owner |
|---|-----------------|--------|-------|
| A1 | **Split Core IR and `langchain` dialect** | Core node types (`core.agent`, `core.router`, …) + dialect nodes (`langchain.deep_agent`, `langchain.middleware.pii`, `langgraph.send`). R1 UI may default to dialect nodes; the split exists in the schema from day 1. | Compiler |
| A2 | **Adapter SPI v0 (internal)** | LangChain support is implemented as `agentcanvas-adapter-langchain` against the SPI; compiler, interpreter and run orchestrator call the SPI only. | Compiler / Runtime |
| A3 | **Import-boundary fitness tests** | CI fails if `langchain*`, `langgraph*`, `deepagents` are imported outside the adapter package (import-linter). | Platform |
| A4 | **Canonical Run Events** | UI, inbox, triggers, cost, learning consume canonical events; LangChain adapter provides the translator. | Runtime / Frontend |
| A5 | **Capability matrix & portability diagnostics** | Manifest schema + validator plumbing (`P001 node not supported by adapter X`), even with a single adapter. | Compiler |
| A6 | **Framework-neutral persistence & memory APIs** | Platform Memory Spaces / Data Studio services exposed via service APIs & MCP, not only via LangGraph `store`. | Platform |
| A7 | **MCP-first tools** | Every custom/HTTP/data tool is also available as an MCP tool (internal MCP gateway per project). | Platform |
| A8 | **OTel GenAI tracing** | Emit/ingest OTel GenAI spans; LangSmith as the default sink (LangSmith accepts OTel). | Runtime |
| A9 | **Neutral naming in UI** | "Agent", "Delegate", "Approval", "Memory" — framework names only on dialect nodes and in the code view. | Design |
| A10 | **Hosting abstraction** | Deploy service targets `HostingMode` implementations; R1 ships `agent_server` + `mda` + `export`. | Deploy |

Explicit **non-goals for R1**: shipping a second adapter; mixed-framework graphs; Agent Spec import/export.

---

## 5. Phased plan to extend support

### Phase F0 — Agnostic foundations (inside R1 build, months 0–4)
Deliver A1–A10. **Exit criteria:** LangChain adapter passes all tests through the SPI; import-boundary tests green; a *toy adapter* (in-house "echo" framework) implements the SPI in < 2 weeks, proving the seams.

### Phase F1 — "Bring your agent" (black-box interop, R1 + 2–3 months)
- **Remote Agent node** (A2A client) and **MCP Agent node**: call any A2A/MCP-exposed agent as a node; canonical events from A2A task updates.
- **Wrapped Agent node**: user points to an agent object in the Workspace (OpenAI Agents SDK, Google ADK, CrewAI crew, Claude Agent SDK, Strands, Pydantic AI…); we generate the Functional API wrapper (`@entrypoint`/`@task`, `deployments-wrap-sdk` for ADK) so it runs on Agent Server inside a LangGraph workflow with threads, streaming and tracing.
- Minimal translators for token/tool events where the framework exposes callbacks/OTel.
- **Exit:** 5 framework agents usable as nodes in LangChain workflows; HITL approvals on them via emulation.

> **Ordering superseded** by [doc 12 §B.2 / §D.1](./12-multi-framework-technical-spec.md#b2-revised-adapter-order-replaces-doc-11-5-f2f4-ordering): OpenAI Agents SDK (agent tier) **and** Google ADK (full T1) in parallel, then Microsoft Agent Framework, CrewAI, Pydantic AI / Strands / LlamaIndex, Mastra.

### Phase F2 — First full second adapter (months +3 to +7)
Recommended first: **OpenAI Agents SDK** (small, well-defined surface — agents, tools, handoffs, guardrails, sessions — so it validates the SPI with limited risk and broad demand).
Steps (template for every adapter, see §6). **Exit:** Tier 1 certification (§7) for OpenAI Agents SDK; portable templates (Router, Support triage, Research-lite) compile on both adapters with equivalent conformance results.

### Phase F3 — Enterprise & workflow-centric frameworks (months +7 to +14)
- **Google ADK** (multi-language, A2A-native, Vertex Agent Engine native hosting).
- **Microsoft Agent Framework** (Python first; .NET via sidecar; Azure AI Foundry native hosting; workflows with checkpointing map well to Graph tier).
- **Open Agent Spec import/export** (interchange + cross-runtime evaluation).
- **Native hosting mode B** for ADK and MS Agent Framework.
- **Exit:** 3 Tier-1 adapters; portability score used by ≥ 20 % of new workflows.

### Phase F4 — Breadth & mixed graphs (months +14 to +20)
- **CrewAI** (Crews & Flows), **Pydantic AI**, **LlamaIndex Workflows**; **Mastra** via Node sidecar (TS ecosystem).
- **Mixed-framework graphs**: per-node `engine`; orchestration by the primary adapter; cross-engine calls in-process where possible (wrapped nodes) or via A2A; canonical state mapping at boundaries (explicit data edges required).
- **Cross-framework "Compare" experiments**: run the same portable workflow on N adapters/models and compare quality, latency, cost in LangSmith.
- Importers (code → IR) for Tier-1 adapters.

### Phase F5 — Ecosystem (month +20 onward)
- Public **Adapter SDK** (docs, scaffolding CLI `agentcanvas adapter new`, conformance kit, certification program).
- Community adapters in the marketplace (signed, tiered).
- Adapter-specific templates and Copilot knowledge packs.

---

## 6. Adapter development playbook (repeatable steps per framework)

| Step | Activity | Output | Typical effort |
|------|----------|--------|----------------|
| 1 | **Verification spike**: current release, concepts, persistence, HITL, streaming, tracing, hosting | Concept map & risk list | 1 wk |
| 2 | **Capability matrix** (native / emulated / unsupported per core concept) | `capabilities.yaml` reviewed by product | 2–3 d |
| 3 | **Dialect design**: framework-specific nodes & config (only where native power matters) | Node manifests, UI forms | 1 wk |
| 4 | **Lowering + codegen**: core & dialect nodes → idiomatic framework code; source maps | Templates, golden tests | 3–4 wk |
| 5 | **Hosting**: Agent Server wrapper (mode A) first; native (mode B) later | Wrapper templates, deploy target | 1–2 wk |
| 6 | **Event translator** → canonical events; OTel mapping | Translator + tests | 1–2 wk |
| 7 | **Persistence & HITL mapping** (sessions ↔ threads, emulated interrupts) | Mapping module, docs | 1–2 wk |
| 8 | **Interpreter/dev runs** (hot reload, breakpoints where possible, mocks) | Dev runtime integration | 2 wk |
| 9 | **Conformance suite**: portable templates with fake models & tool cassettes; trajectory/outcome equivalence thresholds | Green CI across stack versions | 1–2 wk |
| 10 | **Docs, templates, Copilot pack**, beta with design partners, then certification | Tier assignment | 2–3 wk |

≈ **3–4 months** for a Tier-1 adapter with a 3-person squad; ≈ 3–4 weeks for Tier-3 (wrapped/black-box) support.

---

## 7. Support tiers & certification

| Tier | Meaning | Required |
|------|---------|----------|
| **Tier 1 — Full** | Canvas authoring, codegen, dev runs with live overlay, HITL (native or certified emulation), persistence bindings, evals, deploy (mode A, optionally B), importer | Conformance suite 100 % on portable set; nightly upstream CI; SLA |
| **Tier 2 — Authoring** | Codegen + deploy + tracing; limited debugging (no step/breakpoints), HITL emulated | Conformance ≥ 90 % |
| **Tier 3 — Interop** | Framework agents as black-box nodes (wrapped / A2A / MCP) inside workflows of a Tier-1 adapter | Wrapper tests, event smoke tests |
| **Community** | Third-party adapters via SDK | Signed, security-scanned, self-certified conformance report |

---

## 8. Impact on existing specs

| Spec area | Change |
|-----------|--------|
| IR (doc 03) | Core vs dialect node namespaces; `framework` & per-node `engine`; `x-<adapter>` override blocks; portability diagnostics `P0xx`. |
| Compiler (doc 03) | Lowering/codegen/interpreter move behind the SPI; LangChain construct mapping becomes the LangChain adapter's mapping. |
| Node catalog (doc 04) | Every node tagged *core* or *dialect*; R1 catalog = core + `langchain` dialect. |
| Runtime (doc 02) | Run Orchestrator consumes canonical events; hosting modes A/B/C; adapter packages in compiler workers and runtime images. |
| Persistence & memory (doc 08) | Memory Spaces and Data Studio tools exposed via service APIs/MCP for non-LangChain adapters; per-adapter persistence mapping. |
| Learning (doc 09) | Prompt/example artifacts are framework-neutral; optimisers act on core `instructions`; per-adapter injection (few-shot selector implemented per adapter or as a prompt pre-processor). |
| Evals | Cross-framework comparison experiments (F4). |
| Roadmap (doc 06) | F0 folded into Phases 0–1; F1 with GA; F2–F5 in Phase 3+. |

## 9. Risks & mitigations (framework agnosticism)

| Risk | Mitigation |
|------|-----------|
| Diluted focus delays R1 | Hard cap: F0 ≤ 15 % of R1 capacity; no second adapter before GA |
| LCD UX | Dialects + portability score, never hide native power |
| Emulation surprises (e.g. HITL on frameworks without pause/resume) | Capability matrix surfaced in UI; emulations labelled; conformance-tested |
| Maintenance burden | Tiering, demand-driven prioritisation, community SDK, nightly upstream CI |
| Vendor relations / positioning vs LangChain | LangChain remains the reference adapter and default; Agent Server as universal host strengthens, not competes with, the LangChain ecosystem |

## 10. Decision summary
1. **Adopt "portable core + native dialects"** in the IR now.
2. **Implement LangChain as adapter #1 behind an SPI** in R1 (+~15 % effort), with CI-enforced boundaries.
3. **First extension = black-box interop (F1)**, then **OpenAI Agents SDK** as the first full adapter, followed by **Google ADK** and **Microsoft Agent Framework**, then CrewAI / Pydantic AI / LlamaIndex / Mastra.
4. **Agent Server stays the default universal host**; native hosting added per adapter where customers need it.
5. **Standards first**: MCP (tools), A2A (agents), AG-UI (UI events), OTel GenAI (traces), Open Agent Spec (interchange).
