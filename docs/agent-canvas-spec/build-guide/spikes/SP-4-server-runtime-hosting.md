# Spike SP-4 — Hosting on LangGraph Server with a `ServerRuntime` graph factory

| | |
|---|---|
| Sprint | S8 (time-boxed, 0.5 d) |
| Question | Can an exported project (and later AgentCanvas's interpreted mode, ADR 0013) be served by Agent Server through a graph **factory** instead of a module-level graph? |
| Outcome | **Yes.** The export's `langgraph.json` now points at `server.py:make_graph`, an async context manager annotated with `langgraph_sdk.runtime.ServerRuntime`. It was verified end to end under `langgraph dev`. |

## What was verified

1. **The docs.** `ServerRuntime` needs `langgraph-api >= 0.7.31` and `langgraph-sdk >= 0.3.5`.
   - The server reads the factory's type hints to decide what to inject: a `ServerRuntime` and/or a `RunnableConfig`.
   - It calls the factory for runs (`threads.create_run`, where `runtime.execution_runtime` is set) and also for updates, state reads and introspection. The topology must be the same in every case.
   - `ServerRuntime` is **beta**.
2. **A live run.** A Greeter workflow was exported, then `uv sync`, `uv add --dev "langgraph-cli[inmem]"` and `langgraph dev`, with langgraph-api 0.15.1 and langgraph-sdk 0.4.5.
   - `POST /assistants/search` listed the graph.
   - `POST /runs/wait {"assistant_id": "greeter", "input": {"who": "Ada"}}` returned `{"who": "Ada", "out": "Hello Ada"}`.
3. **String annotations still inject.** `from __future__ import annotations` is fine: the server resolves the annotation string against the module's globals. The import is guarded, so the project also runs without the runtime module.

## Design notes for ADR 0013 (interpreted hosting)

- **Loading the IR.** `make_graph(runtime)` can load the pinned IR from the assistant's config and call the same `build_graph` the runner uses.
- **Expensive work.** MCP connections and per-user credentials go under `runtime.execution_runtime`. Introspection calls must still get the same nodes, so MCP-backed graphs build their tools in both paths today, which costs one discovery per call.
- **Identity.** Per-user resources come from `runtime.ensure_user()` (custom auth). They map onto context fields with `source: "user"`.
- **Beta caveat.** The upstream API is beta (D16), so this stays behind a capability flag. The compiled export remains the portable default.
