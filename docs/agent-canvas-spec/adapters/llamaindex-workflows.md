# Adapter Spec — LlamaIndex Workflows

## 1. Verified baseline
- `llama-index-workflows` **2.11.1** (MIT) plus `llama-agents-server`, `-client`, `-control-plane`, `-dbos`, `-agentcore`, `llamactl`.
- Verified: `workflows/decorators.py` (`@step(num_workers=4, retry_policy=…)`), `events.py` (`StartEvent`, `StopEvent`, `InputRequiredEvent`, `HumanResponseEvent`, `StepFailedEvent`, timeout/cancel events), `context/context.py` (`Context.send_event`, `write_event_to_stream`, state store, serializers), `representation/` (build & validate workflow graph), `retry_policy.py`.

## 2. Positioning
Event-driven workflows: steps consume and emit typed events; edges are *event types*. Target **T2**.

## 3. Core IR mapping (lowering rule: one Event subclass per IR edge)

| Core concept | Level | Construct |
|--------------|-------|-----------|
| Workflow | N | `class <Name>(Workflow)` |
| Node | N | `@step async def node(self, ctx: Context, ev: <InEdgeEvent>) -> <OutEdgeEvent>` |
| Sequence | N | step A returns `AtoB` event consumed by step B |
| Router | N | step returns one of several event types (union return annotation) |
| Parallel + join | N | `ctx.send_event(...)` multiple events; join step uses `ctx.collect_events(ev, [TypeA, TypeB])` |
| Map | N | `num_workers` + one event per item; collect at join |
| Loop | N | event back to an earlier step type + counter in state |
| Subgraph | N | nested workflow invoked in a step |
| Agent node | E | LlamaIndex agent (e.g. function agent) run inside a step |
| Shared state | N | `Context` state store (typed) |
| Input / approval | N | `ctx.write_event_to_stream(InputRequiredEvent(...))` + wait for `HumanResponseEvent`; external `ctx.send_event(HumanResponseEvent(...))` |
| Persistence | N | Context serialisation; DBOS durability package |
| Retry / timeout | N | `retry_policy`, workflow timeout |
| Events | N | `stream_events()`; AG-UI `llama-index` integration |

## 4–11 (summary)
Dialect: `llamaindex.agent`, `llamaindex.query_engine`, `llamaindex.retriever` (RAG strengths). Envelope: serialised `Context` + pending event ids. Hosting: Mode A; Mode B `llama-agents` server/control plane or AgentCore; Mode C A2A. Import: `representation` package can build a graph from a Workflow class → IR. Gaps: reducers (E); tool-level approval inside agents (E).

## 12. Plan (~12 weeks) · 13. Risks
Verification (1–2) · event-per-edge lowering & representation import (3–7) · HITL events & context serialisation (6–8) · events (8–9) · conformance & beta (9–12). Risk: event-type explosion for large graphs (namespaced generated event module).
