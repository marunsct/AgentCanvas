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
