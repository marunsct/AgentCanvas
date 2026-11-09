# Tier-3 Wrapped & Remote Agents (F1)

Tier 3 lets users drop agents from **any** framework into a LangChain (or other T1) workflow as a black-box node, before a full adapter exists. Delivered in F1 (months 0–3 after R1).

## 1. Node types

| Node | Input | Runtime mechanism | HITL |
|------|-------|-------------------|------|
| **Remote A2A Agent** | Agent Card URL, auth | A2A 1.0 client (`a2a-sdk` 1.1.x); streaming task updates → canonical events | task state `input-required` / `auth-required` → canonical interrupt; resume by sending a follow-up message on the task |
| **Remote MCP Agent** | MCP server URL + tool name | MCP client (`langchain.mcp` `MCPAdapter`) | MCP elicitation → interrupt (native in `langchain.mcp`) |
| **Wrapped Python Agent** | Workspace reference `module:object` + framework kind | Generated LangGraph `@task` calling the framework runner; hosted in the parent graph | framework-specific (table below) |

## 2. Framework wrappers (verified entrypoints)

| Framework | Wrapper call | Streaming | HITL in wrapper |
|-----------|-------------|-----------|-----------------|
| Claude Agent SDK 0.2.160 | `ClaudeSDKClient(options=ClaudeAgentOptions(...))` / `query(...)`; options incl. `allowed_tools`, `disallowed_tools`, `permission_mode`, `can_use_tool`, `hooks`, `agents`, `mcp_servers`, `resume`/`session_id`, `max_turns`, `max_budget_usd` | message stream → text/tool events (AG-UI `claude-agent-sdk` integration reusable) | **N**: `can_use_tool` async callback awaits the platform decision (in-process wait with timeout; long waits end the run and resume via `resume=<session_id>`) |
| OpenAI Agents SDK | `Runner.run(agent, input, session=…)` | `run_streamed` | N: `RunState` envelope (same as full adapter) |
| Google ADK | `deployments-wrap-sdk` `wrap(Runner(agent=…))` or `Runner.run_async` | runner events | N: `require_confirmation` / `RequestInput` |
| CrewAI | `crew.kickoff_async(inputs=…)` / `flow.kickoff_async` | event bus listener | N: flow `human_feedback` → `HumanFeedbackPending` |
| Strands | `agent.invoke_async(...)` / `stream_async` | async iterator | N: interrupts |
| Pydantic AI | `agent.run(...)` | `run_stream_events` | N: deferred tools |
| MAF | `agent.run(...)` / `workflow.run(...)` | workflow events | N: `request_info`, approvals |
| LlamaIndex | `workflow.run(...)` | `stream_events()` | N: `InputRequiredEvent` |
| Generic callable | `async def run(input: dict, ctx) -> dict` | optional custom events | E: raise `AgentCanvasInterrupt` |

## 3. Contracts
- Inputs/outputs mapped with explicit **data edges** and JSON Schemas (validated at runtime).
- Minimum canonical events: `RUN_*`, `STEP_*` (one step = the whole wrapped agent), text & tool events when the framework streams them.
- Security: wrapped code runs in the same sandbox policy as script nodes; powerful built-in tools (Claude Agent SDK Bash/Edit/Write, OpenAI Shell/ApplyPatch) require an admin-enabled sandbox binding and default to approval.

## 4. Plan (F1, squad of 3, ~12 weeks)
A2A node (1–4) · MCP agent node (3–5) · generic wrapper + Claude Agent SDK + OpenAI + ADK wrappers (4–9) · CrewAI/Strands/Pydantic AI wrappers (8–11) · conformance C-AGENT/C-EVENTS subset & docs (10–12).
