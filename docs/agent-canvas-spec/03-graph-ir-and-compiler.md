# 03 — Graph IR, Type System & Compiler

This is the heart of the product: how a picture becomes a correct, idiomatic LangGraph program — and how we keep them in sync.

## 1. The layered model

```
 Canvas layout (x,y, colors, collapsed)      ← presentation only (stored separately)
        │
 Graph IR  (canonical, language-neutral)     ← SOURCE OF TRUTH
        │  lower()
 Execution Plan (normalized, resolved)       ← resources resolved, groups flattened,
        │                                       expressions parsed, defaults applied
        ├── interpret()  → live LangGraph objects (dev runs)
        └── generate()   → Python project (or TS / MDA project)
```

## 2. Graph IR specification

### 2.1 Top-level document

```jsonc
{
  "ir_version": "1.0",
  "id": "wf_01J9…",
  "name": "Support Triage",
  "kind": "workflow",                  // workflow | component
  "target_hints": { "language": "python", "runtime": "agent_server" },
  "framework": "langchain",            // v0.3: primary adapter (doc 11); nodes may set "engine" in mixed graphs (later phases)
  "state": {                           // graph state schema
    "channels": [
      { "name": "messages", "type": {"$ref": "#/types/Messages"}, "reducer": "add_messages", "io": "inout" },
      { "name": "ticket",   "type": {"$ref": "#/types/Ticket"},   "reducer": "replace",      "io": "in" },
      { "name": "urgency",  "type": {"enum": ["low","medium","high"]}, "reducer": "replace", "io": "out" },
      { "name": "drafts",   "type": {"type":"array","items":{"type":"string"}}, "reducer": "append", "io": "private" }
    ]
  },
  "context": {                         // run-scoped immutable context (Runtime.context)
    "fields": [ { "name": "tenant_id", "type": {"type":"string"}, "required": true } ]
  },
  "types": { "Ticket": { "type": "object", "properties": { … } } },   // JSON Schema defs
  "resources": [ … ],                  // resource nodes (models, tools, backends, stores…)
  "nodes": [ … ],                      // runtime nodes
  "edges": [ … ],                      // flow, wiring, data edges
  "groups": [ … ],                     // visual groups & subgraph definitions
  "triggers": [ … ],
  "persistence": { … },               // v0.2: checkpointer/store/cache/memory spaces (doc 08 §6)
  "data_models": [ … ],               // v0.2: referenced Data Studio models (doc 08 §5)
  "connections": [ … ],               // v0.2: logical DB connections, bound per environment
  "learning": { "feedback_keys": [ … ], "loops": [ … ] },   // v0.2: doc 09
  "settings": {
    "recursion_limit": 50,
    "durability": "async",             // exit | async | sync
    "checkpointer": "platform",        // platform | none | memory | postgres | sqlite
    "store": "platform",
    "node_defaults": { "retry": {"max_attempts": 2}, "timeout": {"run_timeout": 300} },
    "tracing": { "project": "{{workflow.slug}}-{{env}}", "sampling": 1.0 }
  },
  "metadata": { "created_by": "…", "tags": ["support"] }
}
```

### 2.2 Nodes

```jsonc
{
  "id": "n_7f3a",                      // stable, never reused
  "type": "agent.deep@1.3.0",          // node type + pinned version
  "name": "reply_drafter",             // code identifier (unique, snake_case)
  "label": "Reply drafter",
  "config": {                          // validated against node type's config JSON Schema
    "system_prompt": { "kind": "template", "value": "You draft replies for {{ state.ticket.product }}…" },
    "interrupt_on": { "send_reply": { "allowed_decisions": ["approve","edit","reject"] } },
    "planning": false,
    "permissions": [ { "operations": ["write"], "paths": ["/memories/**"], "mode": "deny" } ]
  },
  "reads":  ["messages","ticket"],     // declared channel access (inferred where possible)
  "writes": ["messages","drafts"],
  "io_mapping": {                      // optional explicit input/output mapping (data edges)
    "input":  { "messages": "state.messages" },
    "output": { "drafts": "result.structured_response.drafts" }
  },
  "policies": {
    "retry": { "max_attempts": 3, "initial_interval": 0.5, "backoff_factor": 2, "retry_on": ["RateLimitError"] },
    "cache": { "ttl": 600, "key": "default" },
    "timeout": { "run_timeout": 120, "idle_timeout": 30 },
    "error_route": "n_err1",           // error_handler → Command(goto)
    "defer": false
  },
  "disabled": false,
  "metadata": { "owner": "team-support" }
}
```

### 2.3 Resources

Resources are nodes that *don't execute as graph steps*; they are constructed once and injected.

```jsonc
{ "id": "r_model1", "type": "model.chat@1.0.0", "name": "fast_model",
  "config": { "model": "anthropic:claude-haiku-4-5", "temperature": 0.2, "max_tokens": 2048,
              "fallbacks": ["openai:gpt-5-mini"], "gateway": false } }

{ "id": "r_mcp1", "type": "mcp.server@1.0.0", "name": "zendesk",
  "config": { "transport": "http", "url": "https://mcp.zendesk.example/mcp",
              "auth": { "kind": "oauth2", "connection": "conn_zendesk" },
              "tool_filter": ["search_tickets","get_ticket","send_reply"] } }

{ "id": "r_be1", "type": "backend.composite@1.0.0", "name": "files",
  "config": { "default": "state", "routes": { "/memories/": { "kind": "store", "namespace": "user" } } } }
```

### 2.4 Edges

```jsonc
// Flow edge: runtime transition
{ "id": "e1", "kind": "flow", "from": {"node": "n_classify", "port": "next"}, "to": {"node": "n_router"} }

// Conditional flow from a router: one port per route
{ "id": "e2", "kind": "flow", "from": {"node": "n_router", "port": "route:high"}, "to": {"node": "n_escalate"} }

// Wiring edge: resource injection
{ "id": "e3", "kind": "wiring", "from": {"node": "r_model1", "port": "model"}, "to": {"node": "n_7f3a", "port": "model"} }
{ "id": "e4", "kind": "wiring", "from": {"node": "r_mcp1", "port": "tools"}, "to": {"node": "n_7f3a", "port": "tools"}, "order": 1 }

// Data edge: explicit channel mapping (subgraph/state-isolated nodes)
{ "id": "e5", "kind": "data", "from": {"node": "n_research", "port": "out:findings"}, "to": {"channel": "findings"} }
```

Special node IDs `START` and `END` are implicit.

### 2.5 Groups & subgraphs

```jsonc
{ "id": "g_research", "kind": "subgraph", "name": "researcher",
  "node_ids": ["n_search","n_read","n_note"],
  "interface": { "input": ["query"], "output": ["findings"] },   // subgraph state = own schema
  "state": { "channels": [ … ] },
  "compile_as": "subgraph" }      // subgraph | compiled_subagent | component_ref
```

### 2.6 Canonicalization rules
- Arrays sorted by `id` (except ordered lists such as middleware order, which carry an explicit `order`).
- Layout (`x`,`y`, size, color) stored in a sibling `layout` document so that moving nodes never changes the IR hash.
- Canonical JSON (RFC 8785 JCS) → `ir_hash` (SHA-256) used for caching builds and dedup.

### 2.7 Core IR vs framework dialects (v0.3)
Node types are namespaced: `core.*` (portable across adapters) and dialects such as `langchain.*` / `langgraph.*` (R1) and later `openai.*`, `adk.*`, `msaf.*`, `crewai.*`. Core nodes may carry `x-<adapter>` override blocks. Lowering, codegen and interpretation (§5–7) are performed by the selected **Framework Adapter** — everything below describes the **LangChain adapter**. See [doc 11](./11-framework-agnostic-plan.md).

## 3. Type system

### 3.1 Port types

| Type | Kind | Compiles to |
|------|------|-------------|
| `ChatModel` | resource | `BaseChatModel` (via `init_chat_model`) |
| `Embeddings` | resource | `Embeddings` |
| `Tool` / `Tool[]` | resource | `BaseTool` / list |
| `Middleware[]` (ordered) | resource | `AgentMiddleware` list |
| `Backend` | resource | `BackendProtocol` (State/Store/Filesystem/Sandbox/ContextHub/Composite) |
| `Store` / `Checkpointer` | resource | `BaseStore` / `BaseCheckpointSaver` |
| `Retriever` | resource | `BaseRetriever` |
| `Subagent` | resource | `SubAgent` dict / `CompiledSubAgent` / `AsyncSubAgent` |
| `SkillSet`, `MemorySource` | resource | paths/sources passed to `skills=` / `memory=` |
| `Schema` | resource | Pydantic model / TypedDict / JSON Schema dict for `response_format` |
| `Prompt` | resource | string / `ChatPromptTemplate` / LangSmith prompt ref |
| `Flow` | control | edge in graph |
| `Messages`, `Text`, `JSON<S>`, `Number`, `Bool`, `File`, `Image`, `Documents`, `Any` | data | state channel values |

### 3.2 Compatibility & adapters
- Structural subtyping for `JSON<S>` (S1 assignable to S2 if every required property of S2 exists in S1 with compatible type).
- Auto-adapters: `Text→Messages` (HumanMessage), `Messages→Text` (last AI content), `JSON→Text` (serialize), `Documents→Text` (join), `Tool→Tool[]`.
- `Any` is allowed but produces an info diagnostic in strict mode.

## 4. Validation (diagnostics catalog, excerpt)

| Code | Severity | Rule |
|------|----------|------|
| `E001` | error | Required port unconnected |
| `E002` | error | Port type mismatch without adapter |
| `E010` | error | No path from START to END |
| `E011` | error | Node unreachable from START |
| `E012` | error | Router has a route with no outgoing edge and no default |
| `E020` | error | Two nodes in the same super-step write a channel whose reducer is `replace` (concurrent update) |
| `E021` | error | Node writes a channel not declared in state |
| `E030` | error | Agent has tools but model lacks tool-calling capability (from model profile) |
| `E031` | error | Duplicate tool name in agent |
| `E032` | error | Subagent missing description / duplicate name |
| `E033` | error | `interrupt_on` references unknown tool |
| `E034` | error | Permission path not absolute / contains `..` or `~` |
| `E040` | error | `execute`/shell capability without sandbox backend |
| `E050` | error | Secret literal detected in config |
| `E060` | error | Send target input type incompatible with payload |
| `E070` | error | Node performs a DB/external write **and** calls `interrupt()` (write would repeat on resume) — split the node |
| `E071` | error | Data node/tool without a resolved tenant/user scope for a tenant-scoped entity |
| `E072` | error | Memory Space namespace template uses values produced by a model or tool (allowed: `context`, authenticated identity, and input channels marked `trusted`, e.g. webhook payload fields) |
| `E073` | error | Persistence binding missing for the target environment |
| `W070` | warning | Write node with retries but no idempotency key |
| `W071` | warning | Concurrent writes to the same entity from parallel branches without a conflict strategy |
| `W072` | warning | Checkpointer binding has not passed the conformance suite |
| `W073` | warning | Append-heavy channel without `DeltaChannel` in a long-running chat workflow |
| `P001` | error | Node type not supported by the workflow's framework adapter |
| `P002` | warning | Node supported only via emulation on this adapter (e.g. emulated HITL) |
| `P004` | warning | Node compiled through the Agent Spec bridge path (non-idiomatic generated code) |
| `P003` | info | Workflow portability: compiles on N of M installed adapters |
| `W001` | warning | Cycle without guard (router exit, counter, or explicit `recursion_limit` override) |
| `W002` | warning | MCP tool with `destructive_hint` not behind approval |
| `W003` | warning | Channel written but never read |
| `W010` | warning | Model deprecated / not in environment allow-list |
| `W020` | warning | Prompt > N tokens without caching-capable model |
| `I001` | info | Consider `LLMToolSelectorMiddleware` (> 20 tools on an agent) |

Validation runs incrementally (dependency-tracked per node) and is shared by the editor (Pyodide/WASM build of the validator is a P2 option for zero-latency client-side checks; P0 runs server-side with debounce).

## 5. Lowering (IR → Execution Plan)

1. **Resolve versions** of node types from the registry (pinned).
2. **Resolve resources**: build a DAG of resource dependencies (e.g. Agent ← Model, Tools ← MCP Server ← Connection). Detect cycles (error).
3. **Expand components** (`component_ref`) and flatten visual groups; keep subgraph boundaries.
4. **Expand macros**: e.g. *Lite Agent exploded* → `model` node + `tools` node + `tools_condition` edges; *Map node* → `Send` fan-out + reduce channel; *Loop node* → counter channel + router.
5. **Parse expressions** into an AST (sandboxed language); type-check against state/context schema.
6. **Apply settings/env overrides** (per environment config).
7. **Assign code identifiers**: `f"{slug(name)}"` unique per scope; record `canvas_node_id` ↔ identifier map (source map seed).
8. **Infer reads/writes** for built-in nodes; verify declared ones for script nodes (AST analysis of returned dict keys, best effort).

## 6. Code generation (Python target)

### 6.1 Output project layout

```
<workflow_slug>/
├── pyproject.toml                 # pinned deps: langgraph, langchain, deepagents, providers, agentcanvas-runtime (optional)
├── langgraph.json                 # Agent Server config: graphs, env, auth, http, store index, checkpointer TTL
├── .env.example                   # names of required secrets (no values)
├── src/<pkg>/
│   ├── __init__.py
│   ├── graph.py                   # top-level StateGraph assembly → `graph = builder.compile(...)`
│   ├── state.py                   # State / InputState / OutputState / Context schemas
│   ├── resources.py               # models, tools, MCP adapters, backends, stores (lazy factories)
│   ├── prompts.py                 # prompt templates (or LangSmith/Context Hub refs)
│   ├── nodes/                     # one module per runtime node (generated)
│   ├── agents/                    # one module per Agent widget (create_deep_agent / create_agent)
│   ├── subgraphs/                 # one module per subgraph
│   ├── routers.py                 # routing functions
│   ├── middleware/                # generated config + user custom middleware (from workspace)
│   ├── scripts/                   # user script node bodies (copied verbatim from workspace)
│   ├── tools/                     # user custom tools (from workspace)
│   └── _canvas_sourcemap.json     # identifier/line ↔ canvas_node_id
├── skills/  memories/             # Deep Agents skills & memory seeds
├── tests/                         # generated smoke tests + user tests
└── Dockerfile                     # optional standalone Agent Server image
```

### 6.2 Generation technique
- **Templates + CST**: Jinja2 templates per node type produce code fragments; fragments are assembled and validated with **LibCST** (guarantees syntactically valid, allows safe insertion of code islands); then `ruff format` + `ruff check --fix`; then `pyright` type check (errors → build diagnostics mapped via source map).
- Deterministic output (same IR → byte-identical code) for clean Git diffs.
- Every emitted block is fenced with source-map comments:
  ```python
  # ── @canvas-node n_7f3a "Reply drafter" (agent.deep@1.3.0) ──
  ...
  # ── end n_7f3a ──
  ```
- User code (script nodes, custom tools, middleware) is **never templated** — it's copied from the Workspace verbatim into `scripts/`/`tools/`/`middleware/` and imported, so users can recognize and own it.
- Node metadata (`metadata={"canvas_node_id": "n_7f3a"}`) is set on every `add_node` for trace mapping; agents get `name=` and `.with_config({"metadata": …, "tags": …})`.

### 6.3 Construct mapping (canonical)

| Canvas construct | Generated Python |
|------------------|------------------|
| Workflow | `builder = StateGraph(State, context_schema=Context, input_schema=InputState, output_schema=OutputState)` … `graph = builder.compile(name="…")` (no checkpointer/store when targeting Agent Server — the server injects them; `InMemorySaver` in standalone scripts) |
| State channel + reducer | `Annotated[list[AnyMessage], add_messages]`, `Annotated[list[str], operator.add]`, custom reducer fn; `DeltaChannel` where selected |
| Flow edge | `builder.add_edge("a", "b")`; START/END constants |
| Router node | `builder.add_conditional_edges("src", route_fn, {"high": "escalate", "low": "draft"})` |
| Command-style routing (node decides) | node returns `Command(goto=…, update=…)`; `add_node(..., destinations=(…))` for graph rendering |
| Parallel branches | multiple edges from one node (same super-step); join via multi-source `add_edge(["a","b"], "join")` |
| Map / fan-out | conditional edge returning `[Send("worker", {...}) for item in state["items"]]` + reducer channel |
| Loop with guard | counter channel + router; `recursion_limit` in config |
| Deep Agent widget | `create_deep_agent(model=…, tools=[…], system_prompt=…, middleware=[…], subagents=[…], skills=[…], memory=[…], permissions=[…], backend=…, interrupt_on={…}, response_format=…, context_schema=…, name=…)` added as a node (a compiled graph is a valid node) or used as the whole graph |
| Lite Agent widget | `create_agent(model, tools, system_prompt=…, middleware=[…], response_format=…, name=…)` |
| Subagent (declarative) | `{"name":…, "description":…, "system_prompt":…, "tools":[…], "model":…, "middleware":[…], "skills":[…], "interrupt_on":{…}}` |
| Subagent (compiled, from canvas) | `CompiledSubAgent(name=…, description=…, runnable=<compiled subgraph>)` |
| Async subagent | `AsyncSubAgent(name=…, description=…, graph_id=…, url=…)` |
| Human Approval node | node calling `interrupt({"kind":"approval", …})`, resumes with `Command(resume=…)` |
| Tool-level approvals | `interrupt_on={…}` (Deep Agent) or `HumanInTheLoopMiddleware(interrupt_on={…})` |
| Script node | `builder.add_node("x", scripts.x.run, …)` — user function `run(state, runtime) -> dict | Command` |
| Subgraph group | separate `StateGraph` compiled and added as a node (shared keys) or wrapped in a function (different schema) |
| Node policies | `add_node(…, retry_policy=RetryPolicy(...), cache_policy=CachePolicy(ttl=…), timeout=TimeoutPolicy(run_timeout=…, idle_timeout=…), error_handler=…, defer=…)`; graph-wide `set_node_defaults(...)`; `compile(cache=…)` |
| MCP server | `from langchain.mcp import MCPAdapter` → `tools = await MCPAdapter(<target>).list_tools()` in an async lazy factory |
| Guardrail widgets | `PIIMiddleware`, `ToolCallLimitMiddleware`, `ModelCallLimitMiddleware`, `ModelFallbackMiddleware`, `ModelRetryMiddleware`, `ToolRetryMiddleware`, `SummarizationMiddleware`, `ContextEditingMiddleware`, `LLMToolSelectorMiddleware`, `ProviderToolSearchMiddleware`, … |
| Structured output | Pydantic model generated in `state.py`/`schemas.py`; `response_format=Model` (auto strategy) or `ToolStrategy(Model)` / `ProviderStrategy(Model)` |
| Checkpointer binding | Agent Server targets: `langgraph.json` `checkpointer` (`backend`, `ttl`) or custom checkpointer module; standalone: `compile(checkpointer=AsyncPostgresSaver/…)`; durability via run config |
| Store / Memory Spaces | `langgraph.json` `store` (`index`, `ttl`) or custom store module (e.g. `RoutedStore`); memory tools & Recall/Remember nodes use `runtime.store` with generated namespace builders |
| Cache binding | `compile(cache=RedisCache/InMemoryCache)` + per-node `CachePolicy` |
| Data Studio entities & data nodes | `data/models`, `data/schemas`, `data/repositories`, `data/tools.py`, `data/migrations` (doc 08 §5.4) |
| Learning artifacts | prompt refs resolved at runtime by tag; few-shot selector middleware; learning loops compiled as separate graphs registered in `langgraph.json` |
| Custom stream events | `get_stream_writer()` in scripts; UI subscribes to `custom` mode |
| Trigger (cron/webhook) | `langgraph.json` crons / webhook config in deploy manifest, not graph code |

### 6.4 Deep Agents specifics
- `TodoListMiddleware` is emitted only when *Planning* is toggled (opt-in since deepagents 0.7).
- Overriding a default (e.g. custom summarization thresholds) emits a middleware instance with the same `.name` which replaces the default in place.
- Backends are emitted as concrete instances (`StateBackend()`, `StoreBackend(namespace=lambda rt: (rt.server_info.user.identity,))`, `CompositeBackend(default=…, routes={…})`) — no deprecated factories.
- Permissions emitted as `FilesystemPermission(operations=[…], paths=[…], mode=…)` preserving UI order (first-match-wins).
- Interpreter capability emits the QuickJS `CodeInterpreterMiddleware` with the PTC allow-list; requires Python ≥ 3.11 and `langchain-quickjs>=0.2` pinned.

### 6.5 Alternative target: Managed Deep Agents project
When a workflow is *Agent-tier only* (a single Deep Agent with tools/subagents/skills/memory), the compiler can emit an **MDA project**:
```
agent.py            # agent = define_deep_agent(name=…, model=…, tools=[…], middleware=[…], subagents=[…], permissions=[…], interrupt_on=…, response_format=…)
instructions.md     # system prompt
skills/<name>/SKILL.md
tools/*.py, tools/mcp.py (module-level `mcp`)
middleware/*.py
memory.py, identity.py, sandbox/__init__.py, channels/<name>.py, schedules/<name>.py
pyproject.toml, .env (names only)
```
and deploy with `mda deploy`. Graph-tier constructs (routers, Send, explicit interrupts) are not representable → the target is disabled with an explanation.

### 6.6 Target capability matrix
Each node type's manifest declares `targets: {"python": "full", "mda": "full|partial|none", "typescript": "full|partial|none"}`. The validator reports blocking nodes for the chosen target.

## 7. Interpreter (dev runs)

`agentcanvas_runtime.build(plan, debug_opts) -> CompiledStateGraph`:
- Uses the **same node implementation library** as codegen. **Decision (v0.5, review C-03, ADR 0004):** generated code calls the shared node library (`agentcanvas.runtime.nodes` → published as `agentcanvas-runtime`) and assembles the graph with plain LangGraph calls; this is what the reference implementation does and what `test_generated_code_matches_interpreter` verifies. A fully inlined, dependency-free "clean eject" (`--inline`) is a P2 option. (Superseded wording: "Default export uses inlined idiomatic code"); equivalence tested.
- Debug options: breakpoints → `interrupt_before/after`; pins → wrapper node returning pinned output; mocks → model replaced by `GenericFakeChatModel`/scripted model or `LLMToolEmulator`, tools replaced by cassette players; caches → `InMemoryCache`/Redis cache with `CachePolicy`.
- **MVP (v0.5):** built per run by the AgentCanvas runner (`build_graph(wf, resources, checkpointer)`), optionally cached by `(workflow_id, ir_hash)`. Registering the factory in a LangGraph Agent Server is a later hosting option.

## 8. Source maps & trace mapping
- `_canvas_sourcemap.json`: `{ "n_7f3a": { "identifier": "reply_drafter", "file": "agents/reply_drafter.py", "lines": [12, 58] } }`.
- Python tracebacks from runs are rewritten by the orchestrator to reference canvas nodes ("Error in *Reply drafter* → tool `send_reply`").
- LangSmith spans carry `canvas_node_id` metadata ⇒ trace overlay and "open node from trace".

## 9. Round-tripping: code islands, eject & import

| Mechanism | Description | Pri |
|-----------|-------------|-----|
| **Code islands** | Script nodes, custom tools/middleware/reducers/handlers are user-owned files; edits in the Workspace or in the "Code" view island regions flow back into the IR as file references. | P0 |
| **Eject** | Export the full project to Git; the workflow is marked *ejected* (canvas becomes read-only view generated from code import). Users can "re-attach" only if code still matches the last generated hash + islands. | P1 |
| **Import (visual)** | Import an existing LangGraph project: load the graph (`graph.get_graph(xray=True)`) for topology, AST-analyze the builder code to recover node functions, edges, conditional maps and `create_agent`/`create_deep_agent` calls. Unknown code becomes script nodes. Result: an editable canvas with fidelity score. | P2 |
| **Drift detection** | Generated files have a header hash; CI check (`agentcanvas verify`) fails if generated regions were hand-edited outside islands. | P1 |

## 10. Compiler requirements

| ID | Requirement |
|----|-------------|
| TR-COMP-01 | Deterministic, byte-identical output for identical IR + registry versions + target. |
| TR-COMP-02 | Generated code passes `ruff check`, `ruff format --check`, and `pyright` basic mode with zero errors; strict mode for generated modules (not user islands). |
| TR-COMP-03 | Generated project runs with `langgraph dev` and `langgraph up` without modification. |
| TR-COMP-04 | No secrets in output; `.env.example` lists required variables. |
| TR-COMP-05 | Every graph node and agent carries `canvas_node_id` metadata. |
| TR-COMP-10 | Incremental: re-compiling after a single-node change touches only that node's module + graph assembly. |
| TR-COMP-20 | Dependency pinning: lockfile (`uv.lock`) generated; versions from a curated, tested "stack" (e.g. `stack-2026.09`: langgraph 1.2.x, langchain 1.4.x, deepagents 0.7.x). |
| TR-COMP-30 | Target capability checks before generation. |
| TR-COMP-40 | Interpreted ≡ compiled: differential tests over templates + fuzzed IRs in CI. |
| TR-COMP-50 | Generated smoke tests: import graph, render mermaid, run with fake model through each route. |
