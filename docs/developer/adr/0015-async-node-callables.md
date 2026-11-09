# 0015 — Node-library callables are always async

Status: accepted · Date: 2026-09-29

## Context
LangGraph per-node timeouts only work on async nodes (sync nodes raise ValueError at build time).

## Decision
Every builder in `agentcanvas.runtime.nodes` returns an `async` callable; sync user code runs via `asyncio.to_thread`, which keeps `interrupt()`, `get_config()` and `get_store()` working (verified).

## Consequences
+ Timeouts and cancellation work for every node type. − A thread hop for sync scripts.
