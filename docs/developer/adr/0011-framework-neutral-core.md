# 0011 — Framework-neutral core with an adapter boundary

Status: accepted · Date: 2026-09-29

## Context
The product must be able to add other agent frameworks without rewriting the core (spec docs 11/12).

## Decision
`ir/` and services never import LangChain/LangGraph; framework code lives in adapters (LangChain first). Enforced by import-linter from Sprint 2.

## Consequences
+ Future adapters are additive. − Some indirection in the runner and compiler.
