# Deploy and operate

This page is for whoever runs AgentCanvas for a team. To try it on one computer, see
[Run AgentCanvas on your computer](run-locally.md). The developer-level runbook is the
[deployment & operations guide](../agent-canvas-spec/build-guide/09-deployment-and-operations.md).

## What runs

| Component | Notes |
|---|---|
| **web** | nginx serving the web app and forwarding `/api/` to the API. Sends a strict Content-Security-Policy and rate-limits public calls. |
| **api** | The FastAPI control plane and run engine. Stateless: run two or more replicas. Runs survive restarts and are handed to another replica on shutdown. |
| **Postgres 16 + pgvector** | Everything: workflows, runs, checkpoints, memory, audit log. |
| **workspaces volume** | One git repository per project, shared by the API replicas. |
| Identity provider | Any OpenID Connect provider, for sign-in. |

## Option 1: Docker Compose on one host

```bash
docker build -f infra/Dockerfile.api -t agentcanvas-api apps/api
docker build -f infra/Dockerfile.web -t agentcanvas-web .
cp infra/.env.prod.example infra/.env.prod      # fill it in (never commit it)
docker compose -f infra/compose.prod.yml --env-file infra/.env.prod up -d --wait
```

This starts Postgres, a one-shot migration, two API replicas and the web server on port 8080 (`WEB_PORT`). Put a
TLS-terminating proxy in front and keep response buffering **off** for `/api/` (runs stream Server-Sent Events).

## Option 2: Kubernetes with Helm

The chart is in `infra/helm/agentcanvas`. Create a Kubernetes Secret with `DATABASE_URL`,
`AGENTCANVAS_SECRET_KEY` and provider keys, name it in `secrets.existingSecret`, set the `config.oidc.*` values,
and install:

```bash
helm install agentcanvas infra/helm/agentcanvas -f my-values.yaml
```

Migrations run as a Helm hook Job before the API starts.

## Essential settings

| Setting | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql://user:pass@host:5432/agentcanvas` |
| `AGENTCANVAS_SECRET_KEY` | 64+ random characters. Production refuses the development default. |
| `AGENTCANVAS_ENV` | `prod` (default in compose prod and Helm), `staging` or `dev` |
| `LANGGRAPH_AES_KEY` | 32 characters: encrypts conversation checkpoints at rest |
| `AGENTCANVAS_KMS_KEY` | Master key for project secrets: `file:<path>`, `aws:<key ARN>` or `gcp:<key name>` |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, … | Model provider keys for the whole server (projects can also use their own secrets) |
| `AGENTCANVAS_SCRIPT_EXECUTION` | Where workspace code runs: `subprocess` (default in production), `docker`, `langsmith`, `inprocess` |
| `AGENTCANVAS_CORS_ORIGINS` | The web app's origin, as a JSON list |
| `AGENTCANVAS_RETENTION_DAYS` | Delete conversations idle this long (default 90; 0 keeps them) |
| `AGENTCANVAS_RATE_LIMIT_PER_DEPLOYMENT` / `_PER_WEBHOOK` / `_PER_PROJECT` | Public API requests per minute (120 / 120 / 600) |

## Sign-in

1. In your identity provider, create a **public** client (authorization code + PKCE) with redirect URI
   `https://<your app>/auth/callback`, and enable refresh tokens (`offline_access`).
2. Make the access token's audience the API's (e.g. `agentcanvas-api`).
3. Set `AGENTCANVAS_AUTH_MODE=oidc`, `AGENTCANVAS_OIDC_ISSUER`, `AGENTCANVAS_OIDC_AUDIENCE`,
   `AGENTCANVAS_OIDC_CLIENT_ID`, and `AGENTCANVAS_BOOTSTRAP_ADMIN_EMAILS` for the first admins.

The API checks every token against the provider's keys. `AGENTCANVAS_AUTH_MODE=dev` (no sign-in) is refused in
`staging` and `prod`.

## Observability

- **Health:** `/healthz` (process up) and `/readyz` (database and run store reachable).
- **Metrics:** Prometheus on `/metrics`. A Grafana dashboard and alert rules (including evaluation drift) are in
  `infra/observability/`.
- **Traces:** set `OTEL_EXPORTER_OTLP_ENDPOINT` for OpenTelemetry; `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` for
  LangSmith.

## Backups

`infra/scripts/backup.sh` writes a `pg_dump` of the compose prod database; `infra/scripts/restore.sh <file>`
restores one (and migrates it to the running version). With managed Postgres, rely on its point-in-time recovery
and run the backup nightly as a portable second copy.

## Upgrades

Pull the new images and roll the API. On shutdown each replica stops taking runs, lets running ones reach a step
boundary and hands them to another replica, which resumes them. Conversations move to a newly published workflow
version only when it is resume-compatible.

## Rotating the secrets master key

Set the new key in `AGENTCANVAS_KMS_KEY`, keep the old one in `AGENTCANVAS_KMS_PREVIOUS_KEYS`, then run
`python -m agentcanvas.security.kms rewrap` in an API container.
