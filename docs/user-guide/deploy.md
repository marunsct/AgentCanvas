# Deploy and operate

This page is for whoever runs AgentCanvas for a team. To try it on one computer, use the one-command Docker setup
in [Run AgentCanvas on your computer](run-locally.md#run-everything-in-docker). The developer-level runbook is the
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
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `MISTRAL_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `XAI_API_KEY`, `AZURE_OPENAI_API_KEY` | Model provider keys for the whole server; a project secret of the same name takes precedence ([Models and API keys](models.md)) |
| `AGENTCANVAS_LOCAL_MODELS_HOST` | Host that local model servers (Ollama, LM Studio, vLLM, llama.cpp) are reached on when their address isn't set, e.g. `host.docker.internal` or a GPU machine's name |
| `AGENTCANVAS_SCRIPT_EXECUTION` | Where workspace code runs: `subprocess` (default in production), `docker`, `langsmith`, `inprocess` |
| `AGENTCANVAS_CORS_ORIGINS` | The web app's origin, as a JSON list |
| `AGENTCANVAS_RETENTION_DAYS` | Delete conversations idle this long (default 90; 0 keeps them) |
| `AGENTCANVAS_RATE_LIMIT_PER_DEPLOYMENT` / `_PER_WEBHOOK` / `_PER_PROJECT` / `_PER_SHARE_CLIENT` | Public API requests per minute (120 / 120 / 600 / 30 per share-link visitor address) |
| `TRUSTED_PROXIES` (web) | The address range of a load balancer or ingress in front of the web container, e.g. `10.0.0.0/8`. Only a client address it forwards is believed, for rate limits and the audit log. Default `127.0.0.1/32` (nothing in front). In Helm: `web.trustedProxies`. |

## Sign-in

1. In your identity provider, create a **public** client (authorization code + PKCE) with redirect URI
   `https://<your app>/auth/callback`, and enable refresh tokens (`offline_access`).
2. Make the access token's audience the API's (e.g. `agentcanvas-api`).
3. Set `AGENTCANVAS_AUTH_MODE=oidc`, `AGENTCANVAS_OIDC_ISSUER`, `AGENTCANVAS_OIDC_AUDIENCE`,
   `AGENTCANVAS_OIDC_CLIENT_ID`, and `AGENTCANVAS_BOOTSTRAP_ADMIN_EMAILS` for the first admins.

The API checks every token against the provider's keys. `AGENTCANVAS_AUTH_MODE=dev` (no sign-in) is refused in
`staging` and `prod`.

### Sign-in through Matura

[Matura](https://github.com/marunsct/Platform) manages sign-in, organisations, members and roles for several apps.
With it, an organisation in AgentCanvas is a Matura tenant, and people's roles come from the roles Matura assigns them.

1. Register AgentCanvas in Matura with [`matura/manifest.json`](https://github.com/marunsct/AgentCanvas/blob/main/matura/manifest.json)
   (its four permissions and starter roles `viewer`, `reviewer`, `builder`, `admin`; tenant owners and admins get
   `admin`). Matura creates the public client `agentcanvas`; add `https://<your app>/*` to its redirect URIs.
2. In Matura, create an app secret and a webhook to `https://<your app>/api/v1/matura/events`.
3. Set:

| Setting | Value |
|---|---|
| `AGENTCANVAS_AUTH_MODE` | `matura` |
| `AGENTCANVAS_OIDC_ISSUER` | Matura's realm, e.g. `https://login.example.com/realms/matura` |
| `AGENTCANVAS_OIDC_AUDIENCE`, `AGENTCANVAS_OIDC_CLIENT_ID` | `agentcanvas` |
| `AGENTCANVAS_MATURA_URL`, `AGENTCANVAS_MATURA_CLIENT_SECRET` | Matura's API and the app secret: the change feed is read at start-up and every hour (`AGENTCANVAS_MATURA_SYNC_INTERVAL_S`) |
| `AGENTCANVAS_MATURA_WEBHOOK_SECRET` | the webhook's signing secret |
| `AGENTCANVAS_MATURA_PORTAL_URL` | where admins manage members and roles; AgentCanvas links to it |

What changes for people:

- Each sign-in is for one organisation. People in several switch with the organisation picker on the home page,
  which signs in again for the one they pick.
- What someone may do follows the strongest permission their Matura roles give: `canvas.read` (viewer),
  `inbox.review` (reviewer), `workflow.build` (builder), `org.admin` (admin). Someone whose roles give none of these
  cannot open AgentCanvas in that organisation.
- **Members** shows who has access, read-only, with a link to the portal. Inviting, changing roles and removing
  people happen in Matura; the API answers 409 to the old member and invitation endpoints.
- Changes made in Matura reach AgentCanvas by webhook within seconds; a person's next token carries them anyway.

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
