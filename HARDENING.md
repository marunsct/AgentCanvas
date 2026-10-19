# Hardening plan

The running list of product-hardening work, from the deep review of 2026-10-18 (`docs/agent-canvas-spec/14-deep-review-v0.6.md`)
and what followed. **Update this file in the same PR as every hardening change:** move the item to *Done* with its
version and PR, and add anything new you find to *Open*. One PR per item.

## Done

| Item | Version | PR |
|---|---|---|
| Deep-review fixes: run correctness across replicas, server hardening | v0.28.1, v0.29.0 | #52, #53 |
| Echo engine pauses survive restarts; a run always uses its own project's workspace (`PROJECT_OF` from the database) | v0.29.1 | |
| Share links choose which sites may frame their chat page (`frame_origins`, nginx `auth_request`) | v0.30.0 | |
| A production server warns when workspace code is not isolated (`sandbox.not_isolated`) | v0.30.1 | |
| Real sandbox: workspace code runs in a separate service (`script_execution=remote`), default in the prod compose file and Helm chart | v0.31.0 | |
| The local Docker app uses the sandbox service too | v0.31.1 | |
| Parallel branches run in parallel on the shared flow engine | v0.32.0 | |
| Strands worker environment clash (`OTEL_SDK_DISABLED` passed twice) | v0.32.0 | |
| Canvas no longer redraws on every streamed token (throttled to ~8/s) | v0.32.1 | |
| Keyboard access: palette Enter/Space, ports are focusable buttons opening the connect menu, block labels (PR open until merged) | v0.32.2 | #62 |
| Web session state is a store (`useMe`, `useCan`), no longer a bare module variable | v0.32.3 | |
| Framework version pins checked against each other by a test (`test_framework_pins.py`) | v0.32.3 | |
| CI runs locally (`make ci`, `make ci-full`, pre-push hook); GitHub workflow is manual only (PR open until merged) | unreleased | #63 |

## Open

- **Keyboard access, rest:** drawing an edge without a mouse; the inspector forms.
- **Web:** callers still read `ME` directly (move them to `useMe` when touched); the project is still chosen in `queries.ts`, not in a store; about 25 response types in `apiFetch<…>` calls are written by hand (`{ id: string }`, `Settings`, `Policy`, …) and need replacing one by one with `tsc` running; no ESLint (needs `pnpm add -D eslint …` on a machine with registry access, then a config and a `make ci` stage).
- **Database:** RLS policies on child tables nest 3–4 EXISTS levels (EXPLAIN the hot list queries with a tenant set); the RLS migration locks 35 tables at once.
- **Code:** duplicated engine, worker and packager code (about 1,000–1,300 lines); `Resources` is still LangChain's object for every adapter.
- **CI:** local CI lacks the `export`, `frameworks`, `connectors` and Trivy/prod-compose stages (still only in `ci.yml`); no framework-environment cache; no Mastra `tsc`; the Mastra npm set has no lockfile.
- **Sandbox:** egress allow-lists in `sandbox.json` still work with LangSmith only; prod compose and `helm template` rendering of the sandbox service are unverified.
- **Unverified here (owner runs):** nginx `auth_request` config, `make e2e`, the conformance kit with every framework environment, `lint-imports`.
- **Owner:** release tags v0.1.0–v0.32.x not pushed; old-branch cleanup; close #48, decide #45, mark/merge #36; revoke the token pasted earlier.
