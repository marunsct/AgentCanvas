# 0013 — LangSmith Deployment may host interpreted mode via a ServerRuntime graph factory

Status: accepted · Date: 2026-09-29

## Context
Agent Server officially supports graph factories taking `ServerRuntime` (beta, langgraph-api ≥ 0.7.31); previously unverified (review F-03).

## Decision
The LangSmith Deployment target can register `agentcanvas_runtime.server:make_graph`, an async context manager that loads the pinned IR from assistant config and calls the same `build_graph` as the runner; introspection calls get the same topology without connections. Compiled export remains the portable default.

## Consequences
+ Publish without codegen, per-user connections, identical semantics. − Depends on a beta upstream API (capability-flagged, D16).
