# 12 — Traceability: P0 requirements → tickets

Every **P0** requirement from the functional spec (doc01) and the P0 items of docs 05 and 08 map to at least one ticket in [04-implementation-plan.md](./04-implementation-plan.md). "Partial" means the ticket delivers the P0 part; the rest is P1/P2 as labelled in the spec.

| Requirement | Summary | Ticket(s) | Milestone |
|-------------|---------|-----------|-----------|
| FR-CAN-01 | Pan/zoom, minimap, snapping, auto-layout | M2-T1, M2-T11 (React Flow built-ins + ELK) | M2 |
| FR-CAN-02 | Drag from palette, quick-add, port-drag search | M2-T4, M2-T12 | M2 |
| FR-CAN-03 | Flow / wiring / data edges drawn distinctly | M2-T6 (data edges with M2-T13 subgraph mapping) | M2 |
| FR-CAN-04 | Router branch labels & ports | M2-T5 | M2 |
| FR-CAN-05 | Groups & subgraphs | M2-T13 | M2 |
| FR-CAN-07 | Node status badges | M2-T9, M4-T2 | M2/M4 |
| FR-CAN-09 | Undo/redo, copy/paste | M1-T4, M2-T10 | M1/M2 |
| FR-CAN-11 | Keyboard & command palette | M2-T12 | M2 |
| FR-NODE-01 | Categorised palette | M1-T7, M2-T4 | M1/M2 |
| FR-NODE-02 | Node anatomy, config form, "view code" | M1-T7, M2-T5, M2-T7; code view via `GET /code` | M2 |
| FR-NODE-03 | Literal / expression / secret / promoted config fields | M2-T7 (expressions), M5-T6 (secrets); promote-to-port = partial (P1 follow-up) | M2/M5 |
| FR-NODE-04 | Prompt editor with variables | M2-T7 (`PromptWidget`) | M2 |
| FR-NODE-06 | Custom nodes with provenance badge | M5-T5, M5-T7 | M5 |
| FR-NODE-07 | Retry, cache, timeout, error route | M3-T13 | M3 |
| FR-TYPE-01 | Typed ports, refuse incompatible | M2-T6 | M2 |
| FR-TYPE-02 | Continuous validation + problems panel | M1-T8, M2-T9 | M1/M2 |
| FR-TYPE-03 | Graph-level checks | reference (E010–E013, W001), M1-T8 (E020…) | M0/M1 |
| FR-TYPE-04 | Agent-level checks | M1-T8 (E030–E033), M3-T3 | M1/M3 |
| FR-TYPE-05 | Security checks | M1-T8 (E050), reference E080, M5-T4, doc10 | M1/M5 |
| FR-STATE-01 | State designer with reducers | reference `make_state_type`, M2-T8 | M2 |
| FR-STATE-02 | Input / output / context schemas | M3-T11 (output), M3-T14 (context schema); input schema form M4-T1 | M3/M4 |
| FR-STATE-03 | Presets (Chat, Agent, Pipeline) | M2-T8 | M2 |
| FR-AGENT-01 | Agent slots (model, tools, middleware, subagents, backend…) | M3-T3, M3-T4, M3-T12 | M3 |
| FR-AGENT-02 | System prompt, interrupt_on, permissions | M3-T3, M3-T4 (permissions table = partial) | M3 |
| FR-AGENT-03 | Capability toggles (planning, summarisation…) | M3-T4, M3-T12 | M3 |
| FR-AGENT-04 | Declarative subagents (sync) | M3-T4 | M3 |
| FR-AGENT-05 | Backends State/Store/Composite | M3-T4, M3-T14 | M3 |
| FR-AGENT-10 | Structured output designer | M3-T3 | M3 |
| FR-RUN-01 | Run with chat / form / test case | M4-T1, M6-T9 | M4/M6 |
| FR-RUN-02 | Live overlay | reference, M4-T2, M4-T3 | M0/M4 |
| FR-RUN-03 | Run all / from here / step / re-run | M4-T8 (step via breakpoints), M4-T9 (pins → run from here), M4-T7 (replay) | M4 |
| FR-RUN-04 | Breakpoints | M4-T8 | M4 |
| FR-RUN-05 | State inspector, edit, fork | M4-T6, M4-T7 | M4 |
| FR-RUN-06 | Timeline scrubber | M4-T5 | M4 |
| FR-RUN-07 | Pinned outputs | M4-T9 | M4 |
| FR-RUN-10 | Multiple test threads | M1-T6, M4-T1 | M1/M4 |
| FR-RUN-13 | Cost & latency per node | M4-T11 | M4 |
| FR-HITL-01 | Approval node & tool approvals | reference, M4-T4 | M0/M4 |
| FR-HITL-02 | Human input node | M3-T7, M4-T4 | M3/M4 |
| FR-EVAL-01 | Test cases per workflow | M6-T9 | M6 |
| FR-OBS-01 | Runs traced with metadata | M6-T6 | M6 |
| FR-OBS-02 | Trace overlay on canvas | M6-T6 (link + node mapping by name; full span overlay P1) | M6 |
| FR-VER-01 | Autosave + commit versions | M2-T3, M6-T1 | M2/M6 |
| FR-DEP-01 | Publish to own runtime, export | M6-T3, M6-T4 (LangSmith/MDA targets P1) | M6 |
| FR-DEP-02 | Pre-publish checklist | M6-T4 (validation clean, tests pass M6-T9, secrets bound) | M6 |
| FR-EXP-01 | REST API for published workflows | M6-T4 | M6 |
| FR-EXP-04 | Cron & webhooks | M6-T5 | M6 |
| FR-TPL-01 | 5 templates | M6-T7 | M6 |
| FR-CON-01 | Connections manager | M3-T2, M3-T5, M5-T6 | M3/M5 |
| FR-CON-02 | Secrets vault | M5-T6 | M5 |
| FR-CON-04 | MCP server browser | M3-T5 | M3 |
| FR-GOV-01 | Orgs, projects, basic RBAC | M7-T2, M7-T3 | M7 |
| FR-COST-02 | Actual cost per run/node | M4-T11 | M4 |
| FR-SCR-01…04 | Script nodes, signatures, execution modes, runtime access | M5-T3, M5-T4, M5-T6 | M5 |
| FR-CN-01…03 | Custom node SDK | M5-T7 | M5 |
| FR-PER-01 | Persistence on by default | reference (checkpointer always), M1-T6 | M0/M1 |
| FR-PER-02 | Checkpointer binding (platform/Postgres/SQLite/memory) | M1-T6 (Postgres/memory); per-environment bindings UI P1 | M1 |
| FR-PER-03 | Durability mode | M1-T6 (`durability` run option; verified parameter) | M1 |
| FR-PER-07 | Thread browser view/delete | M1-T6, M4-T5 | M1/M4 |
| FR-MEM-01…03 | Memory spaces, recall/remember nodes, Deep Agent mounting | M3-T14 | M3 |
| FR-MEM-04 | Memory inspector (browse/delete) | M4-T12 | M4 |
| FR-DM-* | Data Studio | re-prioritised to P1 (review S-04) → M9 | GA |

**Non-functional (NFR-01…09):** covered by M1-T8 (validation speed), M4-T3 (UI performance), M8-T3/T5 (latency, availability measurement), doc10 (security), M8-T4 (RPO/RTO).
