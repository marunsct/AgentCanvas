# 0003 — MVP runtime is our own open-source runner

Status: accepted · Date: 2026-09-29

## Context
Self-hosting LangGraph Agent Server in production requires a LangSmith licence; the MVP needs a free local/dev runtime and full control over events.

## Decision
Implement a FastAPI runner on the open-source LangGraph library with Postgres (or SQLite in the local edition) checkpoints, mirroring the Agent Server API shape (threads, runs/stream, state, history).

## Consequences
+ No licence dependency; full control of events and debugging hooks. − We implement threads/runs/streaming ourselves; LangSmith Deployment stays a publish target (see ADR 0013).
