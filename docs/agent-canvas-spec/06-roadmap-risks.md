# 06 — Roadmap, Team, Risks & Open Questions

## 1. Phased delivery

### Phase 0 — Foundations (weeks 0–6)
- IR schema v1 + command API + canonical JSON + Pydantic models → generated TS types.
- Node registry & manifest format; 15 core node types (Chat Model, Deep Agent, Lite Agent, Subagent, MCP Server, HTTP tool, Code tool, Router, If/Else, Map, Join, Approval, Human Input, Script, Return).
- Python compiler (templates + LibCST + ruff + pyright) with golden tests; `langgraph dev` runnable output.
- Interpreter + differential test harness.
- **Exit criterion:** a Deep Research template authored in JSON compiles, runs locally, and both modes produce identical trajectories with a fake model.

### Phase 1 — MVP (weeks 6–16) — "Build, run, debug, export"
- Canvas editor (React Flow), palette, inspector with schema forms, three edge kinds, typed ports, live validation, state designer.
- Deep Agent composer (model/tools/subagents/backends State+Store+Composite/interrupt_on/permissions/structured output).
- Dev runtime (Agent Server dev pool, interpreted mode); live overlay; chat test panel; breakpoints; state inspector; time travel fork; pinned outputs.
- Workspaces v1: Monaco + LSP, script nodes, custom tools, custom middleware, `@canvas_node` hot reload, sandboxed execution.
- Connections & secrets (model providers, MCP with OAuth), LangSmith tracing + trace overlay.
- Publish to AgentCanvas Cloud runtime (REST + streaming), cron & webhook triggers, export to Git.
- 5 templates. Basic RBAC. Single region.
- **Exit criteria:** J1 and J2 journeys completed by 10 design-partner users unaided; p95 editor latency targets met.

### Phase 2 — GA (months 4–8) — "Team & production"
- Evaluation Studio (datasets, evaluators, experiments, release gate), versions & visual diff, environments, multiplayer, comments & reviews.
- Inbox for HITL with notifications; MCP elicitation; permissions interrupts.
- Publish to customer LangSmith Deployment, MDA target, Docker image; MCP / A2A / AG-UI exposure; embeddable chat widget.
- Event handlers & bus triggers, channels (Slack/Teams/email), DLQ.
- Copilot v1 (NL→graph, NL edits, explain, lint).
- Sandboxes backends for agents, skills & memory managers, Context Hub backend, async subagents, rubric.
- SSO/SCIM, audit, model policies, spend limits; EU region; SOC 2 Type I.

### Phase 3 — Scale & ecosystem (months 8–14)
- Marketplace & org registries with signing; custom frontend widgets.
- TypeScript target; import existing LangGraph projects; branching & 3-way merge.
- Online evals & insights; multi-turn simulation; cost optimizer.
- Hybrid/BYOC and air-gapped self-hosted editions; VS Code extension.
- Dynamic subagents & interpreters (as they exit beta upstream).

> **v0.2 delta:** persistence, Data Studio and self-improvement scope is sequenced into these phases in [10-improvement-plan.md §3](./10-improvement-plan.md#3-updated-delivery-sequencing-delta-to-doc-06). Add ~2 engineers (data modelling/codegen, persistence/data plane) and ~0.5 ML engineer (learning loops) to the team below.

> **v0.4 delta:** multi-framework ordering and staffing now follow [doc 12 Part D](./12-multi-framework-technical-spec.md#part-d--detailed-delivery-plan) (source-verified).

> **v0.3 delta:** framework-agnostic foundations (Core IR/dialect split, Adapter SPI, canonical events, import-boundary CI — ≈15 % of R1 effort) are built in Phases 0–1; black-box framework interop ships with GA; full adapters for OpenAI Agents SDK → Google ADK → Microsoft Agent Framework → CrewAI / Pydantic AI / LlamaIndex / Mastra follow in Phase 3+. See [doc 11 §5](./11-framework-agnostic-plan.md#5-phased-plan-to-extend-support).

## 2. Suggested team (for MVP → GA)

| Area | Headcount |
|------|-----------|
| Frontend (canvas, editor, chat, workspace IDE) | 4 |
| Compiler / IR / runtime (Python, LangGraph experts) | 3 |
| Platform backend (services, data plane, K8s, sandboxes) | 3 |
| DevEx (SDK, CLI, templates, docs) | 1.5 |
| Security & SRE | 1.5 |
| Design (product + UX research) | 1.5 |
| PM | 1 |

## 3. Risks & mitigations

| # | Risk | Impact | Mitigation |
|---|------|--------|-----------|
| R1 | **Upstream churn** — LangChain/Deep Agents evolve fast (e.g. deepagents 0.7 made todos opt-in, removed backend factories; `langchain-mcp-adapters` replaced by `langchain.mcp`). | Broken codegen, drift | Curated, versioned **stacks** (`stack-2026.09`); node types pinned per stack; nightly compatibility CI against upstream main; upgrade assistant that migrates IR between stacks. Keep codegen templates thin and mostly delegating to upstream APIs. |
| R2 | **Leaky abstraction** — visual graphs get unwieldy for complex logic ("spaghetti"). | Adoption by pros | Three-tier model, subgraphs/components, wireless links, script nodes, Copilot, outline view; don't force everything visual. |
| R3 | **Interpreted vs compiled drift** | WYSIWYG broken | Shared node implementation library, differential testing (TR-COMP-40), release blocking. |
| R4 | **Security of user code & agent tools** | Breach | Sandboxes by default, egress policies, HITL defaults for destructive tools, admin gating of shell/in-process code, pen-testing. |
| R5 | **Cost blow-ups** (loops, large contexts) | Customer trust | Loop guards (W001), model/tool call limits by default in templates, budgets & alerts, static estimates. |
| R6 | **Overlap with LangSmith Fleet / Studio / MDA** | Positioning | Position as the *visual IDE + compiler* that targets those platforms (and self-hosted), with deeper graph-tier control, workspaces, events and exportable code. Integrate, don't compete. Consider partnership. |
| R7 | **Performance of canvas at scale** | UX | Virtualization, LOD, WebGL layer fallback, subgraph collapse. |
| R8 | **Non-determinism makes tests flaky** | Eval trust | Mock mode, cassettes, repetitions & statistical comparisons in experiments. |
| R10 | **Persistence backend immaturity** — non-Postgres checkpointers/stores and Agent Server custom checkpointer/store hooks are alpha or community-maintained | Data loss / resume failures | Postgres default; conformance gate; staged rollout per backend; clear support tiers |
| R11 | **Self-improvement regressions or memory poisoning** | Quality & safety | Gated ladder (doc 09), quarantine, provenance, auto-rollback |
| R12 | **Business-data access by agents** | Data leaks | Scoped repositories, least privilege, HITL writes, audit |
| R13 | **Framework-agnosticism dilutes R1** or yields lowest-common-denominator UX | Delay / weak product | 15 % cap on F0 work; portable core + native dialects; no second adapter before GA |
| R9 | **Licensing** | Legal | Generated code & runtime helper Apache-2.0; verify licenses of LangGraph Agent Server for self-hosted redistribution (the open-source `langgraph dev` server vs licensed Agent Server) — design the data plane to work with either. |

## 4. Open questions (decisions needed)

1. ✅ **Decided in v0.5 (review D-01):** own open-source runner for MVP; LangSmith Deployment as publish target. *Original question:* **Runtime licensing & topology**: run our own fleet of self-hosted Agent Servers (requires LangSmith license for production self-hosting) vs. always deploying into the customer's LangSmith account vs. building on the open-source LangGraph runtime + our own API layer. *Recommendation:* support "customer's LangSmith Deployment" and "export" first; evaluate licensing for the managed cloud runtime.
2. **Trusted in-process code**: default for enterprise dedicated tenants only?
3. ✅ **Decided (D-02): CEL + sandboxed Jinja.** *Original:* **Expression language**: CEL vs JSONata vs sandboxed Jinja — *recommendation:* CEL for conditions (safe, typed), sandboxed Jinja for templates.
4. ✅ **Decided (D-03): React Flow.** *Original:* **Canvas library**: React Flow (default) vs LiteGraph fork for ComfyUI familiarity — prototype both with a 500-node graph in week 2.
5. **Pricing model**: seats + runtime usage (runs/compute) + pass-through model cost (or BYO keys).
6. **Target languages beyond Python/TS**: likely unnecessary; Java/Go via exposing workflows as APIs instead.
7. **Name** of the product.

## 5. Success metrics

| Metric | MVP target | GA target |
|--------|-----------|-----------|
| Time to first successful run (new user) | < 10 min | < 5 min |
| % workflows published that use ≥ 1 script/custom node | — | 30–50 % (proves the escape hatch) |
| Weekly active builders / projects | 50 design-partner users | 2,000 |
| Run success rate (non-user-error) | 97 % | 99.5 % |
| Exported projects passing `langgraph dev` unmodified | 100 % | 100 % |
| Eval-gated releases (% of prod publishes) | — | 60 % |
| NPS (builders) | 30 | 45 |
