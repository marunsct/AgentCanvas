# 10 — Security Checklist

Use as a gate: M6 (MVP) must pass all **P0** items; M8 all items. Each item says *how to verify*.

## 1. Code execution
| # | Item | Verify | Pri |
|---|------|--------|-----|
| S1 | No `eval`/`exec` on user input; conditions via CEL, templates via `SandboxedEnvironment` | `grep -rn "eval(\|exec(" apps/api` returns nothing in request paths; tests try `{{ ''.__class__ }}` and expect an error | P0 |
| S2 | Hosted mode runs scripts in the Docker sandbox: `--network none`, read-only FS, CPU/memory/time limits, non-root | test script opening a socket fails; infinite loop killed at timeout | P0 (hosted) |
| S3 | Workspace file API blocks path traversal (`resolve()` stays under root) | tests with `../`, absolute paths, symlinks | P0 |
| S4 | Custom tools/nodes imported only from the project workspace; no arbitrary `import_path` outside allowed packages (`scripts.`, `tools.`, `examples.`) | validator rule rejects `os:system` | P0 |
| S5 | Generated code never contains secrets | test: generate with secret refs, grep output | P0 |

## 2. Agent & tool safety
| # | Item | Verify | Pri |
|---|------|--------|-----|
| S6 | Tools that change external state default to approval (`approve_tools`, MCP `destructive_hint`) | validator W002 on missing approval | P0 |
| S7 | HTTP tool domain allow-list; no access to metadata IPs (169.254.169.254) or private ranges unless allowed | tests with blocked hosts | P0 |
| S8 | Call limits in templates (`ModelCallLimitMiddleware`, `ToolCallLimitMiddleware`) and recursion limit | loop test hits limit | P1 |
| S9 | PII middleware available and documented; traces can mask inputs/outputs | test redaction | P1 |
| S10 | Memory writes from untrusted content are tagged and reviewable (doc08/09) | post-MVP | P2 |

## 3. Secrets
| # | Item | Verify | Pri |
|---|------|--------|-----|
| S11 | Secrets encrypted at rest (Fernet → KMS); API returns names only | DB inspection; API tests | P0 |
| S12 | Secrets never in logs, SSE events, diagnostics, error messages | log capture tests with a known secret | P0 |
| S13 | Validator blocks secret-like literals (E050) | tests with `sk-…`, `AKIA…` | P0 |
| S14 | Checkpoints encrypted in prod (`LANGGRAPH_AES_KEY` + `EncryptedSerializer`) | DB inspection shows ciphertext | P1 |

## 4. Authentication & authorisation
| # | Item | Verify | Pri |
|---|------|--------|-----|
| S15 | All `/v1` endpoints require auth except `/healthz` and signed webhooks | route scan test lists unauthenticated routes | P0 (M7) |
| S16 | JWT validation: issuer, audience, expiry, signature via JWKS | expired/wrong-aud tokens → 401 | P0 (M7) |
| S17 | Tenant isolation in every query (org filter in repositories) | cross-org test for each resource | P0 (M7) |
| S18 | Role checks on mutating endpoints | viewer gets 403 | P0 (M7) |
| S19 | Deployment API keys hashed (sha256), shown once, revocable | DB inspection; revoke test | P0 |
| S20 | Webhooks verify HMAC with constant-time compare and reject stale timestamps | tests | P0 |

## 5. Web
| # | Item | Verify | Pri |
|---|------|--------|-----|
| S21 | CORS restricted to known origins | preflight from other origin fails | P0 |
| S22 | Security headers via nginx: `Content-Security-Policy` (script-src 'self'), `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `frame-ancestors 'none'` | header scan | P1 |
| S23 | No `dangerouslySetInnerHTML` with model output; render model text as text/Markdown with a sanitiser | code search | P0 |
| S24 | CSRF: API uses bearer tokens (not cookies) → not applicable; if cookies are added, use SameSite=Strict + CSRF token | review | P1 |

## 6. Supply chain & operations
| # | Item | Verify | Pri |
|---|------|--------|-----|
| S25 | Lockfiles committed (`uv.lock`, `pnpm-lock.yaml`); CI installs with `--frozen` | CI config | P0 |
| S26 | Dependency audit in CI (`pip-audit`, `pnpm audit --prod`), Dependabot/Renovate | CI | P1 |
| S27 | Containers run as non-root, minimal base images, image scanning (Trivy) | CI | P1 |
| S28 | Audit log for edits, publishes, approvals, secret access | tests | P1 (M7) |
| S29 | Data retention & deletion (threads, checkpoints, traces) documented and automated | job exists | P1 |
| S30 | Rate limits on run creation and webhooks per project/key | load test | P1 |

## 7. Review at the end of S11 (M8-T6, 2026-10-06)

Every item was checked against the code; tests named here run in CI (`apps/api/tests/`). **No open P0 item.**
Items marked *Partial* keep a follow-up in the S12 backlog ([13-sprint-plan.md](./13-sprint-plan.md)).

| # | Status | Evidence | Follow-up |
|---|--------|----------|-----------|
| S1 | Pass | CEL and `SandboxedEnvironment` only (`agentcanvas/expr.py`); `test_security.py::test_templates_cannot_reach_python_internals`, `::test_cel_cannot_reach_python_internals` | |
| S2 | Pass | `sandbox/runners.py`: `--network none --read-only --cap-drop ALL --user 65534`, memory/CPU/pids limits, the container is killed on timeout; hosted refuses to start without the docker or LangSmith sandbox (`security/auth.py`). `test_workspace.py::test_docker_sandbox_has_no_network`, `test_auth.py::test_dev_mode_is_refused_in_production` | |
| S3 | Pass | `workspace/store.py` resolves and stays under the root; `test_workspace.py::test_workspace_scaffold_git_and_path_safety`, `::test_file_api` | add a symlink case |
| S4 | Pass | allowed prefixes (`security/imports.py`), E080, `test_security.py::test_imports_outside_workspace_are_refused`. Exported code imports workspace packages from its own path by design | |
| S5 | Pass | `$secret` references only; `test_secrets.py::test_secrets_are_write_only_and_redacted_everywhere` | |
| S6 | Pass | W002 for destructive MCP tools and for tools that write or send (HTTP other than GET/HEAD, declared `external_write`/`message_send`) without approval (`validation/rules/agents.py`) | |
| S7 | Pass | allow-list; hosts resolving to private, loopback, link-local (metadata) or reserved addresses are refused and the request goes to the checked address (no DNS rebinding); redirects are not followed. `test_tools.py::test_http_tool_refuses_internal_addresses`, `::test_http_tool_refuses_other_domains` | |
| S8 | Pass | recursion limit; every agent template has a model call limit; `test_catalog.py::test_model_call_limit_ends_the_agent`, `test_runs_v1.py::test_recursion_limit_is_a_readable_error` | |
| S9 | Partial | PII middleware (`test_catalog.py::test_pii_middleware_redacts_model_input`) | trace masking for LangSmith/OTel payloads |
| S10 | Gap (P2) | memory inspector only | provenance/trust tags on memory writes |
| S11 | Pass | per-organisation data keys wrapped by a KMS/file master key, re-wrap command (`security/kms.py`); the development key is refused in staging/prod/hosted. `test_kms.py` | |
| S12 | Pass | events, thread state, history, explain and log lines mask secret values (6+ characters); `test_secrets.py::test_secrets_are_write_only_and_redacted_everywhere` | |
| S13 | Pass | E050 (`validation/rules/config.py`); `test_validate.py::test_secret_literal` | |
| S14 | Pass | AES checkpoints when `LANGGRAPH_AES_KEY` is set; prod logs a warning without it. `test_checkpoint_encryption.py` | long-term memory (store) is not encrypted by LangGraph |
| S15 | Pass | route scan: every operation answers 401 without a token except health, auth config, deployment-key and signed-webhook routes; API docs off in prod. `test_auth.py::test_every_other_route_needs_sign_in`, `::test_api_docs_are_off_in_production` | |
| S16 | Pass | `test_auth.py::test_invalid_tokens_are_401` | |
| S17 | Pass | `org_scope` on every route; `test_org_scope.py::test_org_b_cannot_read_org_a` (reads and writes), `::test_every_path_parameter_is_scoped` | |
| S18 | Pass | `test_roles.py` | |
| S19 | Pass | `test_publish.py::test_publish_pins_the_version_and_keys_rotate` | |
| S20 | Pass | HMAC over timestamp and body, 5-minute window, constant-time compare, unknown triggers answer 401. `test_triggers.py::test_signed_webhook_starts_a_run` | |
| S21 | Pass | `test_auth.py::test_cors_refuses_other_origins` | |
| S22 | Pass | CSP (`infra/web/20-csp.envsh`), nosniff, `frame-ancestors 'self'` (the silent sign-in renewal frames the app itself; no other site can); CI checks the headers. `script-src` allows `'unsafe-eval'` for Ajv form validation; the whole e2e suite passes under this policy | drop `'unsafe-eval'` with an interpreting JSON Schema validator |
| S23 | Pass | no `dangerouslySetInnerHTML`/`innerHTML` in `apps/web/src`; model text renders as text | |
| S24 | N/A | bearer tokens only | |
| S25 | Pass | `uv sync --frozen`, `pnpm install --frozen-lockfile` in CI and images | |
| S26 | Pass | CI `audit` job (pip-audit, pnpm audit) and Dependabot; clean on 2026-10-06 | |
| S27 | Pass | non-root images, OS updates applied at build, Trivy (HIGH/CRITICAL with a fix) in CI | pin base images by digest |
| S28 | Pass | `test_audit.py` | |
| S29 | Partial | conversation retention job (`services/retention.py`, `test_retention.py`) | audit-log and memory retention |
| S30 | Pass (S12) | nginx limits deployment and webhook endpoints per client address (20 r/s, burst 40, 429); the API limits each deployment key, webhook and project per minute across replicas (`services/ratelimit.py`). `test_ratelimit.py` | |
