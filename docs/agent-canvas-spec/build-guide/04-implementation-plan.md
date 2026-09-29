# 04 — Implementation Plan (Milestones & Tickets)

**How to use this document:** take tickets **in order** inside a milestone (dependencies are listed). Each ticket has: *Goal*, *Depends on*, *Files*, *Steps*, *Acceptance criteria (AC)*, *Tests*, *Estimate* (ideal days for one developer new to the codebase). A milestone is done when all its ACs are demonstrated and CI is green.

Spec references use `docNN §x` (e.g. `doc03 §4` = [03-graph-ir-and-compiler.md](../03-graph-ir-and-compiler.md) section 4). Requirement IDs (FR-…) come from [01-functional-spec.md](../01-functional-spec.md); the mapping is in [12-traceability.md](./12-traceability.md).

## Milestone overview

| Milestone | Outcome (what a user can do at the end) | Est. (1 dev) | Est. (4 devs) |
|-----------|------------------------------------------|-------------:|--------------:|
| **M0** Foundations | Repo, CI, local infra; the walking skeleton runs in the product repo | 1 wk | 3 d |
| **M1** Persistence & API | Workflows, versions, threads stored in Postgres; stable `/v1` API | 3 wk | 1.5 wk |
| **M2** Canvas editor | Build and edit workflows by drag & drop with live validation | 6 wk | 2.5 wk |
| **M3** Node catalog P0 | Agents, Deep Agent, MCP/HTTP tools, map/parallel/loop, human input, guardrails | 6 wk | 2.5 wk |
| **M4** Run & debug | Chat test panel, live overlay, inbox, timeline, state edit, fork, breakpoints, pins | 5 wk | 2 wk |
| **M5** Workspaces & secrets | Code editor, script nodes, custom tools/nodes, sandbox, secrets vault | 5 wk | 2 wk |
| **M6** Export & publish (**MVP**) | Versions & diff, export project, publish endpoint, webhook/cron triggers, tracing, templates | 5 wk | 2 wk |
| **M7** Accounts & access | Login (OIDC), organisations/projects, roles, audit log | 3 wk | 1.5 wk |
| **M8** Production readiness | Containers, Helm, observability, backups, load tests, security review | 3 wk | 1.5 wk |

After M8 continue with GA features from the spec (evals, multiplayer, Data Studio, learning loops, extra frameworks) — see §M9.

---

## M0 — Foundations

### M0-T1 Create the product repository
- **Goal:** product repo with the reference code as starting point.
- **Files:** whole repo (layout in [02 §2](./02-repository-and-conventions.md#2-target-monorepo-layout-grow-into-it-milestone-by-milestone)).
- **Steps:** follow [02 §1](./02-repository-and-conventions.md#1-create-the-product-repository-ticket-m0-t1); add `Makefile` (02 §3); add `README.md` with "how to run" copied from 01 §4–5.
- **AC:** `make infra && make test && make lint` succeed on a fresh clone.
- **Estimate:** 0.5 d

### M0-T2 CI pipeline
- **Depends:** T1 · **Files:** `.github/workflows/ci.yml` (02 §8)
- **AC:** a PR shows green `api` and `web` jobs; a failing test turns CI red.
- **Estimate:** 0.5 d

### M0-T3 ADRs
- **Files:** `docs/adr/0001…0011` (02 §6). **AC:** all eleven merged. **Estimate:** 0.5 d

### M0-T4 Walking skeleton demo
- **Steps:** run fake server + web (01 §4–5); record a 1-minute screen capture; add the Playwright script as `apps/web/e2e/skeleton.spec.ts` (convert `e2e.mjs` to `@playwright/test`).
- **AC:** `pnpm exec playwright test` passes locally and in CI (add job with `npx playwright install --with-deps chromium`).
- **Estimate:** 1 d

---

## M1 — Persistence & API

### M1-T1 Database layer (SQLAlchemy + Alembic)
- **Goal:** async DB session and migrations.
- **Files:** `agentcanvas/db/session.py`, `agentcanvas/db/models.py`, `migrations/` (Alembic).
- **Steps:**
  1. `uv add "sqlalchemy[asyncio]>=2" alembic asyncpg python-ulid structlog`.
  2. `session.py`: `create_async_engine(settings.database_url.replace("postgresql://","postgresql+asyncpg://"))`, `async_sessionmaker`, FastAPI dependency `get_session()`.
  3. `alembic init -t async migrations`; set `target_metadata = Base.metadata`.
  4. SQLAlchemy models for **all 14 tables** in [05 §3](./05-backend-guide.md#3-database-schema-mvp) / `reference/infra/schema.sql` (`organization`, `app_user`, `membership`, `project`, `workflow`, `workflow_version`, `deployment`, `thread`, `run`, `interrupt_task`, `secret`, `connection`, `trigger`, `audit_log`). Until M7, seed one organization/project/user (`scripts/seed_dev.py`).
  5. `alembic revision --autogenerate -m "initial"`; review the file; `alembic upgrade head`.
- **AC:** `alembic upgrade head` on an empty DB creates all tables; `alembic downgrade base` removes them.
- **Tests:** `tests/integration/test_migrations.py` runs upgrade/downgrade against `TEST_DATABASE_URL`.
- **Estimate:** 2 d

### M1-T2 IDs, settings, logging, errors
- **Files:** `agentcanvas/ids.py`, `settings.py` (pydantic-settings), `logging.py` (structlog), `api/errors.py`.
- **Steps:** `new_id("wf") -> "wf_" + ULID()`; settings from env (01 §7); request-id middleware; exception handlers mapping `CompileError`/`ValidationError`/`NotFound`/`Conflict` → problem+json (02 §5).
- **AC:** every response has `x-request-id`; a 404 returns `application/problem+json` with `type/title/status/detail`.
- **Tests:** unit tests for handlers. **Estimate:** 1.5 d

### M1-T3 Workflow CRUD (`/v1/workflows`)
- **Depends:** T1, T2 · **Files:** `api/workflows.py`, `db/repositories/workflows.py`.
- **Endpoints:** `POST /v1/projects/{project_id}/workflows` (create empty IR), `GET /v1/workflows/{id}`, `PUT /v1/workflows/{id}` (full IR, `If-Match: <version>`), `DELETE`, `GET /v1/projects/{id}/workflows?cursor=`.
- **Steps:** store draft IR in `workflow.draft_ir` (JSONB) + `workflow.version` (int, incremented on each save); validate with `Workflow.model_validate`; return diagnostics with every save.
- **AC:** concurrent saves with the same `If-Match` → second gets `409`; invalid IR JSON → `422` problem with pydantic errors; list is paginated.
- **Tests:** API tests for create/get/update/conflict/delete/pagination. **Estimate:** 3 d

### M1-T4 Command API (edit operations)
- **Goal:** the canvas sends small commands instead of whole documents (enables undo/redo, copilot, audit).
- **Files:** `ir/commands.py`, `api/workflows.py` (`POST /v1/workflows/{id}/commands`).
- **Commands (P0):** `AddNode`, `RemoveNode` (also removes its edges), `UpdateNodeConfig` (JSON merge-patch), `RenameNode`, `AddResource`, `RemoveResource`, `Connect`, `Disconnect`, `SetChannels`, `MoveNodes` (layout only).
- **Steps:** each command = Pydantic model with `apply(wf) -> Workflow` (pure, returns a new object) and `invert(wf) -> Command` (for undo). Endpoint applies a batch atomically with `If-Match`, saves, returns new IR + version + diagnostics.
- **AC:** applying a batch then its inverses returns the original IR (property test with Hypothesis).
- **Tests:** unit per command; Hypothesis round-trip. **Estimate:** 4 d

### M1-T5 Layout document
- **Goal:** node positions stored separately from IR (spec doc03 §2.6), so moving nodes never changes the IR hash.
- **Files:** `workflow.layout` JSONB `{node_id: {x, y}}`; `PUT /v1/workflows/{id}/layout`.
- **AC:** moving a node changes `layout` but not `ir_hash`. **Estimate:** 0.5 d

### M1-T6 Threads & runs in the database
- **Goal:** remove in-memory maps from the skeleton.
- **Files:** `db/models.py` (`thread`, `run`), `runtime/runner.py` (move `_stream` here), `api/runs.py`, `api/threads.py`.
- **Steps:**
  1. `POST /v1/workflows/{id}/runs/stream` creates `thread` (if new) and `run` rows (`status=running`, `workflow_version` = draft hash or version id).
  2. Stream as in the skeleton; update `run.status` to `success|interrupted|error|cancelled`, set `ended_at`.
  3. `thread` stores `workflow_id` → fixes the restart problem noted in 01 §4.
  4. Checkpointer/store: `AsyncPostgresSaver` + `AsyncPostgresStore` opened in FastAPI `lifespan` (as in the skeleton) — always when `DATABASE_URL` is set.
  5. Accept `durability: "exit"|"async"|"sync"` in the run request and pass it to `astream(..., durability=…)` (verified parameter; default `async`, use `sync` for workflows with side effects) (FR-PER-03).
  6. `GET /v1/threads?workflow_id=&status=&cursor=`; `GET /v1/threads/{id}` (state), `/history`, `POST /state`, `DELETE /v1/threads/{id}` (calls `checkpointer.adelete_thread(thread_id)`).
- **AC:** restart the API during a paused run → `GET /v1/threads/{id}` still shows the interrupt; resume works.
- **Tests:** integration test with Postgres: run → pause → restart app (new lifespan) → resume. **Estimate:** 3 d

### M1-T7 IR JSON Schema & node registry
- **Goal:** one machine-readable description of every node type (ports, config schema) for the UI and validator.
- **Files:** `registry/nodes/*.json` (one manifest per node type, format in doc04 §12), `registry/loader.py`, `GET /v1/node-types`, script `scripts/export_ir_schema.py` → `packages/ir-schema/ir.schema.json` (`Workflow.model_json_schema()`).
- **Manifest example (`core.llm.json`):**
  ```json
  {"type": "core.llm", "version": "1.0.0", "label": "LLM call", "category": "AI", "icon": "sparkles",
   "ports": {"inputs": [{"name": "model", "type": "ChatModel", "required": true, "wiring": true}],
             "outputs": [{"name": "next", "type": "Flow"}]},
   "config_schema": {"type": "object", "required": ["prompt", "output"],
     "properties": {"prompt": {"type": "string", "title": "Prompt", "format": "prompt"},
                    "output": {"type": "string", "title": "Output channel", "format": "channel"}}}}
  ```
- **AC:** `GET /v1/node-types` lists all implemented types; the validator checks each node's `config` against its `config_schema` (new rule `E004 invalid config`).
- **Tests:** every manifest validates as JSON Schema; every example IR validates. **Estimate:** 2 d

### M1-T8 Validator expansion (P0 rules)
- **Files:** `ir/validate.py` (split into `rules/*.py`, one function per rule, registered in a list).
- **Add rules:** E002 port type mismatch (from manifests), E004 config schema, E020 two parallel writers to a `replace` channel, E030 model without tool calling but tools wired (from model catalog capability flag), E031 duplicate tool names, E033 `approve_tools` names not wired, E050 secret-looking literal (`sk-`, `AKIA`, long base64) in config, E070 script node that both writes externally and interrupts (heuristic: `core.approval` must not be preceded in the same node — structural rule), W003 channel written never read, W010 model not in allow-list.
- **AC:** each rule has a failing and a passing test IR; validation of 200 nodes < 150 ms (benchmark test).
- **Estimate:** 4 d

---

## M2 — Canvas editor

Frontend structure and code patterns are explained in [06-frontend-guide.md](./06-frontend-guide.md).

### M2-T1 App shell
- **Files:** `src/app/{App.tsx,routes.tsx,Layout.tsx}`; routes `/projects`, `/workflows/:id`.
- **Steps:** React Router; TanStack Query provider; layout with left rail, canvas, right panel, bottom dock (doc01 §3).
- **AC:** navigating between projects and a workflow works; unknown route shows 404 page. **Estimate:** 1.5 d

### M2-T2 Generated types & API client
- **Steps:** `pnpm add -D openapi-typescript json-schema-to-typescript`; scripts: `gen:api` (`openapi-typescript http://localhost:8000/openapi.json -o src/api/schema.d.ts`) and `gen:ir` (`json2ts packages/ir-schema/ir.schema.json > src/ir/generated.ts`); thin `apiFetch<T>()` wrapper that parses problem+json into an `ApiError`.
- **AC:** removing a field in Pydantic breaks `tsc` in the web app after regeneration (types are really shared). **Estimate:** 1 d

### M2-T3 Load, edit, autosave
- **Files:** `src/canvas/useWorkflow.ts`, `src/canvas/commandQueue.ts`.
- **Steps:** load IR + layout; every edit becomes a command (M1-T4) queued and sent debounced (300 ms) with `If-Match`; on `409` reload and show a toast "Updated elsewhere — reloaded".
- **AC:** edits survive a page reload; two tabs editing produce a 409 toast instead of silent overwrite. **Estimate:** 3 d

### M2-T4 Palette & drag-and-drop
- **Files:** `src/canvas/Palette.tsx`.
- **Steps:** load `/v1/node-types`, group by `category`, search box; HTML5 drag (`dataTransfer.setData("application/agentcanvas", type)`); on drop use `screenToFlowPosition()` to place; create node with `new_id` from server (or client ULID + server validation) and default config from schema defaults.
- **AC:** dragging "LLM call" onto the canvas adds a node at the drop point and an `AddNode` command is saved. **Estimate:** 2 d

### M2-T5 Custom node renderers with typed ports
- **Files:** `src/canvas/nodes/WidgetNode.tsx`.
- **Steps:** one generic renderer driven by the manifest: header (icon, label, status badge), one `<Handle type="target" id={port.name}>` per input port (left), one source handle per output port (right; routers: one per route `route:<name>`); handle colour by port type; resources render as compact pills.
- **AC:** every node type renders with correct handles; status colours from the run store still work. **Estimate:** 3 d

### M2-T6 Connecting with type checks
- **Steps:** `isValidConnection` compares port types from manifests (snippet in 06 §5); on connect create `Connect` command with `kind = "wiring"` if the source is a resource, else `"flow"`; wiring edges dashed and coloured; flow edges solid.
- **AC:** connecting a Chat Model to an Agent's `tools` port is refused with a tooltip explaining why. **Estimate:** 2 d

### M2-T7 Inspector (config forms)
- **Files:** `src/inspector/Inspector.tsx`, `src/inspector/widgets/{PromptWidget,ChannelSelect,CelEditor}.tsx`.
- **Steps:** RJSF form from the node's `config_schema` (snippet in 06 §6); custom widgets by `format`: `prompt` (textarea with `{{ state.x }}` autocomplete from channels), `channel` (select from state channels), `cel` (input with server-side validation via `/v1/expressions/check`); edits → `UpdateNodeConfig` command.
- **AC:** editing a prompt updates the IR; invalid CEL shows an inline error. **Estimate:** 4 d

### M2-T8 State designer
- **Files:** `src/inspector/StateDesigner.tsx`.
- **Steps:** table of channels (name, type, reducer, default); presets *Chat* (`messages` + `add_messages`) and *Pipeline*; `SetChannels` command.
- **AC:** adding channel `summary` makes it selectable in all `channel` selects. **Estimate:** 2 d

### M2-T9 Problems panel & badges
- **Steps:** diagnostics from every save → bottom-dock list (click focuses node via `fitView({nodes:[id]})`); red/yellow badge on nodes; Run button disabled while errors exist.
- **AC:** removing a model wire shows `E001` on the node and in the panel within 1 s. **Estimate:** 1.5 d

### M2-T10 Undo / redo, copy / paste, delete
- **Steps:** client stack of applied command batches; undo sends inverse batch; `⌘Z / ⌘⇧Z / Delete / ⌘C / ⌘V` (paste re-IDs nodes and keeps internal edges).
- **AC:** 20 random edits undone one by one restore the original IR (Playwright test). **Estimate:** 3 d

### M2-T11 Auto-layout
- **Steps:** "Tidy up" button runs ELK layered left→right (snippet in 06 §7); saves layout.
- **AC:** example workflows lay out without overlapping nodes. **Estimate:** 1 d

### M2-T12 Keyboard & command palette (P0 subset)
- **Steps:** `⌘K` palette (add node, run, tidy, go to node); double-click on empty canvas opens node search at cursor.
- **AC:** a workflow can be built without the mouse except for wiring. **Estimate:** 2 d

### M2-T13 Groups & subgraphs (FR-CAN-05)
- **Steps:** (a) visual group = React Flow parent node (`parentId`) stored in layout only; (b) "Convert to subgraph" creates a `core.subgraph` node whose `config.workflow` is an embedded IR (own channels + `input`/`output` channel mapping); double-click opens it with a breadcrumb.
- **Compile:** builder compiles the inner IR with the same interpreter and adds it as a node; when inner and outer channels differ, wrap it: `async def run(state): out = await inner.ainvoke({k: state[k] for k in inputs}); return {k: out[k] for k in outputs}`.
- **AC:** a 3-node group converted to a subgraph behaves identically (equivalence test); canvas shows nested activity via `ns` events. **Estimate:** 4 d

---

## M3 — Node catalog (P0)

Recipe for every node type: [07-compiler-and-node-types.md](./07-compiler-and-node-types.md) (IR type → manifest → builder → interpreter → template → validator rules → tests → UI). Each ticket below = one pass of that recipe.

### M3-T1 Adapter boundary (framework-agnostic groundwork)
- **Goal:** all LangChain/LangGraph code behind `adapters/langchain/` (spec doc11 §4 A1–A4).
- **Steps:** move `runtime/nodes.py`, `interpreter.py`, `events.py`, codegen templates into `adapters/langchain/`; define `FrameworkAdapter` protocol (doc12 §C.2, minimal methods: `validate`, `build`, `generate`, `events`); core calls the adapter; add `import-linter` contract "core must not import langchain/langgraph/deepagents".
- **AC:** `lint-imports` passes; all tests still pass. **Estimate:** 2 d

### M3-T2 Model catalog & Chat Model resource
- **Files:** `registry/models.yaml` (provider, model id, capabilities: tools, structured_output, vision, context_window, price in/out per 1M tokens), manifest `model.chat`.
- **Config:** `model` (select from catalog or free text), `temperature`, `max_tokens`, `timeout`, `base_url`, `api_key_secret` (secret ref, M5), `fallbacks`.
- **AC:** E030 uses the catalog's `tools` capability; unknown models allowed with warning W010. **Estimate:** 2 d

### M3-T3 Agent (Lite) — full P0 options
- **Config:** system prompt, tools (wiring), `approve_tools`, structured output schema (`response_format` → Pydantic model built from JSON Schema via `pydantic.create_model`, or pass the JSON Schema dict directly: `create_agent(..., response_format=<dict>)` is supported), middleware (wiring, ordered, M3-T12).
- **Writes:** `messages`, optional `structured_response` channel.
- **AC:** agent with structured output writes a dict matching the schema (test with a fake model returning the structured tool call). **Estimate:** 3 d

### M3-T4 Deep Agent widget (P0 options)
- **Builder:** `create_deep_agent(model=…, tools=…, system_prompt=…, subagents=[…], backend=…, interrupt_on=…, middleware=[…], name=…)`. P0 options: subagents (declarative: name, description, system_prompt, tools), backend `State` or `Composite(/memories/ → Store(user))`, planning toggle (adds `TodoListMiddleware()`), `interrupt_on`.
- **AC:** Deep Agent writes a file under `/memories/` that appears in the store for the thread's user (verified pattern: `StoreBackend(namespace=lambda rt: ("users", user_id))`). **Estimate:** 4 d

### M3-T5 MCP server resource
- **Builder:** `from langchain.mcp import MCPAdapter`; `config = {"mcpServers": {name: {"url": url, "auth": token_or_"oauth"}}}`; `async with MCPAdapter(config) as a: tools = await a.list_tools()`; filter by `tool_filter`. Tools are loaded once per graph build (async) — make `build_graph` async-capable (`abuild_graph`).
- **UI:** "Browse tools" button calls `POST /v1/mcp/inspect` to list tools with `destructive_hint`.
- **AC:** tools from a local test MCP server (FastMCP in tests) are callable by an agent; destructive tools default into `approve_tools` (W002 otherwise). **Estimate:** 3 d

### M3-T6 HTTP request tool
- **Config:** method, URL template (Jinja over tool args), headers (secret refs), JSON body schema → tool args schema, allowed domains.
- **Builder:** generate a `StructuredTool` with `args_schema` from JSON Schema; `httpx.AsyncClient` with timeout; reject hosts not in allow-list.
- **AC:** calling a disallowed domain returns a tool error, not an exception. **Estimate:** 2 d

### M3-T7 Human input node
- **Builder:** `interrupt({"kind": "input", "canvas_node_id": id, "prompt": …, "response_schema": …})`; validate the resume value against the schema; write to `output`.
- **AC:** the Inbox (M4-T4) renders a form from `response_schema`. **Estimate:** 1.5 d

### M3-T8 Map node (fan-out)
- **Pattern (verified):** conditional edge returning `[Send("worker_node", {"item": x}) for x in state[list_channel]]`; the worker writes into an `append` channel; the next node runs after all workers.
- **IR:** `core.map {items: <channel>, target: <node id>, as: "item"}`; target node receives `{"item": …}` plus read-only access to configured channels.
- **AC:** map over 5 items produces 5 results; validator requires the result channel to use `append` (E022). **Estimate:** 3 d

### M3-T9 Parallel branches & join
- **Pattern:** several flow edges from one node run in the same step; `builder.add_edge(["a", "b"], "join")` waits for both.
- **IR:** a flow edge list with multiple sources to one target marks a join (UI: `core.join` node that compiles to the multi-source edge).
- **AC:** two branches writing different channels both arrive at the join. **Estimate:** 2 d

### M3-T10 Loop guard
- **Pattern:** `core.counter` (tutorial exercise) + router; validator W001 when a cycle has no router; recursion limit from workflow settings (`config={"recursion_limit": n}`).
- **AC:** an infinite loop stops at the recursion limit with a readable `RUN_ERROR` ("GraphRecursionError: …"). **Estimate:** 1.5 d

### M3-T11 Return / output mapping
- **Steps:** workflow *output schema* (subset of channels); runner returns only those in `RUN_FINISHED.output`; generated graph uses `StateGraph(State, output_schema=Output)`.
- **AC:** API consumers see only declared outputs. **Estimate:** 1 d

### M3-T12 Middleware widgets
- **Types & verified constructors:** `PIIMiddleware(pii_type, strategy="redact|mask|hash|block", apply_to_input=True, apply_to_output=False, apply_to_tool_results=False)`, `ModelCallLimitMiddleware(thread_limit=, run_limit=, exit_behavior="end|error")`, `ToolCallLimitMiddleware(tool_name=, thread_limit=, run_limit=)`, `ModelRetryMiddleware(...)`, `ModelFallbackMiddleware(<models>)`, `SummarizationMiddleware(model, trigger=("tokens", 4000), keep=("messages", 20))`.
- **UI:** middleware resources wired into an agent's ordered `middleware` port; order = edge `order` field (drag to reorder in inspector).
- **AC:** PII redaction visible in the model input (test by capturing the fake model's received messages). **Estimate:** 3 d

### M3-T13 Node policies (retry, timeout, cache)
- **Mapping (verified):** `add_node(..., retry_policy=RetryPolicy(max_attempts=n), timeout=TimeoutPolicy(run_timeout=s, idle_timeout=s), cache_policy=CachePolicy(ttl=s))` and `compile(cache=InMemoryCache())` (Redis cache later).
- **AC:** a flaky script node succeeds on attempt 3 with `max_attempts=3`; a cached node is not re-executed on the next run (test counts calls). **Estimate:** 2 d

### M3-T14 Memory nodes: Recall / Remember / Forget + memory tools (FR-MEM-01…03)
- **IR:** `memory_spaces` in workflow settings (name, namespace template over `context`/identity only, JSON schema, index fields); nodes `core.memory_recall {space, query_template, top_k, output}`, `core.memory_remember {space, key_template, value_channel}`, `core.memory_forget`.
- **Builder:** use LangGraph's store through the runtime: `from langgraph.config import get_store` inside the node, `await store.asearch(namespace, query=…, limit=k)`, `await store.aput(namespace, key, value)`; generated memory tools for agents wrap the same calls.
- **Deep Agent:** a space can be mounted as `/memories/` via `CompositeBackend(routes={"/memories/": StoreBackend(namespace=lambda rt: …)})` (verified pattern, M3-T4).
- **User scoping (verified):** declare a run context `@dataclass class Ctx: user_id: str`; build with `StateGraph(State, context_schema=Ctx)`; nodes take `runtime: Runtime[Ctx]` (`from langgraph.runtime import Runtime`) and use `("users", runtime.context.user_id, "facts")` as namespace; the runner passes `graph.ainvoke(input, config, context=Ctx(user_id=<authenticated user>))` — never a value from the model.
- **AC:** a value remembered on thread A is recalled on thread B for the same user and **not** for another user (namespace includes user id). **Estimate:** 3 d

---

## M4 — Run & debug experience

### M4-T1 Test-run panel (chat & form input)
- **Steps:** right-panel tab "Test": if the state has `messages`, show a chat box; else a form generated from the workflow input schema; thread picker (new / existing).
- **AC:** multi-turn chat on one thread keeps history. **Estimate:** 2 d

### M4-T2 Live overlay (harden skeleton)
- **Steps:** status badges per node (idle/running/ok/error/interrupted/cached), animated flow edges for the active path (edge ids from traversed node pairs), error tooltip with message; nested agent activity shown as a small activity feed on the agent node (from `ns` events).
- **AC:** Playwright test asserts colours for success, error and interrupt runs. **Estimate:** 2 d

### M4-T3 Token streaming preview
- **Steps:** node card shows last 200 characters of streamed text; chat panel shows full stream; coalesce updates at 30 Hz (`requestAnimationFrame` batching).
- **AC:** no UI jank with 2,000 tokens/s (performance test). **Estimate:** 1.5 d

### M4-T4 Inbox (pending approvals)
- **Backend:** when a run ends with an interrupt, insert `interrupt_task` rows (id, thread, node, kind, payload, status=pending); `GET /v1/inbox?status=pending`; `POST /v1/inbox/{id}/decision` → resumes the thread (`Command(resume=…)`) and marks the task `decided`.
- **Frontend:** list + detail card; tool approvals show tool name & args with *Approve / Edit args / Reject (with message)*; input interrupts render an RJSF form from `response_schema`.
- **Resume payload rules:** tool approvals → `{"decisions": [{"type": "approve"} | {"type": "edit", "edited_action": {"name": ..., "args": {...}}} | {"type": "reject", "message": "..."}]}`; approval node → `{"approved": bool, "value": optional}`; input node → the form value.
- **AC:** approving from the Inbox continues the run; the canvas of that thread updates. **Estimate:** 4 d

### M4-T5 Timeline & history
- **Steps:** `GET /v1/threads/{id}/history` → bottom-dock scrubber (one tick per checkpoint, label = step + nodes); clicking a tick shows that checkpoint's state and highlights `next` nodes.
- **AC:** scrubbing never mutates the thread. **Estimate:** 2 d

### M4-T6 State inspector & edit
- **Steps:** JSON viewer of state at the selected checkpoint; "Edit" → `POST /v1/threads/{id}/state {values, as_node}` → creates a new checkpoint (verified `aupdate_state`).
- **AC:** editing a value shows a new checkpoint in history; continuing uses the edited value (full branch behaviour in M4-T7). **Estimate:** 1.5 d

### M4-T7 Fork from checkpoint (time travel)
- **Backend (verified patterns):**
  1. *Replay* from a past checkpoint: `await graph.ainvoke(None, snapshot.config)`.
  2. *Edit & branch* (default "Fork" button): `new_cfg = await graph.aupdate_state(snapshot.config, {"urgency": "high"})` then `await graph.ainvoke(None, new_cfg)`. The edit is recorded as if written by the node that produced that checkpoint, so routers after it are **re-evaluated** (verified: a checkpoint written after `classify` + `urgency=high` continues into `escalate`). The thread keeps all checkpoints; the new branch becomes the latest state.
  3. *Copy to a new thread* (optional): `await graph.aupdate_state({"configurable": {"thread_id": new}}, snapshot.values, as_node="__start__")` then `ainvoke(None, …)` — use when the user wants to keep the original thread's latest state untouched.
- **AC:** Fork from the classify checkpoint with an edited value takes the other branch; history shows both branches (checkpoint `parent_config` links). **Estimate:** 2 d

### M4-T8 Breakpoints
- **Backend (verified):** pass `interrupt_before=[names]` / `interrupt_after=[names]` to `astream` for **debug runs only**; continue with input `None`.
- **Frontend:** click the node gutter to toggle a red dot; "Continue" button.
- **AC:** run stops before the marked node; Continue proceeds. **Estimate:** 1.5 d

### M4-T9 Pinned outputs & mock mode
- **Steps:** pin = store last output delta for a node in `workflow.debug.pins`; interpreter wraps pinned nodes with `lambda state: pinned_delta` in debug runs; mock mode swaps models for `ToolFakeModel` with user-entered scripted replies.
- **AC:** with a pinned classifier, runs never call the model (verified with a fake that raises if called). **Estimate:** 2 d

### M4-T10 Cancel run
- **Steps:** keep `asyncio.Task` per run in a registry; `POST /v1/runs/{id}/cancel` cancels the task; status `cancelled`; UI stop button.
- **AC:** cancelling mid-run stops token streaming within 1 s; the thread can be resumed from the last checkpoint. **Estimate:** 1.5 d

### M4-T11 Cost & latency per node
- **Steps:** from `messages` stream / final AI messages read `usage_metadata` (`input_tokens`, `output_tokens`); price from model catalog; per-node duration from STEP events; emit `CUSTOM ac.usage`; store totals on `run`.
- **AC:** node cards show tokens, $, ms; run list shows totals. **Estimate:** 2 d

### M4-T12 Memory inspector (FR-MEM-04, browse/delete)
- **API:** `GET /v1/memory/{space}/items?namespace=…&q=…` (uses `store.asearch`), `DELETE /v1/memory/{space}/items/{key}`; UI table with search and delete; erasure for one user = delete all keys under their namespace prefix.
- **AC:** deleting an item removes it from subsequent recalls. **Estimate:** 2 d

---

## M5 — Workspaces, scripts, secrets

### M5-T1 Workspace storage
- **Steps:** one directory per project on a volume (`/data/workspaces/<project_id>`), initialised as a git repo (`git init` via `subprocess`); file API `GET/PUT/DELETE /v1/projects/{id}/files/{path}` with path traversal protection (`resolve()` must stay under the root).
- **AC:** `../` paths rejected (test); files persist across restarts. **Estimate:** 2 d

### M5-T2 Code editor
- **Steps:** `@monaco-editor/react` with file tree; Python syntax highlighting; save = PUT; basic diagnostics by running `ruff check --output-format=json` server-side on save (LSP is post-MVP).
- **AC:** ruff errors appear as markers in the editor. **Estimate:** 3 d

### M5-T3 Script node from workspace
- **Contract:** `scripts/<name>.py` defines `def run(state: dict) -> dict` (or `async def`); node config `import_path = "scripts.<name>:run"`; declared `reads/writes` validated against channels.
- **AC:** editing the script and re-running uses the new code without restarting the server (`importlib.reload` in dev). **Estimate:** 2 d

### M5-T4 Sandbox runner
- **Goal:** user code does not run inside the API process in hosted mode (review D-10).
- **Steps:** `sandbox/docker_runner.py`: for each script call run `docker run --rm --network none --memory 512m --cpus 1 --read-only -v <workspace>:/ws:ro agentcanvas-sandbox:py312 python -m runner <import_path>` with JSON state on stdin and JSON update on stdout; timeout 30 s; build the `agentcanvas-sandbox` image with the same Python deps. Dev mode keeps in-process execution (setting `SCRIPT_EXECUTION=inprocess|docker`).
- **AC:** a script trying to open a network socket fails in docker mode; timeouts produce `STEP_FINISHED status=error`. **Estimate:** 4 d

### M5-T5 Custom tools from the workspace
- **Steps:** scan `tools/*.py` with `ast` for functions decorated with `@tool`; register them as `tool.python` resource types in the palette (`import_path` = `tools.<file>:<name>`).
- **AC:** adding a new `@tool` function makes it appear in the palette after save. **Estimate:** 2 d

### M5-T6 Secrets vault & secret references
- **Steps:** `secret` table stores `name`, `ciphertext` (Fernet with key from `AGENTCANVAS_SECRET_KEY`; KMS in M8), `created_by`; API returns names only; config fields accept `{"$secret": "OPENAI_API_KEY"}`; `Resources` resolves secret refs at build time; validator E050 blocks literals that look like keys.
- **AC:** secret values never appear in API responses, logs, generated code or SSE events (tests grep outputs). **Estimate:** 3 d

### M5-T7 Custom node SDK (FR-CN-01…03)
- **Contract:** in `nodes/*.py` users write `@canvas_node(name="acme.upsert", label=…, category=…, inputs=[…], outputs=[…], config=PydanticModel)` over a function `(state_slice, cfg) -> dict` (spec doc05 §3). The decorator only attaches metadata.
- **Discovery:** AST scan of `nodes/*.py` (no import in the API process) → manifest (config schema from the Pydantic model via a sandboxed import in the runner) → palette entry with provenance badge "workspace".
- **Codegen:** copy the file into the exported project; builder = `ac.script_node(import_path="nodes.<file>:<fn>")` with an adapter that passes `cfg`.
- **AC:** a new custom node appears in the palette within 2 s of saving and runs in both interpreted and generated modes. **Estimate:** 4 d

---

## M6 — Export, versions, publish (**MVP release**)

### M6-T1 Versions & diff
- **Steps:** `POST /v1/workflows/{id}/versions {message}` snapshots IR + layout + workspace git commit hash into `workflow_version` (immutable, semver auto-increment); `GET /v1/workflows/{id}/versions/{a}/diff/{b}` returns added/removed/changed nodes/edges and per-field config diffs; UI colours changed nodes.
- **AC:** diff of two versions highlights exactly the edited node. **Estimate:** 3 d

### M6-T2 Project packager
- **Output layout:** `src/<pkg>/graph.py` (generated), `src/<pkg>/scripts|tools` (copied from workspace), `pyproject.toml` (pinned deps), `.env.example` (secret names), `README.md`, `Dockerfile`, `langgraph.json` (for users deploying to LangSmith: points to a module-level `graph = build_graph()` wrapper), `tests/test_smoke.py` (imports graph, draws mermaid).
- **Note:** generated code imports `agentcanvas-runtime` (the node library as a small published package, Apache-2.0). Build it from `adapters/langchain` in this ticket.
- **AC:** unzip → `uv sync && uv run pytest` passes; `docker build .` succeeds. **Estimate:** 4 d

### M6-T3 Export (zip / git push)
- **Steps:** `GET /v1/workflows/{id}/export.zip`; optional push to a git remote with a deploy key (post-MVP UI).
- **AC:** exported project runs with real models using `.env`. **Estimate:** 1 d

### M6-T4 Publish to the runner
- **Steps:** "Publish" pins a version as `deployment` (table `deployment {workflow_id, version_id, env, api_key_hash}`); public endpoints `POST /v1/deployments/{id}/runs/stream` and `/threads/{id}/resume` authenticated by `Authorization: Bearer <api key>`; runs use the pinned version, not the draft.
- **Pre-publish checklist (FR-DEP-02):** publish is blocked unless: no validation errors, all saved test cases pass (M6-T9), every `$secret` reference resolves in the target project.
- **AC:** editing the draft does not change the published behaviour; rotating the key invalidates the old one; publishing with a failing test case is refused with a clear message. **Estimate:** 3 d

### M6-T5 Webhook & cron triggers
- **Steps:** `trigger` table; webhook `POST /v1/hooks/{trigger_id}` verifies HMAC-SHA256 signature header, maps body → workflow input via Jinja mapping; cron via APScheduler (`AsyncIOScheduler`) loading triggers at startup (single instance in MVP; leader-lock in Postgres advisory lock for multi-instance).
- **AC:** invalid signature → 401; a cron every minute creates runs visible in the run list. **Estimate:** 3 d

### M6-T6 Tracing (LangSmith / OTel)
- **Steps:** if `LANGSMITH_TRACING=true`, runs are traced automatically by LangChain; add `config={"metadata": {"workflow_id":…, "canvas_version":…}, "tags": [...], "run_name": wf.name}` to every run; store trace URL on `run` (LangSmith run id from callbacks); "Open trace" link in UI.
- **AC:** a run appears in LangSmith with node names equal to IR names. **Estimate:** 2 d

### M6-T7 Templates
- **Steps:** 5 templates as IR JSON in `registry/templates/`: Support triage, Research (map over sub-questions), RAG Q&A (retriever tool), Approval workflow, Data extraction (structured output); "New from template" dialog.
- **AC:** each template validates, runs with fake models in CI and with real models manually. **Estimate:** 3 d

### M6-T9 Saved test cases (FR-EVAL-01)
- **Steps:** "Save as test case" on any finished run stores `{input, expected_output?, expected_route?}` in `workflow.test_cases` (JSONB); "Run tests" executes all cases with the current draft (optionally mock mode) and shows pass/fail per case; assertions: exact match on selected channels, route taken, no errors.
- **AC:** changing a router condition turns the affected case red. **Estimate:** 3 d

### M6-T8 MVP hardening & release
- **Steps:** error states for every screen, empty states, loading skeletons; docs site from this guide; release notes; tag `v0.1.0`.
- **AC:** 10 design-partner users complete journey J1 (doc01 §2) unaided. **Estimate:** 5 d

---

## M7 — Accounts & access

| Ticket | Summary | Est. |
|--------|---------|-----:|
| M7-T1 OIDC login | Authorization-code + PKCE in the SPA (`oidc-client-ts`); API validates JWT (issuer, audience, JWKS cache) with `pyjwt[crypto]`; dev user in fake mode. **AC:** API rejects missing/expired tokens with 401 problem+json. | 3 d |
| M7-T2 Organisations & projects | `organization`, `membership(user, org, role)`, `project`; every query filtered by org (repository layer adds `WHERE org_id = :org`). **AC:** user of org A cannot read org B's workflow (test). | 3 d |
| M7-T3 Roles | `admin`, `builder`, `viewer` (+ `reviewer` for inbox); FastAPI dependency `require(role)`; UI hides forbidden actions. **AC:** viewer cannot run or edit (403). | 2 d |
| M7-T4 Audit log | append-only `audit_log` for edits, publishes, approvals, secret access; UI table with filters. **AC:** every approval is logged with actor and payload hash. | 2 d |

## M8 — Production readiness

| Ticket | Summary | Est. |
|--------|---------|-----:|
| M8-T1 Containers | multi-stage Dockerfiles for api (uv) and web (nginx serving `dist/`); nginx `proxy_buffering off` for SSE routes. | 2 d |
| M8-T2 Helm chart / compose prod | api (N replicas), web, Postgres (managed), Redis; readiness `/healthz` (DB ping), liveness. | 3 d |
| M8-T3 Observability | OpenTelemetry for FastAPI + SQLAlchemy + httpx; Prometheus metrics (runs, errors, latency, tokens); dashboards. | 3 d |
| M8-T4 Backups & retention | Postgres PITR; checkpoint TTL job (delete threads older than policy via `adelete_thread`); restore runbook tested. | 2 d |
| M8-T5 Load test | k6: 200 concurrent streaming runs with fake models; p95 event latency < 250 ms. | 2 d |
| M8-T6 Security review | complete [10-security-checklist.md](./10-security-checklist.md); fix findings; dependency scanning in CI. | 3 d |

## M9 — After MVP (GA roadmap, follow the spec)

| Area | Spec | First ticket to write |
|------|------|------------------------|
| Evaluation studio | doc01 §4.11 | datasets from saved runs + LangSmith experiment runner |
| Multiplayer | doc02 §4.2 | Yjs document mirroring the IR; server-side validation hook |
| Persistence bindings (Redis/Mongo), RoutedStore, Memory Spaces UI | doc08 §2–3 | binding table + conformance check |
| Data Studio | doc08 §5 | ER canvas reusing React Flow; SQLAlchemy codegen |
| Learning loops | doc09 | feedback capture + example bank |
| MCP / A2A exposure, chat widget | doc01 §4.14 | MCP endpoint per deployment |
| Other frameworks | doc12 | adapter SPI v1 + OpenAI Agents SDK adapter |
| Copilot | doc01 §4.15 | command-generating agent with validator in the loop |
