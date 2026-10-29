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
| `AGENTCANVAS_MODEL_PRICES` | Prices for models whose list price the catalog does not carry (it prices the Claude and OpenAI models), so costs, cost budgets and alerts count them: JSON, USD per million tokens, e.g. `{"openai:gpt-5.5": [5, 30]}`, or `[in, out, cache read, cache write]` to price prompt caching too (without them, cached input is charged as ordinary input). A model not in the catalog or a malformed value stops the server from starting |
| `AGENTCANVAS_SCRIPT_EXECUTION` | Where workspace code runs: `remote` (default in the production compose file and Helm chart: the sandbox service, below), `subprocess` (limits only, not isolation), `docker`, `langsmith`, `inprocess`. A staging or production server refuses `inprocess` and `subprocess`, which run the code as the API's own user |
| `AGENTCANVAS_ALLOW_UNISOLATED_SCRIPTS` | `true` lets a staging or production server run workspace code in `inprocess` or `subprocess` mode anyway, when only people you trust edit workspaces |
| `AGENTCANVAS_SANDBOX_URL`, `AGENTCANVAS_SANDBOX_TOKEN` | With `remote`: where the sandbox service is, and the secret (32+ random characters) the API presents to it. The compose file and chart set the URL; you provide the token |
| `SANDBOX_REPLICAS` (compose), `sandbox.replicas` (Helm) | How many sandbox containers run (4 by default). Each runs one call at a time and, after it, stops anything the code left running and removes what it wrote (`AGENTCANVAS_SANDBOX_SWEEP=1`), so code of different projects never shares a container at the same moment; more replicas, more calls at once |
| `AGENTCANVAS_EGRESS_PROXY`, `AGENTCANVAS_EGRESS_SECRET` | The egress proxy the sandbox reaches the hosts of a project's `sandbox.json` through (`egress:3128` in the compose files, the `-egress` Service in the chart), and the secret (32+ random characters) it checks each call's list with. You provide the secret; with `docker` and no proxy set, the API starts an `agentcanvas-egress` container itself |
| `AGENTCANVAS_CORS_ORIGINS` | The web app's origin, as a JSON list |
| `AGENTCANVAS_RETENTION_DAYS` | Delete conversations idle this long (default 90; 0 keeps them). Files people attached to runs go when no conversation has used them for as long |
| `AGENTCANVAS_MAX_UPLOAD_MB`, `AGENTCANVAS_MAX_PROJECT_FILES_MB` | The largest file a person may attach to a run (default 10, at most 20: the web server accepts 20 MB bodies) and the most attached files a project keeps (default 1024; 0 = no limit) |
| `AGENTCANVAS_RATE_LIMIT_PER_DEPLOYMENT` / `_PER_WEBHOOK` / `_PER_PROJECT` / `_PER_SHARE_CLIENT` | Public API requests per minute (120 / 120 / 600 / 30 per share-link visitor address). An organisation's admin may set lower ones for the organisation ([Settings](settings.md#what-an-organisation-may-override)) |
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
| `AGENTCANVAS_MATURA_PUBLIC_URL` | where people reach Matura (its `MATURA_PUBLIC_URL`); AgentCanvas links to its customer portal at `<this>/portal/t/<tenant id>/` (`members` below it) |
| `AGENTCANVAS_MATURA_PORTAL_URL` | optional: the portal's address when it is not `<public URL>/portal/` |
| `AGENTCANVAS_MATURA_AUDIT_URL` | optional, for later: where security-relevant audit entries are sent (see below) |

What changes for people:

- Each sign-in is for one organisation. People in several switch with the organisation picker on the home page,
  which signs in again for the one they pick.
- What someone may do follows the strongest permission their Matura roles give: `canvas.read` (viewer),
  `inbox.review` (reviewer), `workflow.build` (builder), `org.admin` (admin). Someone whose roles give none of these
  cannot open AgentCanvas in that organisation.
- **Members** shows who has access, read-only, with a link to the portal. Inviting, changing roles and removing
  people happen in Matura; the API answers 409 to the old member and invitation endpoints.
- Changes made in Matura reach AgentCanvas by webhook within seconds; a person's next token carries them anyway.
- **Workspaces.** Matura's workspaces group projects: the home page has a workspace picker when the organisation has
  any, and opens a project of the workspace picked (making one, for builders, when it has none). The API lists them
  at `GET /v1/workspaces`, filters projects with `GET /v1/projects?workspace_id=…`, takes `workspace_id` when a
  project is created and moves a project with `PUT /v1/projects/{id}/workspace`. Workspaces only group; who may do
  what is still decided for the whole organisation.
- **Members** and **Policies** link to the organisation's page in Matura's portal, where its admins manage members,
  roles, single sign-on and security settings.

#### Database isolation

On Postgres, every table that holds an organisation's data has a row-level security policy (forced, so it also
applies to the tables' owner). In Matura mode each signed-in request tells the database which organisation it acts
for (`matura.tenant_id`, per transaction), so a query that forgot to filter by organisation still returns nothing
of another. Background work (runs handed over, schedules, retention, the public run API by deployment key) connects
in all-tenants mode, as do the `dev` and `oidc` modes, which therefore work as before. A connection that sets
neither sees no rows.

- Connect the API as an ordinary role: Postgres superusers and roles with `BYPASSRLS` skip the policies, and the
  official Postgres image makes its `POSTGRES_USER` a superuser. In Matura mode the API refuses to start in staging
  and prod on such a role (and warns elsewhere). `infra/compose.prod.yml` runs migrations as the owner and then
  `python -m agentcanvas.db.app_role`, which creates `agentcanvas_app` (no superuser, no `BYPASSRLS`, password
  `POSTGRES_APP_PASSWORD`); the API connects as that role. The Helm migrate Job does the same when the secret has
  `MIGRATE_DATABASE_URL` (the owner), `APP_DB_USER` and `APP_DB_PASSWORD`. Elsewhere, run migrations as the owner
  (`make migrate` with the owner's `DATABASE_URL`) and set `AGENTCANVAS_AUTO_MIGRATE=false` on the API.
- The API sets `agentcanvas.all_tenants=on` as a connection startup option. Behind PgBouncer in transaction pooling
  mode, startup options are not passed on: use session pooling, or add the setting to the database role
  (`ALTER ROLE <api role> SET agentcanvas.all_tenants = 'on'`).
- SQLite has no row-level security; there the API's own organisation filters are the only isolation.
- AgentCanvas supports Matura's **shared** data mode only (one database for every organisation), as its manifest
  declares. A token whose placement mode (`pmode`) is `schema` or `database` is refused with 503.
- LangGraph's own tables (checkpoints, long-term memory) are not covered by the policies; their rows are reached only
  through the conversations and namespaces of the organisation's own rows.

#### Audit forwarding

AgentCanvas keeps every change in its own audit log (**Settings › Audit log**). Security-relevant ones (publishing and
unpublishing, deployment keys, secrets, approvals, members, organisation settings and policies, release gates, trace
export, project workspaces) also go to an audit sink. Matura has no audit ingest API yet, so by default the sink does
nothing. With `AGENTCANVAS_MATURA_AUDIT_URL` set, each entry is POSTed there as a CloudEvent
(`agentcanvas.audit.<action>`) signed like Matura's webhooks (`Matura-Signature`, with the webhook secret), once and
best effort.

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
