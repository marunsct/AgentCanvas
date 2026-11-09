# Adapter Spec — Pydantic AI

## 1. Verified baseline
- `pydantic-ai` **2.51.0** (MIT) with `pydantic-graph`, `pydantic-evals`; Python.
- Verified: `agent/` (`Agent`, `Agent.from_spec`, `Agent.from_file`, `AgentSpec`), `capabilities/` (hooks, thinking, web_search/web_fetch, mcp, prepare_tools, select_model, content_filter, instrumentation, `HandleDeferredToolCalls`, …), `_deferred.py` (`DeferredToolRequests`, `DeferredToolResults`), `exceptions.py` (`ApprovalRequired`, `CallDeferred`), `toolsets/approval_required.py` (`ApprovalRequiredToolset`), `durable_exec/{temporal,dbos,prefect}`, `ui/{ag_ui,vercel_ai}`, `pydantic_graph` (`GraphBuilder`, steps, decisions, joins, forks), docs `agent-spec.md`, `deferred-tools.md`, `persistence.md`.

## 2. Positioning
Type-safe agent framework with a declarative **Agent Spec (YAML/JSON)**, composable capabilities, first-class deferred/approval tools and durable-execution integrations. Agent tier maps directly; graph tier via `pydantic_graph`. Target **T2 → T1**.

## 3. Core IR mapping

| Core concept | Level | Generated construct |
|--------------|-------|---------------------|
| `core.agent` | N | **AgentSpec YAML** (model, instructions, capabilities) + Python for tools; loaded with `Agent.from_file(...)`; or direct `Agent(model, instructions=…, output_type=…, deps_type=…, tools=[…], capabilities=[…])` |
| Tools / MCP | N | `@agent.tool` / `Tool`, toolsets, MCP capability/toolsets |
| Structured output | N | `output_type=Model` (union with `DeferredToolRequests` when HITL bubbles up) |
| Guardrails / middleware | N | capabilities & hooks (dialect) |
| Sub-agent | N | delegation via tools calling another agent |
| Graph (sequence/router/parallel/join/loop) | N | `pydantic_graph.GraphBuilder` steps, decisions, forks & joins |
| Map | N/E | graph fork over items (verify per version) / generated loop |
| Shared state | N | graph state object |
| Tool approval | N | `ApprovalRequired` / `ApprovalRequiredToolset` → `DeferredToolRequests`; resume via new run with message history + `deferred_tool_results=DeferredToolResults(...)` (correlate by `conversation_id`) |
| Input node | E | `CallDeferred` tool or graph step pause |
| Conversation persistence | N/E | serialise message history (JSON) to the bound store; or Pydantic AI Harness `StepPersistence` |
| Durable execution | N | Temporal / DBOS / Prefect integrations (dialect binding) |
| Events | N | streaming events; AG-UI UI adapter |
| Tracing | N | instrumentation (OTel / Logfire) |

## 4. Dialect nodes (`pydantic_ai.*`)
`pydantic_ai.capability.*` (thinking, web search, content filter, select_model, prepare_tools, …), `pydantic_ai.durable` (Temporal/DBOS/Prefect binding), `pydantic_ai.deferred_tool`, `pydantic_ai.graph_step`.

## 5. Codegen notes
Agent nodes emit `agents/<name>.yaml` (AgentSpec) + `tools/<name>.py`; graphs emit `graph.py` with `GraphBuilder`. YAML makes the agent layer **round-trippable** (import = parse AgentSpec).

## 6. HITL
Stop-the-world flow (verified docs): run ends with `DeferredToolRequests` → canonical `tool_approval` interrupts; envelope stores serialised message history + `conversation_id`; resume = new `agent.run(..., message_history=…, deferred_tool_results=DeferredToolResults(approvals={call_id: True|False|…}))`. In-process approvals via `HandleDeferredToolCalls` capability for auto-policies.

## 7. Persistence
History JSON stored in the bound backend (Postgres/Redis via platform persistence client); durable exec (Temporal/DBOS) optional binding for long-running graphs.

## 8. Events
Reuse AG-UI `pydantic-ai` integration; OTel spans via instrumentation capability with `agentcanvas.node_id`.

## 9. Hosting
Mode A; Mode B with Temporal/DBOS workers where customers run them; Mode C via A2A wrapper.

## 10. Import
AgentSpec YAML/JSON → IR (lossless for agent layer); graphs via AST of `GraphBuilder` usage (T1 requirement).

## 11. Gaps
Input-form HITL (E); map semantics per graph version (verify); reducers (E).

## 12. Plan (squad C, ~16 weeks to T2, +8 to T1)
Verification & matrix (1–2) · AgentSpec emission/import (3–5) · graph lowering (5–9) · deferred HITL & envelope (8–10) · persistence (10–11) · events & OTel (11–12) · conformance & templates (12–15) · beta (16); T1 upgrade: breakpoints, graph import, full C-FLOW.

## 13. Risks
Rapid minor releases (2.x weekly) → minor pins + nightly CI; graph API evolution.
