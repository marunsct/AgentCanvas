# Adapter Spec — Strands Agents

## 1. Verified baseline
- `strands-agents` **1.41.0** (Apache-2.0); Python (`strands-py`) and TypeScript (`strands-ts`) in one repo.
- Verified: `multiagent/graph.py` (`GraphBuilder.add_node(executor, node_id)`, `add_edge(from_node, to_node, condition)`, `set_entry_point`, `set_max_node_executions`, `build`), `multiagent/swarm.py` (`Swarm`, `SwarmNode`, `SwarmState`), `multiagent/a2a`, `types/interrupt.py` (hook/tool interrupts: `event.interrupt(name, reason=…)`; agent returns interrupts; re-invoke with `interruptResponse` items), `session/` (file, S3, repository, snapshot session managers), `telemetry/` (OTel), `hooks/`, `plugins/`.

## 2. Positioning
Model-driven agents with graph and swarm multi-agent patterns and first-class interrupts. Target **T2**.

## 3. Core IR mapping

| Core concept | Level | Construct |
|--------------|-------|-----------|
| `core.agent` | N | `Agent(model=…, tools=[…], system_prompt=…, hooks=[…], session_manager=…)` |
| Tools / MCP | N | `@tool`, MCP clients |
| Graph sequence/router/parallel | N | `GraphBuilder` nodes (agents or nested multi-agent) + conditional edges (`condition` callable over graph state) |
| Loop guard | N | `set_max_node_executions` + conditions |
| Handoff / swarm | N | `Swarm([...])` as dialect macro |
| Sub-agent | N | agents-as-tools |
| Map | E | generated node that invokes an agent per item |
| Shared state | N/E | graph invocation state; IR channels via generated state object |
| Tool approval | N | `BeforeToolCallEvent` hook calling `event.interrupt("…", reason=…)` (generated `ApprovalHook` from `interrupt_on`) |
| Resume | N | re-invoke agent with `interruptResponse` content items |
| Persistence | N | session managers (file/S3/repository/snapshot); adapter ships a Postgres/Redis `SessionRepository` |
| Events | N | async stream + hooks; AG-UI `aws-strands` integration |
| Tracing | N | OTel |

## 4–11 (summary)
- Dialect nodes: `strands.swarm`, `strands.hook.*`, `strands.plugin.*`, `strands.session`.
- Codegen: `graph.py` with `GraphBuilder`; `ApprovalHook(HookProvider)` generated from approval policies.
- Envelope: session id + pending interrupt names/reasons; cross-process via session manager.
- Hosting: Mode A (LangSmith documents Strands via Functional API), Mode B AWS (AgentCore) optional, Mode C A2A.
- Import: AST of `GraphBuilder` calls.
- Gaps: map (E), reducers (E), breakpoints via hook interrupts (dev mode).

## 12. Plan (squad C, ~14 weeks) · 13. Risks
Verification (1–2) · lowering (3–8) · HITL hooks & resume (7–9) · session repository (9–10) · events (10–11) · conformance & beta (11–14). Risk: TS/Python parity; hook API evolution.
