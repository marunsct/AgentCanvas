# Adapter Spec — Mastra (TypeScript, via Node Adapter Host)

## 1. Verified baseline
- `@mastra/core` **1.72** (Apache-2.0 except `ee/` directories, which use a separate enterprise licence and are excluded from generated code).
- Verified: `packages/core/src/workflows/create.ts` (`createWorkflow({ id, inputSchema, outputSchema, steps })`), `workflow.ts` (`.then`, `.parallel`, `.branch`, `.dowhile`, `.dountil`, `.foreach`, `.map`, `.sleep`, `.sleepUntil`, `.waitForEvent`, `.commit()`, `resume({ step, resumeData })`, `restart`, time-travel), `step.ts` (`suspend()`), `step-factories.ts` (`createStepFromAgent`), `storage/` + `stores/*` (pg, libsql, mongodb, redis, dynamodb, mssql, mysql, upstash, clickhouse, …), `a2a`, `mcp`, `observability`, `evals`, `deployer`.

## 2. Positioning
Leading TypeScript agent/workflow framework; first **TS adapter** and reference implementation of the **Node Adapter Host** (JSON-RPC SPI). Target **T2**.

## 3. Core IR mapping

| Core concept | Level | Construct |
|--------------|-------|-----------|
| Workflow | N | `createWorkflow({ id, inputSchema, outputSchema })…commit()` (Zod schemas from IR JSON Schema) |
| Node | N | `createStep({ id, inputSchema, outputSchema, execute })` |
| Agent node | N | `new Agent({...})` + `createStepFromAgent(agent)` |
| Sequence | N | `.then(step)` |
| Router | N | `.branch([[cond, step], …])` |
| Parallel + join | N | `.parallel([a, b])` (outputs keyed by step id) |
| Map | N | `.foreach(step, { concurrency })` |
| Loop | N | `.dowhile(step, cond)` / `.dountil(step, cond)` |
| Wait | N | `.sleep`, `.sleepUntil`, `.waitForEvent` |
| Shared state | N | workflow state (`setState`) |
| HITL | N | step `suspend(payload)` → resume `run.resume({ step, resumeData })` |
| Persistence | N | Mastra storage adapters bound from doc 08 bindings (`@mastra/pg`, `@mastra/redis`, `@mastra/mongodb`, …) |
| Events | N | workflow watch/stream; AG-UI `mastra` integration |

## 4–11 (summary)
- Node Adapter Host: `sidecars/node-adapter-host` exposes `lower/generate/start/resume/events/cancel` over JSON-RPC (stdio or HTTP); the Python control plane treats it as a normal adapter. Codegen uses TS templates + `ts-morph`, formatted with Prettier, type-checked with `tsc`.
- Envelope: run id + snapshot id (Mastra snapshots) + suspended step ids.
- Hosting: Mode A not applicable (Agent Server hosts Python/JS LangGraph graphs; Mastra runs in its own Node server) → **Mode B** (Mastra server/deployers) + canonical events posted by an AgentCanvas Mastra observability exporter; Mode C A2A.
- Import: AST (ts-morph) of `createWorkflow` chains.
- Gaps: reducers (E); breakpoints via suspend in dev mode.

## 12. Plan (squad C, ~16 weeks incl. Node host) · 13. Risks
Node host (1–5) · lowering (4–9) · suspend/resume & storage (8–11) · events exporter (10–12) · conformance & beta (12–16). Risks: fast release cadence (alpha on main — target stable tags); `ee/` licence boundary.
