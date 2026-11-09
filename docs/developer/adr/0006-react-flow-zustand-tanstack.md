# 0006 — React Flow canvas, Zustand and TanStack Query

Status: accepted · Date: 2026-09-29

## Context
The canvas needs custom node renderers, typed handles, good React integration and accessibility.

## Decision
`@xyflow/react` 12 for the canvas, Zustand for client state, TanStack Query for server data, RJSF for config forms, Monaco for code.

## Consequences
+ Mature ecosystem. − Very large graphs (> 1k nodes) may need LOD or a WebGL layer (spike SP-1).
