# 08 — Persistence, Memory & Visual Data Modeling

> Answers: *"Can I add persistence to my graph — to save state, to support self-improvement — and plug in multiple databases (Redis, Postgres, …) with graphical data modelling?"* Yes. This document specifies how, on top of LangGraph's persistence primitives (checkpointers, stores, caches), Deep Agents backends, and a new **Data Studio** for polyglot visual data modelling.
>
> Self-improvement (learning loops) that builds on this persistence is specified in [09-self-improving-agents.md](./09-self-improving-agents.md).

---

## 1. The Persistence Map — six layers, one mental model

Agentic apps persist *very different* kinds of data. Mixing them up is the #1 source of bugs, cost and privacy incidents. AgentCanvas makes the layers explicit and visible in a **Persistence panel** (workflow-level) and as typed resource nodes on the canvas.

| # | Layer | What it holds | Lifetime / scope | LangChain primitive | Typical backends |
|---|-------|---------------|------------------|---------------------|------------------|
| L1 | **Execution state** (short-term memory) | Graph state at every super-step: messages, channels, pending writes, interrupts | Per **thread**; history of checkpoints | **Checkpointer** (`BaseCheckpointSaver`) | Postgres, Redis, MongoDB, SQLite (dev), DynamoDB/Valkey (`langgraph-checkpoint-aws`), Cosmos DB, CockroachDB, Aerospike, ScyllaDB, SingleStore, … |
| L2 | **Long-term memory** | Facts, preferences, episodic summaries, learned instructions; JSON docs in namespaces; optional vector index | **Cross-thread**; scoped by user / assistant / org / custom namespace | **Store** (`BaseStore`) | Postgres + pgvector, Redis (RedisJSON + search), MongoDB (Atlas vector search), Elasticsearch, AstraDB, Bigtable, SingleStore |
| L3 | **Agent files** | Deep Agents virtual filesystem: working files, `AGENTS.md` memory files, skills | Per thread (State) or persistent (Store / Context Hub) | Deep Agents **backends** | `StateBackend`, `StoreBackend`, `ContextHubBackend`, sandbox FS, `CompositeBackend` routing |
| L4 | **Business data** | The customer's domain entities: orders, tickets, leads, documents, graphs | Application-owned | Not a LangGraph primitive → **Data Studio** models + generated repositories & tools | Postgres, MySQL, SQL Server, Oracle, Redis, MongoDB, DynamoDB, Neo4j/Memgraph, Elasticsearch/OpenSearch, Snowflake/BigQuery, vector DBs |
| L5 | **Caches** | Node results, LLM responses, embeddings, tool results | TTL-bound | `CachePolicy` + graph `cache=` (`BaseCache`), LLM caches | In-memory, Redis, SQLite |
| L6 | **Learning artifacts** | Prompt versions, few-shot example banks, skill library, feedback, datasets, evaluator verdicts, fine-tuned model refs | Versioned, org-governed | LangSmith prompts / Context Hub, datasets, feedback; Store namespaces | LangSmith, Store, Git |

Design rules that the validator and UI enforce:
1. **Checkpoints are not a database.** Never query business data from checkpoints; use L2/L4.
2. **Every persisted write has an owner scope** (thread / user / assistant / org / tenant) derived from `Runtime.context`, authenticated identity or trusted input channels — never from model output.
3. **Every layer has a retention policy** (TTL, sweep, archival) set explicitly or inherited from the environment.
4. **PII classification flows across layers** (a field marked PII in Data Studio is masked in traces, redacted by `PIIMiddleware` where configured, and excluded from long-term memory extraction unless allowed).

---

## 2. Execution-state persistence (L1 — checkpointers)

### 2.1 Functional requirements

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-PER-01 | Persistence is **on by default** for every workflow (required for HITL, time travel, fault tolerance, multi-turn). A workflow can opt out ("stateless") only if it contains no interrupts, breakpoints or multi-turn chat. | P0 |
| FR-PER-02 | **Checkpointer binding per environment**: *Platform-managed* (default; Postgres in our data plane or the Agent Server default), *Postgres (BYO)*, *Redis*, *MongoDB*, *DynamoDB/Valkey*, *Cosmos DB*, *CockroachDB*, *SQLite (dev only)*, *In-memory (tests only)*, *Custom* (workspace class implementing `BaseCheckpointSaver`). | P0 (platform, Postgres, SQLite, memory), P1 (Redis, Mongo), P2 (others, custom) |
| FR-PER-03 | **Durability mode** per workflow and override per run: `exit` / `async` (default) / `sync`, with plain-language guidance (sync for payments/side effects; exit for high-throughput stateless-ish flows). | P0 |
| FR-PER-04 | **Retention**: checkpoint TTL (`strategy: delete`, `default_ttl`, `sweep_interval_minutes`) per environment; per-thread TTL override at thread creation; "keep last N checkpoints per thread" pruning; archival of expired threads to object storage (Parquet/JSONL) for audit. | P1 |
| FR-PER-05 | **Storage optimisation**: mark append-heavy channels (messages, logs, findings) as `DeltaChannel` (beta, LangGraph ≥ 1.2) with snapshot frequency; the state designer shows estimated checkpoint growth per turn. | P1 |
| FR-PER-06 | **Encryption at rest** of checkpoint payloads via `EncryptedSerializer` (AES key `LANGGRAPH_AES_KEY` from the vault, per tenant); key rotation runbook. | P1 |
| FR-PER-07 | **Thread management UI**: list/search threads by metadata (user, status, workflow version), view state & history, copy thread, delete thread (cascades to checkpoints), export thread (JSON), bulk delete by filter. | P0 (view/delete), P1 (rest) |
| FR-PER-08 | **Subgraph persistence mode**: per subgraph choose *inherit* (shares the parent checkpointer, own namespace), *per-invocation* (no memory between calls), or *stateful* (keeps its own history across calls) — compiled to the subgraph `checkpointer` setting. | P1 |
| FR-PER-09 | **Conformance gate**: a Redis/Mongo/custom checkpointer binding must pass the checkpointer conformance suite (put / put_writes / get_tuple / list / delete_thread / delta support / copy thread) in the target environment before publish. | P1 |
| FR-PER-10 | **Serializer policy**: JSON+msgpack (default) — pickle fallback disabled unless explicitly allowed by an admin (security). Custom types registered via allow-list. | P1 |

### 2.2 Technical design

> **Verified caveat (review P-04):** `langgraph-checkpoint-redis` (0.5.x) requires Redis with RediSearch + RedisJSON — Redis 8 or Redis Stack. Plain Redis 7 fails with `unknown command 'FT.INFO'`. Binding validation must check `MODULE LIST` before accepting a Redis binding.

- **One checkpointer per compiled graph / deployment.** LangGraph attaches a single checkpointer at `compile()`; subgraphs share it via checkpoint namespaces. AgentCanvas therefore models the checkpointer as a **workflow-level binding**, not a free node (the canvas shows it as a pinned "Persistence" chip on START, which opens the panel).
- **Agent Server targets** (our cloud, LangSmith Deployment): do **not** pass a checkpointer in code (the server injects it). The compiler writes `langgraph.json`:
  ```json
  {
    "checkpointer": { "backend": "mongo", "ttl": { "strategy": "delete", "default_ttl": 43200, "sweep_interval_minutes": 10 } },
    "store": { "ttl": { "refresh_on_read": true, "default_ttl": 10080, "sweep_interval_minutes": 120 },
               "index": { "embed": "openai:text-embedding-3-small", "dims": 1536, "fields": ["$"] } }
  }
  ```
  For custom/Redis backends it emits a `checkpointer.py` with an async context manager yielding the saver and references it from `langgraph.json` (Agent Server "custom checkpointer", alpha). Note: Agent Server still requires Postgres for threads, runs, assistants, crons and the default store — the binding UI states this.
- **Standalone targets** (Docker without Agent Server, scripts, tests): the compiler emits explicit construction:
  ```python
  from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
  async with AsyncPostgresSaver.from_conn_string(os.environ["CHECKPOINT_DB_URI"]) as saver:
      await saver.setup()
      graph = builder.compile(checkpointer=saver, store=store, cache=cache)
  ```
- **Environment bindings**: IR holds *logical* persistence requirements; environments bind them to physical resources (dev → SQLite or platform, prod → customer Postgres). Promotion checks that bindings exist for the target env.
- **Sizing & cost estimator**: estimated bytes/checkpoint = Σ channel sizes (from schema + historical averages) × steps/run × runs/day × retention → shown in the panel with DeltaChannel savings.

---

## 3. Long-term memory (L2 — stores) and the Memory Designer

### 3.1 Concepts on the canvas

- **Memory Store** resource node (one *logical* store per workflow, but it can be a **routed store** over several physical databases — see 3.3).
- **Memory Spaces**: named, typed namespaces declared visually:
  ```
  MemorySpace "user_profile"   namespace = ("users", {context.user_id}, "profile")     schema = UserProfile    index = [preferences, bio]   ttl = none      writable_by = agent(approval)
  MemorySpace "episodes"       namespace = ("users", {context.user_id}, "episodes")    schema = Episode        index = [summary]            ttl = 90d       writable_by = consolidator
  MemorySpace "org_policies"   namespace = ("org", {context.org_id}, "policies")       schema = Policy         index = [text]               ttl = none      writable_by = admin only (read-only for agents)
  MemorySpace "examples"       namespace = ("workflow", "support_triage", "fewshot")   schema = Example        index = [input]              ttl = none      writable_by = learning loop
  ```
- **Memory kinds** (from the LangGraph/LangMem conceptual model), chosen per space: *semantic* (facts/profile — collection or single profile document), *episodic* (past experiences / trajectories), *procedural* (instructions, rules, skills, prompts).
- **Write mode** per space: *hot path* (agent writes during the conversation via tools or `Memory Write` nodes) or *background* (a consolidation workflow on a cron — "sleep-time compute").

### 3.2 Functional requirements

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-MEM-01 | Memory Space designer: name, namespace template (expressions over `context`, authenticated identity, or input channels marked `trusted` — never model/tool output), JSON schema (or Data Studio entity), vector index fields, TTL, write mode, who may write (agent / agent-with-approval / consolidator / admin), PII policy. | P0 |
| FR-MEM-02 | Nodes: **Recall** (get / search with semantic query + filters, top-k → state channel), **Remember** (put/update/merge with schema validation), **Forget** (delete by key/filter), **Memory tools** (auto-generated `search_memory` / `save_memory` tools for agents bound to specific spaces), **Profile loader** (inject profile into system prompt via `@dynamic_prompt`). | P0 |
| FR-MEM-03 | Deep Agents integration: `memory=[...]` files (`AGENTS.md`) and `StoreBackend(namespace=…)` routes are generated from Memory Spaces (e.g. `/memories/` → user space); read-only enforced via `FilesystemPermission(mode="deny")` for writes; write approvals via `mode="interrupt"`. | P0 |
| FR-MEM-04 | **Memory Inspector**: browse spaces per user/org, search (semantic + filter), view provenance (which run/thread/node wrote it), edit, delete, export; diff over time; bulk operations; right-to-erasure for a user across all spaces. | P0 (browse/delete), P1 |
| FR-MEM-05 | **Episodic memory**: optional auto-indexing of completed threads (summary + outcome + tags) into an `episodes` space; "search past conversations" tool scoped by user/org via thread metadata filters. | P1 |
| FR-MEM-06 | **Background consolidation** template: cron-triggered Deep Agent reads recent threads, extracts facts, dedups/merges with existing memories, resolves conflicts (newer wins / confidence), writes with provenance. | P1 |
| FR-MEM-07 | **Memory hygiene**: dedup, contradiction detection, confidence & decay scores, max items per namespace, summarisation of stale items. | P2 |
| FR-MEM-08 | **Memory safety**: memories written from untrusted content are tagged `untrusted`; org/shared spaces read-only for agents by default; injection screening before write; quarantine queue for review. | P1 |

### 3.3 Multiple databases for memory: the Routed Store

LangGraph gives a graph **one** `store`. To support *"user profiles in Postgres, hot session facts in Redis, episodes in MongoDB Atlas vector search"* AgentCanvas ships `agentcanvas_runtime.stores.RoutedStore` — a `BaseStore` implementation that routes operations by **namespace prefix** to child stores (the same idea as Deep Agents' `CompositeBackend`, one level down):

```python
from agentcanvas_runtime.stores import RoutedStore
from langgraph.store.postgres.aio import AsyncPostgresStore
from langgraph.store.redis.aio import AsyncRedisStore   # from langgraph-checkpoint-redis

store = RoutedStore(
    default=pg_store,                                    # ("users", *, "profile"), ("org", …)
    routes={
        ("sessions",): redis_store,                      # TTL-heavy, low-latency facts
        ("users", "*", "episodes"): mongo_store,         # large, vector-searched episodes
    },
)
```
- `batch`/`abatch` split ops per child and merge results preserving order; `search` with a namespace prefix spanning several children fans out and merges by score; `list_namespaces` unions.
- Each child can have its own embedding index; the router records which index a namespace uses so scores are never mixed across embedding models (Agent Server's default store supports only one embedding model per deployment — RoutedStore lifts that restriction only for self-managed children).
- Deployed on Agent Server via the **custom store** hook (async context manager yielding the RoutedStore); conformance-tested like checkpointers.

---

## 4. Caching (L5)

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-CACHE-01 | Workflow cache binding: in-memory (dev), Redis, SQLite; compiled to `compile(cache=…)`. | P1 |
| FR-CACHE-02 | Per-node `CachePolicy` (TTL, custom key expression — e.g. exclude volatile fields). | P1 |
| FR-CACHE-03 | LLM response cache (exact) and **semantic cache** (Redis vector similarity with threshold) as middleware on model calls, with per-tenant isolation and bypass for personalised prompts. | P2 |
| FR-CACHE-04 | Tool-result cache for idempotent read tools (declared `read_only_hint` or marked by user). | P2 |
| FR-CACHE-05 | Cache hit/miss metrics & savings ($) in dashboards. | P2 |

---

## 5. Data Studio — graphical, polyglot data modelling (L4)

### 5.1 Concept

A second canvas type, the **Data Model canvas** (ER-style), lives next to workflow canvases in each project. One **logical model** (entities, fields, relations) can be **mapped to several physical stores**. Entities become first-class types everywhere in AgentCanvas (state channels, port types `JSON<Customer>`, structured outputs, memory space schemas, tool arg schemas).

```
┌─────────────── Data Model canvas: "CRM" ─────────────────────────────────────────┐
│  ┌──────────── Customer ───────────┐        ┌──────────── Ticket ──────────────┐ │
│  │ 🔑 id            uuid            │ 1    N │ 🔑 id           uuid              │ │
│  │    email         string  🛡PII   │────────│ 🔗 customer_id  uuid              │ │
│  │    tier          enum(A,B,C)     │        │    subject      string            │ │
│  │    tenant_id     string  🏢scope │        │    body         text   🛡PII      │ │
│  │    embedding     vector(1536)    │        │    status       enum              │ │
│  │  store: Postgres(crm_db).customers│       │  store: Postgres + Elastic(index) │ │
│  └──────────────────────────────────┘        └───────────────────────────────────┘ │
│  ┌──────── SessionState (Redis) ───┐          ┌──── Account (Neo4j :Account) ─────┐ │
│  │ key: session:{tenant}:{id} TTL 1h│          │ (:Account)-[:OWNS]->(:Customer)  │ │
│  └──────────────────────────────────┘          └───────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Modelling features

> **Priority note (review S-04, 2026-09-28):** Data Studio is scheduled **after the MVP** (GA). Its former P0 items are now P1; MVP agents reach business data through HTTP/MCP tools and script nodes.


| ID | Requirement | Pri |
|----|-------------|-----|
| FR-DM-01 | Entities with fields: types (string, text, int, decimal, bool, date/time, uuid, enum, json, array, reference, **vector(dims)**, file/blob ref), nullability, defaults, constraints (unique, check, regex, min/max), descriptions (used as LLM tool/field descriptions). | P1 |
| FR-DM-02 | Relations: 1:1, 1:N, N:M (join entity auto-generated), cascade rules; drawn by dragging between fields. | P1 |
| FR-DM-03 | Indexes (b-tree, unique, composite, full-text, vector HNSW/IVF, TTL index), partitions. | P1 |
| FR-DM-04 | **Field classifications**: PII (email, phone, name, address, government id, payment), secret, **tenant scope** (row owner/tenant key), audit fields (created_at/by, updated_at/by) auto-added option. | P1 |
| FR-DM-05 | **Physical mappings** per entity (one or more): **Postgres/MySQL/SQL Server** table; **MongoDB** collection (+ JSON-schema validator, indexes); **Redis** (Hash / RedisJSON with key pattern, TTL, RediSearch/RedisVL index); **DynamoDB** (PK/SK design helper, GSIs); **Neo4j/Memgraph** node label & relationship types; **Elasticsearch/OpenSearch** index mapping; **vector DB** collection (pgvector, Qdrant, Pinecone, Weaviate, Milvus); **warehouse** read-only views (Snowflake/BigQuery). Mapping editor shows store-specific options only. | P1 (Postgres, Redis, Mongo), P2 (Dynamo, Neo4j, Elastic, vector DBs, warehouses) |
| FR-DM-06 | **Reverse engineering**: connect an existing DB → introspect schema (tables, FKs, indexes; Mongo sampling-based schema inference; Redis key-pattern sampling; Neo4j `db.schema.visualization`) → editable model. | P1 |
| FR-DM-07 | **Migrations**: model diff → migration plan per store (Alembic for SQL; index/validator scripts for Mongo; RedisVL index changes; Cypher constraint scripts); preview SQL/DDL; destructive-change warnings; approval workflow; apply per environment; rollback scripts; migration history. | P1 |
| FR-DM-08 | **Data browser**: view/edit rows/docs/keys with filters, seed data & fixtures per environment, anonymised prod snapshots for dev (masking by classification). | P1 |
| FR-DM-09 | **Database branching for dev/test**: ephemeral per-branch/per-test-run databases (Postgres branching e.g. Neon, containerised DBs, Redis logical DB/prefix) with seed fixtures. | P2 |
| FR-DM-10 | Model versioning & visual diff shared with workflow versioning; models are importable across projects as packages. | P1 |
| FR-DM-11 | Copilot: "model my domain from this description / these sample JSONs / this CSV", "suggest indexes from query patterns", "explain this schema". | P1 |

### 5.3 Using data in workflows

| Node / feature | Behaviour | Compiles to | Pri |
|----------------|-----------|-------------|-----|
| **DB Connection** (resource) | Named connection per env (DSN via secret ref, pool size, SSL, read replica, IAM auth) | async engine/client factory (SQLAlchemy async engine, Motor/PyMongo async, `redis.asyncio`, Neo4j async driver, aioboto3) | P0 |
| **Get / Find** | Fetch entity by id or filter (visual filter builder over fields with expressions) | repository method | P0 |
| **Query (visual / SQL / Cypher / Mongo aggregation)** | Visual query builder with joins from the model, or raw query with parameters (never string interpolation) | parameterised query in repository | P0 (visual/SQL), P1 (Cypher, aggregation) |
| **Insert / Upsert / Update / Delete** | Writes with schema validation; **idempotency key** required (default `thread_id:node:step`) | repository method + outbox/idempotency table | P0 |
| **Vector search** | Similarity search over a vector field with metadata filters | store-specific vector query / LangChain vector store | P1 |
| **Graph traverse** | Path/relationship query on Neo4j mappings | Cypher (parameterised) | P2 |
| **Cache get/set (Redis)** | Key-pattern based get/set with TTL | redis client | P0 |
| **Transaction scope** (group) | Nodes inside commit together; rollback on error | single node wrapping a DB transaction (see 5.5) | P1 |
| **Data tools for agents** | One click: expose entity operations as LangChain tools (`search_customers`, `get_ticket`, `update_ticket_status`) with field-level allow-lists, row scoping by tenant/user from `Runtime.context`, and HITL on writes by default | generated `@tool` functions with `ToolRuntime` | P0 |
| **SQL agent tool** | Guarded text-to-SQL over selected entities (read-only role, row limits, query allow-list/timeout) | SQL toolkit with restricted engine | P1 |
| **Change events (CDC)** | Entity insert/update/delete as triggers (Postgres logical replication/Debezium, Mongo change streams, Redis keyspace notifications/streams) | Trigger service consumers | P1 |

### 5.4 Code generation for data

Generated under `src/<pkg>/data/`:
```
data/
├── models/          # SQLAlchemy 2.0 (typed Mapped[]) / SQLModel classes; Beanie/ODM or plain Pydantic for Mongo
├── schemas/         # Pydantic v2 I/O schemas (also used for structured output & tool args)
├── repositories/    # typed async repositories per entity & store (tenant-scoped by construction)
├── redis_keys.py    # key builders & TTLs; RedisVL index schemas
├── graph/           # Neo4j constraints & query helpers
├── tools.py         # agent data tools
├── migrations/      # Alembic env + versions; mongo/redis/neo4j migration scripts
└── connections.py   # lazy async clients from env
```
Example (generated, abridged):
```python
# ── @data-entity Customer (Postgres crm_db.customers) ──
class Customer(Base):
    __tablename__ = "customers"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)          # PII: email
    tier: Mapped[Tier] = mapped_column(Enum(Tier))
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)       # tenant scope
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536))

class CustomerRepository:
    def __init__(self, session: AsyncSession, tenant_id: str):
        self._s, self._tenant = session, tenant_id                        # scope enforced in every query
    async def get(self, id: uuid.UUID) -> CustomerOut | None:
        row = await self._s.scalar(select(Customer).where(Customer.id == id, Customer.tenant_id == self._tenant))
        return CustomerOut.model_validate(row) if row else None

@tool
async def search_customers(query: str, runtime: ToolRuntime[Context]) -> list[CustomerSummary]:
    """Search customers of the current tenant by name or email."""
    async with session_scope() as s:
        return await CustomerRepository(s, runtime.context.tenant_id).search(query, limit=20)
```

### 5.5 Correctness: side effects under durable execution (critical)

LangGraph resumes a node **from its beginning** after an interrupt or failure, and retries re-run nodes. Therefore:
- **Validator rules**: `E070` a node that performs a DB write *and* calls `interrupt()` → error (split the node: write after approval in a separate node). `W070` DB write node with retries but no idempotency key. `W071` write inside a parallel branch without conflict strategy.
- **Idempotency**: generated write methods take an idempotency key and use `INSERT … ON CONFLICT DO NOTHING` / conditional writes / an `agentcanvas_idempotency` table (SQL) or `SET NX` (Redis).
- **Transactional outbox** option for writes that must be paired with events: write + outbox row in one transaction; the Trigger service relays.
- Functional API option: data steps can be compiled as `@task`s inside an `@entrypoint` so completed results are checkpointed and not re-executed on resume.
- **Saga/compensation**: node `error_handler` routes to compensation nodes (e.g. undo reservation), visualised as red dashed edges.

### 5.6 Security for data access

- Least-privilege DB roles generated per workflow (read-only role for read nodes/tools; writer role only when write nodes exist).
- Row scoping: repositories require a scope (tenant/user) resolved from authenticated `Runtime` identity/context — the LLM can never supply it.
- Postgres **RLS** policies optionally generated from tenant-scope fields.
- Query safety: parameterised queries only; raw SQL nodes pass a linter (no DDL/DCL; statement allow-list); per-query timeout and row limit.
- Secrets: DSNs are secret refs; IAM auth (RDS IAM, Azure Entra, GCP IAM) preferred.
- Data access audit: every tool/node DB operation emits an audit event (who/which run/which entity ids).

---

## 6. IR additions

```jsonc
"persistence": {
  "checkpointer": { "logical": "default", "durability": "async",
                    "ttl": { "strategy": "delete", "default_ttl_min": 43200, "sweep_interval_min": 10 },
                    "delta_channels": ["messages"], "encryption": true },
  "store": { "kind": "routed",
             "default": "mem_pg",
             "routes": [ { "prefix": ["sessions"], "store": "mem_redis" },
                         { "prefix": ["users", "*", "episodes"], "store": "mem_mongo" } ],
             "ttl": { "default_ttl_min": 10080, "refresh_on_read": true } },
  "cache": { "kind": "redis", "connection": "cache_redis" },
  "memory_spaces": [
    { "name": "user_profile", "namespace": ["users", "{{ context.user_id }}", "profile"],
      "schema": { "$ref": "dm://crm/UserProfile" }, "index": ["preferences", "bio"],
      "kind": "semantic", "write_mode": "hot_path", "writers": ["agent:approval"], "pii": "allow_masked" }
  ]
},
"data_models": [ { "ref": "dm://crm", "version": "1.4.0" } ],
"connections": [ { "name": "crm_db", "kind": "postgres" }, { "name": "cache_redis", "kind": "redis" } ]
```
Environment files bind logical names to physical connections:
```yaml
# environments/prod.yaml
bindings:
  checkpointer: { kind: platform }               # or { kind: postgres, connection: prod_pg }
  stores: { mem_pg: { kind: postgres, connection: prod_pg }, mem_redis: { kind: redis, connection: prod_redis },
            mem_mongo: { kind: mongodb, connection: prod_atlas, vector_index: episodes_idx } }
  connections: { crm_db: { secret: CRM_DB_URI, pool: 20, read_replica_secret: CRM_DB_RO_URI } }
```

## 7. Architecture additions

- **Data Plane → Persistence services**: managed Postgres (checkpoints, store, pgvector), managed Redis (cache, fast store, queues), optional managed MongoDB; or customer BYO via bindings (Hybrid).
- **Connection Broker** (new data-plane service): holds pooled connections/credentials for customer databases, issues short-lived credentials to runs and sandboxes, enforces network policies (private link / VPC peering / SSH tunnel), records data-access audit.
- **Data Studio service** (control plane): model CRUD & versioning, introspection jobs (run in the data plane via the broker — the control plane never connects to customer DBs directly), migration planning (Alembic autogenerate in a sandbox against a shadow DB), code generation.
- **Retention worker**: TTL sweeps beyond what backends provide, archival exports, right-to-erasure orchestration across checkpointer (delete thread), store (delete namespaces/items), business data (generated erase hooks), traces (LangSmith data deletion).
- Control-DB additions (GA; the MVP schema is [build-guide/reference/infra/schema.sql](./build-guide/reference/infra/schema.sql)):
```sql
data_model(id, project_id, name, current_version_id)
data_model_version(id, data_model_id, semver, model jsonb, created_by, created_at)
physical_mapping(id, data_model_version_id, entity, store_kind, connection_name, config jsonb)
migration(id, data_model_version_id, env_id, store_kind, plan jsonb, script_uri, status, applied_by, applied_at)
memory_space(id, workflow_id, name, config jsonb)
persistence_binding(id, env_id, logical_name, kind, config jsonb, conformance_status, verified_at)
erasure_request(id, org_id, subject_ref, scope jsonb, status, report jsonb, requested_at, completed_at)
```

## 8. Non-functional targets for persistence

| ID | Target |
|----|--------|
| NFR-PER-01 | Checkpoint write overhead p95 < 15 ms (async durability, platform Postgres, < 64 KB state). |
| NFR-PER-02 | Store `search` p95 < 80 ms (10⁶ items/namespace prefix, pgvector HNSW). |
| NFR-PER-03 | Right-to-erasure across all layers completed < 72 h with a signed report. |
| NFR-PER-04 | Zero business-data queries executed without a resolved tenant/user scope (enforced in generated code + tested). |
