# Adapter Spec — OpenAI Agents SDK

## 1. Verified baseline
- Package `openai-agents` **0.22.3** (MIT), Python ≥ 3.10. Pre-1.0 → patch-level pin (`>=0.22.3,<0.23`).
- Verified in source: `src/agents/agent.py` (`Agent` dataclass), `tool.py` (`function_tool`, `needs_approval`), `run_state.py` (`RunState`), `memory/` & `extensions/memory/` (sessions), `stream_events.py`, `lifecycle.py` (`RunHooks`, `AgentHooks`), `mcp/server.py`, `tracing/`, `sandbox/`, `docs/human_in_the_loop.md`.

## 2. Positioning
Agent-centric SDK: agents, tools, handoffs, guardrails, sessions, tracing. **No workflow/graph primitive.** The adapter is Tier 1 for the *Agent tier* and Tier 2 for the *Graph tier* (graph compiled to generated async orchestration code). Chosen as the first external adapter because its small surface hardens the SPI quickly.

## 3. Core IR mapping

| Core node / concept | Level | Generated construct |
|---------------------|-------|---------------------|
| `core.agent` | N | `Agent(name=…, instructions=…, model=…, model_settings=ModelSettings(…), tools=[…], mcp_servers=[…], output_type=…, input_guardrails=[…], output_guardrails=[…], hooks=…)` run by `Runner.run` / `Runner.run_streamed` |
| `core.model` | N | model string or `Model`; non-OpenAI providers via LiteLLM extension or the model gateway |
| Function tool | N | `@function_tool(needs_approval=…)` |
| MCP server | N | `MCPServerStreamableHttp` / `MCPServerSse` / `MCPServerStdio`; OpenAI-hosted: `HostedMCPTool` |
| Structured output | N | `output_type=PydanticModel` |
| Sub-agent (delegate) | N | `sub_agent.as_tool(tool_name=…, tool_description=…, needs_approval=…)` |
| Handoff | N | `handoffs=[agent_b, handoff(agent_c, …)]` |
| Guardrails | N | `@input_guardrail` / `@output_guardrail` functions (dialect nodes) |
| Sequence / router / parallel / map / loop / subgraph | E | generated `async def orchestrate(...)` using `await Runner.run(...)`, `asyncio.gather`, Python branching on typed outputs; each block wrapped with canonical node events |
| Shared state | E | `RunContextWrapper.context` (a generated dataclass mirroring IR state) + orchestration variables; emulated reducers |
| HITL tool approval | N | `needs_approval` + `RunState` approve/reject |
| HITL input node | E | orchestration pauses via envelope (`pending.kind="input"`) |
| Conversation memory | N | `SQLiteSession`, `SQLAlchemySession`, `RedisSession`, `MongoDBSession`, `DaprSession`, encrypted session wrapper |
| Long-term memory | E | platform Memory Space tools |
| Streaming | N | `Runner.run_streamed(...).stream_events()` |
| Tracing | N | `add_trace_processor(OtelBridgeProcessor())` |

## 4. Dialect nodes (`openai.*`)
`openai.guardrail.input`, `openai.guardrail.output`, `openai.handoff` (with input filter), `openai.hosted_tool` (web search, file search, code interpreter, image generation — as supported by the SDK), `openai.hosted_mcp`, `openai.sandbox_agent` (admin-gated; requires sandbox binding), `openai.tool_use_behavior` (stop on first tool / stop at tools), `openai.session` binding.

## 5. Code generation (graph tier example: router → specialist → approval)

```python
# ── @canvas-node n_cls "Classify" (core.agent → openai) ──
classifier = Agent(name="classify", instructions=CLASSIFY_PROMPT, model="gpt-5-mini", output_type=Classification)

# ── @canvas-node n_bill "Billing agent" ──
billing = Agent(name="billing", instructions=BILLING_PROMPT, tools=[refund], model="gpt-5.5")

@function_tool(needs_approval=True)          # @canvas-node n_refund (approval from interrupt_on)
async def refund(order_id: str, amount: float) -> str: ...

async def orchestrate(inp: Input, ctx: RunCtx, emit: Emitter, resume: ResumeState | None):
    # generated state machine: each canvas node is a resumable step (step index saved in the envelope)
    if resume is None or resume.step <= 0:
        with emit.node("n_cls"):
            r = await Runner.run(classifier, inp.text, context=ctx, session=ctx.session)
            ctx.state.category = r.final_output.category
    if ctx.state.category == "billing":                                  # @canvas-node n_route
        with emit.node("n_bill"):
            r = await Runner.run(billing, resume.state if resume else inp.text, context=ctx, session=ctx.session)
            if r.interruptions:                                          # HITL pause
                return Paused(step=1, native=r.to_state().to_json(), pending=to_canonical(r.interruptions))
```

## 6. HITL
- Raise: tools with `needs_approval` (bool or async callable `(ctx, params, call_id) -> bool`); `Agent.as_tool(needs_approval=…)`; hosted MCP `require_approval`.
- Pause: `result.interruptions` → canonical `InterruptRequest(kind="tool_approval")` per `ToolApprovalItem`.
- Envelope: `result.to_state().to_json()` stored in `ResumeEnvelope.native_state` (cross-process safe).
- Resume: `RunState.from_json(...)` (or `from_string`) → `state.approve(item)` / `state.reject(item, rejection_message=…)` → `Runner.run(top_agent, state)`.
- *Edit* decision: **emulated** = reject with a message containing the corrected arguments and a system note instructing the model to re-issue the call (conformance case C-HITL-edit validates behaviour); marked `P002`.
- Sticky decisions: `always_approve` / `always_reject` exposed as Inbox "approve for this thread".

## 7. State & persistence
- Thread ↔ `session_id`; session backend bound per environment: Postgres → `SQLAlchemySession`, Redis → `RedisSession`, Mongo → `MongoDBSession`, dev → `SQLiteSession`; encryption via the encrypted session wrapper when FR-PER-06 is on.
- Orchestration step pointer + context state stored in the envelope (Mode A: `entrypoint.final(save=…)`).

## 8. Events & tracing
- Translator maps `RawResponsesStreamEvent` (token deltas → `TEXT_MESSAGE_*`), `RunItemStreamEvent` (tool call/output, handoff, message items → `TOOL_CALL_*`, `ac.handoff`), `AgentUpdatedStreamEvent` (→ `STEP_*` for agent nodes) and generated orchestration spans (→ `STEP_*` for graph nodes).
- `RunHooks` (`on_agent_start/end`, `on_tool_start/end`, `on_handoff`, `on_llm_start/end`) used for usage/cost events.
- Tracing: custom `TracingProcessor` exporting spans to OTel with `agentcanvas.node_id`; option to keep OpenAI tracing on/off per workspace policy (`set_tracing_disabled`).

## 9. Hosting
Mode A (Agent Server `@entrypoint` wrapper, doc 12 §C.7); Mode C (A2A server wrapper generated for exposure). No first-party managed runtime → no Mode B.

## 10. Import
Python AST import of `Agent(...)` definitions and handoff graphs (agent tier) → Core IR; orchestration code becomes Script nodes. Agent Spec import via pyagentspec `openaiagents` loader as fallback.

## 11. Gaps & emulations
Graph tier (E), shared state & reducers (E), input-form HITL (E), edit decision (E), long-term memory (E). No native checkpoints mid-run beyond `RunState` (resumes happen at interruption boundaries only) → breakpoints implemented as orchestration-level pauses between canvas nodes (T1 agent tier requirement satisfied; mid-agent breakpoints unsupported).

## 12. Work plan (squad A, ~14 weeks)
| Weeks | Deliverable |
|------|-------------|
| 1 | Verification refresh, capability matrix sign-off |
| 2–3 | Dialect nodes & forms; ToolSpec/ModelSpec factories |
| 4–7 | Agent-tier lowering/codegen + orchestration generator (graph tier) + source maps |
| 6–8 | Event translator, OTel processor, usage events |
| 8–9 | HITL envelope & resume; Inbox integration |
| 9–10 | Session bindings (Postgres/Redis/Mongo) |
| 10–11 | Mode A hosting; A2A exposure |
| 11–13 | Conformance suite, templates (Triage, Handoff support desk, Research-lite) |
| 13–14 | Beta, certification |

## 13. Risks
Pre-1.0 churn (patch pins, nightly CI); emulated edit semantics (clearly labelled); graph-tier code is generated orchestration — idiomatic but not a framework primitive (documented).
