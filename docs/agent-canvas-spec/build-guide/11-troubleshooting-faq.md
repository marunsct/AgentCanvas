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

## E. Product questions

**Why not use LangSmith Agent Server as the runtime?**
Self-hosting it in production needs a licence; the MVP needs a free local runtime and full control of events (review D-01). Publishing to LangSmith Deployment remains a target.

**Why does generated code import `agentcanvas.runtime.nodes`?**
So canvas runs and exported code behave identically (ADR 0004). A fully inlined "eject" mode is planned (P2).

**Can I add my own node types?**
Yes: follow [07-compiler-and-node-types.md](./07-compiler-and-node-types.md); workspace-level custom nodes arrive in M5 (spec doc05).
