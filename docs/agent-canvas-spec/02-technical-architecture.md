# 02 — Technical Architecture

## 1. Architectural principles

1. **IR-centric.** Every surface (canvas, copilot, API, import) mutates the Graph IR through one command API; every consumer (validator, compiler, interpreter, diff, cost estimator) reads the IR.
2. **Don't rebuild the runtime.** LangGraph (via LangGraph Agent Server) is the execution engine. Our runtime layer only *assembles* graphs, *routes* streams, and *manages* tenancy, secrets and sandboxes.
3. **Control plane / data plane split.** The control plane (editor, IR store, compiler, registry, auth) never executes user code or LLM calls. The data plane (Agent Server workers + sandboxes) does, per tenant, per environment.
4. **Everything is an event.** Edits, runs, interrupts, deploys and triggers flow through an event bus; the UI subscribes via WebSocket.
5. **Generated code is the contract.** The compiled Python project is exactly what runs in prod; debug instrumentation is added via middleware/callbacks, not by changing semantics.
6. **Framework-agnostic core (v0.3).** All framework-specific logic (LangChain/LangGraph/Deep Agents in R1) lives in **adapter packages** behind the Framework Adapter SPI; services consume Core IR and canonical run events only; import boundaries enforced in CI. See [doc 11](./11-framework-agnostic-plan.md).

## 2. High-level architecture

```
                         ┌──────────────────────────── Browser (SPA) ─────────────────────────────┐
                         │ React 19 + TS │ React Flow (xyflow) canvas │ Monaco + LSP │ Yjs CRDT     │
                         │ Zustand store │ TanStack Query │ WebSocket client │ Chat/HITL components │
                         └──────────┬──────────────────────┬───────────────────────┬──────────────┘
                                    │ HTTPS (REST/GraphQL) │ WSS (collab, runs)    │ WSS (LSP, pty)
   ┌────────────────────────────────▼──────────────────────▼───────────────────────▼──────────────┐
   │                                   API Gateway / BFF (auth, rate-limit, tenancy)               │
   └───┬──────────────┬──────────────┬──────────────┬──────────────┬──────────────┬───────────────┘
       │              │              │              │              │              │
 ┌─────▼─────┐ ┌──────▼─────┐ ┌──────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐ ┌─────▼────────┐
 │ Project & │ │ Collab     │ │ Compiler &  │ │ Run        │ │ Workspace  │ │ Trigger &    │
 │ IR service│ │ service    │ │ Validator   │ │ Orchestr.  │ │ service    │ │ Event service│
 │ (CRUD,    │ │ (Yjs, y-   │ │ (IR→code,   │ │ (dispatch, │ │ (git, files│ │ (webhooks,   │
 │ versions, │ │ websocket, │ │ type check, │ │ stream     │ │ LSP proxy, │ │ cron, queues,│
 │ git)      │ │ presence)  │ │ packaging)  │ │ fan-out,   │ │ sandboxes, │ │ channels,    │
 └─────┬─────┘ └──────┬─────┘ └──────┬──────┘ │ HITL inbox)│ │ pkg build) │ │ handlers)    │
       │              │              │        └─────┬──────┘ └─────┬──────┘ └─────┬────────┘
       │              │              │              │              │              │
 ┌─────▼──────────────▼──────────────▼──────────────▼──────────────▼──────────────▼─────────┐
 │  Postgres (control DB) │ Redis (pub/sub, locks, cache) │ Object store (S3: builds,files) │
 │  NATS JetStream / Kafka (event bus) │ Vault/KMS (secrets) │ Node & package registry (OCI)  │
 └───────────────────────────────────────────────┬──────────────────────────────────────────┘
                                                 │ deploy / invoke (Agent Server API)
 ┌───────────────────────────────────────────────▼──────────────────────────────────────────┐
 │ DATA PLANE (per region; per tenant namespace or dedicated)                                │
 │  ┌──────────────────────────────┐   ┌─────────────────────────┐   ┌────────────────────┐  │
 │  │ LangGraph Agent Server pools │   │ Sandbox providers       │   │ Knowledge services │  │
 │  │  - Dev pool (interpreted     │   │ (LangSmith Sandboxes,   │   │ (pgvector, ingest  │  │
 │  │    mode, hot reload)         │   │  Daytona, Modal, E2B,   │   │  workers)          │  │
 │  │  - Deployed revisions        │   │  Runloop, Docker/gVisor)│   └────────────────────┘  │
 │  │  Postgres checkpointer+store │   └─────────────────────────┘                           │
 │  │  Redis task queue            │                                                         │
 │  └──────────────┬───────────────┘                                                         │
 └─────────────────┼─────────────────────────────────────────────────────────────────────────┘
                   │ traces, feedback, datasets, evals, prompts/Context Hub, LLM Gateway
             ┌─────▼──────┐
             │ LangSmith  │  (SaaS, hybrid or self-hosted)
             └────────────┘
```

## 3. Technology stack & rationale

### 3.1 Frontend

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Framework | **React 19 + TypeScript**, Vite | Ecosystem; React Flow; LangGraph `useStream` React SDK for chat. |
| Canvas | **React Flow / xyflow** (MIT) with custom node renderers; **ELK.js** auto-layout | Proven for node editors (used by many workflow tools); handles virtualized rendering; custom edges. Alternative evaluated: LiteGraph.js (ComfyUI's) — faster raw canvas, but weaker React integration & accessibility. Keep a WebGL LOD layer option for > 1k nodes. |
| State | Zustand (UI), **Yjs** (shared doc for multiplayer IR), TanStack Query (server data) | CRDT gives offline + multiplayer; Yjs doc maps to IR JSON. |
| Forms | JSON-Schema-driven forms (RJSF-style custom renderer) | Node config forms generated from node-type schema. |
| Code | **Monaco** + `monaco-languageclient` → pyright/basedpyright & ruff LSP over WebSocket | VS Code-grade editing in browser. |
| Terminal | xterm.js ↔ sandbox pty | Workspace terminal. |
| Chat/HITL | LangGraph SDK `useStream` / Agent Chat UI components; AG-UI for generative UI | Reuse the ecosystem's streaming protocol & interrupt handling. |
| Styling | Tailwind + Radix primitives; design tokens; dark/light | Accessible primitives. |

### 3.2 Backend (control plane)

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Language | **Python 3.12+** for Compiler, Validator, Run Orchestrator, Workspace service; **FastAPI** + Pydantic v2; uv for packaging | The compiler must import and introspect LangChain/LangGraph types, run LibCST/ruff/pyright, and share schemas with the interpreter. One language for IR models end-to-end (Pydantic → JSON Schema → TS types generated for the frontend). |
| Collab service | Node.js (`y-websocket` / Hocuspocus) or `ypy-websocket` | Mature Yjs server. |
| API style | REST (OpenAPI 3.1) + WebSocket; GraphQL optional for BFF | OpenAPI → generated TS client. |
| Async jobs | **MVP:** `asyncio` background tasks + APScheduler for cron (no job framework). **GA:** **Temporal** (or Hatchet) for long control-plane workflows (deploys, eval experiments, ingestion) | Durable, retryable, observable. |
| DB | **PostgreSQL 16** (control DB) with row-level security per tenant | Relational integrity, JSONB for IR snapshots. |
| Cache/pubsub | Redis 7 | Locks, presence, rate limiting, stream fan-out. |
| Event bus | NATS JetStream (default) / Kafka (enterprise) | Triggers, audit, run events. |
| Object storage | S3-compatible | Build artifacts, workspace snapshots, uploaded files, knowledge docs. |
| Secrets | HashiCorp Vault / cloud KMS + envelope encryption | Secret references resolved only in the data plane. |
| Registry | OCI registry for images; private PyPI (devpi/Artifactory) for custom node wheels | Reproducible builds. |
| Auth | OIDC (Keycloak/Auth0/WorkOS), SCIM; JWT to services; OpenFGA (Zanzibar) for fine-grained authz | RBAC + ABAC + sharing. |

### 3.3 Data plane

| Concern | Choice |
|---------|--------|
| Execution engine | **LangGraph Agent Server** (langgraph-api) — self-hosted standalone containers in our K8s, or customer's LangSmith Deployment. Provides assistants, threads, runs, crons, webhooks, streaming, double-texting, stores, MCP & A2A endpoints. |
| Persistence | Default: Postgres checkpointer (`langgraph-checkpoint-postgres`) and Postgres store with pgvector. Per-environment **bindings** to other checkpointers (Redis `langgraph-checkpoint-redis`, MongoDB, DynamoDB/Valkey `langgraph-checkpoint-aws`, Cosmos DB, CockroachDB, …) via Agent Server `checkpointer.backend` / custom checkpointer, and a **RoutedStore** spanning several store backends. TTLs, `DeltaChannel`, `EncryptedSerializer`. Full design in doc 08. |
| Business data | Customer databases (Postgres, MySQL, SQL Server, Redis, MongoDB, DynamoDB, Neo4j, Elasticsearch, vector DBs, warehouses) reached through the **Connection Broker**; models, migrations, repositories and tools generated by **Data Studio** (doc 08 §5). |
| Learning | Learning Service + LangSmith feedback/datasets/experiments/prompts (Context Hub), LangMem-style memory managers & prompt optimisers, Smithtune fine-tuning (doc 09). |
| Queue | Redis-backed Agent Server task queue. |
| Sandboxes | Pluggable: LangSmith Sandboxes, Daytona, Modal, E2B, Runloop, Vercel, AWS AgentCore; self-hosted fallback: Firecracker/gVisor pods. |
| Code interpreter | `langchain-quickjs` (in-process QuickJS for Deep Agents interpreters/PTC). |
| Models | Direct providers via `init_chat_model("provider:model")` or LangSmith **LLM Gateway** (`langsmith:provider/model`) for policies, fallbacks, spend limits. |
| Observability | LangSmith tracing (`LANGSMITH_TRACING`, project per workflow/env) + OTel for infra. |

### 3.4 Why Python as the generated language (and when TS)

| Criterion | Python | TypeScript |
|-----------|--------|-----------|
| LangGraph feature parity | Full (timeouts, error handlers, RunControl drain are Python-only per LangGraph 1.2) | Most features; some lag |
| Deep Agents | Full incl. `RubricMiddleware`, interpreters, profiles, `permissions` with `interrupt` mode | Subset (e.g. permissions modes allow/deny only) |
| Integrations (loaders, vector stores, tools) | Largest | Large |
| Script nodes for data work | pandas, numpy, scikit | Limited |
| Edge / serverless / Next.js embedding | Weaker | Strong |

**Decision:** Python is the canonical target (P0). The IR is language-neutral; a TypeScript target is added in P2 with a *capability matrix* that disables unsupported nodes when TS is selected.

## 4. Services in detail

### 4.1 Project & IR Service
- CRUD for orgs/projects/workflows/components; stores **IR documents** as canonical JSON (JSONB) plus Git mirror.
- **Command API**: all mutations are typed commands (`AddNode`, `RemoveNode`, `Connect`, `Disconnect`, `SetConfig`, `MoveNodes`, `Group`, `ConvertToSubgraph`, `SetStateChannel`, …). Commands are validated, applied to the Yjs doc, persisted, and emitted as events. The Copilot and public API use the same commands.
- Versioning: `commit` snapshots IR + workspace tree hash; tags for published revisions.
- Schema migration of IR documents (`ir_version`) with upgrade functions.

### 4.2 Collaboration Service

> **v0.5:** post-MVP. The MVP uses REST autosave with optimistic locking (`If-Match` / HTTP 409) — review C-07.
- Yjs documents per workflow (`Y.Map` for nodes, edges, state schema; `Y.Array` for ordering), awareness (cursors, selection), persistence snapshots to Postgres every N updates.
- Server-side validation hook: rejects updates that break IR structural invariants (not semantic validity — drafts may be invalid).

### 4.3 Compiler & Validator Service
- Stateless Python workers. Input: IR + node registry versions + target. Output: diagnostics and/or a **build artifact** (Python project tarball + manifest + source map). See doc 03.
- Warm pool with pre-imported `langchain`, `langgraph`, `deepagents`, and popular integrations for fast introspection.
- Incremental compile: per-node codegen cache keyed by (node config hash, node type version, target).

### 4.4 Run Orchestrator
- Resolves *where* to run: dev pool (interpreted mode) for canvas test runs, or a deployed revision.
- Creates threads/runs through the Agent Server API; subscribes to the stream; **re-publishes normalized run events** to the UI over WebSocket, enriched with `canvas_node_id`.
- Maintains the **Inbox** (pending interrupts index) by consuming interrupt events; resumes runs via `Command(resume=...)`.
- Manages breakpoints (debug runs pass `interrupt_before`/`interrupt_after` lists), state edits (`update_state`), history (`get_state_history`), forks (run from checkpoint id), cancel and drain.
- Pinned outputs & mocks: implemented by injecting a debug middleware/wrapper at interpretation time (never in production builds).

### 4.5 Workspace Service
See doc 05. Git repo per project, file API, LSP proxy, sandbox lifecycle, dependency resolution (`uv lock`), custom-node discovery and registration (AST scan + import in sandbox), test runner, package build.

### 4.6 Trigger & Event Service
See doc 05. Ingress for webhooks (HMAC verified), cron scheduler (or delegates to Agent Server crons), queue consumers, channel adapters (Slack, Teams, email). Runs **event handler** scripts in sandboxes; maps events to workflow inputs; idempotency & dedup; DLQ.

### 4.7 Deploy Service
- Builds images (`langgraph build`-equivalent Dockerfile) from compiled artifacts; pushes to OCI registry; rolls out revisions to the data plane (K8s operator) or calls LangSmith Deployment control-plane API / `mda deploy`.
- Manages assistants, crons, webhooks, auth config, env secrets, canary weights, rollbacks.

### 4.8 Eval Service
- Wraps LangSmith datasets/experiments API; runs experiments as Temporal workflows; computes gates; stores summaries for the UI.

### 4.9 Copilot Service
- A Deep Agent (dogfooding) with tools: `get_ir`, `search_node_catalog`, `apply_commands` (dry-run → diagnostics → propose), `explain_trace`, `read_docs` (indexed LangChain docs), `write_workspace_file`. Proposals returned as command batches rendered as ghost diffs.

### 4.10 Data Studio Service (control plane)
Data-model CRUD & versioning, physical mappings, code generation (SQLAlchemy/Pydantic/Redis/Mongo/Neo4j/Elastic), migration planning (autogenerate against a shadow DB in a sandbox), approvals and per-environment application. Introspection and migrations execute in the data plane through the Connection Broker — the control plane never connects to customer databases. See doc 08 §5, §7.

### 4.11 Connection Broker (data plane)
Pooled connections and short-lived credentials for customer databases (IAM auth preferred), private connectivity (PrivateLink, VPC peering, SSH tunnel), least-privilege roles per workflow, per-query timeouts, and a data-access audit stream.

### 4.12 Persistence & Retention Workers (data plane)
Conformance testing of persistence bindings, TTL sweeps, checkpoint pruning/archival to object storage, encryption key rotation, and cross-layer right-to-erasure orchestration (threads, store namespaces, business-data hooks, examples/datasets, traces).

### 4.13 Learning Service
Runs Learning Loops (doc 09): trace queries, memory extraction, example curation, prompt optimisation, eval gates, human review cards, canary routing and auto-rollback. Implemented as AgentCanvas workflows deployed to a dedicated learning assistant (dogfooding) plus Temporal for long jobs (fine-tuning).

### 4.14 Adapter Registry (v0.3)
Registers installed Framework Adapters (manifest, capability matrix, node library, supported stacks & hosting modes); compiler workers and runtime images load adapters by id/version; exposes portability analysis to the UI. R1 contains only `agentcanvas-adapter-langchain`.

## 5. Two execution modes (critical for WYSIWYG)

| | **Interpreted mode** (canvas test runs) | **Compiled mode** (export/deploy) |
|---|---|---|
| How | A generic graph factory in `agentcanvas-runtime` reads the IR at run start and builds the `StateGraph` / `create_deep_agent` objects dynamically. Registered as an Agent Server graph via a **factory function** keyed by `workflow_id@draft_hash`. | Compiler emits a standalone Python project; Agent Server loads the compiled graph once at startup. |
| Latency to first run after edit | ~0 (no build) | Build + deploy (tens of seconds) |
| Debug hooks | Breakpoints, pins, mocks, per-node caches, extra stream metadata | Only tracing metadata |
| Semantics | **Must equal compiled mode.** Both use the same node implementation library (`agentcanvas_runtime.nodes.*`) — codegen emits calls into the same functions or inlines equivalent code. | |
| Guarantee | Differential test suite (TR-COMP-40): for each template and fuzzed IR, run both modes with a fake model and recorded tools; compare state trajectories and outputs. | |

## 6. Run event streaming pipeline

```
Agent Server run ──(stream_mode: values/updates/messages/custom/debug/tasks/checkpoints, subgraphs=True)──►
Run Orchestrator normalizer ──► Redis/NATS topic run.{run_id} ──► WS gateway ──► Browser
```

- The runner uses LangGraph's **v2 typed stream parts** (`graph.astream(..., version="v2")`: `type`, `ns`, `data`). The **v3 `stream_events`** protocol exists but emits an *experimental* warning in langgraph 1.2.12 — adopt it only when stable (review P-05).
- **Node mapping**: LangGraph node name = IR node `name` (unique snake_case); the runner keeps a `name → canvas_node_id` index, and every node also carries `metadata={"canvas_node_id": ...}` for traces (review C-01). Subagent events carry namespaces (`ns`) which map to nested canvases.
- **Wire format:** the canonical AG-UI-based events of [doc 12 §C.3](./12-multi-framework-technical-spec.md#c3-canonical-run-event-protocol-ag-ui-based) are authoritative (review C-02); the envelope below is the transport wrapper used when events are relayed via a bus.
- UI event envelope (relay):

```jsonc
{
  "run_id": "…", "thread_id": "…", "seq": 1042, "ts": "…",
  "kind": "node.start|node.end|node.error|token|tool.call|tool.result|state.update|interrupt|custom|checkpoint|run.end",
  "canvas_node_id": "n_7f3a", "ns": ["researcher:3f2…"], "step": 7,
  "payload": { … }            // redacted per policy before leaving the data plane
}
```
- Back-pressure: token events are coalesced (≤ 30 Hz per node) for the canvas; the chat panel receives full token stream.
- Reconnect: clients resume from `seq` (events buffered 15 min in JetStream); after that, rebuild from checkpoints/history.

## 7. Data model (control DB, simplified)

> **v0.5:** the authoritative MVP DDL is [build-guide/reference/infra/schema.sql](./build-guide/reference/infra/schema.sql) (verified on Postgres 16), explained in [build-guide/05-backend-guide.md §3](./build-guide/05-backend-guide.md#3-database-schema-mvp). The table below is the long-term (GA) model (review C-04).

```sql
org(id, name, plan, region, settings jsonb, created_at)
workspace(id, org_id, name, settings jsonb)                      -- tenancy boundary
member(id, org_id, user_id, role, attrs jsonb)
project(id, workspace_id, name, git_remote, default_branch, settings jsonb)
workflow(id, project_id, slug, name, kind /*workflow|component*/, current_draft_id, created_by)
ir_draft(id, workflow_id, yjs_state bytea, ir jsonb, ir_version, updated_at, updated_by)
workflow_version(id, workflow_id, semver, ir jsonb, ir_hash, workspace_tree_hash,
                 message, author_id, created_at, parent_id)
build(id, workflow_version_id, target /*python|mda|ts*/, status, artifact_uri, source_map_uri,
      diagnostics jsonb, created_at)
environment(id, project_id, name /*dev|staging|prod|…*/, config_overrides jsonb)
deployment(id, environment_id, workflow_id, provider /*canvas_cloud|langsmith|mda|k8s*/,
           external_ref, status, url)
revision(id, deployment_id, build_id, traffic_weight, status, created_at)
assistant(id, deployment_id, external_assistant_id, name, config jsonb)
connection(id, workspace_id, kind, name, config jsonb, secret_ref, scope)
secret(id, workspace_id, env_id, name, vault_path, version, created_by)   -- values never in DB
node_type(id, registry /*builtin|org|community|local*/, name, version, manifest jsonb,
          package_ref, signature, status)
trigger(id, workflow_id, env_id, kind, config jsonb, input_mapping jsonb, enabled)
event_handler(id, project_id, event_pattern, script_path, config jsonb, enabled)
run_index(id, run_id, thread_id, workflow_id, version_id, env_id, status, started_at,
          ended_at, cost_usd, tokens_in, tokens_out, trace_url)               -- denormalized
interrupt_task(id, run_id, thread_id, canvas_node_id, kind /*approval|input|elicitation*/,
               payload jsonb, assignee, due_at, status, decision jsonb, decided_by, decided_at)
dataset_link(id, workflow_id, langsmith_dataset_id), eval_gate(id, workflow_id, env_id, rules jsonb)
comment(id, workflow_id, canvas_node_id, thread_id, author_id, body, resolved)
-- v0.2: persistence, data modelling & learning (details in doc 08 §7, doc 09)
persistence_binding(id, env_id, logical_name, kind, config jsonb, conformance_status, verified_at)
memory_space(id, workflow_id, name, config jsonb)
data_model(id, project_id, name, current_version_id)
data_model_version(id, data_model_id, semver, model jsonb, created_by, created_at)
physical_mapping(id, data_model_version_id, entity, store_kind, connection_name, config jsonb)
migration(id, data_model_version_id, env_id, store_kind, plan jsonb, script_uri, status, applied_by, applied_at)
feedback_schema(id, workflow_id, keys jsonb)
learning_loop(id, workflow_id, kind, config jsonb, enabled)
learned_artifact(id, workflow_id, kind /*memory_batch|example_batch|prompt|skill|assistant_config|model*/, version, ref, provenance jsonb, status /*candidate|approved|promoted|rolled_back*/, eval_report jsonb)
erasure_request(id, org_id, subject_ref, scope jsonb, status, report jsonb, requested_at, completed_at)
audit_log(id, org_id, actor_id, action, target, before jsonb, after jsonb, ip, ts)
```

Row-level security on `workspace_id`. Checkpoints/threads/store live in the **data-plane** Postgres (Agent Server-managed), not the control DB.

## 8. Public API (control plane, excerpt)

```
POST   /v1/projects/{p}/workflows                         create
GET    /v1/workflows/{w}/ir                                get IR (draft or ?version=)
POST   /v1/workflows/{w}/commands                          apply command batch (dry_run?)
POST   /v1/workflows/{w}/validate                          diagnostics
POST   /v1/workflows/{w}/compile   {target}                build → artifact + source map
GET    /v1/workflows/{w}/code?target=python                generated code (read-only)
POST   /v1/workflows/{w}/versions  {message, semver}       commit
GET    /v1/workflows/{w}/versions/{a}/diff/{b}             IR diff
POST   /v1/workflows/{w}/runs      {input, thread_id?, mode: debug|normal, breakpoints[], pins{}, mocks{}}
GET    /v1/runs/{r}/events  (WS)                           normalized event stream
POST   /v1/runs/{r}/resume         {interrupt_id, decision}
POST   /v1/threads/{t}/state       {values, as_node?, checkpoint_id?}   edit state
POST   /v1/threads/{t}/fork        {checkpoint_id}
GET    /v1/threads/{t}/history
POST   /v1/workflows/{w}/publish   {env, target, canary?}
POST   /v1/deployments/{d}/rollback {revision_id}
CRUD   /v1/triggers, /v1/event-handlers, /v1/connections, /v1/secrets, /v1/node-types
GET    /v1/inbox?assignee=me
GET/PUT /v1/environments/{e}/bindings                 persistence & connection bindings
POST   /v1/environments/{e}/bindings/{b}/conformance  run checkpointer/store conformance suite
GET    /v1/threads?workflow=&user=&status=            thread browser; DELETE /v1/threads/{t}; POST /v1/threads/{t}/copy
GET    /v1/memory/{space}/items?ns=&q=                 memory inspector (search, get); PATCH/DELETE items
CRUD   /v1/data-models, /v1/data-models/{m}/versions   Data Studio
POST   /v1/data-models/{m}/introspect {connection}     reverse engineering
POST   /v1/data-models/{m}/migrations {env}            plan → approve → apply
POST   /v1/feedback                                    feedback on run/thread/node
CRUD   /v1/learning-loops; GET /v1/learned-artifacts; POST /v1/learned-artifacts/{a}/promote|rollback
POST   /v1/erasure-requests
POST   /v1/copilot/sessions/{s}/messages
```

The **runtime API** for published agents is the standard **Agent Server API** (assistants, threads, runs, stream, crons, store) plus MCP (`/mcp`) and A2A endpoints — we do not invent a proprietary runtime API.

## 9. Security architecture

| Threat | Control |
|--------|---------|
| Malicious/buggy user code (scripts, custom nodes, handlers) | Executes only in sandboxes (microVM/gVisor), no control-plane credentials, CPU/mem/time quotas, read-only root FS, egress allow-list per project, no metadata-service access. Custom nodes that run *inside* the Agent Server process (non-sandboxed "trusted" nodes) require org-admin approval + signature. |
| Prompt injection → harmful tool use | HITL on destructive tools (default for MCP tools with `destructive_hint`), `ToolCallLimitMiddleware`, filesystem permissions, sandbox for `execute`, content screening middleware, output validation (structured output), allow-listed domains for HTTP tools. |
| Secret leakage | Secrets only injected into data-plane env at runtime; never in IR/code/traces; trace redaction rules; `mask_inputs_outputs`; secret scanning on workspace commits. |
| PII | `PIIMiddleware` (redact/mask/hash/block) on inputs/outputs/tool results; PII-safe trace masking; data retention & erasure. |
| Tenant isolation | Per-tenant K8s namespaces (shared tier) or dedicated clusters (enterprise); per-tenant Postgres schemas/DBs for checkpoints; RLS in control DB; per-tenant KMS keys. |
| Supply chain (custom nodes/marketplace) | Signed wheels (Sigstore), SBOM, vulnerability scanning, pinned hashes, private mirror, review workflow. |
| AuthN/Z | OIDC, short-lived JWTs, OpenFGA checks on every API call, API keys hashed, per-key scopes. |
| Data exfiltration / over-broad data access by agents | Generated repositories require a tenant/user scope from authenticated `Runtime` identity; field allow-lists on data tools; least-privilege DB roles; optional Postgres RLS; parameterised queries only; HITL on writes; data-access audit. |
| Memory poisoning (persistent prompt injection) | Untrusted-source tagging, quarantine & review, read-only shared/org memory, write approvals, provenance & bulk revert (doc 09 §6). |
| Expression language abuse | Expressions use a sandboxed, side-effect-free evaluator (e.g. CEL / JSONata / restricted Jinja), never `eval`. |

## 10. Deployment topology & scaling

- Kubernetes (EKS/GKE/AKS); Helm charts; Terraform modules. Regions: US, EU at GA.
- Control plane stateless services autoscaled via HPA; compiler workers scale on queue depth.
- Data plane: Agent Server API pods + worker pods (scale on queue depth), Postgres (HA, PITR), Redis (HA). Dev pool uses a multi-tenant factory-function server with strict per-run isolation; production deployments per workflow revision (or packed per tenant).
- **Deployment options for customers**: SaaS; **Hybrid** (control plane SaaS, data plane in customer VPC — mirrors LangSmith hybrid/BYOC); fully self-hosted (air-gapped option with local models via vLLM/Ollama).

## 11. Testing strategy (platform)

| Layer | Tests |
|-------|-------|
| IR & commands | Property-based tests (Hypothesis) for command application, undo/redo invariants, CRDT convergence. |
| Validator | Golden diagnostics for a corpus of broken graphs. |
| Compiler | Golden-file snapshot tests per node type & template; generated code must pass `ruff check`, `ruff format --check`, `pyright --strict` (on generated code), and import cleanly. |
| Semantic equivalence | TR-COMP-40 differential tests interpreted vs compiled with `GenericFakeChatModel` and recorded tool cassettes. |
| Runtime | Integration tests against a local Agent Server (`langgraph dev`) in CI; interrupt/resume, time travel, subgraph streaming. |
| Frontend | Component tests (Vitest), canvas E2E (Playwright), visual regression. |
| Security | SAST, DAST, sandbox escape test suite, fuzzing of expression evaluator. |
| Load | k6 for API & WebSocket fan-out; run throughput on data plane. |

## 12. Platform non-functional targets (technical)

| ID | Target |
|----|--------|
| TR-NFR-01 | WebSocket event latency (data plane → browser) p95 < 250 ms. |
| TR-NFR-02 | Compiler p95 < 2 s for 200-node graphs; interpreted run start < 500 ms overhead. |
| TR-NFR-03 | Control DB RPO ≤ 5 min, RTO ≤ 30 min; data-plane checkpoints RPO ≤ 1 min. |
| TR-NFR-04 | Sandbox cold start < 2 s p95 (warm pools), workspace open < 3 s. |
| TR-NFR-05 | All services emit OTel traces/metrics/logs with tenant labels; SLO dashboards. |
