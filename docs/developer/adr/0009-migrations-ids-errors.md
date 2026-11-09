# 0009 — Alembic migrations, prefixed ULIDs, problem+json errors

Status: accepted · Date: 2026-09-29

## Context
Consistent conventions are needed before the first table and endpoint.

## Decision
SQLAlchemy 2.0 models with Alembic migrations; IDs are prefixed ULIDs (`wf_`, `n_`, `th_`, `run_`, …); API errors use RFC 9457 `application/problem+json`.

## Consequences
+ Readable IDs, sortable by time; uniform error handling. − IDs are longer than UUIDs.
