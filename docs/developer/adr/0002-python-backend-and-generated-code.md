# 0002 — Python 3.12 backend; generated agent code is Python

Status: accepted · Date: 2026-09-29

## Context
The compiler must introspect LangChain/LangGraph/Deep Agents types; several features (node timeouts, error handlers, interpreters) are Python-only (spec doc 15 §4).

## Decision
Backend (control plane + runner) in Python 3.12 with FastAPI/Pydantic v2 and uv. Canonical code target is Python; TypeScript target later via the capability matrix.

## Consequences
+ One language for IR models, validator, interpreter and codegen. − Frontend types must be generated from JSON Schema to stay in sync.
