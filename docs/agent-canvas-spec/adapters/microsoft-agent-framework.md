# Adapter Spec — Microsoft Agent Framework (MAF)

## 1. Verified baseline
- Python package `agent-framework` **1.19.0** (tag `python-1.19.0`, MIT), GA 1.x; .NET SDK in the same repo. ~40 Python sub-packages (`agent-framework-redis`, `-postgres`, `-azure-cosmos`, `-orchestrations`, `-declarative`, `-a2a`, `-ag-ui`, `-hosting-*`, `-foundry*`, `-mem0`, `-devui`, …); several are pre-release (`a`/`b` suffixes) → T1 features use only GA packages.
- Verified in source: `core/agent_framework/_agents.py` (`RawAgent`/`Agent(client, instructions, id, name, description, tools, default_options, context_providers, middleware, compaction_strategy, tokenizer)`), `_tools.py` (`ApprovalMode = Literal["always_require","never_require"]`), `_sessions.py` (`AgentSession`, `HistoryProvider`, `ContextProvider`), `_workflows/*` (`WorkflowBuilder`, `Executor`, `handler`, `response_handler`, `WorkflowContext.request_info/send_message/yield_output`, `CheckpointStorage`, `InMemoryCheckpointStorage`, `FileCheckpointStorage`, `WorkflowEvent` types), `packages/orchestrations` (sequential, concurrent, group chat, handoff, magentic), `packages/declarative` (`AgentFactory`), `packages/azure-cosmos` (Cosmos checkpoint storage), sample `samples/02-agents/devui/workflow_spam/workflow.py`.

## 2. Positioning
Pregel-style workflow engine (super-steps, executors, typed messages along edges, checkpoints, request/response HITL) plus a rich agent layer and enterprise hosting (Foundry). Structurally the closest match to LangGraph → **Tier 1**.

## 3. Core IR mapping

| Core concept | Level | Generated construct |
|--------------|-------|---------------------|
| Workflow | N | `WorkflowBuilder(start_executor=…, name=…, checkpoint_storage=…, max_iterations=…).add_…().build()` |
| Script / function node | N | `Executor` subclass with `@handler async def run(self, msg: In, ctx: WorkflowContext[Out]) -> None` (or function executor) |
| `core.agent` | N | `Agent(client=<ChatClient>, instructions=…, tools=[…], middleware=[…], context_providers=[…])`; used as an executor (agents implement `SupportsAgentRun`) |
| Model | N | chat clients: OpenAI, Azure/Foundry, Anthropic, Bedrock, Gemini, Mistral, Ollama, GitHub Copilot, Foundry Local |
| Sequence | N | `add_edge(a, b)` / `add_chain([a, b, c])` |
| Router | N | `add_switch_case_edge_group(src, [Case(condition=…, target=…), Default(target=…)])`; multi-target: `add_multi_selection_edge_group` |
| Parallel + join | N | `add_fan_out_edges(src, [a, b])` + `add_fan_in_edges([a, b], join)` |
| Map | N/E | fan-out to N executor instances when N is static; dynamic lists emulated by a generated dispatcher executor that sends one message per item and a counting fan-in |
| Loop | N | back edges + condition; bounded by `max_iterations` (graph-level) and state counter |
| Subgraph | N | workflow-as-executor (`WorkflowExecutor`) |
| Shared state | N | workflow shared state (`_state.py`) via context; typed messages carry node outputs → IR channels mapped by the adapter |
| Reducers | E | generated merge in join executors |
| Tool approval | N | `@tool(approval_mode="always_require")` → function approval request/response content |
| Input / approval node | N | `await ctx.request_info(request_data, response_type)` + `@response_handler` method |
| Checkpoints | N | `CheckpointStorage` (in-memory, file, Cosmos) — adapter ships **`PostgresCheckpointStorage`** and **`RedisCheckpointStorage`** implementing the protocol |
| Conversation history | N | `HistoryProvider` (in-memory, file, Redis, Cosmos) |
| Long-term memory | N | `ContextProvider` (Mem0, Redis, Cosmos memory) or platform Memory Spaces (E) |
| Middleware / guardrails | N | agent & function middleware |
| Orchestration patterns | N | orchestrations package: sequential, concurrent, group chat, handoff, magentic (dialect macros) |
| Streaming & events | N | `WorkflowEvent` types: `started, status, failed, output, intermediate, data, request_info, warning, error, superstep_started, superstep_completed, executor_invoked, executor_completed, executor_failed, executor_bypassed, group_chat, handoff_sent, magentic_orchestrator` |
| Tracing | N | OpenTelemetry observability module |

## 4. Dialect nodes (`msaf.*`)
`msaf.agent` (client-specific options, compaction strategy), `msaf.executor` (custom typed executor), `msaf.switch_case`, `msaf.multi_selection`, `msaf.orchestration.{group_chat,handoff,magentic,concurrent,sequential}`, `msaf.context_provider.*`, `msaf.middleware.*`, `msaf.hosting.foundry`.

## 5. Code generation (verified API shape)

```python
from agent_framework import Case, Default, Executor, WorkflowBuilder, WorkflowContext, handler, response_handler

class Classify(Executor):                                   # @canvas-node n_cls
    @handler
    async def run(self, ticket: Ticket, ctx: WorkflowContext[Classified]) -> None:
        result = await classifier_agent.run(ticket.text)     # Agent(client=…, instructions=…)
        await ctx.send_message(Classified(ticket=ticket, category=parse(result)))

class Review(Executor):                                     # @canvas-node n_review (Approval)
    @handler
    async def run(self, draft: Draft, ctx: WorkflowContext[Draft]) -> None:
        await ctx.request_info(request_data=ApprovalRequest(text=draft.text), response_type=ApprovalResponse)

    @response_handler
    async def on_response(self, original: ApprovalRequest, response: ApprovalResponse, ctx: WorkflowContext[Draft]) -> None:
        await ctx.send_message(Draft(text=response.edited_text or original.text, approved=response.approved))

workflow = (
    WorkflowBuilder(start_executor=classify, name="support_triage", checkpoint_storage=checkpoints)
    .add_switch_case_edge_group(classify, [
        Case(condition=lambda m: m.category == "billing", target=billing),
        Default(target=tech),
    ])
    .add_edge(billing, review)
    .add_edge(review, send)
    .build()
)
```
(Verified against `samples/02-agents/devui/workflow_spam/workflow.py`: `ctx.request_info(request_data=…, response_type=…)` and `@response_handler async def …(self, original_request, response, ctx)`.)

## 6. HITL
- `request_info` events → canonical `input`/`approval` interrupts (response JSON Schema derived from `response_type`).
- Tool approvals (`approval_mode="always_require"`) → canonical `tool_approval`.
- Resume: send responses keyed by request id; cross-process via checkpoint restore from `CheckpointStorage` (Postgres impl) — `ResumeEnvelope.native_state = {checkpoint_id, pending_request_ids}`.

## 7. State & persistence
- Thread ↔ checkpoint lineage (workflow) + `AgentSession` (agents). Bindings: Postgres → our `PostgresCheckpointStorage` + Redis/Postgres `HistoryProvider`; Azure → Cosmos checkpoint storage & history.
- Upstream contribution plan for the Postgres checkpoint storage.

## 8. Events & tracing
Reuse AG-UI `microsoft-agent-framework` integration for agent streams; native workflow events map directly: `executor_invoked/completed/failed` → `STEP_*`, `superstep_*` → `ac.checkpoint` markers, `request_info` → interrupt, `handoff_sent` → `ac.handoff`, `output` → run output. OTel spans from MAF observability get `agentcanvas.node_id` via middleware.

## 9. Hosting
Mode A (entrypoint wrapper); Mode B: Foundry hosted agents, `hosting-a2a`, `hosting-responses`, `hosting-mcp` (alpha → experimental only); Mode C via A2A.

## 10. Import / round-trip
MAF **declarative YAML** (`agent_framework_declarative`) ↔ Core IR for agents/workflows where supported; Python import via builder-call AST analysis (`WorkflowBuilder(...).add_*` chains). Agent Spec `agent_framework` loader/exporter as fallback.

## 11. Gaps & emulations
Dynamic map (E); reducers (E); .NET target deferred (sidecar, later); pre-release sub-packages excluded from T1.

## 12. Work plan (squad B, ~24 weeks)
Weeks 1–2 verification & matrix · 3–6 dialect nodes, chat-client factories, declarative import/export · 5–12 workflow lowering (edges, switch-case, fan-out/in, sub-workflows, executors from nodes) · 10–14 HITL & Postgres/Redis checkpoint storage (+ conformance of storage) · 13–16 events & OTel · 16–19 hosting A/B (Foundry) · 18–22 conformance, templates · 22–24 beta & certification.

## 13. Risks
Large, fast-growing package set (pin GA packages); Azure-centric hosting features (optional bindings).
