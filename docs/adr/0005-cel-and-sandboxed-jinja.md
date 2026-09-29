# 0005 — CEL for conditions, sandboxed Jinja for templates

Status: accepted · Date: 2026-09-29

## Context
Routers and prompts need user-written expressions; `eval` is unacceptable.

## Decision
Conditions use CEL (`cel-python`); text templates use `jinja2.sandbox.SandboxedEnvironment` with `StrictUndefined`. Both are validated server-side (`POST /v1/expressions/check`).

## Consequences
+ Safe, side-effect-free, typed conditions. − Users learn two small languages; CEL tooling in the browser is limited.
