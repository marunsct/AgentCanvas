# 14 — Deep Review Report (v0.5 → v0.6)

> **Historical:** This is the deep review from spec v0.5 to v0.6 (2026-09); its decisions became ADRs 0012–0017. What is open now, after the reviews of 2026-10-18 and 2026-11-08, is in [hardening.md](../hardening.md) and what changed in the [changelog](https://github.com/marunsct/AgentCanvas/blob/main/CHANGELOG.md).

**Date:** 2026-09-29 · **Scope:** docs 00–13, adapters/, build-guide/ and the reference implementation · **Goal:** re-verify every claim against the *current* LangChain ecosystem, close the gaps a real build exposes, and deepen the product concept (doc [15](../spec/15-concept-evolution-and-new-features.md)) before sprint execution starts ([build-guide/13](./sprint-plan-and-execution-log.md)).

---

## 1. How this review was done

1. **Versions re-checked on PyPI** (2026-09-29) and a clean environment installed with the extras the spec needs:

   | Package | Spec (v0.5) | Latest (verified) | Note |
   |---------|-------------|-------------------|------|
   | `langgraph` | 1.2.12 | **1.2.12** | unchanged |
   | `langchain` | 1.4.2 | **1.4.3** (2026-09-28) | patch; bump stack pin |
   | `langchain-core` | 1.6.5 | 1.6.5 | unchanged |
   | `deepagents` | 0.7.19 | 0.7.19 | extras: `quickjs`, `aws`, `video` |
   | `langsmith` | — (not pinned) | **0.14.1** | now ships the `langsmith.sandbox` SDK |
   | `langgraph-sdk` / `langgraph-api` / `langgraph-cli` | — | 0.4.5 / 0.15.1 / 0.4.32 | event streaming needs api ≥ 0.10, sdk ≥ 0.4 |
   | `langgraph-checkpoint-postgres` / `-redis` / `-sqlite` | 3.1.2 / 0.5.2 / — | 3.1.2 / 0.5.2 / 3.1.1 | unchanged |
   | `langchain-quickjs` | "≥ 0.2" | **0.3.7** (required by `deepagents[quickjs]`) | spec pin was wrong |
   | `langchain-mcp-adapters` | superseded | 0.3.2 | replaced by `langchain.mcp` (LangChain 1.4) |
   | `langmem` | referenced by docs 09/10 | 0.0.30 — **last release 2025-10-27** | stale ≈ 11 months — see V-08 |
   | `fastapi` / `cel-python` | 0.141 / 0.5.0 | 0.141.1 / 0.5.0 | unchanged |

2. **Official documentation re-read at source**: `langchain-ai/docs` at commit `1a30368` (2026-09-28): LangGraph (event streaming, fault tolerance, persistence, Pregel/DeltaChannel, functional API), LangChain (agents, middleware, MCP, frontend patterns), Deep Agents (backends, subagents, dynamic subagents, interpreters, rubric, profiles, ACP/A2A/AG-UI, multimodal), LangSmith (Agent Server, graph rebuild/`ServerRuntime`, event streaming API, sandboxes + auth proxy, Engine, decision models, LLM Gateway, Fleet, Managed Deep Agents).
3. **Every constructor/function the spec names was introspected** (`inspect.signature`) and the risky behaviours were **executed** (timeouts on sync nodes, `interrupt()` inside `asyncio.to_thread`, v3 event streaming, MCP import).
4. **The reference implementation's 20 tests were re-run** in a clean `uv sync`: all pass. Its design was then read against the new findings.

## 2. Summary

| Category | Found | Resolution |
|----------|------:|-----------|
| Factual / version errors | 4 | fixed in docs (V-01, V-02, V-09, §4 C-08) |
| New runtime pitfalls (verified by execution) | 4 | documented + handled in the product code (V-03, V-04, V-06, V-10) |
| Newly-verified capabilities that change a decision | 2 | ADRs 0013/0014 (V-05, V-08) |
| Upstream features the spec did not use | 27 | mapped to requirements in doc 15 (V-07) |
| Security gaps | 3 | new validator rules E041, E042, W074 (V-11) |
| Contradictions / process issues | 5 | §4 |

## 3. Findings

### 3.1 Verified errors and pitfalls

| ID | Finding (verified) | Impact | Resolution |
|----|-------------------|--------|-----------|
| **V-01** | `from langchain.mcp import MCPAdapter` fails with `ModuleNotFoundError: fastmcp` unless LangChain is installed with the **`mcp` extra** (`langchain[mcp]`, pulls `fastmcp>=4,<5`). The namespace is **beta** and emits `LangChainBetaWarning`. `MCPAdapter(target)` accepts an http(s) URL string, a `Path` (spawns a **local stdio subprocess**), an `MCPConfig` dict (`{"mcpServers": …}`), a FastMCP server/client or a transport. | MCP node unusable in a default install; stdio targets are a code-execution vector. | Stack pins `langchain[mcp]`; the runtime filters the beta warning once and records it in diagnostics (`I010 beta API in use`). Stdio/`Path` targets are **admin-gated** (new rule **E042**) and only run inside a sandbox in the hosted edition. |
| **V-02** | `CodeInterpreterMiddleware` is exported by **`langchain_quickjs`** and requires `langchain-quickjs>=0.3.7` via `deepagents[quickjs]`; doc 03 §6.4 said `>=0.2`. It is still **experimental**. | Wrong pin → resolver conflict. | Doc 03 corrected; interpreter capability marked *experimental* in the node manifest. |
| **V-03** | **Per-node timeouts only work on `async` nodes.** `add_node(sync_fn, timeout=…)` raises `ValueError: Node timeouts are only supported for async nodes…`. The reference node library returns sync callables for approval, set, router and script nodes. | Setting a timeout policy on those nodes crashes graph build. | **All node-library builders return `async` callables.** Sync user functions (scripts) are wrapped with `await asyncio.to_thread(fn, state)` — verified: `interrupt()`, `get_config()` and `get_store()` keep working because `to_thread` copies the context. |
| **V-04** | v3 event streaming (`astream_events(version="v3")`) still warns *"The v3 streaming protocol on Pregel is experimental"* in 1.2.12; in async code `stream.output` is a **method** (`await run.output()`), not the awaitable property shown in the docs. Agent Server event streaming is **Beta** and needs `langgraph-api>=0.10`, `langgraph-sdk>=0.4`. | Adopting v3 now couples the UI protocol to a moving API. | Keep `astream(..., version="v2")` (review P-05 stands). Canonical events (doc 12 §C.3) stay the contract, so switching the producer to v3 later is an adapter-internal change. Re-evaluate when the warning is removed (tracked as spike **SP-3** in the sprint plan). |
| **V-06** | **`DeltaChannel` has a different reducer contract**: `reducer(state, [write1, write2, …])` receives *batches* and must be deterministic and batching-invariant. **Switching a persisted channel between delta and non-delta is unsupported** (existing threads become unreadable). Deep Agents ≥ 0.6 already stores messages and files as delta channels, so **downgrading deepagents across 0.6 is unsafe** once threads exist. | FR-STATE-01 listed `DeltaChannel` as just another reducer; a user toggling it on a published workflow would corrupt live threads. | `DeltaChannel` becomes a **channel storage mode** (`storage: "delta" | "full"`) orthogonal to the reducer, allowed only with batch-safe built-in reducers (`append`, `add_messages`) until custom batch reducers are supported. New publish-time rule **E074**: changing a channel's storage mode (or removing a delta channel) while the previous version has live threads is blocking. The stack-upgrade assistant refuses deepagents downgrades across a delta boundary. |
| **V-10** | Reference runner streams the graph **inside the HTTP response generator**; a browser disconnect cancels the run mid-step. It also keeps `thread → workflow` in memory, and allows the `examples.` import prefix everywhere. | Lost runs on flaky networks; restart breaks resume; demo code importable in prod. | Product runner uses the queue + background task design of build-guide/05 §5 (run survives disconnect), stores threads in the DB, and allows `examples.` only when `AGENTCANVAS_ENV=dev`. |

### 3.2 Newly verified capabilities that change a decision

| ID | Finding | Decision |
|----|---------|----------|
| **V-05** | **Graph factories on Agent Server are now officially documented** ("Rebuild graph at runtime"): a factory may be an `async def make_graph(config, runtime: ServerRuntime)` or an **async context manager**; `runtime.execution_runtime` distinguishes real execution from introspection (schemas, drawing); `runtime.ensure_user()` exposes the authenticated user. `ServerRuntime` is **beta** (`langgraph-api>=0.7.31`, `langgraph-sdk>=0.3.5`). This resolves review F-03 ("factory not verified"). | **ADR 0013:** the *LangSmith Deployment* publish target may host **interpreted mode** — the factory loads the pinned IR from the assistant's config and calls the same `build_graph` the runner uses. Benefits: publish without regenerating code, per-user tool loading (MCP per-user OAuth), and identical semantics to canvas runs. Compiled export remains the portable default. |
| **V-08** | `langmem` has had **no release since 2025-10-27** while docs 09/10 build the learning ladder on "LangMem-style memory managers and prompt optimisers". | **ADR 0014:** AgentCanvas does not take a hard dependency on `langmem`. Memory extraction, consolidation and prompt optimisation are implemented as **AgentCanvas learning-loop workflows** built from supported primitives (`create_agent` + structured output + `BaseStore` + LangSmith datasets/prompts). A `langmem` adapter may be offered as optional. |

### 3.3 Upstream features the spec did not use (V-07)

All verified by import/signature or official docs. Where each lands is specified in doc 15 (FR-X-…).

| # | Upstream capability | Where it lands |
|---|--------------------|----------------|
| 1 | `add_node(trace_policy=TracePolicy(process_inputs=…, process_outputs=…))`, helper `omit_payload`, `configure_trace_policy()` — per-node trace shaping (not secret redaction) | FR-X-07 per-node trace policy |
| 2 | `compile(transformers=[…])`, `create_agent(transformers=…)` — custom stream transformers → `stream.extensions` projections (v3) | FR-X-09 custom stream projections (P2, after v3 is stable) |
| 3 | `TimeoutPolicy(refresh_on="auto"|"heartbeat")` + `Runtime.heartbeat` — idle timeouts refreshed by long-running scripts | Node policy UI, script SDK `runtime.heartbeat()` |
| 4 | `Runtime.execution_info`, `Runtime.server_info`, `Runtime.previous`, `Runtime.control` / `drain_requested` | Script SDK context object |
| 5 | `StateGraph.set_node_defaults(...)` (graph-wide retry/timeout/error handler) | Workflow settings → `node_defaults` |
| 6 | `HumanInTheLoopMiddleware(description_prefix=…, edit_notice=…)` | Approval widget advanced settings |
| 7 | `PIIMiddleware(detector=<regex or callable>)` — custom PII types | PII guard: custom detector field |
| 8 | `SummarizationMiddleware(trigger=[…TriggerClause…], keep=…, trim_tokens_to_summarize=…)` | Summarization widget |
| 9 | `ToolCallLimitMiddleware(exit_behavior="continue"` default), `ModelCallLimitMiddleware(exit_behavior="end"` default) | Correct defaults in manifests |
| 10 | `ToolErrorMiddleware(on_error=…, tools=[…])` | Tool error widget |
| 11 | Deep Agents `SubAgent.mode="fork"` (**experimental**) — the spec's "forked subagent" | FR-AGENT-04 wording fixed |
| 12 | Deep Agents `SummarizationToolMiddleware` / `create_summarization_tool_middleware` — agent-invoked compaction tool | Deep Agent capability toggle |
| 13 | Deep Agents `LocalShellBackend(root_dir, virtual_mode=True, …)` | Dev-only backend (rule E041 forbids in hosted edition) |
| 14 | Deep Agents `LangSmithSandbox(sandbox)` backend; `CompositeBackend(artifacts_root=…)` | Sandbox backend options |
| 15 | Deep Agents `FilesystemMiddleware` tool allow-list; new `delete` tool; multimodal `read_file` (PDF, audio, video, images) | Deep Agent filesystem panel; FR-X-16 multimodal |
| 16 | `deepagents-acp` — expose a Deep Agent over the **Agent Client Protocol** to IDEs | FR-X-11 IDE exposure |
| 17 | Dynamic subagents (beta, via interpreters) | stays P2 (as planned) |
| 18 | `langsmith.sandbox` SDK: `SandboxClient`, snapshots, **mounts** (S3/GCS/Git/Context Hub), **auth proxy** (secret injection into outbound HTTPS + egress allow/deny lists), service URLs/tunnels | FR-X-08 sandbox policy designer; hosted script runner option |
| 19 | LangSmith **Engine** — clusters production-trace issues, proposes fix PRs, creates dataset examples, red-teaming (beta) | FR-X-06 Engine bridge |
| 20 | LangSmith **decision models** (SemIf via LLM Gateway; TypeSafe Jev) — typed answers (Noul probability / Choice / Score) as online evaluators or direct calls | FR-X-05 decision-model nodes |
| 21 | LLM Gateway policies: model access, spend, rate-limit, header, fallbacks, credits | FR-GOV-03 implementation path (N-08 promoted) |
| 22 | Agent Server: custom routes / middleware / lifespan, stateless & background runs, join/rejoin stream, workload isolation, double-texting modes | Publish target options; runner parity list |
| 23 | Frontend patterns: `useStream` **headless tools** (tool runs in the browser; server side `interrupt()`), **declarative generative UI** (component catalog; json-render / A2UI), branching chat, message queues, join/rejoin, reasoning tokens | FR-X-03 headless tools, FR-X-04 generative UI designer |
| 24 | **LangSmith Fleet** (formerly Agent Builder) — no-code agents from templates | Positioning (doc 15 §4) |
| 25 | Managed Deep Agents: channels (HTTP/Slack), schedules, identity, connections, MCP endpoint, agent-owned interrupts | MDA target mapping (doc 03 §6.5) |
| 26 | SmithDB trace store & SDK migration; Insights; multi-turn simulation; Harbor integrations | Eval Studio & observability backlog |
| 27 | Stream v3 `lifecycle` channel (`started/running/completed/failed/interrupted` with `cause` linking a subagent to its dispatching tool call) | Canonical event mapping for nested canvases (when v3 adopted) |

### 3.4 Security

| ID | Gap | New control |
|----|-----|-------------|
| **V-11a** | `LocalShellBackend` executes shell commands on the host. | **E041** error: `LocalShellBackend`/`FilesystemBackend` in a workflow bound to a hosted environment. |
| **V-11b** | `MCPAdapter` with a `Path` or stdio transport spawns a subprocess on the runner host. | **E042** error unless the project setting `allow_local_mcp` is on (admin) *and* execution is sandboxed. |
| **V-11c** | LangSmith sandboxes default to **allow-all egress** when no `access_control` is set. | **W074** warning: sandbox binding without an egress allow-list; the sandbox policy designer defaults to `["*:443"]` + declared hosts. |

## 4. Contradictions and process issues

| ID | Issue | Resolution |
|----|-------|-----------|
| C-08 | 01 FR-AGENT-04 calls it a "Forked subagent (inherits parent context)"; the real API is `SubAgent(mode="fork")`, **experimental**. | Wording aligned; P1 retained, flagged experimental. |
| C-09 | build-guide/02 §1 says "create a **new** repository" for the product; this repository is where the product is being built. | **ADR 0012:** single repository — spec (`docs/`) and product (`apps/`, `packages/`, `infra/`) live together so that spec changes and code land in the same PR. |
| C-10 | Two plans exist (doc 06 phases and build-guide/04 milestones) and neither is time-boxed. | [build-guide/13-sprint-plan.md](./sprint-plan-and-execution-log.md) is the executable schedule: 2-week sprints mapped onto M0–M9; doc 06 and build-guide/04 remain the *what*. |
| C-11 | build-guide/04 lists M6-T9 before M6-T8. | Order clarified in the sprint plan (T9 in Sprint 8, T8 = release hardening in Sprint 9). |
| C-12 | 01 lists journey J6 before J5. | Cosmetic; left as is to keep IDs stable. |

## 5. Decisions recorded in this revision

| ADR | Decision |
|-----|----------|
| 0012 | One repository for spec + product (C-09). |
| 0013 | LangSmith Deployment target can host interpreted mode through a `ServerRuntime` graph factory (V-05). |
| 0014 | No hard dependency on `langmem`; learning loops use core primitives (V-08). |
| 0015 | Node-library callables are always `async`; sync user code runs via `asyncio.to_thread` (V-03). |
| 0016 | `DeltaChannel` is a channel *storage mode*, not a reducer; representation changes on live workflows are blocked (V-06). |
| 0017 | Stack `stack-2026.09`: `langgraph 1.2.12`, `langchain[mcp] 1.4.3`, `langchain-core 1.6.5`, `deepagents 0.7.19`, `langsmith 0.14.1`, Python 3.12 (V-09). |

## 6. What changed in the documents

- **New:** this report; [15-concept-evolution-and-new-features.md](../spec/15-concept-evolution-and-new-features.md) (deepened WYSIWYG concept, language decision revisited, positioning, 24 new requirements FR-X-01…24, events-as-code for Workspaces); [build-guide/13-sprint-plan.md](./sprint-plan-and-execution-log.md).
- **Updated:** README (v0.6, doc map, decisions D14–D16, feature list 21–30); 01 (FR-AGENT-04, FR-STATE-01, FR-PER-05 wording); 03 (async node rule, `DeltaChannel` storage mode, v3 status, `ServerRuntime` factory, quickjs pin, new diagnostics); 04 (new widgets, corrected middleware defaults); 05 (LangSmith sandbox runner option, heartbeat, events-as-code pointer); build-guide/01 (versions); build-guide/11 (new FAQ entries).

## 7. Remaining known limitations

- Items tagged *beta/experimental* upstream (MCP namespace, `ServerRuntime`, v3 streaming, `DeltaChannel`, `mode="fork"`, dynamic subagents, interpreters) are used only behind capability flags with a stable fallback.
- LangSmith Engine, decision models and Fleet are SaaS features; self-hosted availability differs and is checked at connection time.
- Pricing/availability statements about SaaS features (e.g. SemIf's free period ended 2026-09-28) are time-sensitive and must be re-checked before GA.
