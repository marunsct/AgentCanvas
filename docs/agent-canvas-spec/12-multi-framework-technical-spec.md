# 12 — Multi-Framework Support: Verified Research, Technical Specification & Delivery Plan

> Supersedes the *indicative* concept mapping in [doc 11 §3](./11-framework-agnostic-plan.md#3-concept-mapping-across-frameworks-planning-reference). Every framework fact in this document was verified **against the framework's own source repository** (shallow clones of the default branch taken on **2026-09-28**), not against blog posts. Per-framework adapter specifications live in [`adapters/`](./adapters/).

---

## Part A — Verification research

### A.1 Method

1. Shallow-cloned each framework's official repository and read the public API from source (class fields, constructor signatures, decorators, exported symbols, docs in-repo, samples).
2. Resolved the latest release tag via `git ls-remote --tags`.
3. For each Core IR concept (doc 11 §2.1) classified support as **N** native · **E** emulatable by the adapter · **U** unsupported, and recorded the exact API that implements it.
4. Checked licences (bundling/redistribution), interop protocol support (MCP, A2A, AG-UI, OpenTelemetry) and hosting/deploy options.
5. Checked two cross-framework assets that can be reused: **Open Agent Spec** adapters and **AG-UI** framework integrations.

### A.2 Versions verified

| Framework | Package(s) | Latest release verified | Languages | Licence |
|-----------|------------|-------------------------|-----------|---------|
| LangChain / LangGraph / Deep Agents (reference) | `langchain`, `langgraph`, `deepagents` | 1.4.2 / 1.2.12 / 0.7.19 | Py, TS | MIT |
| OpenAI Agents SDK | `openai-agents` | **0.22.3** (pre-1.0) | Py (JS SDK separate) | MIT |
| Google Agent Development Kit | `google-adk` | **2.10.0** | Py, TS, Java, Go | Apache-2.0 |
| Microsoft Agent Framework | `agent-framework` (+ ~40 sub-packages) | **python-1.19.0** (sub-packages mix GA and `b`/`a` pre-releases) | Py, .NET | MIT |
| CrewAI | `crewai` | **1.15.22** | Py | MIT |
| Pydantic AI | `pydantic-ai`, `pydantic-graph`, `pydantic-evals` | **2.51.0** | Py | MIT |
| LlamaIndex Workflows | `llama-index-workflows` (+ `llama-agents-*` server/DBOS) | **2.11.1** | Py (TS separate) | MIT |
| Mastra | `@mastra/core` (+ `@mastra/*` stores) | **1.72** (alpha pre-release on main) | TS | Apache-2.0 (except `ee/` dirs) |
| Strands Agents | `strands-agents` | **1.41.0** | Py, TS | Apache-2.0 |
| Claude Agent SDK | `claude-agent-sdk` | **0.2.160** | Py, TS | MIT |
| Open Agent Spec | `pyagentspec`, `tsagentspec` | **26.3.1** (26.4.0.dev on main) | Py, TS | Apache-2.0 / UPL |
| A2A SDK | `a2a-sdk` | **1.1.5** — implements A2A spec **1.0** (0.3 compat mode) | Py | Apache-2.0 |
| AG-UI | `ag-ui-protocol` SDKs + integrations | rolling (per-integration versions) | Py, TS, … | MIT |

### A.3 Key findings (what changed versus the doc 11 assumptions)

| # | Finding | Evidence (source) | Consequence for AgentCanvas |
|---|---------|-------------------|-----------------------------|
| K1 | **Google ADK 2.x has a real graph workflow engine**: `google.adk.workflow.Workflow(name, edges=[("START", a, b), (router, {"route": node})], max_concurrency, rerun_on_resume)`, `@node(parallel_worker=True)` fan-out over iterables, `JoinNode`, `RetryConfig`, `NodeTimeoutError`, nodes that `yield Event(state=…, route=…)`, nested workflows, and **`RequestInput`** for human input with resume. | `src/google/adk/workflow/*`, `contributing/samples/workflows/{sequence,route,state,request_input,parallel_worker,loop,nested_workflow}` | ADK can be a **Tier-1 Graph-tier adapter**, not only an agent-tier one. Strong first candidate. |
| K2 | **Microsoft Agent Framework (MAF) is GA (1.x) and has Pregel-style workflows**: `WorkflowBuilder(start_executor, checkpoint_storage, max_iterations, …)` with `add_edge`, `add_chain`, `add_fan_out_edges`, `add_fan_in_edges`, `add_switch_case_edge_group`, `add_multi_selection_edge_group`; super-step events; `ctx.request_info(...)` for HITL; `CheckpointStorage` (in-memory, file, Azure Cosmos). Tools support `approval_mode="always_require"`. Orchestrations package: sequential, concurrent, group chat, handoff, magentic. Declarative YAML agents. | `python/packages/core/agent_framework/_workflows/*`, `_tools.py`, `packages/orchestrations`, `packages/declarative` | Excellent structural fit to LangGraph semantics (super-steps, checkpoints, request/response HITL). Tier-1 candidate. |
| K3 | **OpenAI Agents SDK has native HITL** (not emulated): `function_tool(needs_approval=bool|callable)` → `result.interruptions` (`ToolApprovalItem`) → `result.to_state()` → `RunState.approve/reject(...)` → `Runner.run(agent, state)`; `RunState.to_json()/from_json()` makes pauses **serialisable**. Sessions: SQLite, OpenAI Conversations, Redis, SQLAlchemy, MongoDB, Dapr, encrypted. **No graph/workflow primitive** — orchestration is handoffs, agents-as-tools, or code. Still pre-1.0 (0.22.x). | `src/agents/{agent.py,tool.py,run_state.py,memory/,extensions/memory/}`, `docs/human_in_the_loop.md` | Great Agent-tier adapter; Graph tier must be **emulated** (generated Python orchestration or hosted in LangGraph). Pre-1.0 → pin tightly. |
| K4 | **CrewAI Flows now have a serialisable declarative contract**: `FlowDefinition` (methods, trigger conditions, state schema as dict/Pydantic/JSON Schema, persistence, human feedback, actions of type code/tool/crew/agent/expression/script, `each` loops) independent of authoring and runtime; `@start/@listen/@router/or_/and_`, `@human_feedback(message, emit, llm, default_outcome, provider, learn…)`, `@persist`, state `CheckpointConfig(location, on_events, provider)`. | `lib/crewai/src/crewai/flow/{flow_definition.py,dsl/,human_feedback.py,persistence/}`, `state/checkpoint_config.py` | Generate/import CrewAI Flows via **FlowDefinition** rather than Python AST — much more robust codegen and round-trip. |
| K5 | **Pydantic AI 2.x**: declarative **Agent Specs** (`Agent.from_spec` / `Agent.from_file`, YAML/JSON), composable **capabilities**, **deferred tools** for HITL (`ApprovalRequired`, `CallDeferred`, `DeferredToolRequests` → resume with `DeferredToolResults`), durable execution integrations (**Temporal, DBOS, Prefect**), `pydantic_graph.GraphBuilder`, AG-UI & Vercel AI UI adapters, `pydantic_evals`. | `pydantic_ai_slim/pydantic_ai/{agent,capabilities,durable_exec,_deferred.py,toolsets/approval_required.py}`, `docs/{agent-spec,deferred-tools,persistence}.md` | Agent tier maps cleanly via Agent Specs; graph tier via `pydantic_graph`. Tier-2 → Tier-1 candidate. |
| K6 | **AG-UI already has integrations for almost every target**: `a2a, adk-middleware, ag2, agent-spec, agno, aws-strands, claude-agent-sdk, claude-managed-agents, crew-ai, langchain, langgraph, langroid, llama-index, mastra, microsoft-agent-framework (py/.NET), pydantic-ai, vercel-ai-sdk, watsonx`; protocol events `RUN_STARTED/FINISHED/ERROR`, `STEP_STARTED/FINISHED`, `TEXT_MESSAGE_*`, `TOOL_CALL_START/ARGS/END/RESULT`, `STATE_SNAPSHOT/DELTA`, `MESSAGES_SNAPSHOT`, `REASONING_*`, `ACTIVITY_*`, `RAW`, `CUSTOM`, and a `RunFinished` **interrupt outcome**. | `ag-ui/integrations/*`, `sdks/python/ag_ui/core/events.py` | **Adopt AG-UI as the base of the Canonical Run Event protocol** and reuse existing integrations as event translators → large reduction of per-adapter work. |
| K7 | **Open Agent Spec ships loaders *and* exporters** for `langgraph`, `openaiagents`, `agent_framework` (MAF), `crewai`, `autogen`, `wayflow`; components: `Agent`, `Flow` with nodes (`Start/End/LlmNode/AgentNode/ToolNode/ApiNode/BranchingNode/MapNode/ParallelMapNode/ParallelFlowNode/FlowNode/CatchExceptionNode/Input/OutputMessage`), `ControlFlowEdge`/`DataFlowEdge`, `ManagerWorkers`, `Swarm`, `RemoteAgent`, `A2AAgent`, MCP, datastores, standard tracing events. Adapters have explicit `NotImplementedError` gaps. | `pyagentspec/src/pyagentspec/{flows,adapters/*}` | Use Agent Spec as the **interchange format** and as an optional *bridge lowering path* ("IR → Agent Spec → framework via pyagentspec") for fast Tier-2 coverage; keep direct codegen for Tier-1 quality. |
| K8 | **Strands** has `multiagent.GraphBuilder` (`add_node`, `add_edge`, `set_entry_point`, `set_max_node_executions`, `build`), `Swarm`, A2A, first-class **interrupts** (hooks/tools raise → agent returns interrupts → respond & re-invoke), session managers (file, S3, repository, snapshot), OTel. | `strands-py/src/strands/{multiagent,session,types/interrupt.py}` | Upgrade Strands from "black-box only" to a **Tier-2 candidate**. |
| K9 | **Mastra** (TS) workflows: `createWorkflow/createStep/createStepFromAgent`, `.then .parallel .branch .dowhile .dountil .foreach .map .sleep .sleepUntil .waitForEvent .commit`, `suspend()`/`resume()`, `restart`, time-travel, snapshots; ~30 storage adapters (pg, libsql, mongodb, redis, dynamodb, mssql, mysql, …). | `packages/core/src/workflows/workflow.ts`, `stores/*` | Strong TS target → reference for the **Node sidecar SPI**. Watch `ee/` licensing. |
| K10 | **LlamaIndex Workflows** 2.x: event-driven `@step(num_workers, retry_policy)` methods, `StartEvent/StopEvent`, `InputRequiredEvent/HumanResponseEvent`, Context state store with serializers, workflow representation/validation, server/client, DBOS durability, AgentCore deploy. | `packages/llama-index-workflows/src/workflows/*` | Graph tier maps via event types (each edge = event class). Tier-2. |
| K11 | **Claude Agent SDK** is an agent *harness*: `ClaudeAgentOptions(tools, allowed_tools, disallowed_tools, system_prompt, mcp_servers, permission_mode, can_use_tool, hooks, agents, resume/session_id, max_turns, max_budget_usd, model, fallback_model)`. | `src/claude_agent_sdk/types.py` | Tier-3 wrapped node with native approval via `can_use_tool` callback; no graph codegen. |
| K12 | **A2A 1.0** is the stable spec; task states include `input-required` and `auth-required` (usable for remote HITL). | `a2a-python` README & enums | Remote Agent node maps `input-required` → canonical interrupt. |
| K13 | **LangSmith Agent Server hosts foreign frameworks** via Functional API (`@entrypoint`/`@task`, `entrypoint.final(value, save)`) and `deployments-wrap-sdk` for ADK. | LangSmith docs (doc 11 research) | Hosting mode A confirmed for every Python adapter. |

### A.4 Verified concept matrix (Core IR → framework)

Legend: **N** native (API named) · **E** emulated by adapter (mechanism named) · **U** unsupported. "Tier target" is the planned certification level (§C.9).

| Core IR concept | LangChain (ref) | OpenAI Agents SDK 0.22 | Google ADK 2.10 | MS Agent Framework 1.19 | CrewAI 1.15 | Pydantic AI 2.51 | LlamaIndex WF 2.11 | Strands 1.41 | Mastra 1.72 (TS) | Claude Agent SDK 0.2 |
|---|---|---|---|---|---|---|---|---|---|---|
| `core.agent` (tool loop) | N `create_agent`/`create_deep_agent` | N `Agent`+`Runner` | N `LlmAgent` | N `Agent(client=…)` | N `Agent`+`Task` (single-task crew) | N `Agent` | E (agent via llama-index `FunctionAgent` inside a step) | N `Agent` | N `Agent` | N `query`/`ClaudeSDKClient` |
| `core.model` | N `init_chat_model` | N model str/`Model`, LiteLLM ext | N model str/`BaseLlm` (LiteLLM) | N chat clients (OpenAI, Foundry, Anthropic, Bedrock, Gemini, Mistral, Ollama…) | N `LLM` (LiteLLM-style ids) | N `provider:model` | N LLM classes | N model providers | N model router | N Claude models only |
| Function tool | N | N `function_tool` | N `FunctionTool` | N `@tool` | N `BaseTool`/`@tool` | N `@agent.tool`/toolsets | N | N `@tool` | N `createTool` | N in-process MCP tools |
| MCP tools | N `langchain.mcp` | N `MCPServerStdio/Sse/StreamableHttp`, `HostedMCPTool` | N MCP toolset | N MCP | N `mcps` | N MCP toolsets/capability | N | N | N | N `mcp_servers` |
| Structured output | N `response_format` | N `output_type` | N `output_schema` | N response format options | N `output_pydantic/json` | N `output_type` | N | N `structured_output` | N | E (JSON schema prompt + validation) |
| Instructions/templating | N | N `instructions` (str/callable), `prompt` | N `instruction` with `{state}` templating | N `instructions` | N role/goal/backstory, `{input}` interpolation | N `instructions` | N | N | N | N `system_prompt` |
| Guardrails / middleware | N middleware | N input/output guardrails, tool guardrails, hooks | N callbacks (before/after model/tool, on_error), plugins | N middleware, context providers | N guardrails, hooks, event listeners | N capabilities/hooks | E step wrappers | N hooks, plugins, interventions | N processors | N hooks |
| Sub-agent delegation | N subagents (`task` tool) | N `Agent.as_tool` | N sub_agents / `transfer_to_agent` | N agent-as-tool, orchestrations | N hierarchical process / delegation | N agent delegation via tools | E | N agents-as-tools, Swarm | N agent networks | N `agents` (subagents) |
| Handoff (control transfer) | N `Command(goto, graph=PARENT)` | N `handoffs` | N transfer to peer/parent | N handoff orchestration | E flow routing | E | E | N Swarm handoff | E | U |
| Graph: sequence | N `add_edge` | E generated orchestration code | N `Workflow` edges | N `add_edge`/`add_chain` | N `@listen` | N `pydantic_graph` | N events | N `GraphBuilder` | N `.then` | U |
| Graph: router | N conditional edges | E | N `Event(route=)` + route map | N switch-case edge group | N `@router` | N decisions | N event types | N conditional edges | N `.branch` | U |
| Graph: parallel + join | N | E `asyncio.gather` | N parallel edges + `JoinNode` | N fan-out/fan-in edges | N `and_` listeners | N forks/joins | N multiple events + `collect` | N | N `.parallel` | U |
| Graph: map (fan-out over list) | N `Send` | E | N `parallel_worker=True` | N fan-out | N `each` action | N | N `num_workers` | E | N `.foreach` | U |
| Graph: loop with guard | N | E | N loops + `max`/router | N `max_iterations` + edges | N router back-edges | N | N | N `set_max_node_executions` | N `.dowhile/.dountil` | U |
| Graph: subgraph | N | E | N nested `Workflow` | N `WorkflowExecutor` | N flow in flow / crew action | N | N | N nested multiagent | N nested workflows | U |
| Shared typed state | N channels + reducers | E (`RunContextWrapper.context` + orchestration vars) | N session `state` (dict), `output_key` | N shared state (`_state.py`) + messages | N flow state (dict/Pydantic) | N graph state | N `Context` store | N `invocation_state` | N workflow state (`setState`) | U |
| Reducers | N | E (adapter merge fns) | E (adapter merge in function nodes) | E | E | E | E | E | E | U |
| HITL tool approval | N `interrupt_on` | **N** `needs_approval` → `RunState.approve/reject` | **N** `require_confirmation` | **N** `approval_mode="always_require"` | N task `human_input` | **N** deferred tools (`ApprovalRequired`) | E (`InputRequiredEvent`) | **N** interrupts | N `suspend` in tool | **N** `can_use_tool` |
| HITL input/form node | N `interrupt()` | E (tool + interruption) | **N** `RequestInput` | **N** `ctx.request_info` | **N** `@human_feedback` | E (deferred call) | **N** `InputRequiredEvent`/`HumanResponseEvent` | **N** interrupt | **N** `suspend/resume` | E |
| Durable resume after pause | N checkpoints | **N** `RunState.to_json/from_json` | N sessions/events (`rerun_on_resume`) | N `CheckpointStorage` | N `@persist` / `CheckpointConfig` | N durable_exec (Temporal/DBOS/Prefect) or history | N context serializers / DBOS | N session managers + snapshots | N snapshots | N `resume` session id |
| Conversation memory | N checkpointer | N `Session` (SQLite/Redis/SQLAlchemy/Mongo/Dapr) | N `SessionService` (in-memory/SQLite/DB/Vertex) | N `HistoryProvider` (in-memory/file/Redis/Cosmos) | N | E (serialize history) / Harness `StepPersistence` | N context | N session managers | N memory | N sessions |
| Long-term memory | N Store | E (platform Memory Spaces via tools) | N `MemoryService` (SQLite, Vertex Memory Bank/RAG) | N context providers (Mem0, Redis, Cosmos) | N unified memory (LanceDB/Qdrant) | E / Harness `Memory` | E | N vended memory stores | N | E |
| Streaming tokens/events | N `stream_events` v3 | N `run_streamed` events | N event stream | N `WorkflowEvent`s + streaming | N event bus | N stream/`run_stream_events` | N `stream_events` | N async iterators | N `stream` | N message stream |
| OTel / tracing | N LangSmith | N trace processors (`add_trace_processor`) | N OTel GenAI semconv | N OTel observability | N telemetry + listeners | N Logfire/OTel | N | N OTel | N observability | E (hooks → spans) |
| AG-UI integration exists | Y | — (not in AG-UI list; adapter writes translator) | Y `adk-middleware` | Y | Y `crew-ai` | Y | Y `llama-index` | Y `aws-strands` | Y | Y |
| A2A | via Agent Server | E | N (`RemoteA2aAgent`, A2A server) | N hosting-a2a | N `a2a` module | E | E | N | N | E |
| Declarative spec | Agent Spec adapter | Agent Spec adapter | N YAML `AgentConfig` | N declarative YAML | N `FlowDefinition` | N Agent Spec (YAML/JSON) | E | E | E | U |
| Native hosting | Agent Server / MDA | self-host | Cloud Run, GKE, **Vertex AI Agent Engine**, Docker (`adk deploy …`) | **Foundry hosted agents**, hosting-a2a/mcp/responses | CrewAI AMP | self-host (+ Temporal/DBOS) | llama-agents server/control-plane, AgentCore | self-host / AgentCore | Mastra deployers | self-host |
| **Tier target** | T1 (R1) | T1 agent-tier, T2 graph-tier | **T1** | **T1** | T1 (Flows) | T2 → T1 | T2 | T2 | T2 (TS sidecar) | T3 |

---

## Part B — Revised strategy

### B.1 What the evidence changes
1. **Two frameworks (ADK 2.x, MAF 1.x) are structurally close to LangGraph** (graph workflows, checkpoints, request/response HITL, super-step semantics). They are the best candidates for full Graph-tier parity.
2. **OpenAI Agents SDK is agent-centric** with excellent native HITL but no workflow engine and a pre-1.0 API. Ideal for the Agent tier and as the *smallest* adapter to harden the SPI — but not sufficient alone to prove Graph-tier portability.
3. **Cross-framework infrastructure already exists** (AG-UI integrations, Agent Spec adapters, A2A 1.0, OTel GenAI semconv). We *compose* these rather than build N bespoke translators.

### B.2 Revised adapter order (replaces doc 11 §5 F2–F4 ordering)

| Order | Adapter | Target tier | Why |
|------:|---------|-------------|-----|
| 0 | LangChain / LangGraph / Deep Agents | T1 | Release 1 reference adapter |
| 1a | **OpenAI Agents SDK** | T1 (agent tier) / T2 (graph tier) | Smallest surface → hardens SPI in ~10 weeks; native HITL & serialisable run state; high demand |
| 1b | **Google ADK** (in parallel, second squad) | T1 | Native graph `Workflow`, `RequestInput`, confirmations, sessions, OTel, A2A, Vertex Agent Engine hosting; multi-language |
| 2 | **Microsoft Agent Framework** | T1 | GA, Pregel-like workflows + checkpoints + `request_info`; enterprise/Azure demand; .NET later via sidecar |
| 3 | **CrewAI** (Crews + Flows via `FlowDefinition`) | T1 | Declarative contract makes codegen/import robust; large user base |
| 4 | **Pydantic AI** | T2 → T1 | Agent Specs, deferred tools, durable exec |
| 5 | **Strands**, **LlamaIndex Workflows** | T2 | Graph/multi-agent + interrupts exist; smaller demand |
| 6 | **Mastra** (TS sidecar) | T2 | TypeScript ecosystem; reference for Node SPI |
| — | Claude Agent SDK, Agno, smolagents, any A2A/MCP agent | T3 | Wrapped/black-box nodes from F1 |

### B.3 Two lowering paths per adapter

```
                ┌─────────────── Direct path (Tier 1, default) ───────────────┐
Core IR + dialect ─► adapter.lower() ─► framework AST/templates ─► idiomatic code
                └─────────────── Bridge path (Tier 2 accelerator) ────────────┘
Core IR ─► AgentSpecExporter (ours) ─► Agent Spec JSON/YAML ─► pyagentspec <fw> loader ─► runtime objects
```
- **Direct path** produces idiomatic, readable, exportable code (the WYSIWYG promise). Required for Tier 1.
- **Bridge path** reuses pyagentspec loaders (LangGraph, OpenAI Agents, MAF, CrewAI, AutoGen, WayFlow) to run portable workflows on a framework *before* its direct adapter exists — used for dev runs, cross-framework comparison experiments and Tier-2 coverage. Gaps (`NotImplementedError` in pyagentspec converters) are surfaced as `P002` diagnostics.

---

## Part C — Technical specification

### C.1 Package & repository layout

```
agentcanvas/
├── core/                         # framework-neutral: IR models, validator core, plan lowering, events, SPI
│   ├── ir/                       # Pydantic models: Workflow, Node, Edge, State, Resource, Persistence…
│   ├── spi/                      # FrameworkAdapter protocol + helper base classes
│   ├── events/                   # Canonical Run Events (AG-UI based) + helpers
│   ├── hitl/                     # canonical interrupt/resume envelope
│   ├── capabilities/             # capability schema, portability analysis
│   └── conformance/              # shared conformance suite, fake models, tool cassettes
├── adapters/
│   ├── langchain/                # agentcanvas-adapter-langchain (R1)
│   ├── openai_agents/            # agentcanvas-adapter-openai-agents
│   ├── google_adk/
│   ├── ms_agent_framework/
│   ├── crewai/
│   ├── pydantic_ai/
│   ├── llamaindex_workflows/
│   ├── strands/
│   ├── agentspec_bridge/         # Bridge path (pyagentspec) + Agent Spec import/export
│   └── wrapped/                  # Tier-3 wrappers: claude_agent_sdk, generic A2A/MCP, generic callable
├── sidecars/node-adapter-host/   # TS adapters (Mastra, OpenAI JS, ADK TS) over JSON-RPC
└── runtime/                      # agentcanvas_runtime: host wrappers, RoutedStore, memory/data clients, event emitters
```
Rules: `core/**` may not import any framework package (import-linter contract in CI); each adapter is an independently versioned wheel with an **optional-extra** dependency on its framework (`agentcanvas-adapter-google-adk[adk]`).

### C.2 Adapter SPI (normative)

```python
# agentcanvas/core/spi/adapter.py
from typing import Protocol, Literal, AsyncIterator, Any
from agentcanvas.core.ir import ExecutionPlan, NodeTypeManifest, Diagnostic
from agentcanvas.core.events import CanonicalEvent
from agentcanvas.core.hitl import ResumeEnvelope, InterruptDecision

SupportLevel = Literal["native", "emulated", "unsupported"]

class AdapterManifest(BaseModel):
    id: str                          # "google_adk"
    version: str                     # adapter semver
    framework: str                   # "google-adk"
    framework_range: str             # ">=2.10,<2.12"  (stack pin)
    languages: list[Literal["python", "typescript", "java", "go", "dotnet"]]
    hosting_modes: list[Literal["agent_server", "native", "a2a_remote", "mcp_remote"]]
    tier: Literal["T1", "T2", "T3", "community"]
    licence: str

class CapabilityMatrix(BaseModel):
    concepts: dict[str, "CapabilityEntry"]   # key = core concept id, e.g. "hitl.tool_approval"

class CapabilityEntry(BaseModel):
    level: SupportLevel
    native_api: str | None = None            # e.g. "google.adk.tools.FunctionTool(require_confirmation=...)"
    emulation: str | None = None             # description of emulation mechanism
    caveats: list[str] = []
    since_framework: str | None = None

class FrameworkAdapter(Protocol):
    manifest: AdapterManifest
    capabilities: CapabilityMatrix

    # authoring
    def node_library(self) -> list[NodeTypeManifest]: ...
    def validate(self, plan: ExecutionPlan) -> list[Diagnostic]: ...
    # compilation
    def lower(self, plan: ExecutionPlan) -> "AdapterPlan": ...
    def generate(self, aplan: "AdapterPlan", target: "Target") -> "BuildArtifact": ...
    # execution
    async def start(self, aplan: "AdapterPlan", run: "RunRequest") -> "RunHandle": ...
    async def resume(self, handle_ref: "RunRef", envelope: ResumeEnvelope,
                     decisions: list[InterruptDecision]) -> "RunHandle": ...
    def events(self, handle: "RunHandle") -> AsyncIterator[CanonicalEvent]: ...
    async def cancel(self, handle: "RunHandle", graceful: bool = True) -> None: ...
    # state
    def serialize_state(self, handle: "RunHandle") -> ResumeEnvelope: ...
    def persistence_mapping(self) -> "PersistenceMapping": ...
    # hosting & packaging
    def host_wrapper(self, aplan: "AdapterPlan", mode: str) -> "HostArtifact": ...
    # optional
    def importer(self) -> "Importer | None": ...
    def conformance_profile(self) -> "ConformanceProfile": ...
```

Contracts:
- `lower()` must be **pure** and deterministic; `generate()` byte-deterministic (TR-COMP-01 applies to every adapter).
- `events()` must emit the **mandatory canonical events** (C.3) with `canvas_node_id` on node/tool/interrupt events.
- `serialize_state()` must produce a `ResumeEnvelope` that can resume the run **in a different process** (required for Agent Server hosting and horizontal scaling).
- Every `emulated` capability must be covered by a conformance case (C.8).

### C.3 Canonical Run Event protocol (AG-UI based)

We adopt **AG-UI events as the wire base** and add a small, namespaced extension set for canvas semantics. Framework translators can therefore **reuse AG-UI integrations** and only add the extension events.

| Canonical event | AG-UI base | Extension payload (AgentCanvas) | Mandatory |
|-----------------|-----------|----------------------------------|-----------|
| Run started / finished / error | `RUN_STARTED`, `RUN_FINISHED`, `RUN_ERROR` | `workflow_id`, `version`, `adapter`, `thread_id`; `RUN_FINISHED` outcome `success`/`interrupt` | ✅ |
| Node started / finished | `STEP_STARTED`, `STEP_FINISHED` | `canvas_node_id`, `ns` (nesting path), `attempt`, `duration_ms`, `status` (`ok/error/timeout/bypassed/cached`) | ✅ |
| Token stream | `TEXT_MESSAGE_START/CONTENT/END` | `canvas_node_id` | ✅ |
| Reasoning stream | `REASONING_*` | `canvas_node_id` | optional |
| Tool call | `TOOL_CALL_START/ARGS/END/RESULT` | `canvas_node_id`, `tool_source` (`function/mcp/hosted/agent`) | ✅ |
| State | `STATE_SNAPSHOT`, `STATE_DELTA` (JSON Patch) | `channel` names mapped to IR state | ✅ (snapshot at node end at minimum) |
| Messages | `MESSAGES_SNAPSHOT` | — | optional |
| Interrupt | `RUN_FINISHED` (interrupt outcome) + `CUSTOM name="ac.interrupt"` | `InterruptRequest` (C.4) | ✅ |
| Handoff / delegation | `CUSTOM name="ac.handoff"` / `"ac.delegate"` | `from_agent`, `to_agent`, `canvas_node_id` | ✅ where concept supported |
| Checkpoint | `CUSTOM name="ac.checkpoint"` | `checkpoint_id`, `step` | T1 only |
| Usage/cost | `CUSTOM name="ac.usage"` | tokens in/out, model, cost | ✅ |
| Framework-native | `RAW` | original event (debug view only) | optional |

Translators live in each adapter (`adapter.events()`), wrapping the existing AG-UI integration where one exists (ADK `adk-middleware`, `microsoft-agent-framework`, `crew-ai`, `pydantic-ai`, `llama-index`, `aws-strands`, `mastra`, `claude-agent-sdk`) and adding node mapping via source-map metadata. OpenAI Agents SDK needs a native translator (`RawResponsesStreamEvent`, `RunItemStreamEvent`, `AgentUpdatedStreamEvent` + `RunHooks`).

Traces: in parallel, adapters ensure **OTel GenAI semantic-convention** spans (ADK, MAF, Strands, Pydantic AI emit these natively; OpenAI via a `TracingProcessor` exporting to OTel; CrewAI via its telemetry/listeners) → OTel collector → LangSmith (or customer backend). Span attribute `agentcanvas.node_id` is injected by generated code.

### C.4 Canonical HITL protocol

```python
class InterruptRequest(BaseModel):
    id: str                                   # stable per pending interrupt
    kind: Literal["tool_approval", "input", "elicitation", "file_permission", "remote_input"]
    canvas_node_id: str
    tool: ToolCallInfo | None = None          # name, args, call_id, source
    prompt: str | None = None                 # question / message
    response_schema: dict | None = None       # JSON Schema of expected answer
    allowed_decisions: list[Literal["approve", "edit", "reject", "respond"]] = ["approve", "reject"]
    native_ref: dict                          # adapter-specific handle (never interpreted by core)

class InterruptDecision(BaseModel):
    interrupt_id: str
    type: Literal["approve", "edit", "reject", "respond"]
    edited_args: dict | None = None
    message: str | None = None
    value: Any = None

class ResumeEnvelope(BaseModel):
    adapter: str; adapter_version: str; framework_version: str
    native_state: bytes | dict                # e.g. OpenAI RunState.to_json(), MAF checkpoint id, ADK session/event ids
    pending: list[InterruptRequest]
    state_snapshot: dict                      # canonical IR state view for the canvas
```

Native mappings (verified):

| Adapter | Raise | Resume |
|---------|-------|--------|
| LangChain | `interrupt()` / `HumanInTheLoopMiddleware` | `Command(resume={"decisions": [...]})` |
| OpenAI Agents | `needs_approval` → `result.interruptions` (`ToolApprovalItem`) | `state = result.to_state()` (or `RunState.from_json`) → `state.approve(item)` / `state.reject(item, rejection_message=…)` → `Runner.run(agent, state)`; *edit* = reject with message + adapter-injected corrected call (documented emulation) |
| Google ADK | `FunctionTool(require_confirmation=…)`; workflow node yields `RequestInput(message=…)` | send confirmation / user response event into the same session; `Workflow(rerun_on_resume=True)` |
| MAF | `@tool(approval_mode="always_require")`; executor `ctx.request_info(data, response_type)` | send responses to pending `request_info` ids; restore from `CheckpointStorage` if cross-process |
| CrewAI | Flow `@human_feedback(...)`; task `human_input=True` | feedback provider returns outcome → `emit` route; persisted flow state via `@persist` |
| Pydantic AI | tool raises `ApprovalRequired` / `CallDeferred` → `DeferredToolRequests` | `agent.run(..., message_history=…, deferred_tool_results=DeferredToolResults(...))` |
| LlamaIndex WF | `ctx.write_event_to_stream(InputRequiredEvent(...))` | `ctx.send_event(HumanResponseEvent(...))` |
| Strands | hook/tool raises interrupt → agent returns interrupts | re-invoke agent with interrupt responses |
| Mastra | step `suspend(payload)` | `run.resume({ step, resumeData })` |
| Claude Agent SDK | `can_use_tool` callback (async) | callback awaits platform decision (in-process wait with timeout) — T3 |
| A2A remote | task state `input-required` / `auth-required` | send follow-up message on same task |

### C.5 State, persistence & memory mapping

| Concern | Rule |
|---------|------|
| Thread identity | Canvas `thread_id` ↔ framework conversation handle: LangGraph `thread_id`; OpenAI `Session(session_id)`; ADK `session_id` (+`user_id`, `app_name`); MAF `AgentSession` / workflow checkpoint lineage; CrewAI flow `id` (persisted); Pydantic AI message-history key; Strands session id; Mastra run id. |
| Hosting mode A (Agent Server) | The `@entrypoint` stores the **ResumeEnvelope** in `entrypoint.final(save=…)`; conversation history is kept either in the framework's session backend (bound per environment to Postgres/Redis/Mongo via doc 08 bindings) or inside the envelope for small runs. |
| Persistence bindings (doc 08) | Each adapter maps logical bindings to its native backends: OpenAI `SQLAlchemySession`/`RedisSession`/`MongoDBSession`; ADK `DatabaseSessionService` (SQLAlchemy URL) / `VertexAiSessionService`; MAF `HistoryProvider` (Redis/Cosmos) + `CheckpointStorage` (custom Postgres implementation shipped by us); CrewAI `FlowPersistence` (SQLite default; Postgres implementation shipped by us); Mastra stores (`@mastra/pg`, `@mastra/redis`, …). |
| Shared IR state | Adapters expose IR channels via a **state bridge**: ADK `ctx.state`, CrewAI flow state model, MAF shared state, LlamaIndex `Context` store, Mastra workflow state; reducers not native anywhere except LangGraph are **emulated** by generated merge functions (`E` in matrix). |
| Long-term memory | Platform Memory Spaces are reachable by every adapter through generated **memory tools** + `agentcanvas_runtime.memory` client; native memory services (ADK `MemoryService`, MAF context providers, CrewAI memory) can be selected as *dialect* bindings. |
| Data Studio | Generated repositories/tools (doc 08 §5) are framework-neutral Python; each adapter wraps them in its tool type. |

### C.6 Tool & model bridges

- **Single tool definition** (`ToolSpec`: name, description, JSON Schema args, implementation ref, approval policy, read-only/destructive hints) → per-adapter tool factory: `langchain.tools.tool`, `agents.function_tool`, `google.adk.tools.FunctionTool`, `agent_framework.tool`, `crewai.tools.BaseTool`, Pydantic AI `Tool`, Strands `@tool`, Mastra `createTool` (sidecar).
- **MCP**: tools from MCP servers are attached natively where supported (all T1/T2 frameworks verified above); the project MCP gateway (doc 11 A7) exposes platform tools uniformly.
- **Models**: `ModelSpec(provider, model, params, gateway)` → per-adapter model object; via the model gateway (LangSmith LLM Gateway or OpenAI-compatible proxy) when the framework cannot reach a provider natively (e.g. Claude models in OpenAI Agents SDK via LiteLLM extension or gateway).
- **Capability-aware validation**: e.g. `P001` if a Claude Agent SDK node is given a non-Claude model; `P002` if structured output is emulated.

### C.7 Hosting wrappers

**Mode A — Agent Server host (Python adapters).** Generated `host.py`:

```python
from langgraph.func import entrypoint, task
from langgraph.types import interrupt
from agentcanvas_runtime.hosting import envelope_codec, emit_canonical

@task
async def run_native(inp: dict, envelope: dict | None, decisions: list | None) -> dict:
    return await ADAPTER.start_or_resume(PLAN, inp, envelope, decisions)   # returns {"output"?, "pending"?, "envelope"}

@entrypoint()
async def agent(inp: dict, *, previous: dict | None = None):
    res = await run_native(inp, (previous or {}).get("envelope"), None)
    while res.get("pending"):
        decisions = interrupt({"interrupts": res["pending"]})             # surfaces in AgentCanvas Inbox / Agent Server
        res = await run_native(inp, res["envelope"], decisions)
    return entrypoint.final(value=res["output"], save={"envelope": res["envelope"]})
```
(ADK may alternatively use `deployments-wrap-sdk`'s `wrap(Runner(...))` + `LangsmithSessionService`.) Canonical events are forwarded through the `custom` stream mode.

**Mode B — native hosting.** Deploy service targets: ADK `adk deploy agent_engine|cloud_run|gke|docker`; MAF Foundry hosted agents / `hosting-*` packages; CrewAI AMP; LlamaIndex `llama-agents` server/control plane. Event translator attaches as a sidecar/plugin (ADK plugin, MAF middleware, CrewAI event listener) posting canonical events to our ingest endpoint; HITL resolved through the framework's native resume API.

**Mode C — remote.** A2A 1.0 client node (task streaming; `input-required` → interrupt) and MCP client node.

**TypeScript adapters.** `sidecars/node-adapter-host` implements the SPI over JSON-RPC (lower/generate/start/resume/events) so the Python control plane can drive Mastra/OpenAI-JS/ADK-TS; hosted in its own container per deployment with the same canonical event contract.

### C.8 Conformance suite (normative)

Shared cases in `agentcanvas/core/conformance`, run with **fake models** (scripted responses per framework's model interface) and **tool cassettes**. Each adapter declares which cases apply (by capability level).

| Suite | Cases (examples) | Pass criteria |
|-------|------------------|---------------|
| C-AGENT | single tool call; parallel tool calls; structured output; MCP tool; model error + retry | same tool sequence & final output as reference |
| C-FLOW | sequence; router (3 routes); parallel + join; map over 5 items; bounded loop; nested subgraph | same node visit multiset & final state (order-insensitive where parallel) |
| C-HITL | approve; edit; reject with message; input form; interrupt inside subgraph/sub-agent; resume **in a new process** from envelope | identical post-resume trajectory |
| C-STATE | channel writes, emulated reducers (append, merge), state snapshot events | canonical state equal |
| C-PERSIST | thread continuation across 3 turns; session backend bound to Postgres & Redis | history preserved |
| C-EVENTS | mandatory canonical events emitted with `canvas_node_id`; token streaming order | schema-valid, complete |
| C-CODEGEN | ruff/pyright clean (or eslint/tsc), byte-deterministic, runs with framework CLI (`adk run`, etc.) | pass |
| C-HOST | Mode A wrapper passes C-HITL & C-PERSIST on Agent Server (`langgraph dev`) | pass |

Portable templates (Router, Support triage, Research-lite, Approval workflow, Map-reduce summariser) must produce **equivalent outcomes** on every T1 adapter — this is the "same canvas, any framework" guarantee.

### C.9 Certification tiers (refined)

| Tier | Requirements |
|------|-------------|
| **T1** | Direct lowering path; C-AGENT, C-FLOW (all), C-HITL (all incl. cross-process), C-STATE, C-PERSIST, C-EVENTS, C-CODEGEN, C-HOST green; dev-run debugging (live overlay, breakpoints via interrupts or step hooks); importer for its own code *or* declarative spec (ADK YAML, MAF declarative, CrewAI FlowDefinition, Pydantic AI Agent Spec); nightly upstream CI; documented emulations |
| **T2** | Direct or bridge path; C-AGENT, C-FLOW (supported subset), C-HITL (approve/reject), C-EVENTS, C-CODEGEN; no breakpoints required |
| **T3** | Wrapped node; C-AGENT basic, C-EVENTS (run + text + tool), HITL where native callback exists |

### C.10 Stack & version management
- Each adapter pins a **framework range** per AgentCanvas stack (e.g. `stack-2026.10`: `openai-agents>=0.22,<0.23`, `google-adk>=2.10,<2.11`, `agent-framework>=1.19,<1.20`, `crewai>=1.15,<1.16`, `pydantic-ai>=2.51,<2.52`).
- **Nightly upstream CI** runs conformance against the framework's latest release and `main`; failures open issues and block stack promotion.
- Pre-1.0 frameworks (OpenAI Agents 0.x, Claude Agent SDK 0.x) get **patch-level pins** and a dedicated breaking-change watcher.
- MAF sub-packages in `b`/`a` pre-release (e.g. `hosting-mcp` alpha, `purview` beta) are **not** used by T1 features unless flagged experimental.

### C.11 UI/UX specification for multi-framework

| Element | Behaviour |
|---------|-----------|
| New workflow dialog | Choose **framework** (LangChain default; others shown with tier badge); "Portable" toggle restricts palette to core nodes |
| Palette | Core nodes + active framework's dialect nodes; foreign-framework nodes greyed with reason |
| Portability bar | "Compiles on: LangChain ✓ · ADK ✓ · OpenAI ⚠ (2 emulated) · CrewAI ✗ (1 blocker)" → click for per-node table |
| Switch framework | Re-target wizard: shows blockers/emulations, proposes dialect-node replacements (e.g. `langchain.deep_agent` → `adk.llm_agent` + planner) |
| Code view | Shows generated code for the selected framework; side-by-side compare across frameworks |
| Compare experiment | Run the same dataset on N frameworks/models; results grid (quality, latency, cost, tokens) |
| Node inspector | "Implemented as: `google.adk.workflow.JoinNode`" + capability level + caveats |

### C.12 Security considerations per adapter
- Framework code executes only in data-plane workers/sandboxes (doc 02); adapters run in the compiler workers **only for lowering/generation**, never executing user code.
- Framework-specific powerful tools (OpenAI `ShellTool`/`ApplyPatchTool`, sandbox agents; ADK code executors; MAF Hyperlight; Claude Agent SDK Bash/Edit tools) are **admin-gated dialect nodes**, default `needs_approval`/`permission_mode` restrictive, and must run with a sandbox binding.
- Licence gate: adapters record framework licence; Mastra `ee/` components excluded from generated code.

---

## Part D — Detailed delivery plan

### D.1 Timeline overview (relative to Release 1 GA = month 0)

```
Month:     -4   -2    0    2    4    6    8   10   12   14   16   18   20
R1 build   ████████████ (F0 foundations inside: IR split, SPI v0, canonical events on AG-UI, import-linter)
F1 interop           ██████ (Tier-3 wrappers, A2A/MCP nodes, Agent Spec import/export, bridge path)
F2a OpenAI                ████████ (T1 agent tier)
F2b ADK                   ██████████████ (T1)
F3 MAF                               ██████████████ (T1)
F4 CrewAI                                      ██████████████ (T1 Flows)
F5 PydAI/Strands/LlamaIdx                               ██████████████ (T2)
F6 Mastra (TS sidecar)                                        ██████████ (T2)
Mixed-framework graphs                                   ████████████
Adapter SDK public                                                 ██████
```

### D.2 Workstreams & milestones

**WS0 — Foundations (inside R1, months −4…0), squad: 2 eng + 0.5 PM**
| Milestone | Deliverables | Exit criteria |
|-----------|-------------|---------------|
| M0.1 IR split | `core.*` vs `langchain.*`/`langgraph.*` node namespaces; `x-<adapter>` overrides; IR v1.1 migration | All R1 templates validate in new IR |
| M0.2 SPI v0 | `FrameworkAdapter` protocol (C.2); LangChain adapter refactored behind it | Compiler/runtime call SPI only; import-linter green |
| M0.3 Canonical events v1 | AG-UI-based event schema + extensions (C.3); LangChain translator; UI consumes only canonical events | Live overlay works via canonical events |
| M0.4 HITL envelope | `InterruptRequest/Decision/ResumeEnvelope` (C.4) for LangChain | Inbox works through canonical HITL |
| M0.5 Conformance kit | C-AGENT/C-FLOW/C-HITL/C-EVENTS for LangChain; fake-model harness | Suite green |
| M0.6 Toy adapter | "echo" adapter in < 2 weeks | Proves seams |

**WS1 — Interop (months 0…3), squad: 3 eng**
| Milestone | Deliverables | Exit |
|-----------|-------------|------|
| M1.1 Remote agents | A2A 1.0 client node (streaming, `input-required`→interrupt), MCP-agent node | Call ADK/MAF/Strands A2A servers from canvas |
| M1.2 Wrapped agents | Generic Python callable wrapper + dedicated wrappers: Claude Agent SDK (`can_use_tool` → Inbox), OpenAI Agents (black-box), ADK (`deployments-wrap-sdk`), CrewAI crew, Strands agent | 5 frameworks as T3 nodes, Mode A hosted |
| M1.3 Agent Spec | Import/export Core IR ↔ Agent Spec (Agent, Flow, nodes, edges, ManagerWorkers, Swarm) | Round-trip of 10 portable templates |
| M1.4 Bridge path | pyagentspec loaders for OpenAI Agents / MAF / CrewAI dev runs | Portable templates run on 3 frameworks (T2-preview) |

**WS2a — OpenAI Agents SDK adapter (months 1…4), squad A: 3 eng** — spec: [adapters/openai-agents-sdk.md](./adapters/openai-agents-sdk.md)
- M2a.1 capability matrix & dialect nodes (guardrails, handoffs, hosted tools, sandbox agent — admin-gated) · M2a.2 lowering + codegen (agent tier; graph tier via generated async orchestration) · M2a.3 translator (stream events + RunHooks + TracingProcessor→OTel) · M2a.4 HITL via `RunState` JSON envelope · M2a.5 sessions bound to Postgres/Redis/Mongo · M2a.6 conformance & beta. **Exit:** T1 (agent tier), T2 (graph tier).

**WS2b — Google ADK adapter (months 1…7), squad B: 3 eng** — spec: [adapters/google-adk.md](./adapters/google-adk.md)
- M2b.1 capabilities & dialect nodes (LlmAgent options, planners, code executors, plugins, Sequential/Parallel/Loop agents) · M2b.2 graph lowering to `Workflow` edges/routes/JoinNode/parallel_worker · M2b.3 state bridge (`ctx.state`, `output_key`, emulated reducers) · M2b.4 HITL (`require_confirmation`, `RequestInput`) · M2b.5 sessions/memory bindings (`DatabaseSessionService`, Vertex) · M2b.6 events via AG-UI `adk-middleware` + node mapping · M2b.7 hosting A (wrap) and B (Agent Engine/Cloud Run) · M2b.8 importer from ADK YAML `AgentConfig` · M2b.9 conformance. **Exit:** T1.

**WS3 — Microsoft Agent Framework adapter (months 4…10), squad B** — spec: [adapters/microsoft-agent-framework.md](./adapters/microsoft-agent-framework.md)
- Lowering to `WorkflowBuilder` (edges, chains, fan-out/in, switch-case, multi-selection), executors from nodes, agents as executors; HITL (`approval_mode`, `request_info`); **Postgres `CheckpointStorage`** implementation (ours, contributed upstream); history providers bound per env; events via AG-UI MAF integration; hosting A + Foundry hosted agents; declarative YAML import/export; orchestrations as dialect macros (group chat, magentic). **Exit:** T1; .NET via sidecar = later.

**WS4 — CrewAI adapter (months 7…13), squad A** — spec: [adapters/crewai.md](./adapters/crewai.md)
- Graph tier → **`FlowDefinition`** generation (plus readable DSL Python projection); Agent tier → Agent/Task/Crew; HITL via `@human_feedback` & task `human_input`; persistence via `FlowPersistence` (Postgres impl ours) and `CheckpointConfig`; events via AG-UI `crew-ai` + event bus listener; importer from `FlowDefinition`. **Exit:** T1 (Flows), T2 (Crews hierarchical).

**WS5 — Pydantic AI, Strands, LlamaIndex Workflows (months 9…15), squad C: 3 eng** — specs in [adapters/](./adapters/)
- Pydantic AI: Agent Spec YAML emission, capabilities as dialect nodes, deferred-tool HITL, `pydantic_graph` lowering, durable exec binding (Temporal/DBOS) → T2 then T1.
- Strands: `GraphBuilder`/`Swarm` lowering, interrupts, session managers → T2.
- LlamaIndex Workflows: event-class-per-edge lowering, `InputRequiredEvent` HITL, Context state → T2.

**WS6 — TypeScript sidecar & Mastra (months 11…16), squad C** — spec: [adapters/mastra.md](./adapters/mastra.md)
- Node adapter host (JSON-RPC SPI), Mastra lowering (`createWorkflow` chains, `.branch/.parallel/.foreach/.dowhile`, `suspend/resume`), storage bindings (`@mastra/pg`, `@mastra/redis`), AG-UI Mastra integration. **Exit:** T2.

**WS7 — Mixed-framework graphs & comparison (months 10…16)**
- Per-node `engine`; boundary contracts (explicit data edges, schema-validated); in-process composition for Python adapters (foreign agent wrapped as a node/task inside the primary adapter), A2A for cross-language; cross-framework compare experiments in LangSmith.

**WS8 — Public Adapter SDK (months 16…20)**
- `agentcanvas adapter new` scaffolding, docs, conformance kit as a package, certification programme, marketplace listing, community T3/T2 adapters (Agno, smolagents, AG2, Langroid…).

### D.3 Staffing & cost (incremental to core product team)

| Period | Squads | Engineers |
|--------|--------|-----------|
| R1 build (F0) | Foundations (shared with core) | +2 |
| Months 0–7 | Interop + OpenAI + ADK | 6–7 |
| Months 7–16 | MAF/CrewAI + PydAI/Strands/LlamaIndex + TS sidecar | 6–9 |
| Months 16–20 | SDK & ecosystem + maintenance rotation | 3 + 1 upstream-watch rotation per 3 adapters |

### D.4 Definition of Done per adapter (checklist)
- [ ] Verification spike report (versions, API citations, licence) committed under `adapters/<fw>/VERIFICATION.md`
- [ ] Capability matrix reviewed by product & security
- [ ] Dialect node manifests + UI forms + docs
- [ ] Direct lowering & codegen with golden tests; generated project runs with the framework's own CLI
- [ ] Canonical event translator (AG-UI reuse where available) with node mapping
- [ ] HITL mapping incl. cross-process resume via `ResumeEnvelope`
- [ ] Persistence mapping for Postgres & Redis bindings (+ Mongo where native)
- [ ] Hosting mode A; mode B where planned
- [ ] Conformance suite green for the target tier; nightly upstream CI
- [ ] Templates (≥ 3) and Copilot knowledge pack
- [ ] Beta with ≥ 3 design partners; certification sign-off

### D.5 Risks (evidence-based)

| Risk | Evidence | Mitigation |
|------|----------|-----------|
| Pre-1.0 API churn | OpenAI Agents 0.22.x, Claude Agent SDK 0.2.x, many MAF sub-packages `a`/`b` | Patch pins, nightly CI, avoid pre-release sub-packages in T1 |
| Fast-moving graph APIs | ADK `workflow` package is new in 2.x; Mastra main is alpha | Target released tags only; adapter-level shims; conformance on upgrade |
| Semantic gaps in state reducers | Only LangGraph has native reducers | Generated merge functions + C-STATE tests; warn on concurrent writes (`W071`) |
| Missing Postgres persistence in some frameworks | MAF checkpoints (memory/file/Cosmos), CrewAI persistence (SQLite) | Ship & upstream Postgres implementations; conformance-test them |
| Bridge-path gaps | pyagentspec converters raise `NotImplementedError` for some features | Surface as `P002`; fall back to direct path |
| Event fidelity varies across AG-UI integrations | Integrations are community-maintained, per-framework | Pin integration versions; extension events added by our translators; C-EVENTS gate |
| Licensing | Mastra `ee/` directories | Licence gate in adapter manifest; exclude from codegen |

---

## Part E — Updates to earlier documents

- Doc 11 §3's concept mapping is now **superseded by A.4** (verified); doc 11 §5 phase ordering is **superseded by B.2 and D.1**.
- Doc 02: add `agentcanvas/core` vs `adapters/*` packaging (C.1) and the Node sidecar (C.7).
- Doc 03: IR `framework`/`engine`, `x-<adapter>` blocks, portability diagnostics (`P001–P003`) — unchanged; add `P004` *bridge path used (non-idiomatic code)*.
- Doc 08: persistence bindings get per-adapter mappings (C.5).

## Sources (verified 2026-09-28)
Framework repositories (default branch, shallow clones): `openai/openai-agents-python`, `google/adk-python`, `microsoft/agent-framework`, `crewAIInc/crewAI`, `pydantic/pydantic-ai`, `run-llama/workflows-py`, `mastra-ai/mastra`, `strands-agents/sdk-python`, `anthropics/claude-agent-sdk-python`, `oracle/agent-spec`, `a2aproject/a2a-python`, `ag-ui-protocol/ag-ui`; LangChain docs repo `langchain-ai/docs` (Agent Server hosting of other frameworks).
