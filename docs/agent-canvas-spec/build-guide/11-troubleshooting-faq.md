# 11 — Troubleshooting & FAQ

Every entry marked ✅ was reproduced during the review with the pinned versions (01 §1).

## A. LangChain / LangGraph

**P-01 ✅ `NotImplementedError` from `bind_tools` in tests.**
LangChain's `GenericFakeChatModel` does not implement tool binding, and agents call `bind_tools()`. Use `agentcanvas.testing.ToolFakeModel` (overrides `bind_tools` to return itself).

**P-02 ✅ `ValueError: No generations found in stream.`**
Happens when the run streams tokens (`stream_mode` contains `"messages"`) and a fake model returns a message that has only tool calls (no text). Create fakes with `disable_streaming=True` (done by `agentcanvas.testing.scripted`).

**P-03 ✅ Approvals repeat forever / the agent re-asks after I approved (tests & fake server).**
Scripted fakes are stateful. If the resume builds *new* fake models, the script restarts and the model re-issues the tool call. Give each thread its own `Resources` (`resources_factory(thread_id)`). Real models are stateless and not affected.

**P-04 ✅ `RedisSearchError … unknown command 'FT.INFO'`.**
`langgraph-checkpoint-redis` needs RediSearch + RedisJSON. Use **Redis 8** (modules built in) or Redis Stack, not Redis 7. Check with `redis-cli MODULE LIST`.

**P-05 ✅ `LangChainBetaWarning: The v3 streaming protocol on Pregel is experimental.`**
Use `graph.astream(..., version="v2")` (stable typed parts `{"type","ns","data"}`) as the reference does. Revisit v3 when it is stable.

**P-06 ✅ Code before `interrupt()` runs twice.**
On resume LangGraph re-runs the interrupted node from the start; `interrupt()` then returns the resume value. Move side effects (emails, DB writes, payments) into a node **after** the approval node. Validator rule E070 flags risky patterns.

**P-07 ✅ `GraphRecursionError: Recursion limit of N reached without hitting a stop condition`.**
A loop has no exit or the router never takes it. Add a counter + router exit (tutorial Part 10) or raise `recursion_limit` in the run config for legitimately long workflows.

**P-09 ✅ `ValueError: Node timeouts are only supported for async nodes …` (v0.6).**
LangGraph cancels timed-out attempts with asyncio, so `timeout=` only works on `async def` nodes. Every AgentCanvas node builder returns an async callable; wrap sync user code with `await asyncio.to_thread(fn, state)` — `interrupt()`, `get_config()` and `get_store()` still work inside it (context is copied).

**P-10 ✅ `ModuleNotFoundError: No module named 'fastmcp'` when importing `langchain.mcp` (v0.6).**
Install LangChain with the MCP extra: `uv add "langchain[mcp]"`. The namespace is beta and emits `LangChainBetaWarning` on import.

**P-11 ✅ `TypeError: object method can't be used in 'await' expression` with v3 event streaming (v0.6).**
In async code the v3 run stream's `output` is a method: `await run.output()`. (The docs' sync examples use a property.) The product keeps v2 until v3 is stable (P-05).

**P-12 ✅ Old threads become unreadable after changing a channel to/from `DeltaChannel` (v0.6).**
Delta and non-delta representations are not interchangeable for persisted threads, and deepagents ≥ 0.6 already stores messages/files as delta channels. Never flip a channel's storage mode on a published workflow with live threads (validator E074), and never downgrade deepagents across 0.6.

**P-13 ✅ `KeyError: 'checkpoint_ns'` from `AsyncSqliteSaver.aput_writes` when editing a past checkpoint (Sprint 2).**
`graph.aupdate_state(config, …)` with a `checkpoint_id` but no `checkpoint_ns` in `configurable` fails on the SQLite saver (Postgres/memory tolerate it). Always send `{"thread_id": …, "checkpoint_id": …, "checkpoint_ns": ""}` for past checkpoints — the LangChain adapter does this in `_config()`.

**P-14 ✅ `NotImplementedError` from `checkpointer.acopy_thread(...)` (Sprint 2).**
The method exists on `BaseCheckpointSaver` but `InMemorySaver`, `AsyncSqliteSaver` and `AsyncPostgresSaver` do not implement it (langgraph-checkpoint, 2026-09). AgentCanvas copies threads through the public saver contract (`alist` → `aput` + `aput_writes`, ids and namespaces preserved) in `adapters/langchain/persistence.py:copy_thread`; a canary test tells us when upstream implements it.

**P-15 ✅ A node's `error_handler` does not run; the run fails with the original exception (Sprint 2).**
In langgraph 1.2.12, node-level error handlers are **bypassed when streaming with `stream_mode` containing `"messages"` or `"custom"`, or with `subgraphs=True`** (they work with `invoke`, `"updates"`, `"tasks"`, `"values"`). Verified for raised exceptions and `NodeTimeoutError`. Workaround in the runner: workflows that declare an error route stream with `["tasks", "updates"]` only (no token streaming) and emit `CUSTOM ac.warning tokens_disabled`. The strict-xfail canary `tests/test_upstream_canaries.py` turns red when upstream fixes it.

**P-16 ✅ `Please remove duplicate middleware instances.` when building an agent (Sprint 5).**
`create_agent` rejects two middleware with the same `.name`. Names include a key for some types (`PIIMiddleware[email]`, `ToolCallLimitMiddleware[send_email]`) but not for others (two `ModelCallLimitMiddleware` clash). Deep Agents (deepagents 0.7) already contain `SummarizationMiddleware` in their base stack, so wiring a Summarization widget into a Deep Agent fails the same way. **Planning (`TodoListMiddleware`) is no longer in the Deep Agent base stack** — the widget's "Planning" toggle adds it, so a separate To-dos widget would clash. Validator **E035** reports all three cases before a run.

**P-17 ✅ Structured output: the tool name changes between runs, or a scripted test cannot answer it (Sprint 5).**
With a JSON-Schema `response_format` that has no `title`, LangChain names the structured-output tool `response_format_<random>`. AgentCanvas always sets a title (the schema's, or `<node>_output`) so generated code, traces and tests are deterministic. The agent writes the result to `structured_response`; declare that channel (validator E021 says so).

**P-18 ✅ A per-user memory folder fails after an approval with "needs the run's user" (Sprint 5).**
LangGraph does **not** checkpoint `Runtime.context`: every invocation (start, resume, continue, fork) must pass it again. The runner stores each run's context on the `run` row and reuses the thread's last context for resume/continue/decisions and the source thread's for forks — so a reviewer approving someone else's run never becomes the run's user (the `/memories/` namespace stays the requester's).

**P-19 ✅ `TypeError: can only concatenate list (not "str") to list` in a map worker (Sprint 5).**
`operator.add` as the `append` reducer rejects single values, and most widgets (LLM call, Set values) write one value. AgentCanvas's `append` reducer wraps non-list writes into a one-item list; a map worker can write its result straight into an `append` channel (E022 requires one: parallel workers writing a `replace` channel would raise P-10).

**P-20 ✅ MCP: "A string target must be an http(s) URL" / tools stop working after the adapter closes? (Sprint 6).**
`langchain.mcp.MCPAdapter` (beta) refuses string targets that are not http(s) URLs — `fastmcp.Client` would otherwise treat a string naming an existing file as a local script and *launch it*. AgentCanvas always passes an explicit transport (`StreamableHttpTransport(url, headers=…)`, or `StdioTransport` only when an admin allows local MCP, E042). Tools listed inside `async with MCPAdapter(...)` keep working after the context closes (each call reconnects), so tools are discovered once per graph build. Tool annotations (`destructive_hint`, `read_only_hint`) are in `tool.metadata["mcp"]["tool"]["annotations"]`; MCP tool results are content blocks with random ids — compare their text in tests.

**P-21 ✅ `store.asearch(ns, query="tea")` returns everything (Sprint 6).**
Without a semantic index (`index=` on the store) the query is ignored. Memory Recall and `search_memory` fall back to keyword ranking over key + value; configure an embedding index for semantic recall.

**P-22 ✅ Token usage attributed to the wrong node, or not priced (Sprint 6).**
Callback metadata carries the checkpoint namespace: a call inside an agent subgraph has `checkpoint_ns = "agent:<id>|model:<id>"` — the first segment is the canvas node. The model's own `.metadata` is merged into its callback metadata, so AgentCanvas tags each model with its catalog id (`ac_model`) to price usage; models whose catalog entry has no prices make the run cost "incomplete" (W015 for USD budgets).

**P-08 ✅ Breakpoint run ends with no interrupts but the run is not finished.**
With `interrupt_before/after`, `get_state().interrupts` is empty and `next` holds the pending node. Treat `next` non-empty as "paused"; continue with `ainvoke(None, config)`.

**P-09 ✅ Router took the wrong branch after I edited old state.**
`aupdate_state(checkpoint.config, values)` records the edit as the node that produced that checkpoint, so routers after it are re-evaluated with the new values — that is expected (M4-T7). To avoid re-routing, edit a checkpoint *after* the router.

**P-10 ✅ `InvalidUpdateError: At key '…': Can receive only one value per step.`**
Two parallel nodes wrote the same channel whose reducer is `replace`. Change the reducer to `append`/`merge` or write to different channels (validator E020).

**P-11 `KeyError` in a node reading state.**
State is `total=False`: keys exist only after something wrote them. Use `state.get("key")` or give the channel a default via an initial Set node.

**P-12 Agent node output is missing in the parent state.**
The agent subgraph shares only matching keys. The workflow state must have a `messages` channel with reducer `add_messages` (and `structured_response` if you use structured output).

**P-23 ✅ LangSmith Sandboxes: every host reachable, or a memory change ignored (Sprint 7, SP-2).**
Without `access_control` a sandbox reaches **every** host on every port; AgentCanvas always sends an `allow_list` (empty = offline, plus port 443 for hosts that header rules target). `update_sandbox(name, proxy_config=…)` patches egress and injected headers in place, but the size (`mem_bytes`, `vcpus`) is fixed at creation — a new `memory_mb` replaces the sandbox. TTLs must be multiples of 60. Secrets go into `opaque` header values (write-only), never into the sandbox environment.

**P-24 ✅ `astream_events(version="v3")`: "'async for' requires … got coroutine" (Sprint 8, SP-3).**
In LangGraph 1.2.x the v3 call is a coroutine: `run = await graph.astream_events(x, cfg, version="v3")`, then `async for ev in run` and `await run.output()` (a method). It is still marked experimental and its default stream has `values` events only; AgentCanvas keeps `astream` v2.

**P-25 ✅ Agent Server: a graph factory is called with the wrong arguments (Sprint 8, SP-4).**
The server injects by *type hint*: annotate `runtime: ServerRuntime` (from `langgraph_sdk.runtime`) and/or `config: RunnableConfig`. String annotations (`from __future__ import annotations`) work if the name is importable in the module. Return the same topology for runs and introspection.

**P-26 ✅ A LangChain callback collector only sees the root run (Sprint 8).**
`collect_runs()` / `run_collector_var` is a non-inheritable configure hook: child runs (graph nodes) never reach it. Assert node names through the canonical STEP events instead, or pass a handler in `config["callbacks"]`.

## B. Templates & code generation

**T-01 ✅ `jinja2.exceptions.UndefinedError: 'dict object' has no attribute '<key>'` during code generation.**
The template reads an optional config key with `n.config.key`. Use `n.config.get("key", default)` (StrictUndefined is intentional).

**T-02 ✅ Generated strings contain `'`.**
You used `| tojson` for a Python literal. Use the `py` filter (`repr`).

**T-03 ✅ pyright: function not assignable to `StateNode` parameter.**
Annotate node callables' state parameter as `Any` (the state type is built at runtime), not `dict`.

**T-04 `CompileError: generated code is not valid Python`.**
The template produced a syntax error; the message contains ruff's error with a line number. Render without formatting (`generate(wf, format_code=False)`) to inspect the raw output.

## C. API & streaming

**A-01 SSE events arrive all at once at the end.**
A proxy buffers the response. nginx: `proxy_buffering off;` (see `reference/infra/nginx.conf`); send header `X-Accel-Buffering: no`; Vite dev proxy streams correctly.

**A-02 `EventSource` cannot POST.**
Correct — use `fetch` + `ReadableStream` (`reference/web/src/sse.ts`).

**A-03 ✅ `404 thread not found` after restarting the API (M0 reference).**
Workflows and the thread→workflow map are in memory in M0; ticket M1-T6 stores them in Postgres.

**A-04 CORS errors in the browser.**
In development use the Vite proxy (`/api` → `:8000`) so the browser sees one origin. In production serve web and API behind the same host, or configure `CORSMiddleware` with explicit origins.

**A-05 `409 Conflict` when saving.**
Someone (or another tab) saved first. Reload the workflow and re-apply; the canvas does this automatically (M2-T3).

## D. Environment

**E-01 ✅ Postgres `initdb: could not access directory … Permission denied`.**
Run Postgres as a non-root user with a data directory that user owns — or use Docker (`reference/infra/docker-compose.yml`).

**E-02 `uv sync` uses the wrong Python.**
`uv python install 3.12`; the project's `requires-python = ">=3.12"` makes uv pick it.

**E-03 pnpm lockfile errors in CI.**
Commit `pnpm-lock.yaml`; install with `pnpm install --frozen-lockfile`; use the same pnpm major version (10) locally and in CI.

**E-04 Playwright cannot find a browser.**
Run `npx playwright install chromium` (or point `executablePath` at a system Chromium).

## F. Frontend (Sprint 3)

**F-01 ✅ `openapi-typescript` crashes with `Cannot read properties of undefined (reading 'createKeywordTypeNode')`.**
It uses the TypeScript compiler's JavaScript API, which TypeScript 7 (native compiler) no longer ships. AgentCanvas generates API types from the OpenAPI *component schemas* with `json-schema-to-typescript` instead (`apps/web/scripts/gen-api-types.mjs`, `pnpm gen`).

**F-02 ✅ `Maximum update depth exceeded` in React Flow's `StoreUpdater`.**
A prop derived on every render (e.g. `new Map(...)` of manifests, or a Zustand v5 selector returning a fresh array) re-triggers React Flow's store sync forever. Memoize derived collections (`useMemo`) and use `useShallow` (or a constant fallback like `NONE`) for selectors that build arrays.

**F-03 ✅ `[React Flow]: Couldn't create edge for source handle id: "model"` (error 008) after a reload; edges missing.**
Handles that appear after a node's first render (ports known only once node-type manifests load, or a router gaining a route) are not measured. Render the canvas only after manifests are loaded, and call `useUpdateNodeInternals()(id)` when a node's handle set changes — but **not on mount**: re-measuring every node on mount is O(n²) (500 nodes: 5.9 s → 0.8 s load after the fix).

**F-04 ✅ Initial view is zoomed in on part of the graph.**
`<ReactFlow fitView>` can fit before every node is measured. Fit once when `useNodesInitialized()` becomes true, with `maxZoom: 1`.

**F-06 ✅ Ctrl/⌘-click or box selection keeps only one node selected (Sprint 6).**
Rebuilding React Flow nodes from the IR on every store change reset `selected` to the editor's single selected element. Merge React Flow's own `selected` flag into rebuilt nodes (the editor's selection is only ever added), and clear it explicitly when focusing a node from elsewhere.

**F-07 ✅ An e2e drag sometimes creates no edge (Sprint 6).**
A content-sized node whose validation badge appears/clears after the server answers moves its handles between "measure" and "press". Keep node widths fixed (resource pills are 200 px) and wait for "Saved" before measuring handles in tests.

**F-08 ✅ Playwright: "strict mode violation … resolved to 2 elements" for a button (Sprint 7).**
`getByRole("button", { name: "Save" })` matches by *substring*, so a new "Save secret" button on the same page broke the workspace journey. Use `{ name: "Save", exact: true }` for short labels. Run the whole e2e suite (not only the new spec) before pushing UI additions.

**F-05 ✅ A renamed field (or `values` key) comes back after the edit is saved (Sprint 5).**
A JSON Merge Patch (RFC 7396) *merges* nested objects, so sending a nested object wholesale keeps its old keys. `diffToMergePatch` must diff recursively and send `null` for removed nested keys. Coalescing queued edits needs the same care: `mergePatch(p1, p2)` drops the `null`s of `p2`, and "delete, then set an object" has no single-patch equivalent — `composePatches` keeps nulls and refuses that case (the editor then queues both commands).

## E. Product questions

**Why not use LangSmith Agent Server as the runtime?**
Self-hosting it in production needs a licence; the MVP needs a free local runtime and full control of events (review D-01). Publishing to LangSmith Deployment remains a target.

**Why does generated code import `agentcanvas.runtime.nodes`?**
So canvas runs and exported code behave identically (ADR 0004). A fully inlined "eject" mode is planned (P2).

**Can I add my own node types?**
Yes: follow [07-compiler-and-node-types.md](./07-compiler-and-node-types.md); workspace-level custom nodes arrive in M5 (spec doc05).
