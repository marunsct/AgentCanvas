# Adapter Spec — Google Agent Development Kit (ADK)

## 1. Verified baseline
- Package `google-adk` **2.10.0** (Apache-2.0); ADK also exists for TypeScript, Java and Go (Python is the adapter's first language).
- Verified in source: `agents/llm_agent.py` (`LlmAgent` fields & callbacks), `agents/{sequential,parallel,loop}_agent.py`, `agents/remote_a2a_agent.py`, `agents/langgraph_agent.py`, `agents/config_schemas/AgentConfig.json` (YAML agent config), **`workflow/`** (`Workflow`, `node`, `FunctionNode`, `JoinNode`, `RetryConfig`, `NodeTimeoutError`, `START`, `DEFAULT_ROUTE`), `events/RequestInput`, `tools/function_tool.py` (`require_confirmation`), `tools/tool_confirmation.py` (`ToolConfirmation`), `sessions/*`, `memory/*`, `plugins/*`, `telemetry/*` (OTel GenAI semconv), `cli/cli_tools_click.py` (`adk deploy cloud_run|docker|agent_engine|gke`), samples under `contributing/samples/workflows/*`.

## 2. Positioning
ADK 2.x offers both an agent layer and a **graph workflow engine** with routing, fan-out/fan-in, loops, nested workflows, retries/timeouts and human input — the closest Python match to LangGraph after Microsoft Agent Framework. Target **Tier 1**, with native hosting on Vertex AI Agent Engine / Cloud Run / GKE.

## 3. Core IR mapping

| Core node / concept | Level | Generated construct |
|---------------------|-------|---------------------|
| Workflow | N | `root_agent = Workflow(name=…, edges=[…], max_concurrency=…)` |
| `core.agent` | N | `Agent(name, model, instruction, tools, output_schema, output_key, before/after_model_callback, before/after_tool_callback, on_*_error_callback, planner, code_executor, generate_content_config)` used directly as a workflow node |
| Function / script node | N | plain function or `@node(...)` taking `node_input` / `ctx` / state-param injection, returning a value or yielding `Event(state=…, route=…, message=…)` |
| Sequence | N | edge chain tuple `("START", a, b, c)` |
| Router | N | router node yields `Event(route="x")`; edge `(router, {"x": node_x, "y": node_y})`; `DEFAULT_ROUTE` for default |
| Parallel + join | N | tuple of nodes in a chain + `JoinNode(name=…)` (waits for all predecessors; receives dict keyed by node name) |
| Map (fan-out) | N | `parallel_worker=True` on the agent or `@node(parallel_worker=True)` — runs once per item of the preceding iterable output |
| Loop with guard | N | back edge from a router route (e.g. `(route_headline, {"unrelated": generate_headline})`); guard = counter in state + route to exit; `max_concurrency` for parallel limits |
| Subgraph | N | nested `Workflow` used as a node |
| Node retry / timeout | N | `RetryConfig`; timeouts raise `NodeTimeoutError` |
| Shared state | N | `ctx.state[...]`, `Event(state={…})`, `output_key`, parameter injection by state key; `{key}` / `{key?}` templating in instructions |
| Reducers | E | generated function nodes merge values (append/merge/sum) before writing state |
| Sub-agent / transfer | N | `sub_agents=[…]` + `transfer_to_agent`; `disallow_transfer_to_parent/peers` |
| Remote agent | N | `RemoteA2aAgent` |
| HITL tool approval | N | `FunctionTool(func, require_confirmation=True | callable)`; response carries `ToolConfirmation` |
| HITL input node | N | node yields `RequestInput(message=…)`; resumed with user input as next `node_input`; `Workflow(rerun_on_resume=True)` |
| Conversation state | N | `SessionService`: `InMemorySessionService`, `SqliteSessionService`, `DatabaseSessionService` (SQLAlchemy URL → Postgres/MySQL), `VertexAiSessionService` |
| Long-term memory | N | `MemoryService`: in-memory, SQLite, `VertexAiMemoryBankService`, `VertexAiRagMemoryService`; or platform Memory Spaces (E) |
| Artifacts / files | N | Artifact service (dialect binding) |
| Guardrails / middleware | N | callbacks + `plugins` (`BasePlugin`; e.g. reflect-retry, context filter, logging) |
| Streaming | N | runner event stream |
| Tracing | N | OTel GenAI semconv built in |

## 4. Dialect nodes (`adk.*`)
`adk.llm_agent` (full option set incl. `mode chat|task|single_turn`, `include_contents`, `static_instruction`, planner), `adk.sequential_agent`, `adk.parallel_agent`, `adk.loop_agent` (classic agent-composition layer), `adk.code_executor`, `adk.plugin.*`, `adk.memory_service`, `adk.artifact_service`, `adk.remote_a2a_agent`, `adk.join`, `adk.google_search` & other built-in tools (as available in the installed version).

## 5. Code generation (router + loop + approval — mirrors verified samples)

```python
from google.adk import Agent, Event, Workflow
from google.adk.events import RequestInput
from google.adk.tools import FunctionTool

# ── @canvas-node n_classify ──
classify = Agent(name="classify", model="gemini-3.6-flash",
                 instruction="Classify the ticket: {ticket_text}",
                 output_schema=Classification, output_key="classification")

# ── @canvas-node n_route ──
def route_ticket(classification: Classification):
    yield Event(route=classification.category)

# ── @canvas-node n_refund (tool with approval from interrupt_on) ──
refund_tool = FunctionTool(issue_refund, require_confirmation=True)
billing = Agent(name="billing", model="gemini-3.6-pro", instruction=BILLING_PROMPT, tools=[refund_tool])

# ── @canvas-node n_review (Human input) ──
def request_review(draft: str):
    yield RequestInput(message=f"Approve this reply?\n---\n{draft}")

root_agent = Workflow(
    name="support_triage",
    edges=[
        ("START", ingest, classify, route_ticket),
        (route_ticket, {"billing": billing, "tech": tech_agent}),
        (billing, draft_reply, request_review, handle_review),
        (handle_review, {"revise": draft_reply, "approved": send_reply}),
    ],
)
```

## 6. HITL
- Tool approval: `require_confirmation` → canonical `tool_approval`; resume by delivering the function response containing a `ToolConfirmation` (confirmed/payload) into the same session.
- Input node: `RequestInput` → canonical `input` with `response_schema` from the node's declared input; resume by sending the human response; `rerun_on_resume` controls node re-execution (default `True`) — the adapter sets it per node to preserve side-effect correctness (doc 08 §5.5).
- Cross-process: session events are the durable log; `ResumeEnvelope.native_state = {app_name, user_id, session_id, pending_ids}`.

## 7. State & persistence
- Thread ↔ `session_id` (+ `user_id` from authenticated identity, `app_name` = workflow slug).
- Bindings: Postgres/MySQL → `DatabaseSessionService(db_url)`; dev → `SqliteSessionService`; GCP → `VertexAiSessionService`. Memory Spaces via platform tools, or `MemoryService` dialect binding.
- IR state channels ↔ session `state` keys (namespaced `ac:<channel>` to avoid collisions with user keys; ADK state prefixes such as `user:`/`app:` exposed as scope options).

## 8. Events & tracing
- Reuse AG-UI **`adk-middleware`** integration for text/tool/state events; add node mapping from workflow node names (`slug__canvasid`) and `ac.*` extension events (interrupts, checkpoints = session event ids, usage).
- OTel: ADK emits GenAI semconv spans; generated code adds `agentcanvas.node_id` via a small `BasePlugin` (`AgentCanvasPlugin`) that also emits canonical events in Mode B.

## 9. Hosting
- Mode A: `deployments-wrap-sdk` `wrap(Runner(...))` + `LangsmithSessionService`, or our generic `@entrypoint` wrapper.
- Mode B: `adk deploy agent_engine | cloud_run | gke | docker` driven by the Deploy service; `AgentCanvasPlugin` posts canonical events; HITL through ADK APIs.
- Mode C: ADK A2A server exposure; `RemoteA2aAgent` for consuming.

## 10. Import / round-trip
- **ADK YAML `AgentConfig`** (JSON Schema in `agents/config_schemas/AgentConfig.json`) ↔ Core IR for the agent layer (lossless for supported fields).
- `Workflow(edges=…)` Python import via AST (edge tuples and route dicts are statically analysable); function bodies become Script nodes.

## 11. Gaps & emulations
Reducers (E, generated merges); IR `Send` with arbitrary payloads (mapped to `parallel_worker` over a generated list; per-item payload must be the item); breakpoints implemented as injected `RequestInput`-style debug pauses in dev mode only.

## 12. Work plan (squad B, ~24 weeks)
| Weeks | Deliverable |
|------|-------------|
| 1–2 | Verification refresh (incl. TS/Java parity notes), capability sign-off |
| 3–5 | Dialect nodes; ToolSpec/ModelSpec factories; YAML AgentConfig import/export |
| 5–11 | Workflow lowering (sequence, routes, JoinNode, parallel_worker, loops, nested), state bridge, emulated reducers, source maps |
| 10–13 | HITL (`require_confirmation`, `RequestInput`), envelope, Inbox |
| 12–14 | Session/memory bindings (DatabaseSessionService on Postgres) |
| 13–16 | Events: adk-middleware reuse + AgentCanvasPlugin; OTel mapping |
| 16–19 | Hosting A & B (Agent Engine, Cloud Run) |
| 18–22 | Conformance (full T1), templates (Triage, Research fan-out, Approval loop) |
| 22–24 | Beta & certification |

## 13. Risks
`workflow` package is new in 2.x and evolving (pin minor; nightly CI); feature parity differs across ADK languages (Python first); Vertex-specific services require GCP credentials (bindings optional).
