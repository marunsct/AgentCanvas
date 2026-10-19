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
| **Hardening pass 2026-10-20** (found by running the suites on Postgres and the browser journeys, then fuzzing): a chat stopped after its first message (the run's recording could not be saved, the thread stayed "running"); the next message could get 409 right after `RUN_FINISHED`; concurrent Alembic runs crashed (advisory lock, lock timeout); four exporter 500s on malformed free-form settings; the knowledge-QA export failed its own tests; NUL bytes and out-of-range numbers were 500s on Postgres; unexpected errors answer as problem+json with the request id | unreleased | |
| Robustness tests: every API operation with hostile input (`test_api_robustness.py`); damaged workflows through every reader and a run (`test_workflow_fuzz.py`, `ROBUSTNESS_ROUNDS`/`ROBUSTNESS_SEED` for a soak) | unreleased | |
| Local CI: the API suite on a throwaway Postgres container, `frameworks` and `export` stages, Helm from its image, exports' environments deleted after their tests | unreleased | |
| Keyboard access: the port menu takes focus, traps Tab, returns focus on Escape; the web client gives up on a server that never answers; `useMe`/`useCan` in the Home, Members, Copilot and Editor pages | unreleased | |
| Mastra npm set locked (`package-lock.json`, `npm ci` in `make mastra` and the image; a test checks it matches `package.json`) | unreleased | |
| API image on Python 3.14 (#45), verified with every framework and Mastra built in and the API suite on 3.14 | unreleased | #45 |
| Sandbox: a warning when `sandbox.json` lists egress/inject that the mode in use cannot give | unreleased | |

## Verified 2026-10-20 (no change needed)

- **Row-level security cost.** 120,000 runs, 12,000 threads, 20 organisations, as the non-superuser role: a point query
  through the four-level policy chain takes about 1 ms; a whole-table aggregate takes 330 ms with all tenants and 176 ms
  narrowed to one (Postgres turns the nested `EXISTS` into hashed sub-plans once per query). Nothing to change until
  tables are far larger; re-measure with `EXPLAIN (ANALYZE)` as that role if a tenant's list pages get slow.
- **Production compose and Helm.** `infra/compose.prod.yml` renders and refuses to start without its secrets; the Helm chart
  lints `--strict` and renders the sandbox Deployment (read-only root, no capabilities, only the sandbox token) with a
  NetworkPolicy that lets only the API in and nothing out.
- **Dependency audit** (`pip-audit`, `pnpm audit`) is clean. Ruff's security rules (`S`) report only the intended `exec` of
  generated code inside worker processes.
- **Browser journeys**: 72 pass on the production build under the strict CSP (Chromium 1243, `E2E_PROD=1`).

## Open

- **Keyboard access, rest:** the inspector forms (the canvas and port menu can now be used without a mouse).
- **Web:** other callers still read `ME` directly (move them to `useMe` when touched); the project is still chosen in `queries.ts`
  (58 uses of `PROJECT_ID` and friends), not in a store; about 25 response types in `apiFetch<…>` calls are written by hand
  (`{ id: string }`, `Settings`, `Policy`, …) and need replacing one by one with `tsc` running; no ESLint (needs
  `pnpm add -D eslint …` on a machine with registry access, then a config and a `make ci` stage).
- **Database:** the RLS migration still takes locks on 35 tables in one transaction (it now gives up after 60 s instead of
  queueing other queries; run it in a quiet moment on a large installation).
- **Code:** duplicated engine, worker and packager code (about 1,000–1,300 lines); `Resources` is still LangChain's object for
  every adapter. Worker temp folders (`/tmp/agentcanvas-<framework>-*`) are removed on a clean exit; a killed server leaves
  them (a startup sweep of old ones would fix it).
- **CI:** local CI lacks the Trivy scan and the prod-compose probes of `images`; no framework-environment cache; no Mastra `tsc`
  (the Mastra environment has no TypeScript compiler installed).
- **Sandbox:** egress allow-lists in `sandbox.json` still work with LangSmith only (now warned about, not enforced).
- **Exports:** an export of a workflow that searches a knowledge base the project lacks leaves the base out silently; the export
  plan could list it as a missing prerequisite.
- **Unverified here (owner runs):** nginx `auth_request` config, the conformance kit's remaining frameworks beyond those
  installed in `apps/api/.frameworks`, `lint-imports` beyond `make lint`, Trivy, Kubernetes (kind) and compose deploys of exports.
- **Owner:** release tags v0.1.0–v0.32.x not pushed; delete the merged branches still on GitHub (`claude/project-thread-*` ×5, `claude/sprint-11…13-*`, `claude/trusting-curie-n0i720`, `claude/sandbox-warning`); revoke the token pasted earlier. #48 is closed, #45 and #36 are merged. Commit or discard `.sitepkgs.tgz` (80 MB, untracked in the repository root).
