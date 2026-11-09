# Adapter Spec — CrewAI (Crews & Flows)

## 1. Verified baseline
- Package `crewai` **1.15.22** (MIT), Python. Monorepo `lib/crewai`, `lib/crewai-tools`, `lib/cli`.
- Verified in source: `agent/core.py` (Agent: role, goal, backstory, llm, tools, reasoning, guardrail…), `task.py` (Task: description, expected_output, agent, context, async_execution, output_json, output_pydantic, tools, callback, guardrail, human_input, markdown), `process.py` (`Process.sequential|hierarchical`), `flow/dsl` (`start`, `listen`, `router`, `or_`, `and_`, `human_feedback`), **`flow/flow_definition.py` (`FlowDefinition` — serialisable declarative flow contract)**, `flow/human_feedback.py` (`@human_feedback(message, emit, llm, default_outcome, metadata, provider, learn…)`), `flow/async_feedback` (`HumanFeedbackPending`, `PendingFeedbackContext`, `HumanFeedbackProvider`), `flow/runtime` (`resume(feedback)`, `resume_async`), `flow/persistence` (`FlowPersistence`, `@persist`, SQLite), `state/checkpoint_config.py` (`CheckpointConfig(location, on_events, provider)`), `memory/` (unified memory; LanceDB/Qdrant storage), `events/` (event bus & listeners), `a2a/`, `mcp/`.

## 2. Positioning
Two layers: **Crews** (role-based autonomous collaboration) and **Flows** (event-driven, precise control, state, persistence, human feedback). Flows map to the Graph tier; Crews and Agents to the Agent tier. The declarative `FlowDefinition` is the adapter's **primary codegen target and import source**. Target **T1 for Flows**, **T2 for hierarchical Crews** (manager-driven delegation is non-deterministic to visualise step-by-step).

## 3. Core IR mapping

| Core concept | Level | Generated construct |
|--------------|-------|---------------------|
| Workflow | N | `class <Name>Flow(Flow[StateModel])` + emitted `FlowDefinition` |
| Script node | N | flow method (code action) |
| `core.agent` | N | `Agent(role, goal, backstory, llm, tools, …)` executed via a one-task `Crew` or a Flow agent action |
| Crew (multi-agent) | N | `Crew(agents, tasks, process=Process.sequential|hierarchical)` as a **crew action** node |
| Sequence | N | `@listen(previous_method)` |
| Router | N | `@router(source)` returning a route label; `@listen("label")` |
| Parallel + join | N | multiple `@listen(x)` + `@listen(and_(a, b))` join; `or_` for first-of |
| Map | N | `each` action (FlowDefinition `FlowEachActionDefinition`) |
| Loop | N | router back-edges with state counter |
| Subgraph | N | flow action invoking another Flow |
| Shared state | N | Pydantic `StateModel` (from IR state schema) on `self.state` |
| Reducers | E | generated merge helpers on state writes |
| Structured output | N | task `output_pydantic` / `output_json` |
| Tool approval | E/N | task `human_input=True` (review of task output) natively; per-tool approval emulated via a wrapped tool raising `HumanFeedbackPending`-style pause (conformance-tested) |
| Input / approval node | N | `@human_feedback(message=…, emit=[…], llm=…, default_outcome=…)`; async pause via `HumanFeedbackPending(PendingFeedbackContext)`; resume `flow.resume(feedback)` / `resume_async` |
| Persistence | N | `@persist` with `FlowPersistence` (SQLite built-in; adapter ships **Postgres** & **Redis** implementations) and `CheckpointConfig` |
| Memory | N | CrewAI unified memory (LanceDB/Qdrant) as dialect binding, or platform Memory Spaces (E) |
| Guardrails | N | task/agent `guardrail` |
| MCP | N | agent `mcps` / MCP tools |
| Events | N | `crewai_event_bus` listeners |

## 4. Dialect nodes (`crewai.*`)
`crewai.agent` (role/goal/backstory, reasoning, delegation), `crewai.task`, `crewai.crew` (process, manager LLM, planning), `crewai.human_feedback`, `crewai.knowledge`, `crewai.memory`, `crewai.guardrail`, `crewai.persist`.

## 5. Code generation

```python
from crewai.flow import Flow, HumanFeedbackResult, human_feedback, listen, persist, router, start   # verified exports

class TriageState(BaseModel):                     # from IR state schema
    ticket: Ticket | None = None
    category: str = ""
    draft: str = ""

@persist(postgres_persistence)                    # binding from doc 08 (adapter-provided FlowPersistence)
class SupportTriageFlow(Flow[TriageState]):
    @start()                                      # @canvas-node n_ingest
    def ingest(self):
        return self.state.ticket

    @router(ingest)                               # @canvas-node n_route
    def route(self):
        self.state.category = classify_crew.kickoff(inputs={"ticket": self.state.ticket.text}).pydantic.category
        return self.state.category                # "billing" | "tech"

    @listen("billing")                            # @canvas-node n_billing (crew action)
    @human_feedback(message="Approve this reply?", emit=["approved", "rejected"], llm="gpt-5-mini",
                    default_outcome="rejected")   # @canvas-node n_review
    def billing(self):
        self.state.draft = billing_crew.kickoff(inputs={"ticket": self.state.ticket.text}).raw
        return self.state.draft

    @listen("approved")                           # @canvas-node n_send
    def send(self, result: HumanFeedbackResult):
        send_reply(self.state.ticket, result.output)
```
The compiler also emits the equivalent **`FlowDefinition`** (JSON) — used for validation, import and visual diffing.

## 6. HITL
- `@human_feedback` with an **async provider** implemented by the adapter: raising `HumanFeedbackPending(context=PendingFeedbackContext(...))` ends the run with a canonical `input`/`approval` interrupt; `ResumeEnvelope.native_state = {flow_class, flow_id, pending_context}`.
- Resume: restore persisted flow (`@persist`) and call `flow.resume(feedback)` / `await flow.resume_async(feedback)`; `emit` outcomes map to canvas route ports; `llm` maps free-text feedback to outcomes.
- `learn=True` (feedback learning) surfaced as a Learning Loop hook (doc 09).

## 7. State & persistence
Thread ↔ flow `id` (persisted state). Bindings: Postgres/Redis `FlowPersistence` (ours), SQLite (dev). `CheckpointConfig(location, on_events, provider)` enabled for T1 durability; crews within flows are re-run on resume unless outputs cached in state (validator warns on side effects, `W070`).

## 8. Events & tracing
Reuse AG-UI **`crew-ai`** integration; add a `BaseEventListener` subclass that maps flow method start/finish, crew/task/agent/tool events to canonical events with `canvas_node_id` (from method name ↔ source map). OTel via CrewAI telemetry integration + our listener spans.

## 9. Hosting
Mode A (entrypoint wrapper around `flow.kickoff_async` / `resume_async`); Mode B CrewAI AMP (control plane) where customers use it; Mode C via CrewAI `a2a` module.

## 10. Import / round-trip
`FlowDefinition` ↔ Core IR (primary, lossless for supported actions); DSL Python → `build_flow_definition` → IR; crews from `agents.yaml`/`tasks.yaml` project layout → IR. Agent Spec `crewai` loader/exporter as fallback.

## 11. Gaps & emulations
Per-tool approval (E); reducers (E); hierarchical crew internals are opaque steps (T2); breakpoints via pause before flow methods (dev mode).

## 12. Work plan (squad A, ~24 weeks)
1–2 verification & matrix · 3–6 dialect nodes, FlowDefinition model integration · 5–12 lowering to Flow DSL + FlowDefinition, crew/agent/task generation · 10–14 HITL async provider, resume, Postgres/Redis persistence · 13–16 events (AG-UI crew-ai + listener) · 16–18 hosting A/B · 18–22 conformance & templates (Content pipeline, Support triage, Research crew) · 22–24 beta & certification.

## 13. Risks
FlowDefinition is recent — pin minor; crews' autonomous behaviour limits deterministic conformance (use fake LLMs + outcome equivalence); SQLite-only built-in persistence (ship Postgres/Redis).
