# 0016 — DeltaChannel is a channel storage mode

Status: accepted · Date: 2026-09-29

## Context
`DeltaChannel` reducers receive batches and delta/non-delta representations are not interchangeable for persisted threads.

## Decision
Channels have `storage: full | delta`, orthogonal to the reducer; delta only with batch-safe built-in reducers. Changing storage mode on a published workflow with live threads is blocked (E074).

## Consequences
+ Long threads stay small without corrupting old threads. − Users cannot freely toggle the option after publishing.
