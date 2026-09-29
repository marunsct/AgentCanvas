# 09 — Deployment & Operations

Files referenced here are in [`reference/infra/`](./reference/infra/): `docker-compose.yml`, `Dockerfile.api`, `Dockerfile.web`, `nginx.conf`, `schema.sql`.

> Verification status: `docker-compose.yml` passes `docker compose config`; the Dockerfiles and nginx config follow the official uv, Node and nginx patterns but were **not built in the review environment** (no Docker daemon). Build them in M8-T1 and fix anything that fails there.

## 1. Environments

| Env | Purpose | Data | Models |
|-----|---------|------|--------|
| `dev` | laptops | docker-compose Postgres/Redis | fake server or personal keys |
| `ci` | tests | ephemeral Postgres service | fake models only |
| `staging` | pre-release checks, demos | managed Postgres (separate DB) | real keys with low spend limits |
| `prod` | customers | managed Postgres with PITR, Redis 8 | real keys, per-project secrets |

## 2. Build images

```bash
# from the product repo root
docker build -f infra/Dockerfile.api -t agentcanvas-api:$(git rev-parse --short HEAD) apps/api
cp infra/nginx.conf apps/web/nginx.conf
docker build -f infra/Dockerfile.web -t agentcanvas-web:$(git rev-parse --short HEAD) apps/web
```
- The API image runs as a non-root user and exposes `/healthz`.
- `ruff` is a runtime dependency (the code generator formats output with it).

## 3. Single-host deployment (small teams, staging)

Add to `docker-compose.yml`:
```yaml
  api:
    image: agentcanvas-api:latest
    env_file: .env.prod                      # DATABASE_URL, AGENTCANVAS_SECRET_KEY, LANGGRAPH_AES_KEY, OIDC_*
    depends_on: { postgres: { condition: service_healthy } }
    command: ["/app/.venv/bin/uvicorn", "agentcanvas.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
  web:
    image: agentcanvas-web:latest
    ports: ["80:80"]
    depends_on: [api]
```
Before first start: `docker compose run --rm api /app/.venv/bin/alembic upgrade head`.
Put a TLS-terminating proxy (Caddy/Traefik/cloud LB) in front; keep **response buffering off** for `/api/*` so Server-Sent Events stream in real time.

## 4. Kubernetes (M8-T2)

| Object | Notes |
|--------|-------|
| `Deployment api` | 2+ replicas; `readinessProbe`/`livenessProbe` → `/healthz`; resources: 1 CPU / 1 GiB to start; `terminationGracePeriodSeconds: 60` so streaming runs can finish |
| `Deployment web` | nginx; 2 replicas |
| `Job migrate` | `alembic upgrade head` as a Helm pre-upgrade hook |
| `Ingress` | annotation to disable buffering, e.g. `nginx.ingress.kubernetes.io/proxy-buffering: "off"`, `proxy-read-timeout: "3600"` |
| Secrets | from the cloud secret manager via External Secrets; never in the chart values |
| Cron scheduler | runs inside API pods; only the holder of Postgres advisory lock `pg_try_advisory_lock(42)` schedules (05 §9) |

**Sticky sessions are not required:** runs persist in Postgres; a reconnecting client reads state via `GET /v1/threads/{id}`.

## 5. Configuration & secrets in production
- Required env: `AGENTCANVAS_ENV=prod`, `DATABASE_URL`, `AGENTCANVAS_SECRET_KEY` (64+ random chars), `LANGGRAPH_AES_KEY` (encrypts checkpoints; with it set, use `EncryptedSerializer.from_pycryptodome_aes()` as the checkpointer `serde`), `OIDC_ISSUER`, `OIDC_AUDIENCE`, `CORS_ORIGINS`.
- Model provider keys are **per project secrets** (M5-T6), not process env vars.
- Rotate `AGENTCANVAS_SECRET_KEY` with a re-encryption job (decrypt with old, encrypt with new); M8 moves to KMS envelope encryption.

## 6. Observability
- **Logs:** JSON (structlog) to stdout; ship with your platform's collector. Fields: `request_id`, `workflow_id`, `thread_id`, `run_id`, `level`, `event`.
- **Traces:** OpenTelemetry (`opentelemetry-instrumentation-fastapi`, `-sqlalchemy`, `-httpx`); LLM traces in LangSmith when `LANGSMITH_TRACING=true` (runs tagged with `workflow_id`).
- **Metrics:** `runs_total{status}`, `run_duration_seconds`, `tokens_total{direction}`, `interrupts_pending`, `sse_clients`, HTTP RED metrics.
- **Alerts:** error rate > 5 % over 10 min; p95 run start latency > 2 s; pending interrupts older than SLA; DB connections > 80 %.

## 7. Backups, retention, disaster recovery
- Postgres: managed PITR (≥ 7 days) + nightly logical dump of the control tables.
- Checkpoint retention job (daily): delete threads older than the project's retention (default 30 days) → `await checkpointer.adelete_thread(thread_id)` + delete `thread` rows.
- Restore drill quarterly: restore to a new instance, run smoke tests, measure RTO.

## 8. Release process
1. Merge to `main` → CI builds images tagged with the commit SHA.
2. Deploy to staging automatically; run Playwright smoke tests against staging.
3. Tag `vX.Y.Z` → promote the same images to prod (no rebuild); migrations run first.
4. Rollback = redeploy the previous image tag; migrations must be backward compatible for one release (expand → migrate → contract).

## 9. Runbooks (write these in M8)
| Situation | First steps |
|-----------|-------------|
| Runs stuck "running" | check API pod restarts; runs whose task vanished → mark `error` by a sweeper job (runs `running` > 1 h with no events) |
| SSE shows nothing until the end | a proxy is buffering: check ingress/nginx `proxy_buffering off`, header `X-Accel-Buffering: no` |
| DB connection exhaustion | lower pool size per replica; check long transactions; add PgBouncer. LangGraph's `AsyncPostgresSaver.from_conn_string` already connects with `autocommit=True, prepare_threshold=0` (no prepared statements, verified in source), so **transaction pooling works for checkpoints**; if you build your own psycopg pool for the saver, use the same three settings (`autocommit=True, prepare_threshold=0, row_factory=dict_row`). For SQLAlchemy + asyncpg behind transaction pooling set `connect_args={"statement_cache_size": 0}`. |
| Model provider outage | enable fallback models (ModelFallbackMiddleware) in affected workflows; show provider status banner |
| Leaked secret | revoke at provider, rotate secret in project, audit `audit_log` for `secret.read` events |
