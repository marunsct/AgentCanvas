# 04 — Node (Widget) Catalog

Legend — **Kind**: `R` resource (wired, constructed once), `N` runtime node (a LangGraph step), `M` middleware (wired into an agent's ordered middleware slot), `T` trigger, `O` output/channel. **Pri**: P0 / P1 / P2.
Ports are written `name: Type`. Inputs ← , outputs → .
**Framework scope (v0.3):** node types are either *core* (portable: triggers, logic & control flow, HITL, data & transform, memory, outputs, generic Agent/Subagent/Model/Tool/MCP) or *dialect* (framework-specific: Deep Agent harness options, LangChain middleware, `Send`, Deep Agents backends…). The "Compiles to" column describes the **LangChain adapter** (R1). Other adapters publish their own mappings and support levels (doc 11).

## 1. Triggers (T)

| Node | Ports / config | Compiles to | Pri |
|------|----------------|-------------|-----|
| Manual / Chat input | → `messages: Messages` or → typed input per Input schema | Graph input schema | P0 |
| API call | Input schema; auth mode | Agent Server run endpoint | P0 |
| Webhook | path, HMAC secret ref, payload schema, mapping expression, dedup key | Trigger service → run create | P0 |
| Schedule (cron) | cron, timezone, payload | Agent Server cron job | P0 |
| Event (bus) | topic pattern, filter expression, handler script (optional) | Trigger service consumer | P1 |
| Channel message | Slack / Teams / Discord / WhatsApp / Email; thread mapping | Channel adapter → thread per conversation | P1 |
| File drop | bucket/prefix, file types | Object-store notification → run | P1 |
| Form | generated form from Input schema, hosted URL | Hosted page → run | P1 |
| Workflow completed | source workflow, status filter | Chained run | P1 |
| MCP / A2A inbound | exposed tool name/description | Agent Server MCP / A2A endpoint | P1 |

## 2. Agents

| Node | Kind | Ports / config | Compiles to | Pri |
|------|------|----------------|-------------|-----|
| **Deep Agent** | N (or whole graph) | ← `model: ChatModel`, ← `tools: Tool[]`, ← `middleware: Middleware[]`, ← `subagents: Subagent[]`, ← `skills: SkillSet`, ← `memory: MemorySource[]`, ← `backend: Backend`, ← `response_format: Schema`; → `messages`, → `structured_response`, → `files`. Config: name, system prompt, planning toggle, general-purpose subagent toggle/override, interrupt_on table, permissions table, summarization thresholds, interpreter toggle + PTC allow-list, rubric. | `create_deep_agent(...)` | P0 |
| **Lite Agent** | N | ← model, tools, middleware, response_format; config: system prompt, name | `create_agent(...)` | P0 |
| **Subagent** (declarative) | R (`Subagent`) | ← model (optional override), tools, middleware, skills; config: name, description, system prompt, mode (isolated/forked), interrupt_on, permissions, response_format | `SubAgent` dict | P0 |
| **Compiled Subagent** | R | ← `graph: Flow` (a subgraph/group on the canvas); config: name, description | `CompiledSubAgent(...)` | P1 |
| **Async Subagent** | R | config: name, description, graph_id / assistant, url or ASGI co-deployed, headers | `AsyncSubAgent(...)` | P1 |
| **Supervisor** (pattern) | N (macro) | ← agents[]; routing prompt | `create_agent` with sub-agents wrapped as tools (subagents pattern) | P1 |
| **Handoff group / Swarm** (pattern) | N (macro) | ← agents[]; handoff rules | agents + `Command(goto=…, graph=Command.PARENT)` handoff tools | P1 |
| **Router agent** (pattern) | N (macro) | ← classifier model, routes[] | structured-output classifier + conditional edges | P0 |

## 3. Models & AI resources

| Node | Kind | Config | Compiles to | Pri |
|------|------|--------|-------------|-----|
| Chat Model | R | provider:model (catalog with capability profile: tool calling, structured output, vision, reasoning, context window, price), temperature, max tokens, reasoning effort, timeout, base URL, gateway toggle (`langsmith:provider/model`), rate limiter | `init_chat_model(...)` | P0 |
| Configurable Model | R | allow-listed models selectable at runtime via context/assistant config | `init_chat_model(configurable_fields=…)` / dynamic model middleware | P1 |
| Embeddings | R | provider/model, dims | `init_embeddings(...)` | P1 |
| Prompt | R | template, variables, LangSmith prompt / Context Hub ref, version pin | `ChatPromptTemplate` / hub pull | P0 |
| Schema | R | visual schema designer | Pydantic model | P0 |

## 4. Tools

| Node | Kind | Config | Compiles to | Pri |
|------|------|--------|-------------|-----|
| MCP Server | R (`Tool[]`) | transport (http/stdio/in-process), URL/command, auth (bearer/OAuth 2.1/per-user), tool filter, elicitation → interrupt | `langchain.mcp.MCPAdapter` | P0 |
| HTTP Request tool | R | method, URL template, headers (secret refs), body schema, response mapping, allowed domains | `@tool` generated function (httpx) | P0 |
| Code tool (custom) | R | workspace file + function; args schema inferred from type hints | user `@tool` | P0 |
| OpenAPI toolkit | R | spec URL/file, operations filter, auth | generated tools per operation | P1 |
| Retriever tool | R | ← `retriever: Retriever`; name/description | retriever wrapped as tool | P1 |
| Web search | R | provider (Tavily, Exa, Brave, provider-native search), limits | integration tool / server-side tool | P0 |
| Provider server tools | R | code execution, web fetch, computer use etc. (provider-native, dict tools) | tool dicts | P1 |
| SQL toolkit | R | DB connection, read-only toggle, allowed tables | SQLDatabase toolkit | P1 |
| Integration tool packs | R | Gmail, GCal, Slack, GitHub, Jira, Salesforce, HubSpot, Notion, Zendesk… (via MCP where possible) | tools / MCP | P1 |
| Workflow-as-tool | R | reference another published workflow | tool calling the Agent Server (RemoteGraph) | P1 |
| Human-as-tool (ask user) | R | question schema | tool that calls `interrupt()` | P0 |

## 5. Middleware & guardrails (M) — ordered slot on agents

| Node | Compiles to | Pri |
|------|-------------|-----|
| Human approval (tool-level) | `HumanInTheLoopMiddleware(interrupt_on={...})` | P0 |
| Summarization | `SummarizationMiddleware(model=…, trigger=…, keep=…)` | P0 |
| PII guard | `PIIMiddleware(<type>, strategy="redact|mask|hash|block", detector=<regex|callable>?, apply_to_input/output/tool_results)` — custom types via `detector` | P0 |
| Model retry | `ModelRetryMiddleware(max_retries, backoff…)` | P0 |
| Model fallback | `ModelFallbackMiddleware(<models…>)` | P0 |
| Tool retry | `ToolRetryMiddleware(...)` | P0 |
| Tool error handling | `ToolErrorMiddleware(...)` | P1 |
| Model call limit | `ModelCallLimitMiddleware(thread_limit, run_limit, exit_behavior="end")` (upstream default `end`) | P0 |
| Tool call limit | `ToolCallLimitMiddleware(tool_name?, thread_limit, run_limit, exit_behavior="continue")` (upstream default `continue`) | P0 |
| To-do planning | `TodoListMiddleware()` | P0 |
| Tool selector | `LLMToolSelectorMiddleware(model, max_tools, always_include)` | P1 |
| Provider tool search | `ProviderToolSearchMiddleware(...)` (deferred tool loading) | P2 |
| Context editing | `ContextEditingMiddleware(edits=[ClearToolUsesEdit(...)])` | P1 |
| Shell tool | `ShellToolMiddleware(execution_policy=Docker/Codex sandbox)` (admin-gated) | P2 |
| File search | `FilesystemFileSearchMiddleware(root_path=…)` | P2 |
| Tool emulator (testing) | `LLMToolEmulator(tools=[…])` | P1 |
| Rubric self-grading | `deepagents.RubricMiddleware(model, max_iterations, tools)` | P1 |
| Dynamic prompt | `@dynamic_prompt` from a template over state/context | P0 |
| Provider prompt caching | `AnthropicPromptCachingMiddleware` / Bedrock (auto on Deep Agents) | P1 |
| Custom middleware | workspace class `AgentMiddleware` with hooks `before_agent`, `before_model`, `wrap_model_call`, `after_model`, `wrap_tool_call`, `after_agent` | P0 |

## 6. Memory, files & knowledge

| Node | Kind | Compiles to | Pri |
|------|------|-------------|-----|
| State backend | R `Backend` | `StateBackend()` | P0 |
| Store backend | R `Backend` | `StoreBackend(namespace=…)` scope: user / assistant / thread / org | P0 |
| Composite backend | R `Backend` | `CompositeBackend(default=…, routes={prefix: backend})` | P0 |
| Sandbox backend | R `Backend` | provider sandbox (Daytona, Modal, Runloop, E2B, Vercel, AgentCore, LangSmith Sandboxes); scope thread/assistant; seed files; snapshot | P1 |
| Context Hub backend | R `Backend` | `ContextHubBackend(...)` (versioned files in LangSmith) | P1 |
| Local filesystem backend | R `Backend` | `FilesystemBackend(root_dir=…)` (dev/self-host only; E041 in hosted envs) | P2 |
| Local shell backend | R `Backend` | `LocalShellBackend(root_dir, virtual_mode=True, timeout, max_output_bytes)` — **dev only** (E041) | P2 |
| LangSmith sandbox backend | R `Backend` | `LangSmithSandbox(sandbox)` from a `langsmith.sandbox.SandboxClient`; policy from the sandbox policy designer (FR-X-08) | P1 |
| Skills | R `SkillSet` | skill sources (paths on backend) → `skills=[…]` | P1 |
| Memory files | R `MemorySource` | `memory=["/memories/AGENTS.md"]` | P1 |
| Long-term memory (Store) | R `Store` + N ops | `store.put/get/search` nodes with namespace templates, semantic index | P1 |
| Knowledge base | R `Retriever` | vector store retriever with filters, reranker | P1 |
| Document loader / splitter | N | loaders + text splitters (ingestion workflows) | P2 |

## 6b. Persistence, data & caching (v0.2 — see doc 08)

| Node | Kind | Compiles to | Pri |
|------|------|-------------|-----|
| Persistence (workflow chip) | binding | checkpointer / store / cache bindings, durability, TTL | P0 |
| DB Connection | R | async client/engine factory (Postgres, MySQL, SQL Server, Redis, MongoDB, DynamoDB, Neo4j, Elasticsearch, vector DBs) | P0 |
| Memory Space | R | typed namespace + index + TTL + writers | P0 |
| Routed Store | R `Store` | `agentcanvas_runtime.stores.RoutedStore(default=…, routes={…})` | P1 |
| Recall | N | `runtime.store.search/get` → channel | P0 |
| Remember / Forget | N | `runtime.store.put/delete` with schema validation & provenance | P0 |
| Memory tools | R `Tool[]` | generated `search_memory` / `save_memory` scoped to spaces | P0 |
| Profile loader | M | `@dynamic_prompt` injecting profile memory | P1 |
| Get / Find (entity) | N | generated repository call | P0 |
| Query (visual / SQL / Cypher / aggregation) | N | parameterised query | P0–P1 |
| Insert / Upsert / Update / Delete | N | idempotent repository write | P0 |
| Vector search | N / R | vector query or retriever | P1 |
| Graph traverse | N | parameterised Cypher | P2 |
| Cache get / set | N | Redis get/set with TTL | P0 |
| Transaction scope | group | single node wrapping a DB transaction | P1 |
| Data tools | R `Tool[]` | generated scoped CRUD/search tools with HITL on writes | P0 |
| CDC trigger | T | Postgres logical replication / Mongo change streams / Redis streams | P1 |

## 6c. Learning & feedback (v0.2 — see doc 09)

| Node | Kind | Pri |
|------|------|-----|
| Feedback Source | T | P1 |
| Trace Query | N | P1 |
| Memory Extractor / Consolidator | N | P1 |
| Example Curator | N | P1 |
| Few-shot Selector | M | P1 |
| Prompt Optimizer | N | P1 |
| Skill Writer | N | P2 |
| Variant Router (bandit) | N | P2 |
| Fine-tune Job | N | P2 |
| Eval Gate | N | P1 |
| Human Review (artifact) | N | P1 |
| Promote / Canary / Rollback | N | P1 |

## 7. Logic & control flow (N)

| Node | Semantics | Compiles to | Pri |
|------|-----------|-------------|-----|
| Router (expression) | routes by expression over state | `add_conditional_edges` with generated route fn | P0 |
| Router (LLM classifier) | structured-output classification → route | model node + conditional edges | P0 |
| If / Else | boolean expression | conditional edges | P0 |
| Switch | enum match | conditional edges | P0 |
| Parallel (split) | run branches concurrently | multiple edges from one node | P0 |
| Join / Barrier | wait for all branches | `add_edge([a,b], join)` | P0 |
| Map (fan-out) | for each item → worker (subgraph) | `Send` + reducer | P0 |
| Reduce | aggregate list channel | node with reducer logic | P0 |
| Loop (while / until) | guarded loop with max iterations | counter channel + router | P0 |
| Evaluator–Optimizer | generate → critique → revise until pass/max | macro (two nodes + router) | P1 |
| Wait / Delay | durable sleep until time/event | interrupt + scheduled resume (cron/trigger) | P2 |
| Subgraph (inline) | nested graph | compiled subgraph node | P0 |
| Call workflow | invoke another published workflow | `RemoteGraph` node | P1 |
| Deferred node | runs when all other branches done | `add_node(..., defer=True)` | P2 |
| Error handler | receives `langgraph.errors.NodeError`, routes/compensates | `error_handler=` target | P1 |
| End / Return | explicit output mapping | edge to END + output schema | P0 |

## 8. Human-in-the-loop (N)

| Node | Semantics | Compiles to | Pri |
|------|-----------|-------------|-----|
| Approval | approve / reject (+edit of proposed payload) | `interrupt({...})` + resume handling | P0 |
| Human input | ask questions via generated form | `interrupt(form_schema)` | P0 |
| Review & edit state | human edits a channel value | `interrupt` returning edited value | P1 |
| Assignment | route to role/user with SLA | interrupt payload metadata consumed by Inbox | P1 |

## 9. Data & transform (N)

| Node | Compiles to | Pri |
|------|-------------|-----|
| Set / Map fields (expression) | generated pure function | P0 |
| Template (text) | Jinja-sandboxed render | P0 |
| Parse JSON / Validate schema | pydantic validation | P0 |
| Extract (LLM structured output) | `model.with_structured_output(Schema)` | P0 |
| Classify (LLM) | structured output enum | P0 |
| Summarize (LLM) | prompt + model | P0 |
| Code interpreter (JS) step | QuickJS evaluation over state | P2 |
| Python script | user function in sandbox or trusted in-process | P0 |

## 10. Outputs & channels (O)

| Node | Compiles to | Pri |
|------|-------------|-----|
| Return / Response | output schema mapping | P0 |
| Send message (Slack/Teams/Email) | channel adapter tool/node | P1 |
| Webhook out | HTTP POST node (signed) | P0 |
| Write to DB / sheet | integration node | P1 |
| Emit event | publish to event bus (for chaining & custom events) | P1 |
| Stream custom UI event | `get_stream_writer()` payload for generative UI | P1 |

## 11. Evaluation nodes (design-time)

| Node | Purpose | Pri |
|------|---------|-----|
| Test case | attach inputs + expectations to the workflow | P0 |
| Evaluator | LLM-judge / code / trajectory / schema match | P1 |
| Dataset | LangSmith dataset reference | P1 |

## 11b. v0.6 additions (doc 15)

| Node | Kind | Compiles to | Pri |
|------|------|-------------|-----|
| Headless (client) tool | R `Tool` | server tool whose body calls `interrupt({"kind": "client_tool", ...})`; chat widget executes and resumes with the result (FR-X-03) | P1 |
| UI Output (generative UI) | N | writes a UI spec validated against the project's component catalog to a `ui` channel and emits `custom` stream events (FR-X-04) | P1 |
| Decide (decision model) | N / evaluator | typed decision-model call via LLM Gateway (probability / choice / score), fallback to structured-output LLM (FR-X-05) | P2 |
| Budget guard | M / policy | `ModelCallLimitMiddleware` + runner budget check on usage metadata (FR-X-15) | P1 |
| Summarization tool | M | Deep Agents `SummarizationToolMiddleware` — agent-invoked compaction | P1 |
| Emit event | N | `runtime.emit(event_type, payload)` → `custom` stream + event bus, schema-checked (FR-X-22) | P1 |
| Wait for event | N | `interrupt({"kind": "wait_event", "event": …, "correlation": …})`; resumed by a trigger handler (`act.resume`) | P1 |

## 11c. Implemented type ids (product, Sprints 5–6)

| Catalog entry | Type id | Kind |
|---------------|---------|------|
| Chat Model | `model.chat` | resource → `ChatModel` |
| Code tool (custom) · HTTP request tool | `tool.python` · `tool.http` | resource → `Tool` |
| MCP server · Memory tools | `mcp.server` · `tool.memory` | resource → `Tool[]` (spread into the agent's tools) |
| Recall · Remember · Forget | `core.memory_recall` · `core.memory_remember` · `core.memory_forget` | runtime (namespace = space, scope, owner) |
| Subgraph | `core.subgraph` (embedded workflow + input/output channels) | runtime |
| LLM call · Lite Agent · Deep Agent | `core.llm` · `core.agent` · `agent.deep` | runtime |
| Subagent (declarative) | `agent.subagent` | resource → `Subagent` (← model, tools, middleware) |
| State backend · Store backend | `backend.state` · `backend.store` (mount folder, scope user/workflow/thread) | resource → `Backend` |
| Middleware: PII guard, model/tool call limit, model/tool retry, model fallback, summarization, to-dos, tool selector | `mw.pii`, `mw.model_call_limit`, `mw.tool_call_limit`, `mw.model_retry`, `mw.tool_retry`, `mw.model_fallback` (← models), `mw.summarization` (← model), `mw.todo`, `mw.tool_selector` (← model) | resource → `Middleware` |
| Router · Map · Join | `core.router` · `core.map` (outputs `each`, `empty`) · `core.join` | runtime |
| Approval · Human input | `core.approval` · `core.human_input` | runtime |
| Set values · Python script | `core.set` · `core.script` | runtime |

Resources may take inputs from other resources (a fallback middleware takes models, a subagent takes a model and tools); the Execution Plan builds them in dependency order. Ordered multi-ports (`middleware`, `tools`, `subagents`) use the edge `order`.

## 12. Node type manifest (for built-ins and custom nodes)

```yaml
name: agent.deep
version: 1.3.0
display: { label: Deep Agent, icon: brain, category: Agents, color: "#7C3AED" }
kind: runtime            # runtime | resource | middleware | trigger | output
ports:
  inputs:
    - { name: model, type: ChatModel, required: true, wiring: true }
    - { name: tools, type: "Tool[]", multi: true, wiring: true }
    - { name: middleware, type: "Middleware[]", multi: true, ordered: true, wiring: true }
    - { name: subagents, type: "Subagent[]", multi: true, wiring: true }
    - { name: backend, type: Backend, wiring: true }
    - { name: response_format, type: Schema, wiring: true }
  outputs:
    - { name: next, type: Flow }
reads: [messages, files]
writes: [messages, files, structured_response]
config_schema: { $ref: "./schemas/agent.deep.config.json" }
targets: { python: full, mda: full, typescript: partial }
side_effects: []          # v0.6: external_write | message_send | code_exec | spend | pii_read (blast-radius map, FR-X-02)
stability: stable         # v0.6: stable | beta | experimental (upstream API maturity; I010)
codegen: { template: "./templates/agent_deep.py.j2", runtime_impl: "agentcanvas_runtime.nodes.agent_deep:build" }
docs: "https://…/nodes/agent-deep"
```
