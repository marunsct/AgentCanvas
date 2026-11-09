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

This starts Postgres, a one-shot migration, two API replicas and the web server on port 8080 (`WEB_PORT`) of
`127.0.0.1`: it speaks plain HTTP, so put a TLS-terminating proxy in front on the same host (or set `WEB_BIND=0.0.0.0`
when the proxy runs elsewhere and the port is firewalled) and keep response buffering **off** for `/api/` (runs stream
Server-Sent Events). The API containers run with a read-only root (writable: the workspaces volume, `/tmp` and the
app user's home, both in memory), no capabilities and a 2 GB memory limit (`API_MEM_LIMIT`); `API_READ_ONLY=false`
turns the read-only root off should a framework need to write elsewhere.

**Database connections.** Each API replica opens up to `AGENTCANVAS_DB_POOL_SIZE` + `AGENTCANVAS_DB_MAX_OVERFLOW` +
`AGENTCANVAS_PERSISTENCE_POOL_SIZE` connections (10 + 20 + 20 = 50 by default). Postgres's `max_connections` must be
at least `API_REPLICAS × 50 + 10` (the 10 for migrations, `psql` and backups). The compose file starts Postgres with
`max_connections=200` (`POSTGRES_MAX_CONNECTIONS`), enough for three replicas; for more, raise it or lower the pools
in `infra/.env.prod`. A managed Postgres or PgBouncer needs the same sum.

**Which file is read.** The API containers (and the migration) read every setting in `infra/.env.prod`, so any
setting on this page (retention, model prices, provider keys, `AGENTCANVAS_PUBLIC_URL`, `OTEL_*`, `LANGSMITH_*`,
`AGENTCANVAS_MATURA_*`...) goes in that file; the compose file's own values win where both set one. The name is
fixed: `--env-file` only fills in the compose file's `${...}` values, so a file of another name is not read by the
API. The sandbox and egress containers do not read it (they get only their own token or secret, and the egress proxy
`AGENTCANVAS_EGRESS_ALLOW_PRIVATE`).

## Option 2: Kubernetes with Helm

The chart is in `infra/helm/agentcanvas`. No AgentCanvas image is published: build both images and push them to a
registry your cluster can pull from.

```bash
REGISTRY=registry.example.com/acme   # yours
TAG=$(git rev-parse --short HEAD)
docker build -f infra/Dockerfile.api -t $REGISTRY/agentcanvas-api:$TAG apps/api
docker build -f infra/Dockerfile.web -t $REGISTRY/agentcanvas-web:$TAG .
docker push $REGISTRY/agentcanvas-api:$TAG && docker push $REGISTRY/agentcanvas-web:$TAG
```

Create the Secret the API reads its keys from. Every key becomes an environment variable of the API and the
migration Job; the sandbox and egress pods get only their own key.

| Key | Required | Value |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql://<role>:<password>@<host>:5432/agentcanvas` |
| `AGENTCANVAS_SECRET_KEY` | yes | 64+ random characters (`openssl rand -hex 32`) |
| `LANGGRAPH_AES_KEY` | yes | 16, 24 or 32 characters: encrypts conversation checkpoints (`openssl rand -hex 16`) |
| `AGENTCANVAS_SANDBOX_TOKEN` | with `sandbox.enabled` (default) | 32+ random characters |
| `AGENTCANVAS_EGRESS_SECRET` | with `sandbox.egress.enabled` (default) | 32+ random characters |
| `MIGRATE_DATABASE_URL`, `APP_DB_USER`, `APP_DB_PASSWORD` | for Matura's row-level security | the owner's URL for migrations, and the ordinary role the migration Job creates for the API |
| `AGENTCANVAS_KMS_KEY`, provider keys (`ANTHROPIC_API_KEY`...), `LANGSMITH_API_KEY`, `AGENTCANVAS_MATURA_CLIENT_SECRET`, `AGENTCANVAS_MATURA_WEBHOOK_SECRET` | no | see the settings below |

```bash
kubectl create secret generic agentcanvas-secrets \
  --from-literal=DATABASE_URL=postgresql://… \
  --from-literal=AGENTCANVAS_SECRET_KEY=$(openssl rand -hex 32) \
  --from-literal=LANGGRAPH_AES_KEY=$(openssl rand -hex 16) \
  --from-literal=AGENTCANVAS_SANDBOX_TOKEN=$(openssl rand -hex 32) \
  --from-literal=AGENTCANVAS_EGRESS_SECRET=$(openssl rand -hex 32)
helm install agentcanvas infra/helm/agentcanvas -f my-values.yaml \
  --set image.api.repository=$REGISTRY/agentcanvas-api --set image.api.tag=$TAG \
  --set image.web.repository=$REGISTRY/agentcanvas-web --set image.web.tag=$TAG
```

`my-values.yaml` names the Secret (`secrets.existingSecret`) and sets `config.oidc.*`; `values.yaml` in the chart
lists everything else. The chart refuses to render without the image repositories, the Secret's name or the OIDC
issuer and audience, and `helm install` / `helm upgrade` fail when the Secret lacks a required key. Migrations run as
a Helm hook Job before the API starts.

## Essential settings

Every setting, with its default, is listed in [Server settings](server-settings.md); these are the ones to decide first.

| Setting | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql://user:pass@host:5432/agentcanvas` |
| `AGENTCANVAS_SECRET_KEY` | 64+ random characters. Production refuses the development default. |
| `AGENTCANVAS_ENV` | `prod` (default in compose prod and Helm), `staging` or `dev` |
| `LANGGRAPH_AES_KEY` | 16, 24 or 32 characters (32 for AES-256: `openssl rand -hex 16`): encrypts conversation checkpoints at rest |
| `AGENTCANVAS_KMS_KEY` | Master key for project secrets: `file:<path>`, `aws:<key ARN>` or `gcp:<key name>` |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `MISTRAL_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `XAI_API_KEY`, `AZURE_OPENAI_API_KEY` | Model provider keys for the whole server; a project secret of the same name takes precedence ([Models and API keys](models.md)) |
| `AGENTCANVAS_LOCAL_MODELS_HOST` | Host that local model servers (Ollama, LM Studio, vLLM, llama.cpp) are reached on when their address isn't set, e.g. `host.docker.internal` or a GPU machine's name |
| `AGENTCANVAS_MODEL_PRICES` | Prices for models whose list price the catalog does not carry (it prices the Claude and OpenAI models), so costs, cost budgets and alerts count them: JSON, USD per million tokens, e.g. `{"openai:gpt-5.5": [5, 30]}`, or `[in, out, cache read, cache write]` to price prompt caching too (without them, cached input is charged as ordinary input). A model not in the catalog or a malformed value stops the server from starting |
| `AGENTCANVAS_SCRIPT_EXECUTION` | Where workspace code runs: `remote` (default in the production compose file and Helm chart: the sandbox service, below), `subprocess` (limits only, not isolation), `docker`, `langsmith`, `inprocess`. A staging or production server refuses `inprocess` and `subprocess`, which run the code as the API's own user |
| `AGENTCANVAS_ALLOW_UNISOLATED_SCRIPTS` | `true` lets a staging or production server run workspace code in `inprocess` or `subprocess` mode anyway, when only people you trust edit workspaces |
| `AGENTCANVAS_SANDBOX_URL`, `AGENTCANVAS_SANDBOX_TOKEN` | With `remote`: where the sandbox service is, and the secret (32+ random characters) the API presents to it. The compose file and chart set the URL; you provide the token |
| `SANDBOX_REPLICAS` (compose), `sandbox.replicas` (Helm) | How many sandbox containers run (4 by default). Each runs one call at a time and, after it, stops anything the code left running and removes what it wrote (`AGENTCANVAS_SANDBOX_SWEEP=1`), so code of different projects never shares a container at the same moment; more replicas, more calls at once |
| `AGENTCANVAS_EGRESS_PROXY`, `AGENTCANVAS_EGRESS_SECRET` | The egress proxy the sandbox reaches the hosts of a project's `sandbox.json` through (`egress:3128` in the compose files, the `-egress` Service in the chart), and the secret (32+ random characters) it checks each call's list with. You provide the secret; with `docker` and no proxy set, the API starts an `agentcanvas-egress` container itself. `AGENTCANVAS_EGRESS_PROXY_RESOLVE=true` (set by the chart) hands the sandbox the proxy's address instead of its name, so the sandbox pods need no DNS |
| `AGENTCANVAS_CORS_ORIGINS` | The web app's origin, as a JSON list |
| `AGENTCANVAS_PUBLIC_URL` | Where people reach the web app, e.g. `https://canvas.example.com`. Signing in to app connectors ([App connectors](agents-and-connectors.md#sign-in-per-person)) returns the browser to `<this>/oauth/callback`, and servers that support Client ID Metadata Documents fetch ours from `<this>/api/v1/oauth/client-metadata.json` (it must be https and public for that). Unset: the first CORS origin, which is right for the development server only |
| `AGENTCANVAS_RETENTION_DAYS` | Delete conversations idle this long (default 90; 0 keeps them). Files people attached to runs go when no conversation has used them for as long |
| `AGENTCANVAS_MAX_UPLOAD_MB`, `AGENTCANVAS_MAX_PROJECT_FILES_MB` | The largest file a person may attach to a run (default 10, at most 20: the web server accepts 20 MB bodies) and the most attached files a project keeps (default 1024; 0 = no limit) |
| `AGENTCANVAS_RATE_LIMIT_PER_DEPLOYMENT` / `_PER_WEBHOOK` / `_PER_PROJECT` / `_PER_SHARE_CLIENT` | Public API requests per minute (120 / 120 / 600 / 30 per share-link visitor address). An organisation's admin may set lower ones for the organisation ([Settings](settings.md#what-an-organisation-may-override)) |
| `TRUSTED_PROXIES` (web) | The address ranges (space-separated) of a load balancer or ingress in front of the web container, e.g. `10.0.0.0/8`. Only a client address it forwards is believed, for rate limits and the audit log. Default `127.0.0.1/32` (nothing in front). In Helm: `web.trustedProxies`, by default every RFC 1918 range (`10.0.0.0/8 172.16.0.0/12 192.168.0.0/16`); narrow it to your ingress controller's pod range. |

## Sign-in

1. In your identity provider, create a **public** client (authorization code + PKCE) with redirect URI
   `https://<your app>/auth/callback`, and enable refresh tokens (`offline_access`).
2. Make the access token's audience the API's (e.g. `agentcanvas-api`).
3. Set `AGENTCANVAS_AUTH_MODE=oidc`, `AGENTCANVAS_OIDC_ISSUER`, `AGENTCANVAS_OIDC_AUDIENCE`,
   `AGENTCANVAS_OIDC_CLIENT_ID`, and `AGENTCANVAS_BOOTSTRAP_ADMIN_EMAILS` for the first admins.

The API checks every token against the provider's keys. `AGENTCANVAS_AUTH_MODE=dev` (no sign-in) is refused in
`staging` and `prod`.

To give roles by group (see [Roles from groups](accounts.md#roles-from-groups)), add
the groups to the **access token** the SPA sends, as a claim in your provider (a "groups" mapper in Keycloak, a
groups claim in Okta or Entra ID). The API reads the claim from that token only.

For provisioning over SCIM point the provider at `https://<your app>/api/scim/v2` with the token an admin makes under
[Provisioning](accounts.md#provisioning-from-your-directory-scim). The web container's proxy forwards `/api/scim/v2` like
any other API path and tells the API its `/api` prefix (`X-Forwarded-Prefix`) so the addresses it returns are right.

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

With the Helm chart, set `config.authMode: matura` and the `config.matura.*` values (`url`, `publicUrl`, `portalUrl`,
`auditUrl`, `syncIntervalS`); the two secrets go in the Secret as `AGENTCANVAS_MATURA_CLIENT_SECRET` and
`AGENTCANVAS_MATURA_WEBHOOK_SECRET`.

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

`infra/scripts/backup.sh` writes a `pg_dump` of the compose prod database (`agentcanvas-<time>.dump`) and an archive
of the workspaces volume beside it (`agentcanvas-<time>.workspaces.tar.gz`, the projects' git repositories).
`infra/scripts/restore.sh <file.dump>` stops the API and web, recreates the database, creates the API's database role
(`agentcanvas_app`, with `POSTGRES_APP_PASSWORD`) before loading the dump, restores the workspaces when their archive
lies beside the dump (or is given as the second argument), runs the migrations (an older backup is brought up to the
running version) and starts everything again. With managed Postgres, rely on its point-in-time recovery and run the
backup nightly as a portable second copy; back the workspaces volume up with your platform's snapshots.

**Keep the keys with the backup, stored apart from it.** A dump without them restores, but what they encrypted
cannot be read:

| Key | Without it |
|---|---|
| `AGENTCANVAS_SECRET_KEY`, or the master key in `AGENTCANVAS_KMS_KEY` (and any in `AGENTCANVAS_KMS_PREVIOUS_KEYS`) | project secrets, webhook signing secrets and app-connector sign-ins are lost |
| `LANGGRAPH_AES_KEY` | encrypted conversation checkpoints cannot be resumed |
| `POSTGRES_APP_PASSWORD` | none lost: `restore.sh` sets the API role's password from whatever the settings say |

The sandbox token and the egress secret encrypt nothing kept: new ones work with an old backup.

## Upgrades

Build (or pull) the new images and roll the API. On shutdown each replica stops taking runs, lets running ones reach
a step boundary and hands them to another replica, which resumes them. Conversations move to a newly published
workflow version only when it is resume-compatible. Take a backup first.

- **Helm:** push the new images and `helm upgrade agentcanvas infra/helm/agentcanvas -f my-values.yaml --set
  image.api.tag=<new> --set image.web.tag=<new>`. The migration Job runs first; the API rolls one pod at a time and
  never drops below its replica count, so there is no downtime.
- **Compose:** build the new images (the commands under Option 1, or set `API_IMAGE` / `WEB_IMAGE` to new tags), then
  `docker compose -f infra/compose.prod.yml --env-file infra/.env.prod up -d --wait`. The `migrate` service runs again
  before the API starts. Compose recreates **every API replica at once**: open streams end and requests fail for the
  half minute or so until the new replicas are ready, and runs that were going resume on them (each replica takes over
  handed-over runs at startup). Upgrade in a quiet hour, or use Kubernetes for rolling upgrades.

## Scaling

- **API:** stateless; add replicas for more concurrent runs and requests (`API_REPLICAS` in `infra/.env.prod`, then
  `up -d`; `api.replicas` in Helm). Each replica adds up to 50 database connections: check `max_connections` (see
  Database connections above). Replicas share the workspaces volume (in Kubernetes a ReadWriteMany one).
- **Sandbox:** each sandbox container runs one call at a time; add `SANDBOX_REPLICAS` / `sandbox.replicas` when
  workspace code waits for a free sandbox ("every sandbox was busy").
- **Web:** nginx only; two replicas are plenty for most teams (`web.replicas`).

### Postgres major versions

The compose files pin `pgvector/pgvector:pg16` by digest; a new digest of the same major version (security fixes) is
a plain image update. A new **major** version (pg17) cannot start on pg16's data directory. With compose: take a
backup (`infra/scripts/backup.sh`), stop the stack, move the `pgdata` volume aside (do not delete it until the new
version runs), set `POSTGRES_IMAGE` to the new major version's image, start Postgres alone, and restore the dump with
`infra/scripts/restore.sh` (it creates the API's role on the new server first and runs the migrations afterwards). The pgvector extension must exist
in the new image (the `pgvector/pgvector` images have it). With managed Postgres, use the provider's major-version
upgrade, after a snapshot.

## Rotating the secrets master key

Set the new key in `AGENTCANVAS_KMS_KEY`, keep the old one in `AGENTCANVAS_KMS_PREVIOUS_KEYS` (a JSON list, e.g.
`["file:/run/secrets/old-master.key"]`), then run `python -m agentcanvas.security.kms rewrap` in an API container.
When every organisation's key is re-wrapped (the command prints how many it changed; run it again and it prints 0),
remove the old key.

## Rotating `AGENTCANVAS_SECRET_KEY`

With `AGENTCANVAS_KMS_KEY` empty, the master key of project secrets is derived from `AGENTCANVAS_SECRET_KEY` (a
staging or production server logs `kms.derived_from_secret_key` at startup to say so). Changing the secret key
alone then makes every organisation's secrets unreadable: the API still starts, but logs
`secret.org_key_unreadable` for each organisation, and runs that need a secret fail. Keep the old secret key as a
previous master key while you move to the new one:

1. With the old secret key still deployed, run `python -m agentcanvas.security.kms rewrap` once. Secrets saved
   before envelope encryption (v0.3.0) are read with the secret key itself; this moves them onto their
   organisation's data key, which the next steps keep readable.
2. Write the old secret key, exactly as it is set now and with nothing else, to a file the API containers can
   read, e.g. a mounted secret at `/run/secrets/old-secret-key`. A key file is read as one line, so the old key must
   be at least 32 characters (production refuses shorter ones).
3. Choose the master key from now on. Best: a key file (`file:/run/secrets/master.key`, 32+ random characters) or
   your cloud KMS (`aws:…`, `gcp:…`) in `AGENTCANVAS_KMS_KEY`, so the secret key and the master key are separate.
   Otherwise leave `AGENTCANVAS_KMS_KEY` empty and the new secret key becomes the master key.
4. Deploy with the new `AGENTCANVAS_SECRET_KEY`, that `AGENTCANVAS_KMS_KEY`, and
   `AGENTCANVAS_KMS_PREVIOUS_KEYS=["file:/run/secrets/old-secret-key"]`. Every replica reads secrets with either key.
5. Run `python -m agentcanvas.security.kms rewrap` in an API container, then again to see `re-wrapped 0`.
6. Remove `AGENTCANVAS_KMS_PREVIOUS_KEYS` and the old key file, and roll the API once more.
