# 0012 — Spec and product live in one repository

Status: accepted · Date: 2026-09-29

## Context
The build guide originally proposed a separate product repository; this repository already hosts the spec and is where the product is built.

## Decision
The monorepo lives here: `apps/api`, `apps/web`, `packages/`, `infra/`, with the spec in `docs/`. Spec and code change in the same pull request.

## Consequences
+ Spec and implementation cannot drift silently. − The repository mixes documentation and code; CI jobs are scoped by path.
