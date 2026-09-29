# 10 — Improvement Plan (v0.1 → v0.2)

Result of a second research pass over the LangGraph persistence docs (checkpointers, stores, durability modes, `DeltaChannel`, encryption, custom checkpointer/store contracts), the checkpointer/store integration catalogue, Agent Server configuration (`checkpointer.backend`, TTLs, store index, custom checkpointer/store — alpha), Deep Agents memory (scoped memory, episodic memory, background consolidation, read-only vs writable memory), LangMem (memory managers, prompt optimisers), and LangSmith (feedback, online evals, Smithtune fine-tuning, Context Hub). Each item lists the gap in v0.1, the improvement, where it is specified, and priority.

## 1. Improvements adopted in this revision

| # | Gap in v0.1 | Improvement | Spec | Pri |
|---|-------------|-------------|------|-----|
| I-01 | Persistence was implicit ("platform checkpointer") | **Persistence Map** with six explicit layers (execution state, long-term memory, agent files, business data, caches, learning artifacts) and design rules | 08 §1 | P0 |
| I-02 | No choice of checkpoint database | **Checkpointer bindings per environment**: platform / Postgres / Redis / MongoDB / DynamoDB-Valkey / Cosmos / CockroachDB / SQLite / memory / custom; compiled to `langgraph.json` `checkpointer.backend` or explicit `compile(checkpointer=…)` | 08 §2 | P0–P2 |
| I-03 | Only one memory store assumed | **RoutedStore** — namespace-prefix routing over multiple physical stores (Postgres + Redis + Mongo …), deployed via the Agent Server custom-store hook | 08 §3.3 | P1 |
| I-04 | No visual data design | **Data Studio**: ER-style polyglot modelling; physical mappings to SQL, Redis, Mongo, DynamoDB, Neo4j, Elastic, vector DBs; reverse engineering; migrations; data browser | 08 §5 | P0–P2 |
| I-05 | Agents couldn't safely use business data | **Generated repositories & data tools** with tenant/user row scoping from `Runtime`, field allow-lists, HITL on writes, least-privilege DB roles, optional RLS | 08 §5.3, §5.6 | P0 |
| I-06 | Durable-execution replay could duplicate side effects | **Side-effect correctness rules** (E070/W070/W071), idempotency keys, transactional outbox, `@task` compilation, saga/compensation via `error_handler` | 08 §5.5 | P0 |
| I-07 | Unbounded checkpoint growth | TTL policies, per-thread TTL, pruning, `DeltaChannel` for append-heavy channels, growth estimator, archival | 08 §2.1 | P1 |
| I-08 | Checkpoint data unencrypted by default | `EncryptedSerializer` with per-tenant keys from the vault; pickle fallback disabled by default | 08 §2.1 | P1 |
| I-09 | Memory was a raw slot | **Memory Spaces** (typed, namespaced, indexed, TTL, writers, PII policy) + Recall/Remember/Forget nodes + generated memory tools + **Memory Inspector** | 08 §3 | P0 |
| I-10 | No cross-session learning | **Learning ladder** R1–R6 (memory → examples → skills → prompts → routing → weights) with Learning Loop nodes and templates | 09 | P1–P2 |
| I-11 | Feedback not first-class | Feedback schema designer; HITL edits captured automatically as supervised signal; outcome events joined to threads | 09 §2 | P1 |
| I-12 | Learned changes could silently degrade prod | Versioned, revertible artifacts; mandatory eval gates; human review; canary; auto-rollback | 09 §4–5 | P1 |
| I-13 | Custom persistence backends untested | **Conformance gate** (checkpointer/store contract tests incl. delta & copy-thread) before a binding can be used for publish | 08 §2.1 FR-PER-09 | P1 |
| I-14 | GDPR erasure only covered control DB | **Cross-layer erasure orchestration** (threads, store namespaces, business data hooks, examples/datasets, traces) with signed report | 08 §7 | P1 |
| I-15 | State schema evolution deferred to P2 | Promoted to **P1** because persisted threads outlive versions: channel-rename/remove migration wizard; versioned state schemas; compatibility check on publish ("threads created by v1.3 can resume on v1.4?") | 01 FR-STATE-05 | P1 |
| I-16 | Caching was node-level only | Cache binding (Redis), semantic LLM cache, tool-result cache for read-only tools, savings metrics | 08 §4 | P1–P2 |
| I-17 | DB connectivity from cloud to customer networks unspecified | **Connection Broker** in the data plane (private link, VPC peering, SSH tunnel, short-lived creds, access audit); control plane never touches customer DBs | 08 §7 | P1 |
| I-18 | Dev/test used shared databases | Ephemeral database branches and seeded fixtures per test run / branch; anonymised prod snapshots by field classification | 08 FR-DM-08/09 | P2 |

## 2. Further improvements recommended (next revisions)

| # | Improvement | Rationale | Pri |
|---|-------------|-----------|-----|
| N-01 | **Knowledge-graph memory** (Neo4j/Memgraph mappings as a memory space type: entities & relations extracted from conversations, GraphRAG retrieval) | Relational facts ("who owns which account") are poorly served by flat JSON memories | P2 |
| N-02 | **Temporal memory** (valid-from/valid-to on facts; "as of" recall) | Preferences and facts change; avoids contradictions | P2 |
| N-03 | **Thread-level analytics warehouse export** (checkpoints/feedback/outcomes → Parquet/BigQuery/Snowflake) | Product analytics & offline learning at scale | P2 |
| N-04 | **Data contracts** between workflows and data models (breaking-change detection when an entity changes and a workflow depends on it) | Safe evolution across teams | P1 |
| N-05 | **Point-in-time replay across data**: store DB read snapshots/versions in trace metadata so time-travel debugging can reproduce data-dependent behaviour | True WYSIWYG debugging for data-driven agents | P2 |
| N-06 | **Multi-region data residency** for checkpoints/stores with region-pinned threads | EU/US customers | P1 |
| N-07 | **Stream v3 projections in the UI protocol** (typed `messages`, `lifecycle`, `subgraphs` channels) replacing ad-hoc normalisation | Less custom code, better subagent visualisation | P1 |
| N-08 | **LLM Gateway integration** for model policies, fallbacks and spend limits instead of custom proxy | Leverage upstream | P1 |
| N-09 | **Harbor-style agent evals** for sandboxed coding/data agents (task environments with verifiers) | Better evals for agents that act on environments | P2 |
| N-10 | **Offline-first local mode** (desktop app with SQLite checkpointer/store and local models) | Privacy-sensitive users, demos | P2 |

## 3. Updated delivery sequencing (delta to doc 06)

| Phase | Added scope |
|-------|------------|
| Phase 1 (MVP) | Persistence panel with platform/Postgres/SQLite checkpointer bindings; durability modes; thread browser; Memory Spaces + Recall/Remember nodes + Memory Inspector (browse/delete); Data Studio v1 (entities, relations, classifications, Postgres & Redis mappings, codegen of models/repositories/tools, manual migrations); side-effect validator rules & idempotency keys. |
| Phase 2 (GA) | Redis & Mongo checkpointers; RoutedStore; TTL/retention & archival; encryption; conformance gate; migrations workflow (Alembic etc.); reverse engineering; data browser; CDC triggers; Connection Broker; episodic memory & consolidation template; feedback designer; Learning Center with R2 (examples) & R4 (prompt optimisation) loops, eval gates and canary; cross-layer erasure. |
| Phase 3 | Dynamo/Neo4j/Elastic/vector mappings; semantic cache; DB branching; skill writer (R3); bandit routing (R5); fine-tuning (R6); knowledge-graph & temporal memory; warehouse export. |

## 4. Open questions added

1. Do we operate managed MongoDB/Redis in the data plane, or only support BYO for them (Postgres + Redis managed, Mongo BYO)? *Recommendation:* managed Postgres + Redis; BYO for everything else.
2. RoutedStore upstreaming: propose a namespace-routing store to LangGraph to avoid maintaining it long-term?
3. Should prompt promotion be possible without redeploy (tag-based resolution at runtime) in regulated environments, or must every change be a new immutable revision? *Recommendation:* configurable per environment; prod in regulated orgs = immutable revisions.
