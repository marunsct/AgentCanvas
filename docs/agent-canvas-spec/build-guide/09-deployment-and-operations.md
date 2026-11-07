# 09 — Deployment & Operations

Files referenced here are in the repository's [`infra/`](https://github.com/marunsct/AgentCanvas/tree/main/infra) folder: `Dockerfile.api`, `Dockerfile.web`, `nginx.conf`, `compose.prod.yml`, `.env.prod.example`, the Helm chart `helm/agentcanvas/`, and `docker-compose.yml` (development services only).

> Verification status (S11): both images build, and `compose.prod.yml` comes up healthy with two API replicas behind nginx on Postgres; a run streams through nginx and both replicas serve. CI repeats this on every push (job `images`) and lints and renders the Helm chart.

## 1. Environments

| Env | Purpose | Data | Models |
|-----|---------|------|--------|
| `dev` | laptops | docker-compose Postgres/Redis | fake server or personal keys |
| `ci` | tests | ephemeral Postgres service | fake models only |
| `staging` | pre-release checks, demos | managed Postgres (separate DB) | real keys with low spend limits |
| `prod` | customers | managed Postgres with PITR | real keys, per-project secrets |

## 2. Build images

```bash
# from the repository root
docker build -f infra/Dockerfile.api -t agentcanvas-api:$(git rev-parse --short HEAD) apps/api
docker build -f infra/Dockerfile.web -t agentcanvas-web:$(git rev-parse --short HEAD) .
```

- **API image:** multi-stage (uv builds the virtualenv; the runtime stage has no build tools), runs as uid 10001, includes `git` for project workspaces, logs JSON, and exposes `/healthz` (liveness) and `/readyz` (the database and the run store answer; 503 otherwise). `ruff` is a runtime dependency: the code generator formats output with it. On SIGTERM uvicorn lets open streams finish for up to 30 s.
- **Web image:** nginx (unprivileged, port 8080) serves the built app and proxies `/api/` to `$API_UPSTREAM` (default `http://api:8000`) without buffering, so Server-Sent Events stream in real time. Hashed assets are cached for a year; the app shell is always revalidated.
- Base images are pinned by digest in the Dockerfiles; Dependabot proposes new digests weekly. To build from a mirror, replace the exact reference with BuildKit: `docker build --build-context 'python:3.12-slim@sha256:<digest>=docker-image://mirror.gcr.io/library/python@sha256:<digest>' …` (same digest: the mirror serves the same image).

## 3. Single host: `compose.prod.yml` (small teams, staging)

```bash
cp infra/.env.prod.example infra/.env.prod      # fill in: database password, secret key, OIDC, provider keys
docker compose -f infra/compose.prod.yml --env-file infra/.env.prod up -d --wait
```

It runs Postgres (with pgvector), a one-shot `migrate` service (`alembic upgrade head`) that must succeed before any API replica starts, `API_REPLICAS` API containers (2 by default) sharing one workspaces volume, and the web container on `WEB_PORT` (8080). Put a TLS-terminating proxy (Caddy, Traefik or a cloud load balancer) in front of `web` and keep **response buffering off** for `/api/*`.

## 4. Kubernetes: the Helm chart

No image is published: build both (section 2), push them to your registry and name them in `image.*.repository` and
`image.*.tag` (the chart refuses to render without the repositories). The Secret needs `DATABASE_URL`,
`AGENTCANVAS_SECRET_KEY`, `LANGGRAPH_AES_KEY`, `AGENTCANVAS_SANDBOX_TOKEN` (with `sandbox.enabled`) and
`AGENTCANVAS_EGRESS_SECRET` (with `sandbox.egress.enabled`); `helm install` and `helm upgrade` fail when one is missing
(a `lookup` of the Secret, so `helm template` does not check it). The full list is in
[Deploy and operate](../../user-guide/deploy.md#option-2-kubernetes-with-helm).

```bash
REGISTRY=registry.example.com/acme TAG=$(git rev-parse --short HEAD)
for i in api web; do docker tag agentcanvas-$i:$TAG $REGISTRY/agentcanvas-$i:$TAG && docker push $REGISTRY/agentcanvas-$i:$TAG; done
kubectl create secret generic agentcanvas-secrets \
  --from-literal=DATABASE_URL=postgresql://… --from-literal=AGENTCANVAS_SECRET_KEY=$(openssl rand -hex 32) \
  --from-literal=LANGGRAPH_AES_KEY=$(openssl rand -hex 16) --from-literal=AGENTCANVAS_SANDBOX_TOKEN=$(openssl rand -hex 32) \
  --from-literal=AGENTCANVAS_EGRESS_SECRET=$(openssl rand -hex 32) --from-literal=ANTHROPIC_API_KEY=…
helm install agentcanvas infra/helm/agentcanvas \
  --set image.api.repository=$REGISTRY/agentcanvas-api --set image.api.tag=$TAG \
  --set image.web.repository=$REGISTRY/agentcanvas-web --set image.web.tag=$TAG \
  --set secrets.existingSecret=agentcanvas-secrets \
  --set config.oidc.issuer=https://login.example.com/realms/acme --set config.oidc.audience=agentcanvas-api \
  --set config.oidc.clientId=agentcanvas-web --set ingress.enabled=true --set ingress.host=agentcanvas.example.com
```

| Object | Notes |
|--------|-------|
| `Deployment …-api` | 2 replicas by default; startup and liveness probes on `/healthz`, readiness on `/readyz`; rolling updates never drop below the replica count; `terminationGracePeriodSeconds: 60` and a 5 s `preStop` so open streams can finish; non-root, all capabilities dropped |
| `Deployment …-web` | nginx; 2 replicas; proxies `/api/` to the API service |
| `Job …-migrate-<revision>` | `alembic upgrade head` as a `pre-install,pre-upgrade` hook, so new API pods never start against an old schema |
| `PersistentVolumeClaim …-workspaces` | project workspaces (git) shared by every API replica: needs a **ReadWriteMany** storage class (EFS, Filestore, Azure Files, NFS); kept on uninstall |
| `Ingress` | routes everything to the web service; annotations turn proxy buffering off and allow hour-long streams |
| `NetworkPolicy …-api` | only the web pods reach the API port |
| `PodDisruptionBudget …-api`, `-web`, `-sandbox`, `-egress` | `minAvailable: 1` for each component running 2+ replicas, so a node drain or cluster upgrade never takes all of them (`podDisruptionBudget.*`); every Deployment also spreads its pods across nodes (`topologySpread.*`, `ScheduleAnyway`) |
| Secrets | never in values: name an existing Secret (`secrets.existingSecret`), for example one synced by External Secrets |

**Several replicas are safe:** runs persist in Postgres, a reconnecting client reads state via `GET /v1/threads/{id}`, so sticky sessions are not required. Each cron minute starts one run however many replicas tick: a scheduler claims the minute by moving the trigger's `last_fired` forward in a conditional update, and only the replica whose update matched starts the run.

**Safe redeploy (FR-X-10).** A rolling update never loses a run. On SIGTERM an API pod leaves the Service (`preStop`), uvicorn gives open streams 30 s, then the pod *drains*: `/readyz` answers 503 `{"status":"draining"}`, new runs get 503 with `Retry-After: 5`, and running runs get `AGENTCANVAS_DRAIN_GRACE_S` (20 s) to finish. Runs still going are stopped at their last checkpoint: they end `interrupted` with error code `redeploy` and `run.handover` set. Every replica looks for handed-over runs at startup and every `AGENTCANVAS_HANDOVER_INTERVAL_S` (10 s), claims each with a conditional update (one replica wins), and continues the conversation from its checkpoint on the version it was running. Keep 5 + 30 + drain grace below `terminationGracePeriodSeconds` (60) and compose's `stop_grace_period` (60 s). A step that was cut off runs again on the new pod, so steps with side effects should be idempotent (the same rule as retries). A pod killed without draining (OOM, a lost node) cannot hand its runs over, so each replica refreshes the heartbeat of its running runs on every hand-over tick; a run still `running` without a heartbeat for `AGENTCANVAS_ORPHAN_AFTER_S` (60 s) is marked `interrupted` and handed over the same way.

**Publishing over running conversations.** A deployment's conversations continue on its newest version only when that version can resume them: every state field keeps its type and reducer and every pause point (approval, human input, tool approval) keeps its step name and kind. Otherwise a conversation finishes on the version it started on (`run.workflow_version_id` shows which), and new conversations get the new version.

**Client addresses.** The audit log records the caller's IP from `X-Forwarded-For` only when the direct peer is a trusted proxy: set `FORWARDED_ALLOW_IPS` (uvicorn) to the proxies' addresses. The chart and `compose.prod.yml` use `*` because only the web pods can reach the API: the chart adds a NetworkPolicy that admits only them (`networkPolicy.enabled`), and compose publishes no API port. If you turn the policy off or expose the API another way, list the proxies instead.

## 5. Configuration & secrets in production
- Required env: `AGENTCANVAS_ENV=prod`, `DATABASE_URL`, `AGENTCANVAS_SECRET_KEY` (64+ random chars), `LANGGRAPH_AES_KEY` (encrypts checkpoints; with it set, use `EncryptedSerializer.from_pycryptodome_aes()` as the checkpointer `serde`), `AGENTCANVAS_AUTH_MODE=oidc` with `AGENTCANVAS_OIDC_ISSUER`, `AGENTCANVAS_OIDC_AUDIENCE` and `AGENTCANVAS_OIDC_CLIENT_ID`, `AGENTCANVAS_CORS_ORIGINS`. The API refuses to start in staging, prod or the hosted edition with `AUTH_MODE=dev`.
- **Sign-in (M7-T1).** Register a public SPA client at your identity provider with redirect URI `https://<app>/auth/callback` and post-logout URI `https://<app>/`; the web app uses authorization code + PKCE. The API checks the access token's signature (JWKS from the issuer's discovery document, or `AGENTCANVAS_OIDC_JWKS_URL`), `iss`, `aud` and `exp`. The default scopes (`AGENTCANVAS_OIDC_SCOPES`) include `offline_access`, so the client gets a refresh token and renews the access token silently; allow refresh tokens for the client at the provider (or drop the scope to fall back to a hidden-iframe renewal, which browsers that block third-party cookies break). Optional: `AGENTCANVAS_OIDC_EMAIL_CLAIM`, `AGENTCANVAS_OIDC_TRUST_EMAIL=true` for providers that send no `email_verified` but own the email domain.
- **Database connections per API process:** the control database pool keeps `AGENTCANVAS_DB_POOL_SIZE` (10) open and opens up to `AGENTCANVAS_DB_MAX_OVERFLOW` (20) more; checkpoints and memory use their own pool of `AGENTCANVAS_PERSISTENCE_POOL_SIZE` (20). Size Postgres `max_connections` (or PgBouncer) for replicas × (pool + overflow + persistence pool) plus about 10 for migrations, `psql` and backups: 2 × 50 + 10 = 110 by default, above Postgres's own default of 100, so `compose.prod.yml` starts Postgres with `max_connections=200` (`POSTGRES_MAX_CONNECTIONS`, three replicas). Requests give their connection back when the response starts, so open streams hold none (M8-T5: 200 concurrent streams on two replicas, p95 time to first event 89 ms).
- **First admin.** List one or more emails in `AGENTCANVAS_BOOTSTRAP_ADMIN_EMAILS` (JSON list); on their first verified sign-in they become admins of the default organisation (once: an admin removed later is not re-added). With sign-in on, the seeded development user loses its memberships at startup. Admins then invite everyone else from the Members page (`PUT /v1/org/members`).
- **Roles (M7-T3):** `viewer` reads; `reviewer` also answers the Inbox; `builder` also builds, runs and publishes; `admin` also manages members and reads the audit log (`GET /v1/org/audit`, M7-T4).
- **Policies (FR-X-19).** Admins add organisation rules on the Policies page (`/v1/org/policies`). A rule picks steps, resources or the whole workflow (`scope`, narrowed by a CEL `match`) and states what must hold (`require`, CEL). CEL sees `node` (`type`, `config`, `effects` with `effect`/`gated`, `ungated`, `models`, `tools`), `resource` (`type`, `config`, `used_by`), `workflow` (`nodes`, `resources`, `node_types`, `models`) and `env`. Examples: `node.effects.all(e, e.effect != "external_write" || e.gated)`; for `scope=resource`, `match: resource.type == "model.chat"`, `require: resource.config.model in ["anthropic:claude-sonnet-5-5"]` with `environments: ["prod"]`. Rules without environments apply in the editor and at every publish; rules with environments block publishing there and show as warnings in the editor. A rule that cannot be evaluated counts as broken. Steps and resources inside subgraphs are checked as well.
- **Long-term memory per organisation.** Runs keep memories under the workflow's organisation (namespaces start with `org:<id>`), and the Memory inspector works inside the caller's organisation. On upgrade from v0.1.0, existing memories move to the default organisation once, at the first start; the API records that and never moves memories again. Deployment API end users are kept as `api:<deployment>:<user_id>`, apart from organisation members.
- Model provider keys are **per project secrets** (M5-T6), not process env vars.
- **Secrets at rest (envelope encryption).** Project secrets and webhook signing secrets are encrypted with a random data key per organisation; the data key is stored wrapped by the master key in `AGENTCANVAS_KMS_KEY`: `file:/run/secrets/master.key` (32+ random characters), `aws:<key ARN>` (install `boto3`), `gcp:<key resource name>` (install `google-cloud-kms`), or empty to derive it from `AGENTCANVAS_SECRET_KEY` (development). **Rotate** the master key without downtime: deploy with the new key in `AGENTCANVAS_KMS_KEY` and the old one in `AGENTCANVAS_KMS_PREVIOUS_KEYS` (JSON list), run `python -m agentcanvas.security.kms rewrap` once (it also moves secrets written before v0.3.0 onto the data keys), then remove the old key.
- **Checkpoints at rest.** Set `LANGGRAPH_AES_KEY` (exactly 32 characters) to encrypt conversation checkpoints with AES; checkpoints written before stay readable. The API logs `checkpoints.unencrypted` at startup in staging and prod without it.
- **Browser policy.** The web image sends a Content-Security-Policy; sign-in calls the identity provider, so its origin (taken from `OIDC_ISSUER`, which compose and the chart pass from the API settings) is allowed in `connect-src`. If the token or keys endpoints live on another host, list those origins in `CSP_CONNECT_SRC` (chart: `web.cspConnectSrc`).
- **Rate limits.** nginx limits the public deployment and webhook endpoints to 20 requests/s per client address (bursts of 40, then 429). The API also limits each caller per minute, counted in the database so the limits hold across replicas: `AGENTCANVAS_RATE_LIMIT_PER_DEPLOYMENT` (120, per API key), `AGENTCANVAS_RATE_LIMIT_PER_WEBHOOK` (120, per trigger) and `AGENTCANVAS_RATE_LIMIT_PER_PROJECT` (600, all of a project's deployments and webhooks together); `AGENTCANVAS_RATE_LIMIT_BY_ENVIRONMENT` overrides the per-key limit for a deployment environment (JSON, e.g. `{"dev": 30}`); 0 turns a limit off. Over a limit the API answers 429 with `Retry-After`.

## 6. Observability
- **Logs:** JSON (structlog) to stdout in the images; ship with your platform's collector. Fields: `request_id`, `workflow_id`, `thread_id`, `run_id`, `level`, `event`, and `trace_id` / `span_id` inside a traced request.
- **Metrics:** Prometheus at `GET /metrics` on each API pod (`AGENTCANVAS_METRICS_ENABLED`, on by default). The web image's nginx answers 404 for `/api/metrics`, so scrape the pods directly; the Helm chart adds `prometheus.io/*` pod annotations and lets the `monitoring` namespace through its NetworkPolicy (`observability.metrics.*`).

  | Metric | Labels | Meaning |
  |---|---|---|
  | `agentcanvas_runs_started_total` | `origin` (user, trigger, event, test, api…) | runs started |
  | `agentcanvas_runs_finished_total` | `origin`, `status` (success, interrupted, error, cancelled) | runs finished or paused |
  | `agentcanvas_run_duration_seconds` | `status` | histogram of run duration |
  | `agentcanvas_runs_active` | | runs in progress in this pod |
  | `agentcanvas_tokens_total` / `agentcanvas_cost_usd_total` | `direction` | model usage and cost |
  | `agentcanvas_sse_clients` | | open run event streams |
  | `agentcanvas_http_requests_total` / `agentcanvas_http_request_duration_seconds` | `method`, `route` (template, never ids), `status` | HTTP traffic |
  | `agentcanvas_online_score_mean` | `deployment`, `evaluator` | mean online score over the evaluator's alert window (S13) |
  | `agentcanvas_eval_alert_open` | `deployment`, `evaluator`, `kind` (below, drop) | 1 while a drift alert is open; every pod refreshes it from the database once a minute |
  | `agentcanvas_eval_alerts_total` | `kind` | drift alerts opened |

- **Traces:** OpenTelemetry spans for FastAPI, the control database (SQLAlchemy) and outgoing httpx calls when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (OTLP/HTTP; the standard `OTEL_*` variables configure it; Helm: `observability.otlpEndpoint`). Request spans carry `request.id`. LLM traces go to LangSmith when `LANGSMITH_TRACING=true` (runs tagged with `workflow_id`).
- **Dashboard and alerts:** `infra/observability/grafana-dashboard.json` (import it and pick the Prometheus data source) and `infra/observability/alerts.yaml`: server errors above 5 % for 10 min, more than 20 % of runs failing, API p95 above 2 s (streams excluded), no API pod scraped, and an online evaluator in drift for 5 min (`AgentCanvasEvaluationDrift`, severity ticket).
- **Online evaluation (S13).** Scoring of sampled deployment runs happens in each API pod after the response is sent (at 30 requests/s with every run scored, p95 rose from 42 to 52 ms on compose prod; lower the sampling rate for heavy traffic or LLM judges); an LLM judge's calls use the project's model keys and their cost is reported apart from run cost (the *Production* tab). LangSmith dataset sync uses the project secret `LANGSMITH_API_KEY`, else the server's environment variable of that name, and `LANGSMITH_ENDPOINT` for a self-hosted LangSmith.

## 7. Backups, retention, disaster recovery
- **Retention (M8-T4).** A job in every API pod deletes conversations (threads with their checkpoints, runs and decisions) that have been inactive longer than the organisation keeps them: `PUT /v1/org/settings {"retention_days": 30}` (admin; `null` = the server default `AGENTCANVAS_RETENTION_DAYS`, 90; `0` = keep forever). Conversations that are running or waiting for a person are never deleted. It checks every `AGENTCANVAS_RETENTION_INTERVAL_S` (1 h) and counts deletions in `agentcanvas_threads_expired_total`. The same job deletes audit-log rows older than `AGENTCANVAS_AUDIT_RETENTION_DAYS` (400; `0` keeps them forever) and, only when `AGENTCANVAS_MEMORY_RETENTION_DAYS` is set (off by default), long-term memories (and Deep Agent store files) not updated for that many days, in every organisation. Otherwise erase memories from the Memory inspector.
- **Managed Postgres:** turn on point-in-time recovery (7 days or more). Restoring to a point in time is the provider's operation; then point `DATABASE_URL` at the restored instance and roll the API pods (the migration hook runs first).
- **Portable copy:** `infra/scripts/backup.sh` writes a `pg_dump` (custom format) of the whole database: control tables, checkpoints and long-term memory. Run it nightly and keep the files off the database host. Workspaces live on their own volume (`/data/workspaces`, git repositories): back that volume up with your platform's snapshots.
- **Restore runbook** (rehearsed on compose prod, 2026-10-05: a conversation deleted after the backup came back with its state; about 11 s for a small database):
  1. `ENV_FILE=infra/.env.prod infra/scripts/restore.sh <file.dump>`. This stops `web` and `api`, recreates the database from the dump, runs migrations (a backup from an older release is brought up to date), and starts the API and web again.
  2. Check `GET /readyz`, sign in, and open a recent conversation.
  3. Runs that were in progress when the backup was taken show as running; cancel or resume them from the Run panel.
- **Drill** quarterly: restore into a scratch environment, run the smoke checks above and record how long it took.

## 8. Release process
1. Merge to `main` → CI builds images tagged with the commit SHA.
2. Deploy to staging automatically; run Playwright smoke tests against staging.
3. Tag `vX.Y.Z` → promote the same images to prod (no rebuild); migrations run first.
4. Rollback = redeploy the previous image tag; migrations must be backward compatible for one release (expand → migrate → contract).

## 9. Runbooks (write these in M8)
| Situation | First steps |
|-----------|-------------|
| Runs stuck "running" | graceful restarts hand runs over (FR-X-10, §4); runs of a pod that was killed (OOM, node loss) are taken over by another pod after `AGENTCANVAS_ORPHAN_AFTER_S` (60 s) without a heartbeat (S12). If they stay `running`, check that at least one API pod is up, then cancel them from the Run panel |
| SSE shows nothing until the end | a proxy is buffering: check ingress/nginx `proxy_buffering off`, header `X-Accel-Buffering: no` |
| DB connection exhaustion | lower pool size per replica; check long transactions; add PgBouncer. LangGraph's `AsyncPostgresSaver.from_conn_string` already connects with `autocommit=True, prepare_threshold=0` (no prepared statements, verified in source), so **transaction pooling works for checkpoints**; if you build your own psycopg pool for the saver, use the same three settings (`autocommit=True, prepare_threshold=0, row_factory=dict_row`). For SQLAlchemy + asyncpg behind transaction pooling set `connect_args={"statement_cache_size": 0}`. |
| Model provider outage | enable fallback models (ModelFallbackMiddleware) in affected workflows; show provider status banner |
| Evaluation drift alert | open the workflow's Evaluation page → *Production*: compare the hourly mean with the 7-day trend, open the lowest-scoring runs (their timelines are one click away), and send them to an annotation queue; a recent publish or a model provider change is the usual cause. The alert resolves by itself once the window's mean recovers |
| Leaked secret | revoke at provider, rotate secret in project, audit `audit_log` for `secret.read` events |
