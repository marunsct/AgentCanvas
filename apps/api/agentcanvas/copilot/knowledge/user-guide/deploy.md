# Deploy and operate

This page is for whoever runs AgentCanvas for a team. To try it on one computer, use the one-command Docker setup
in [Run AgentCanvas on your computer](run-locally.md#run-everything-in-docker). The detail behind this page (what
the images and the chart contain, metrics, retention, restore, and a runbook for each alert) is in the
[Operations reference](operations.md).

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
docker build -f infra/Dockerfile.api --build-arg FRAMEWORKS=all --build-arg WITH_MASTRA=true \
  --build-arg GIT_SHA=$(git rev-parse HEAD) -t agentcanvas-api apps/api
docker build -f infra/Dockerfile.web --build-arg GIT_SHA=$(git rev-parse HEAD) -t agentcanvas-web .
cp infra/.env.prod.example infra/.env.prod      # fill it in (never commit it)
docker compose -f infra/compose.prod.yml --env-file infra/.env.prod up -d --wait
```

This starts Postgres, a one-shot migration, two API replicas and the web server on port 8080 (`WEB_PORT`) of
`127.0.0.1`: it speaks plain HTTP, so put a TLS-terminating proxy in front on the same host (or set `WEB_BIND=0.0.0.0`
when the proxy runs elsewhere and the port is firewalled) and keep response buffering **off** for `/api/` (runs stream
Server-Sent Events). Have the proxy speak HTTP/2 to browsers (Caddy, Traefik and cloud load balancers do by default;
nginx needs `http2 on;` next to `listen 443 ssl;`): over HTTP/1.1 a browser opens at most six connections to a site
for all its tabs, and each open editor, run or Copilot answer holds one, so with several tabs open other requests
wait. The API containers run with a read-only root (writable: the workspaces volume, `/tmp` and the
app user's home, both in memory), no capabilities and a 2 GB memory limit (`API_MEM_LIMIT`); `API_READ_ONLY=false`
turns the read-only root off should a framework need to write elsewhere.

**Which frameworks the image runs.** Like the Docker app, these commands build every framework into the API image
(about 4.5 GB instead of 1.1 GB), so workflows designed for CrewAI, Google ADK, Pydantic AI, Agno, the Microsoft Agent
Framework, Strands, LlamaIndex or Mastra run on this server too. For a smaller image, list only the ones you use,
for example `--build-arg FRAMEWORKS="crewai google_adk" --build-arg WITH_MASTRA=false`, or `FRAMEWORKS=none` (and
`WITH_MASTRA=false`) for LangGraph and the OpenAI Agents SDK only, which every image has. A workflow whose framework is
missing can still be designed and exported, but its runs stop with *… is not installed on this server*, and the
workflow's **Framework** setting marks it *not set up*. The ids are in [Choosing a framework](frameworks.md).

**Cloud KMS clients.** The API image includes the AWS KMS (`boto3`) and Google Cloud KMS (`google-cloud-kms`) clients,
so a master key in `AGENTCANVAS_KMS_KEY=aws:…` or `gcp:…` works with every image. A server that keeps its master key
in a file can leave them out (about 60 MB) with `--build-arg KMS=""`, or keep one with `--build-arg KMS=aws`. A server
whose master key needs a client it does not have stops at startup with a message naming the setting and what to
install. Installed from source, add the one you use: `uv sync --extra kms-aws` or `uv sync --extra kms-gcp`.

**Cloud model providers.** The API image also includes the integrations for models in your own cloud account: AWS
Bedrock (`langchain-aws`, about 60 MB) and Claude on Google Vertex AI (`langchain-google-vertexai`, about 105 MB);
Gemini on Vertex AI needs neither. A server that uses neither can leave them out with `--build-arg MODELS=""`, or keep
one with `--build-arg MODELS=aws`. Without its integration, a model block of that provider gets the editor warning
`W011` and its runs stop with a message naming the package. Installed from source: `uv sync --extra models-aws` or
`uv sync --extra models-gcp`. How the models find their credentials and region: [Models in your own cloud
account](models.md#models-in-your-own-cloud-account).

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
docker build -f infra/Dockerfile.api --build-arg FRAMEWORKS=all --build-arg WITH_MASTRA=true \
  --build-arg GIT_SHA=$(git rev-parse HEAD) -t $REGISTRY/agentcanvas-api:$TAG apps/api
docker build -f infra/Dockerfile.web --build-arg GIT_SHA=$(git rev-parse HEAD) -t $REGISTRY/agentcanvas-web:$TAG .
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

**A cloud identity instead of keys.** AWS Bedrock and Google Vertex AI models (and a KMS master key) can use the API
pods' own cloud identity, so no access key is stored anywhere. Set `api.serviceAccount.create: true` and give the
service account the annotation of your cloud: `eks.amazonaws.com/role-arn: arn:aws:iam::<account>:role/<role>` for
AWS IRSA, or `iam.gke.io/gcp-service-account: <name>@<project>.iam.gserviceaccount.com` for GKE Workload Identity
(and bind the Google service account to it). For EKS Pod Identity, associate the role with the service account's
name (`<release>-agentcanvas-api`, or yours in `api.serviceAccount.name`). To use a service account you manage,
set only `api.serviceAccount.name`. The pods still mount no Kubernetes API token. Set the default region beside it
(`AWS_REGION`, `GOOGLE_CLOUD_LOCATION` and `GOOGLE_CLOUD_PROJECT` in the Secret) if model blocks do not name one.
Every project on the server can use this identity: grant it only the models you want offered. See [Models in your
own cloud account](models.md#models-in-your-own-cloud-account).

Kubernetes puts no limit on the processes a pod starts unless the kubelet's `podPidsLimit` is set (the compose files
set `pids_limit` on the sandbox containers). Each sandbox call's code may start about 256 processes or threads, so a
fork loop stops there; still set `podPidsLimit` (e.g. 1024) on the nodes the sandbox pods run on
(`sandbox.nodeSelector`), so that no pod can use up a node's process table.

## Essential settings

Every setting, with its default, is listed in [Server settings](server-settings.md); these are the ones to decide first.

| Setting | Purpose |
|---|---|
| `DATABASE_URL` | `postgresql://user:pass@host:5432/agentcanvas` |
| `AGENTCANVAS_SECRET_KEY` | 64+ random characters. Production refuses the development default. |
| `AGENTCANVAS_ENV` | `prod` (default in compose prod and Helm), `staging` or `dev` |
| `AGENTCANVAS_EDITION` (Helm: `config.edition`) | `dev` (the default): one company's own server. Builders may let a step reach its private network (**Allow private network** on a web API tool or a remote agent, app connectors at internal addresses), from the API's own network; never a link-local or cloud metadata address. `hosted`: a server shared by organisations that do not trust each other. Every outbound connection (models, tools, remote agents, app connectors, knowledge, trace export) goes to public addresses only, whatever a step allows: the editor flags steps that run code in the server process (E041) or allow private networks (E043). Set `hosted` for any server whose organisations must be kept from your internal network. |
| `LANGGRAPH_AES_KEY` | 16, 24 or 32 characters (32 for AES-256: `openssl rand -hex 16`): encrypts conversation checkpoints at rest |
| `AGENTCANVAS_KMS_KEY` | Master key for project secrets: `file:<path>`, `aws:<key ARN>` or `gcp:<key name>`. The API needs `kms:Encrypt` and `kms:Decrypt` on an AWS key (credentials from the environment: an instance or pod role), or the Cloud KMS CryptoKey Encrypter/Decrypter role on a Google key (Application Default Credentials, such as Workload Identity) |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `MISTRAL_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `XAI_API_KEY`, `AZURE_OPENAI_API_KEY` | Model provider keys for the whole server; a project secret of the same name takes precedence ([Models and API keys](models.md)) |
| `AGENTCANVAS_LOCAL_MODELS_HOST` | Host that local model servers (Ollama, LM Studio, vLLM, llama.cpp) are reached on when their address isn't set, e.g. `host.docker.internal` or a GPU machine's name |
| `AGENTCANVAS_MODEL_PRICES` | Prices for models whose list price the catalog does not carry (it prices the Claude and OpenAI models), so costs, cost budgets and alerts count them: JSON, USD per million tokens, e.g. `{"openai:gpt-5.5": [5, 30]}`, or `[in, out, cache read, cache write]` to price prompt caching too (without them, cached input is charged as ordinary input). A model not in the catalog or a malformed value stops the server from starting |
| `AGENTCANVAS_SCRIPT_EXECUTION` | Where workspace code runs: `remote` (default in the production compose file and Helm chart: the sandbox service, below), `subprocess` (limits only, not isolation), `docker`, `langsmith`, `inprocess`. A staging or production server refuses `inprocess` and `subprocess`, which run the code as the API's own user |
| `AGENTCANVAS_ALLOW_UNISOLATED_SCRIPTS` | `true` lets a staging or production server run workspace code in `inprocess` or `subprocess` mode anyway, when only people you trust edit workspaces. With `inprocess` it also allows the **Agent from another framework** block, which runs workspace code inside the API itself; every other server refuses it (E041) |
| `AGENTCANVAS_SANDBOX_URL`, `AGENTCANVAS_SANDBOX_TOKEN` | With `remote`: where the sandbox service is, and the secret (32+ random characters) the API presents to it. The compose file and chart set the URL; you provide the token |
| `SANDBOX_REPLICAS` (compose), `sandbox.replicas` (Helm) | How many sandbox containers run (4 by default). Each runs one call at a time and, after it, stops anything the code left running and removes what it wrote (`AGENTCANVAS_SANDBOX_SWEEP=1`), so code of different projects never shares a container at the same moment; more replicas, more calls at once |
| `AGENTCANVAS_EGRESS_PROXY`, `AGENTCANVAS_EGRESS_SECRET` | The egress proxy the sandbox reaches the hosts of a project's `sandbox.json` through (`egress:3128` in the compose files, the `-egress` Service in the chart), and the secret (32+ random characters) it checks each call's list with. You provide the secret; with `docker` and no proxy set, the API starts an `agentcanvas-egress` container itself. `AGENTCANVAS_EGRESS_PROXY_RESOLVE=true` (set by the chart) hands the sandbox the proxy's address instead of its name, so the sandbox pods need no DNS |
| `AGENTCANVAS_ALLOW_LOCAL_MCP`, `AGENTCANVAS_SANDBOX_MCP` | Local (stdio) app connectors: `ALLOW_LOCAL_MCP=true` lets builders use them, and `SANDBOX_MCP=true` runs them in the sandbox instead of on the API server ([below](#local-app-connectors-in-the-sandbox)). Both default to `false`. The hosted edition runs them only in the sandbox |
| `AGENTCANVAS_CORS_ORIGINS` | The web app's origin, as a JSON list |
| `AGENTCANVAS_AUDIT_SINK_URL`, `AGENTCANVAS_AUDIT_SINK_SECRET` | Optional: where security events are also POSTed as signed CloudEvents, and the secret that signs them ([Security events](#security-events-and-audit-forwarding)) |
| `AGENTCANVAS_AUDIT_SPLUNK_HEC_URL`, `AGENTCANVAS_AUDIT_SPLUNK_HEC_TOKEN` | Optional: Splunk's HTTP Event Collector, which also receives security events, and its token ([Security events](#security-events-and-audit-forwarding)) |
| `AGENTCANVAS_AUDIT_FORWARD_ALL` | `true` forwards every audit entry, not only security events (default `false`) |
| `AGENTCANVAS_PUBLIC_URL` | Where people reach the web app, e.g. `https://canvas.example.com`. Signing in to app connectors ([App connectors](agents-and-connectors.md#sign-in-per-person)) returns the browser to `<this>/oauth/callback`, and servers that support Client ID Metadata Documents fetch ours from `<this>/api/v1/oauth/client-metadata.json` (it must be https and public for that). Unset: the first CORS origin, which is right for the development server only |
| `AGENTCANVAS_RETENTION_DAYS` | Delete conversations idle this long (default 90; 0 keeps them). Files people attached to runs go when no conversation has used them for as long |
| `AGENTCANVAS_MAX_UPLOAD_MB`, `AGENTCANVAS_MAX_PROJECT_FILES_MB` | The largest file a person may attach to a run (default 10, at most 20: the web server accepts 20 MB bodies) and the most attached files a project keeps (default 1024; 0 = no limit) |
| `AGENTCANVAS_RATE_LIMIT_PER_DEPLOYMENT` / `_PER_WEBHOOK` / `_PER_PROJECT` / `_PER_SHARE_CLIENT` | Public API requests per minute (120 / 120 / 600 / 30 per share-link visitor address). An organisation's admin may set lower ones for the organisation ([Settings](settings.md#what-an-organisation-may-override)) |
| `TRUSTED_PROXIES` (web) | The address ranges (space-separated) of a load balancer or ingress in front of the web container, e.g. `10.0.0.0/8`. Only a client address it forwards is believed, for rate limits, the audit log and organisations' allowed networks. Default `127.0.0.1/32` (nothing in front). In Helm: `web.trustedProxies`, by default every RFC 1918 range (`10.0.0.0/8 172.16.0.0/12 192.168.0.0/16`); narrow it to your ingress controller's pod range. |
| `SECURITY_CONTACT`, `SECURITY_POLICY` (web) | What `/.well-known/security.txt` (RFC 9116) names as the place to report a vulnerability (an `https:`, `mailto:` or `tel:` address) and the disclosure policy (an `https:` page). Default: AgentCanvas's private advisory form and security policy on GitHub; set your own security team's. Anything but a plain address stops the web container from starting. In Helm: `web.securityContact`, `web.securityPolicy`. |

### Local app connectors in the sandbox

Local app connectors (Playwright's browser, the filesystem, git, Slack's `npx` server…) start a program and talk to
it over its input and output. On a production server, run them in the sandbox, never on the API server:

```bash
AGENTCANVAS_ALLOW_LOCAL_MCP=true
AGENTCANVAS_SANDBOX_MCP=true   # needs AGENTCANVAS_SCRIPT_EXECUTION=remote (the default here) or docker
```

- **`remote`** (compose, Helm): the API opens a WebSocket to the sandbox service (`/mcp`, with the sandbox token),
  which starts the program with the connector's own environment and relays its input and output for as long as
  the connection lasts. A connector takes a sandbox for its whole life, as a script call does for its own, so plan
  replicas for both: with the defaults, a run's connector holds one until the run ends. `AGENTCANVAS_SANDBOX_MCP_URL`
  sends connectors to a sandbox service of their own (for example one running the browser image below), and
  `AGENTCANVAS_SANDBOX_URL` keeps serving scripts.
- **`docker`**: each connector gets a container of its own (`ac-mcp-…`, label `agentcanvas.mcp=1`) from
  `AGENTCANVAS_SANDBOX_MCP_IMAGE` (default: `AGENTCANVAS_SANDBOX_IMAGE`), as nobody, read-only with a 512 MB `/tmp`,
  no capabilities, one CPU, at most 512 processes, `--init`, and no network or only the egress network. Its
  environment goes in a file readable only by the API's user, removed once the container exists.
- Either way the program reaches only the hosts of its project's `sandbox.json` egress list, through the egress
  proxy (a relay inside the sandbox adds the signed list, so browsers that send no proxy password work too), and is
  stopped when it uses more memory than `mcp.memory_mb`, after `mcp.idle_s` without a call, after `mcp.lifetime_s`
  in any case, or when its run ends ([Sandbox](workspace.md#sandbox)).
- `AGENTCANVAS_SANDBOX_MCP_SESSIONS` (default 8) caps the connector sandboxes one API server runs at once; past
  it the least recently used idle one is stopped, or the run fails with "8 app connector sandboxes are in use".
- The image needs Python 3 (which starts the program) and the program itself. Programs are not downloaded at run
  time unless the egress list allows the registry (`registry.npmjs.org`, `pypi.org`, `files.pythonhosted.org`);
  the browser image below comes with the gallery's sandbox connectors.
- The hosted edition allows only the gallery's connectors marked to run in the sandbox (E046), and checks it again
  before every run. With `AGENTCANVAS_EDITION=hosted` and `SANDBOX_MCP` off, no local connector runs at all: the
  hosted edition never starts one on the API server.

**The browser image.** Build the API image with `--build-arg WITH_BROWSER=true` and run the sandbox from it; the API
itself keeps the plain image:

```bash
docker build -f infra/Dockerfile.api --build-arg FRAMEWORKS=all --build-arg WITH_MASTRA=true \
  --build-arg WITH_BROWSER=true -t agentcanvas-api:browser apps/api
```

- It adds Node.js, the gallery's connectors that run in the sandbox (**Browser (Playwright)**, **Sequential
  thinking**, at the gallery's versions, installed from `agentcanvas/sandbox/connectors/package-lock.json`) and
  Chromium with its system libraries: about 950 MB more on disk (about 370 MB to download): Chromium, Node.js and
  the connectors 595 MB, Chromium's libraries and fonts 350 MB.
- Compose: `SANDBOX_IMAGE=agentcanvas-api:browser`. Helm: `sandbox.image`. Docker mode:
  `AGENTCANVAS_SANDBOX_MCP_IMAGE=agentcanvas-api:browser`.
- In it, `npx @playwright/mcp@0.0.83 …` starts the installed copy, offline; Playwright MCP uses that Chromium and
  writes its snapshots under `/tmp`. Chromium runs without its own sandbox (the container is the sandbox) and with
  the connector's memory limit: give a browser `"mcp": {"memory_mb": 1024}` or more in `sandbox.json`, and the
  sandbox pods or containers that much on top of what scripts need (the compose file gives them 2 GB).
- To offer another connector in the hosted edition, add it to that `package.json` (and its lockfile) at the
  gallery's exact version and mark the gallery entry `sandboxable`; a test checks the two agree.

## Sign-in

1. In your identity provider, create a **public** client (authorization code + PKCE) with redirect URI
   `https://<your app>/auth/callback`, and enable refresh tokens (`offline_access`).
2. Make the access token's audience the API's (e.g. `agentcanvas-api`).
3. Set `AGENTCANVAS_AUTH_MODE=oidc`, `AGENTCANVAS_OIDC_ISSUER`, `AGENTCANVAS_OIDC_AUDIENCE`,
   `AGENTCANVAS_OIDC_CLIENT_ID`, and `AGENTCANVAS_BOOTSTRAP_ADMIN_EMAILS` for the first admins.

The API checks every token against the provider's keys. `AGENTCANVAS_AUTH_MODE=dev` (no sign-in) is refused in
`staging` and `prod`.

The first admins, and everyone you invite, are matched by a **verified** email in the access token (the
`AGENTCANVAS_OIDC_EMAIL_CLAIM` claim, `email` by default, with `email_verified: true`). A provider that never sends
`email_verified` (Entra ID) needs `AGENTCANVAS_OIDC_TRUST_EMAIL=true`. Without it the sign-in is refused with a
message that says so, and the server logs `oidc.email_unverified`; set it, and the next sign-in of a bootstrap
email still makes that person admin.

The web app sends the provider's **access token**, and the API reads it as a signed JWT whose `iss` is the issuer
and whose `aud` is the audience you set. What that takes, provider by provider:

| Provider | Setup |
|---|---|
| Keycloak | A public client with PKCE; an "audience" mapper that adds `AGENTCANVAS_OIDC_AUDIENCE` to the access token. Issuer `https://<host>/realms/<realm>`. Keycloak sends `email_verified`. |
| Okta | A custom authorization server (the org server's access tokens are not for your API): its audience is `AGENTCANVAS_OIDC_AUDIENCE`, its issuer `https://<org>.okta.com/oauth2/<server id>`. Add `email` (and `groups`) as access-token claims. |
| Auth0 | An API whose identifier is `AGENTCANVAS_OIDC_AUDIENCE` (the web app asks for it as `audience`), and a single-page application. Auth0 puts no email in access tokens: add `email` and `email_verified` with a post-login Action, and set `AGENTCANVAS_OIDC_EMAIL_CLAIM` to the claim name it uses. Issuer `https://<tenant>.auth0.com/`. |
| Entra ID | An app registration with a single-page-application redirect URI. **Expose an API**: its Application ID URI (e.g. `api://agentcanvas`) is `AGENTCANVAS_OIDC_AUDIENCE`; add a scope (e.g. `access`) and ask for it in `AGENTCANVAS_OIDC_SCOPES` (`openid profile email offline_access api://agentcanvas/access`), or the access token is for Microsoft Graph, not AgentCanvas. In the manifest set `accessTokenAcceptedVersion` (`requestedAccessTokenVersion`) to `2`: otherwise access tokens carry the v1 issuer `https://sts.windows.net/<tenant>/`, which does not match the v2 issuer `https://login.microsoftonline.com/<tenant>/v2.0` and every request is refused with "Invalid issuer". Add `email` (and `groups`) as optional claims of the access token, and set `AGENTCANVAS_OIDC_TRUST_EMAIL=true`: Entra ID sends no `email_verified`. |
| Google | Not directly: Google's access tokens are opaque, not JWTs. Use a broker with Google as its upstream (Keycloak, Auth0, Dex). |

To give roles by group (see [Roles from groups](accounts.md#roles-from-groups)), add
the groups to the **access token** the SPA sends, as a claim in your provider (a "groups" mapper in Keycloak, a
groups claim in Okta or Entra ID). The API reads the claim from that token only.

For provisioning over SCIM point the provider at `https://<your app>/api/scim/v2` with the token an admin makes under
[Provisioning](accounts.md#provisioning-from-your-directory-scim). The web container's proxy forwards `/api/scim/v2` like
any other API path and tells the API its `/api` prefix (`X-Forwarded-Prefix`) so the addresses it returns are right.

### Multi-factor sign-in

AgentCanvas reads how a person signed in from the access token: `acr` (the authentication level, named by the
provider) and `amr` (the methods used, such as `pwd`, `mfa`, `otp`, `hwk`).

| Setting | Effect |
|---|---|
| `AGENTCANVAS_OIDC_REQUIRED_ACR`, `AGENTCANVAS_OIDC_REQUIRED_AMR` | Every sign-in on the server must show an `acr` from the first list or an `amr` from the second, e.g. `AGENTCANVAS_OIDC_REQUIRED_AMR='["mfa"]'`. Empty (default): no requirement |
| `AGENTCANVAS_OIDC_MFA_ACR`, `AGENTCANVAS_OIDC_MFA_AMR` | What counts as multi-factor for an organisation that requires it ([Multi-factor sign-in](accounts.md#multi-factor-sign-in)). Default: `amr` contains `mfa` |

A token that does not show it gets `401` with problem type `…/errors/mfa-required` and
`WWW-Authenticate: Bearer error="insufficient_user_authentication"` (RFC 9470), naming the `acr_values` to ask for.
The web app then signs in again at the provider with `prompt=login` and those `acr_values`; if the provider still
does not ask for a second factor, the page shows the error instead of signing in again in a loop. It is the
provider that decides to ask for one:

| Provider | What to set |
|---|---|
| Keycloak | Step-up authentication: an authentication flow with conditions by level of authentication, and the mapping of `acr` values to levels in the client (for example `gold` to level 2). List that value in `AGENTCANVAS_OIDC_MFA_ACR` (or `_REQUIRED_ACR`) and leave the `amr` lists empty, unless you add Keycloak's `amr` mapper. |
| Entra ID | A Conditional Access policy that requires MFA for the app; Entra ID then puts `mfa` in `amr`. |
| Okta | An app sign-on policy (or authentication policy) that requires a second factor; Okta puts `mfa` in `amr`. |
| Auth0 | Multi-factor authentication, with an Action that asks for it for this API; Auth0 puts `mfa` in `amr`. |

Personal API tokens and deployment keys do not pass through the provider and are not affected. Sessions renew
silently at the provider: how long one may stay idle or last at most is set there (the refresh token's idle and
maximum lifetimes).

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
  (`make migrate` with the owner's `DATABASE_URL`) and set `AGENTCANVAS_AUTO_MIGRATE=false` on the API. With it off, the
  API refuses to start on a database the migrations have not reached and says which command to run.
- That role may read and add audit-log entries but not change or delete them: `app_role` revokes UPDATE and DELETE
  on `audit_log`, so a SQL injection or a compromised API server cannot rewrite history. Retention deletes entries
  past the audit retention through the function `audit_expire`, which runs as the owner, deletes only the oldest
  entries of an organisation's hash chain, records where the chain now starts, and refuses entries less than a day
  old. An API that connects as the owner (no `APP_DB_USER`), and SQLite, keep the hash chain but not this guarantee.
- The API asks for `agentcanvas.all_tenants=on` as a connection startup option, and `app_role` also makes it the
  role's own default when it runs as a superuser (on a managed Postgres, whose owner is not one, it prints a notice
  instead). PgBouncer refuses or drops startup options, so behind it the role needs that default (a superuser runs
  `ALTER ROLE <api role> SET agentcanvas.all_tenants = 'on'`); the API refuses to start when its connections would
  see no rows. The rest of the PgBouncer setup is in the [runbook](operations.md#runbooks) (DB connection exhaustion).
- SQLite has no row-level security; there the API's own organisation filters are the only isolation.
- AgentCanvas supports Matura's **shared** data mode only (one database for every organisation), as its manifest
  declares. A token whose placement mode (`pmode`) is `schema` or `database` is refused with 503.
- LangGraph's own tables (checkpoints, long-term memory) are not covered by the policies; their rows are reached only
  through the conversations and namespaces of the organisation's own rows.

### Security events and audit forwarding

AgentCanvas keeps every change in its own audit log (**Settings › Audit log**). Security-relevant ones also leave the
server, in every sign-in mode: publishing and unpublishing, deployment keys, callback secrets and share links,
triggers (webhooks and schedules) created or switched on, secrets, OAuth app credentials and connected accounts,
sandbox settings, approvals (and conversation state edited by hand), where approval notifications go, the
organisation's **Stop all runs**, members, invitations accepted, organisation settings and policies, spending caps,
release gates, trace export, project workspaces, API and SCIM tokens, deleted projects and workflows, erased
memories, git connections and pushes, app connector servers that changed tools a deployment pinned
(`mcp_tool_drift`), and every change SCIM or the sign-in token's groups make to members and roles:

- **Always**, as a JSON log line `audit.event` (logger `agentcanvas.audit`, level info) with `org_id`, `actor_id`,
  `action`, `target`, `detail`, `ip` and `at`. Ship the API's logs to your SIEM and filter on it.
- **With `AGENTCANVAS_AUDIT_SINK_URL`** (and `AGENTCANVAS_AUDIT_SINK_SECRET`, which the server refuses to start
  without), each entry is POSTed there as a CloudEvent (`agentcanvas.audit.<action>`, `tenantid` = the
  organisation, `id` = `audit-<the entry's id>`) signed in the `AgentCanvas-Signature` header:
  `t=<unix time>,v1=<hex HMAC-SHA256 of "<t>.<body>">` with the secret. Check the signature and refuse a `t` older
  than five minutes.
- **With `AGENTCANVAS_AUDIT_SPLUNK_HEC_URL`** (the collector's full URL, such as
  `https://splunk.example.com:8088/services/collector/event`) and `AGENTCANVAS_AUDIT_SPLUNK_HEC_TOKEN`, each entry
  goes to Splunk's HTTP Event Collector as one event (`sourcetype` `agentcanvas:audit`, `source` `agentcanvas`, the
  entry's time); the token's default index receives it. Staging and production refuse an `http://` URL, which would
  send the token in clear.
- **In Matura mode with `AGENTCANVAS_MATURA_AUDIT_URL`**, the same CloudEvents go to Matura, signed like its webhooks
  (`Matura-Signature`, with the webhook secret). Matura has no audit ingest API yet: this is for later.

Set `AGENTCANVAS_AUDIT_FORWARD_ALL=true` to forward every audit entry (every change anyone makes), not only the
security-relevant ones.

Delivery is reliable. Each forwarded entry is queued for every destination in the database (the audit outbox), in the
same transaction that records it, and every API server delivers the queue: a row is claimed by one server at a time,
so several replicas share the work without sending an entry twice. A failed delivery is retried after 5 s, doubling
up to an hour, `AGENTCANVAS_AUDIT_FORWARD_MAX_ATTEMPTS` times (24 by default, about 15 hours); then the entry is kept
as undelivered (dead) for an operator to send again. A server restart or a receiver outage loses nothing. A receiver
may rarely see an entry twice (a server stopped between sending it and recording that it was sent): drop copies by
the event's `id`. The metrics, alerts and commands for this are in the
[Operations reference](operations.md#runbooks).

Refused requests are logged too: a 401 (an unknown, revoked or expired API token, SCIM token or deployment key, a
missing or invalid sign-in) as a warning `auth.refused`, a 403 (a role or organisation refusal) as `auth.forbidden`,
each with the method, path, route, client address, the organisation and person when known, the request id and the
credential's kind and first characters (`personal token act_AbCdEf`, as the app shows them); never the credential.

## Observability

- **Health:** `/healthz` (process up) and `/readyz` (database and run store reachable).
- **Version:** which release and commit a server runs: `GET /v1/version` (signed in; Settings › You › About shows
  it), the `agentcanvas_build_info{version,commit}` metric, the `api.started` log line, and the images' labels
  (`docker inspect --format '{{json .Config.Labels}}' <image>`). The commit is known when the image was built with
  `--build-arg GIT_SHA=...`, as in the commands above.
- **Metrics:** Prometheus on `/metrics`. A Grafana dashboard and alert rules (including evaluation drift) are in
  `infra/observability/`.
- **Traces:** set `OTEL_EXPORTER_OTLP_ENDPOINT` for OpenTelemetry; `LANGSMITH_TRACING` / `LANGSMITH_API_KEY` for
  LangSmith.

The metrics, the log fields and what to do when an alert fires are in the
[Operations reference](operations.md#observability).

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

Afterwards, Settings › You › About (or `GET /v1/version`) shows the new release, and `agentcanvas_build_info` which
release each pod runs.

### Rolling back

When an upgrade goes wrong, run the previous images on the same database. A release's migrations are written so that
the release before it still runs on the migrated database (new tables and columns that the older code ignores), so
the schema stays where it is: the migration step of the older image sees a database a later release migrated, logs
`db.newer_than_code` and leaves it alone, and the API starts.

- **Helm:** `helm rollback agentcanvas` returns to the previous revision (`helm history agentcanvas` lists them;
  `helm rollback agentcanvas <revision>` picks one), or `helm upgrade` with the previous `image.api.tag` and
  `image.web.tag`.
- **Compose:** set `API_IMAGE` and `WEB_IMAGE` to the previous tags (or build the previous commit), then
  `docker compose -f infra/compose.prod.yml --env-file infra/.env.prod up -d --wait`. Images older than the release
  that added this section run a bare `alembic upgrade head` as their migration step, which fails on the newer
  database; start those with `up -d --wait --no-deps api web`, which skips it. (`helm rollback` runs no migration Job.)
- **Check** that Settings › You › About shows the previous release, then upgrade again once the fix is out.
- **Downgrading the schema** is rarely needed, and only when a release's notes say its migrations do not work with
  the release before. It can drop data the newer columns hold, so take a backup first, and run it with the **newer**
  image (only it has those migrations' downgrade steps), down to the revision the previous image reports with
  `alembic heads`: for example `docker compose -f infra/compose.prod.yml --env-file infra/.env.prod run --rm migrate
  alembic downgrade <revision>`, then roll back the images.
- **When data is damaged**, restore the backup taken before the upgrade ([Backups](#backups)).

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
