# 0010 — OIDC authentication, single organisation in the MVP

Status: accepted · Date: 2026-09-29

## Context
Enterprise customers use their identity provider; multi-org adds complexity early.

## Decision
OIDC login (Keycloak locally), JWT verified by the API, roles admin/builder/reviewer/viewer. Until M7 a dev user is injected.

## Consequences
+ Standard integration. − Multi-org tenancy arrives in M7.
