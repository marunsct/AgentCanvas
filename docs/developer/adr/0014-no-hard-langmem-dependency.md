# 0014 — No hard dependency on langmem

Status: accepted · Date: 2026-09-29

## Context
`langmem` has not been released since 2025-10-27 while the learning ladder referenced it.

## Decision
Memory extraction, consolidation and prompt optimisation are AgentCanvas learning-loop workflows on core primitives (`create_agent` + structured output, `BaseStore`, LangSmith datasets/prompts). A langmem-backed implementation may be an optional adapter.

## Consequences
+ No stale dependency on the critical path. − We maintain those loops ourselves.
