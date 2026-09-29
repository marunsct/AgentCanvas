# 13 — Deep Review Report (v0.4 → v0.5)

**Date:** 2026-09-28 · **Scope:** docs 00–12, adapters/, and all code shown in them · **Goal:** make the specification buildable by a beginner, without prior context.

## 1. How the review was done

1. **Every API name used in the spec was imported** from the real packages (installed: `langgraph 1.2.12`, `langchain 1.4.2`, `langchain-core 1.6.5`, `deepagents 0.7.19`, `langgraph-checkpoint-postgres 3.1.2`, `langgraph-checkpoint-redis 0.5.2`, `fastapi 0.141`, `cel-python 0.5.0`).
2. **The worked example's behaviour was executed**: graph with retry/timeout policies, router, agent with tool approval, pause → resume, state history, time-travel fork, state edit, Postgres checkpointer & store, Deep Agent with a composite backend, streaming (v2 and v3), custom stream events.
3. **A walking-skeleton implementation was built and tested** (`build-guide/reference/`): IR models, validator, safe expressions, runtime node library, interpreter, code generator, FastAPI runner with SSE, React Flow UI. Verified with 20 backend tests (incl. security tests), 2 frontend tests, ruff + pyright (0 errors), a clean-room install from lockfiles, and a headless-browser end-to-end test (run → nodes light up → approve → complete, twice).
4. **Each document was read for** contradictions, undecided choices, scope that a small team cannot deliver, and missing information a newcomer would need.

## 2. Summary

| Category | Found | Fixed in this revision |
|----------|------:|-----------------------:|
| Factual API errors | 3 | 3 |
| Runtime pitfalls not documented | 6 | 6 (documented + handled in reference code) |
| Security gaps (found in our own reference code) | 1 | 1 |
| Contradictions between documents | 7 | 7 |
| Undecided choices blocking implementation | 11 | 11 (decisions recorded as ADRs) |
| Scope/sequence problems | 4 | 4 |
| Beginner gaps (missing how-to material) | 14 | 14 (new `build-guide/`) |

## 3. Findings and resolutions

### 3.1 Factual API errors

| ID | Where | Problem | Resolution |
|----|-------|---------|-----------|
| F-01 | 01 FR-RUN-12, 02 §3.4, README | `RunControl` was implied to live in `langgraph.types`. | It is `langgraph.runtime.RunControl` (drain raises `langgraph.errors.GraphDrained`). Docs corrected. |
| F-02 | 04 §7 (Error handler), 01 | `NodeError` import location unspecified/implied `langgraph.types`. | It is `langgraph.errors.NodeError`. Docs corrected. |
| F-03 | 03 §6.3, 07 | `make_graph` async factory registered in `langgraph.json` — not verified for self-hosted runners, and MVP does not use Agent Server (see D-01). | Generated code exposes `build_graph(resources, checkpointer, store)`; hosts decide how to call it. The LangSmith-deploy target wraps it in a module-level graph (build guide §09). |

### 3.2 Runtime pitfalls (now documented in build-guide/11-troubleshooting-faq.md)

| ID | Pitfall (verified) | Handling |
|----|-------------------|----------|
| P-01 | LangChain fake chat models do not implement `bind_tools()` → agents fail with `NotImplementedError`. | `agentcanvas.testing.ToolFakeModel`. |
| P-02 | With `stream_mode="messages"`, a fake model reply that contains only tool calls yields no chunks → `ValueError: No generations found in stream`. | Fakes use `disable_streaming=True`. |
| P-03 | Scripted fakes are stateful; a resume that builds a fresh fake restarts the script and re-issues the tool call (endless approvals). | Resources are resolved **per thread** (`resources_factory(thread_id)`). |
| P-04 | `langgraph-checkpoint-redis` needs Redis with **RediSearch + RedisJSON** (Redis 8, or Redis Stack). Plain Redis 7 fails with `unknown command 'FT.INFO'`. | Docker Compose uses `redis:8`; binding validation checks modules (`MODULE LIST`) before use. |
| P-05 | `stream_events(version="v3")` exists but warns *"experimental"* at runtime. | MVP uses `astream(..., version="v2")` (stable, typed parts). v3 adoption tracked for later. |
| P-06 | When resuming after `interrupt()`, LangGraph **re-runs the interrupted node from its start**. Code before `interrupt()` runs twice. | Validator rule E070 + guidance: side effects go after the approval, in a separate node. |

### 3.2b Security gap found in the reference during review

| ID | Problem | Resolution |
|----|---------|-----------|
| SEC-01 | Script nodes and Python tools imported **any** `module:function` from `import_path` (e.g. `os:system`). | `safe_import()` allow-lists workspace packages (`scripts.`, `tools.`, `examples.`); validator rule **E080**; tests in `tests/test_security.py` (also proving the template/CEL sandboxes block `__class__`/`__globals__` escapes). |

### 3.3 Contradictions

| ID | Contradiction | Resolution |
|----|--------------|-----------|
| C-01 | 02 §6 says graph node names are `slug__nodeid`; 03 §5 says identifier = `slug(name)`. | **Node name = IR `name`** (unique snake_case). The runtime keeps a `name → canvas_node_id` index; `metadata={"canvas_node_id": …}` is also set for traces. |
| C-02 | 02 §6 defines its own UI event schema; 12 §C.3 defines AG-UI-based canonical events. | **12 §C.3 is authoritative.** 02 §6 now points to it. The reference translator emits `RUN_STARTED/FINISHED/ERROR`, `STEP_STARTED/FINISHED`, `TEXT_MESSAGE_CONTENT`, `STATE_DELTA`, `CUSTOM ac.interrupt`. |
| C-03 | 03 §7: generated code "inlined idiomatic code by default" vs. "calls the runtime library" — two strategies, equivalence unclear. | **MVP: generated code calls the same node library the interpreter uses** (`agentcanvas.runtime.nodes`) and assembles the graph with plain LangGraph calls. Equivalence is tested (`test_generated_code_matches_interpreter`). A fully-inlined "clean eject" is a P2 option. |
| C-04 | Control-DB tables defined in 02 §7 **and** 08 §7 (duplicates). | Single authoritative DDL: build-guide/05-backend-guide.md §3. |
| C-05 | D6 "LangGraph Agent Server is the runtime" vs. licensing open question in 06 §4.1. | See D-01: the MVP ships its own open-source runner; Agent Server/LangSmith Deployment is a publish target. |
| C-06 | 02 lists "Temporal (or Hatchet)" as "Arq/Celery-free" (confusing). | MVP: no workflow engine; background work uses `asyncio` tasks + a Postgres job table. Temporal introduced at GA for long jobs (evals, deploys). |
| C-07 | Doc 06 roadmap has MVP including Yjs multiplayer (P1) in some tables and not others. | Multiplayer (Yjs) is **post-MVP**. MVP uses REST autosave with optimistic locking (`version` column, HTTP 409 on conflict). |

### 3.4 Undecided choices → decisions (recorded as ADRs in build-guide/02 §6)

| ID | Question | Decision |
|----|----------|----------|
| D-01 | Runtime for MVP | **AgentCanvas Runner**: FastAPI + LangGraph library + Postgres checkpointer/store + SSE, implementing a subset of the Agent Server API shape (threads, runs/stream, state, history). Fully open source. LangSmith Deployment = optional publish target. |
| D-02 | Expression language | **CEL** (`cel-python`) for conditions; **sandboxed Jinja2** (`jinja2.sandbox.SandboxedEnvironment`, `StrictUndefined`) for templates. Never `eval`. |
| D-03 | Canvas library | **React Flow** (`@xyflow/react` 12). |
| D-04 | Frontend stack | React 19, TypeScript, Vite, Zustand, TanStack Query, `@rjsf/core` for config forms, Monaco for code. |
| D-05 | Streaming transport | **SSE over POST** (fetch + stream reader) for runs; WebSocket only later for collaboration. |
| D-06 | Database migrations | **Alembic** (SQLAlchemy 2.0 models). |
| D-07 | Auth in MVP | OIDC login via any provider (Keycloak in docker-compose for local), JWT verified by the API; single organisation; roles `admin`, `builder`, `viewer`. |
| D-08 | IDs | Prefixed ULIDs: `wf_`, `n_`, `r_`, `e_`, `th_`, `run_`, `ver_`. |
| D-09 | API error format | RFC 9457 `application/problem+json`. |
| D-10 | Script execution in MVP | Scripts run **in-process** only in the local/dev edition; the hosted edition runs them in a Docker sandbox runner (gVisor where available). |
| D-11 | Python/Node versions | Python 3.12, Node 22 LTS, pnpm 10, uv for Python packaging. |

### 3.5 Scope and sequencing

| ID | Problem | Resolution |
|----|---------|-----------|
| S-01 | "MVP" in 06 contained ~40 features — too large for a first release by a small or junior team. | New **milestone plan M0–M8** (build-guide/04) with a walking skeleton first and each milestone shippable. |
| S-02 | Framework-agnostic groundwork (doc 11 §4) not mapped to tickets. | Mapped into M1/M3/M5 tickets (IR core/dialect split, canonical events, adapter boundary). |
| S-03 | No requirement → ticket traceability. | build-guide/12-traceability.md maps every P0 requirement to tickets. |
| S-04 | Persistence (doc 08) and learning (doc 09) scope mixed with MVP. | MVP persistence = Postgres checkpointer + store + thread browser; Redis/Mongo bindings, RoutedStore, Data Studio → post-MVP milestones. |

### 3.6 Beginner gaps (all addressed by the new build guide)

Missing before this revision: concept primer; environment setup with exact versions; local infrastructure (Docker Compose); repository layout for the real product; coding conventions; git/PR workflow; step-by-step tutorial; ticket-level plan with acceptance criteria; full DDL; API contract examples; frontend architecture; "how to add a node type" recipe; testing strategy with fakes; deployment runbook; security checklist; troubleshooting FAQ.

## 4. Remaining known limitations (explicitly accepted)

- The reference implementation covers 6 node types; the full catalog (doc 04) is built incrementally per the milestone plan.
- Non-LangChain adapters (doc 12) are not in the reference implementation; they start after GA as planned.
- Model identifiers in examples (e.g. `openai:gpt-5-mini`, `anthropic:claude-sonnet-5`) are illustrative; the model catalog is data-driven and validated against the provider at connection time.
