# 0001 — The Graph IR is the single source of truth

Status: accepted · Date: 2026-09-29

## Context
Canvas, generated code, API clients and the Copilot all need one shared model of a workflow. Treating the canvas layout or the generated code as the master makes diffing, validation and multiple code targets impossible.

## Decision
A canonical JSON Graph IR (Pydantic models in `apps/api/agentcanvas/ir`) is the only persisted definition of a workflow. Canvas and code are projections. Layout lives in a separate document so moving nodes never changes the IR hash.

## Consequences
+ Versioning, diff, validation, codegen and AI editing work on one structure. − Every UI feature needs an IR representation first; IR schema migrations must be maintained (`ir_version`).
