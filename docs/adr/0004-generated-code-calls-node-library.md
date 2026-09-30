# 0004 — Generated code calls the shared node library

Status: accepted · Date: 2026-09-29

## Context
Interpreted (canvas) and compiled (export) modes must behave identically — the WYSIWYG fidelity contract.

## Decision
Generated code assembles the graph with plain LangGraph calls but builds each node through the same `agentcanvas.runtime.nodes` builders the interpreter uses. Equivalence is tested per node type. A fully inlined 'clean eject' is a later option.

## Consequences
+ Behavioural equivalence by construction. − Exported projects depend on the small Apache-2.0 `agentcanvas-runtime` package.
