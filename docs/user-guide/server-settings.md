# Server settings

Every setting of the AgentCanvas server, set as an environment variable (or in `apps/api/.env` when running from
source). Generated from the server's code by `apps/api/scripts/export_settings_reference.py`; do not edit by hand.
Settings › **Server** shows the effective values of the most important ones on a running server.

Lists are JSON (`["a", "b"]`), booleans `true` or `false`. Settings whose names end in `_S` are seconds.

| Variable | Default | What it does |
|---|---|---|
| `AGENTCANVAS_ENV` | `"dev"` | staging and prod refuse development defaults |
| `AGENTCANVAS_EDITION` | `"dev"` | hosted = a multi-tenant service: blocks that reach the server or private networks are refused |
| `DATABASE_URL` | `"sqlite+aiosqlite:///./agentcanvas.db"` | SQLAlchemy URL of the control DB. postgresql://… (production) or sqlite+aiosqlite:///… (local edition). |
| `AGENTCANVAS_PERSISTENCE_URL` |  | LangGraph checkpointer/store location; defaults to the control DB ("memory" = ephemeral, tests only) |
| `AGENTCANVAS_AUTO_MIGRATE` | `True` | run Alembic `upgrade head` at startup; off: refuse a database not at head |
| `AGENTCANVAS_SEED_DEV_DATA` | `True` | create the default organisation/project/user when missing |
| `AGENTCANVAS_SECRET_KEY` | `"dev-only-change-me" (change it)` | signs sessions and links; staging and prod refuse the default |
| `AGENTCANVAS_KMS_KEY` | `""` | master key that wraps each organisation's data key (see agentcanvas/security/kms.py): "" derives it from secret_key (development), or file:<path>, aws:<key ARN>, gcp:<key name>; previous keys still unwrap |
| `AGENTCANVAS_KMS_PREVIOUS_KEYS` | `[]` | earlier master keys, still used to unwrap data keys after a rotation |
| `AGENTCANVAS_AUTH_MODE` | `"dev"` | Authentication. dev = every request is the seeded development admin (refused in staging/prod); oidc = bearer JWTs from the identity provider, checked against its JWKS. matura = bearer JWTs from Matura (oidc_issuer = its realm, oidc_audience = this app's client id): the token names the organisation (`tid`) and its permissions (`perms`); members and roles are managed in Matura. |
| `AGENTCANVAS_OIDC_ISSUER` |  | e.g. https://login.example.com/realms/acme (the token's `iss`) |
| `AGENTCANVAS_OIDC_AUDIENCE` |  | the token's `aud` the API accepts |
| `AGENTCANVAS_OIDC_CLIENT_ID` |  | public SPA client id, handed to the web app by /v1/auth/config |
| `AGENTCANVAS_OIDC_JWKS_URL` |  | None = from the issuer's discovery document |
| `AGENTCANVAS_OIDC_SCOPES` | `"openid profile email offline_access"` | offline_access: silent token refresh |
| `AGENTCANVAS_OIDC_EMAIL_CLAIM` | `"email"` | the token claim that holds the person's email |
| `AGENTCANVAS_OIDC_TRUST_EMAIL` | `False` | treat the email claim as verified when the provider sends no `email_verified` (e.g. a corporate IdP that owns the domain); leave off for providers where users choose their own email |
| `AGENTCANVAS_OIDC_JWKS_CACHE_S` | `3600` | how long the provider's signing keys are kept before they are fetched again |
| `AGENTCANVAS_OIDC_LEEWAY_S` | `30` | clock skew tolerated on exp/nbf/iat |
| `AGENTCANVAS_OIDC_REQUIRED_ACR` | `[]` | what every sign-in must show about how the person signed in: an `acr` (authentication level, as the identity provider names it, e.g. Keycloak's "2") on this list, or an `amr` (methods, e.g. "mfa", "hwk") on that one. Empty (default): no requirement. A sign-in without it is asked to sign in again at the provider, with these acr_values |
| `AGENTCANVAS_OIDC_REQUIRED_AMR` | `[]` | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_OIDC_MFA_ACR` | `[]` | what counts as multi-factor for an organisation that requires it (Settings > Organisation > Security): an `acr` on the first list or an `amr` on the second. Entra ID, Okta and Auth0 put "mfa" in `amr`; Keycloak needs an acr level configured (and listed here) |
| `AGENTCANVAS_OIDC_MFA_AMR` | `['mfa']` | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_MATURA_URL` |  | Matura (auth_mode = matura). The API URL, app slug and client secret read the change feed; the webhook secret checks POST /v1/matura/events; the portal is where admins manage members and roles. e.g. https://matura.example.com |
| `AGENTCANVAS_MATURA_APP` | `"agentcanvas"` | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_MATURA_CLIENT_SECRET` |  | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_MATURA_WEBHOOK_SECRET` |  | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_MATURA_PORTAL_URL` |  | overrides the portal's address; default <matura_public_url>/portal/ |
| `AGENTCANVAS_MATURA_PUBLIC_URL` |  | where people reach Matura (its MATURA_PUBLIC_URL), for portal links |
| `AGENTCANVAS_MATURA_AUDIT_URL` |  | where security-relevant audit entries are POSTed, signed with the webhook secret (placeholder until Matura has an audit ingest API); unset = kept in AgentCanvas's own audit log only |
| `AGENTCANVAS_AUDIT_SINK_URL` |  | security-relevant audit entries, also POSTed here as signed CloudEvents (any sign-in mode); each one is also always logged as `audit.event`. The secret signs them (AgentCanvas-Signature) and is needed with the URL. |
| `AGENTCANVAS_AUDIT_SINK_SECRET` |  | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_AUDIT_SPLUNK_HEC_URL` |  | security-relevant audit entries, also sent to Splunk's HTTP Event Collector: the full URL (e.g. https://splunk.example.com:8088/services/collector/event) and an HEC token, needed with the URL |
| `AGENTCANVAS_AUDIT_SPLUNK_HEC_TOKEN` |  | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_AUDIT_FORWARD_ALL` | `False` | forward every audit entry (every change anyone makes), not only the security-relevant ones |
| `AGENTCANVAS_AUDIT_FORWARD_MAX_ATTEMPTS` | `24` | a delivery that keeps failing is retried with a growing delay (up to an hour) this many times, about 15 hours in all with the default, then kept as undelivered (dead) for an operator to retry |
| `AGENTCANVAS_AUDIT_FORWARD_INTERVAL_S` | `2.0` | how often each server looks for entries to deliver |
| `AGENTCANVAS_MATURA_SYNC_INTERVAL_S` | `3600` | how often the change feed is read (webhooks bring changes in between) |
| `AGENTCANVAS_BOOTSTRAP_ADMIN_EMAILS` | `[]` | emails that become admins of the default organisation on first sign-in (bootstraps an empty install) |
| `AGENTCANVAS_ALLOW_LOCAL_MCP` | `False` | app connectors that start a local process (stdio MCP servers); local edition |
| `AGENTCANVAS_WORKSPACES_DIR` | `"./workspaces"` | one git repository per project |
| `AGENTCANVAS_SCRIPT_EXECUTION` | `"inprocess"` | how workspace code runs: in the API process (dev), a child process, a Docker container or a LangSmith Sandbox (credentials from LANGSMITH_API_KEY) |
| `AGENTCANVAS_ALLOW_UNISOLATED_SCRIPTS` | `False` | staging and prod refuse to run workspace code as the API's own user (inprocess, subprocess) unless this says so: for a server only trusted people edit |
| `AGENTCANVAS_SANDBOX_URL` |  | "remote": the sandbox service (`agentcanvas.sandbox.service`), e.g. http://sandbox:8090 |
| `AGENTCANVAS_SANDBOX_TOKEN` |  | the bearer token the service expects |
| `AGENTCANVAS_SANDBOX_IMAGE` | `"python:3.12-slim"` | "docker": the image workspace code runs in |
| `AGENTCANVAS_EGRESS_PROXY` |  | sandbox.json egress allow-lists (docker, remote): sandboxed code reaches the network only through this proxy (`agentcanvas.sandbox.egress`), which admits the hosts of the call's signed list. "remote": its address as the sandbox sees it (e.g. egress:3128); "docker": unset starts and uses the `agentcanvas-egress` container |
| `AGENTCANVAS_EGRESS_PROXY_RESOLVE` | `False` | "remote": hand the sandbox the proxy's IPv4 address instead of its name, so the sandbox needs no DNS (Kubernetes: a Service's ClusterIP is the same from every pod; not in compose, where the API sees the proxy on another network) |
| `AGENTCANVAS_EGRESS_SECRET` |  | signs each call's list; shared with the proxy (docker: derived when unset) |
| `AGENTCANVAS_EGRESS_ALLOW_PRIVATE` | `False` | the proxy reaches private addresses (10.x, 172.16.x, the database, 127.x) only when the operator says so: a project's sandbox.json cannot open them. Link-local and cloud metadata addresses are never reached. |
| `AGENTCANVAS_LANGSMITH_SNAPSHOT` |  | snapshot name/id to boot from; None = LangSmith's default snapshot |
| `AGENTCANVAS_LANGSMITH_SANDBOX_ENDPOINT` |  | None = the SDK default (LANGSMITH_ENDPOINT) |
| `AGENTCANVAS_LANGSMITH_IDLE_S` | `600` | idle sandboxes stop after this (a multiple of 60) |
| `AGENTCANVAS_RECURSION_LIMIT` | `50` | steps one run may take before it stops (LangGraph's recursion limit) |
| `AGENTCANVAS_COPILOT_ENGINE` | `"loop"` | the Copilot's engine: our own tool loop, or LangChain's Deep Agents harness (the loop stays the default until Deep Agents has passed the same suite and the real-model evaluation) |
| `AGENTCANVAS_CORS_ORIGINS` | `['http://localhost:5173']` | browser origins allowed to call the API |
| `AGENTCANVAS_PUBLIC_URL` |  | where people reach the web app, e.g. https://canvas.example.com: OAuth sign-ins to app connectors return to <public_url>/oauth/callback and (https only) authorization servers fetch our client metadata document from <public_url>/api/v1/oauth/client-metadata.json. Unset: the first CORS origin (the development server). |
| `AGENTCANVAS_REFUSE_CLIENTS` | `[]` | networks whose requests get 403: the one the API shares with the sandbox service (compose: sandbox-control), so workspace code there cannot call the API from inside |
| `AGENTCANVAS_IGNORE_ORG_NETWORKS` | `False` | an emergency switch: true ignores every organisation's IP allow-list, for an admin locked out by their own (say, their address changed). Switch it off again once the list is fixed. |
| `AGENTCANVAS_LOG_JSON` | `False` | one JSON object per log line (for a log collector) instead of readable text |
| `AGENTCANVAS_METRICS_ENABLED` | `True` | Prometheus at GET /metrics |
| `AGENTCANVAS_RETENTION_DAYS` | `90` | conversations (threads, their checkpoints and runs) are deleted this many days after their last activity unless the organisation sets its own; 0 keeps them forever |
| `AGENTCANVAS_AUDIT_RETENTION_DAYS` | `400` | audit-log rows older than this are deleted; 0 keeps them forever |
| `AGENTCANVAS_MEMORY_RETENTION_DAYS` | `0` | long-term memories not updated for this long are deleted; 0 (default): kept |
| `AGENTCANVAS_EVENT_DEDUP_DAYS` | `30` | an event handler's dedup values (`@on(dedup_key=...)`) are kept this many days: the same value delivered again later runs the handler again. Never longer than the organisation's conversation retention; 0 = only that |
| `AGENTCANVAS_APPROVAL_EXPIRY_DAYS` | `90` | a decision nobody gave for this many days expires: its run is cancelled and the conversation then follows the conversation retention like any other, unless its step escalates it to a role (then it waits as long again). 0 = approvals wait forever (and keep their conversations forever) |
| `AGENTCANVAS_ORG_DELETION_GRACE_DAYS` | `7` | an organisation an admin deletes is suspended at once and deleted for good this many days later; until then an admin can take the deletion back |
| `AGENTCANVAS_OFFBOARDING_INTERVAL_S` | `60` | how often each server looks for organisations due for deletion and exports to build (a server also starts an export it was asked for at once) |
| `AGENTCANVAS_ORG_EXPORT_KEEP_HOURS` | `24` | an organisation export is kept this many hours after it is built, then deleted; it can be downloaded once |
| `AGENTCANVAS_MEMORY_EMBEDDING` | `""` | long-term memory's semantic index: "builtin" or "<provider>:<model>"; empty (default): by words. One size of vector per server (the pgvector column), so one model for every project; a model other than the built-in or a well-known hosted one needs its size in `memory_embedding_dims` (0: measured at startup) |
| `AGENTCANVAS_MEMORY_EMBEDDING_DIMS` | `0` | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_MEMORY_EMBEDDING_BASE_URL` | `""` | a local server's address (LM Studio, Ollama) for `memory_embedding` |
| `AGENTCANVAS_SSO_GROUPS_ADD_MEMBERS` |  | whether an organisation's groups-to-roles table may add people who are not its members; unset: only when the server has one organisation, so one tenant's admin cannot enrol every person of another |
| `AGENTCANVAS_OCR` | `"auto"` | OCR for scanned PDFs and pictures added to a knowledge base: "auto" (default) uses Tesseract when it is installed (the Docker image has it), "off" never runs it. Languages are Tesseract codes joined by "+", e.g. "eng+deu". |
| `AGENTCANVAS_OCR_LANGUAGES` | `"eng"` | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_OCR_MAX_PAGES` | `100` | pages OCR reads in one file; the rest are left out and the title says so |
| `AGENTCANVAS_OCR_PAGE_TIMEOUT_S` | `60` | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_DB_POOL_SIZE` | `10` | control-database connections per API process (Postgres): pool_size kept open, up to max_overflow more |
| `AGENTCANVAS_DB_MAX_OVERFLOW` | `20` | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_RATE_LIMIT_PER_DEPLOYMENT` | `120` | requests per minute to the public API, counted across instances; 0 = no limit. Per deployment (its API key) and per webhook trigger, and per project over all its deployments and webhooks together. `rate_limit_by_environment` overrides the per-deployment limit for a deployment environment, e.g. {"dev": 30}. |
| `AGENTCANVAS_RATE_LIMIT_PER_WEBHOOK` | `120` | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_RATE_LIMIT_PER_PROJECT` | `600` | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_RATE_LIMIT_BY_ENVIRONMENT` | `{}` | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_RATE_LIMIT_PER_SHARE_CLIENT` | `30` | share links: requests per minute from one client address to one link, on top of the deployment's and project's limits, so one anonymous visitor cannot use up a deployment's whole budget |
| `AGENTCANVAS_MAX_CONCURRENT_RUNS` | `0` | runs in flight at once in one organisation, across every server; past it a run waits in the organisation's queue (highest priority, then oldest, first). 0 = no limit. An organisation may set a lower one. |
| `AGENTCANVAS_MAX_COST_USD_DAY` | `0` | spending caps for each organisation, in USD per UTC day and per calendar month (UTC): what all its runs (drafts, debug runs, experiments, deployments, triggers), the Copilot and AI judges spent on models with a price. At a cap, new runs are refused (429) and runs in flight stop. 0 = no cap. An organisation may set a lower one, and one per project. |
| `AGENTCANVAS_MAX_COST_USD_MONTH` | `0` | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_COST_WARN_PERCENT` | `80` | the share of a spending cap, in percent, at which a cost alert is raised (reaching the cap always raises one). An organisation may choose its own. |
| `AGENTCANVAS_MAX_TOKEN_DAYS` | `0` | the longest an API token may live, in days; 0 = no limit (a token may also never expire). An organisation may set a shorter one. |
| `AGENTCANVAS_APPROVE_OWN_RUNS` | `True` | false: nobody may approve or answer an Inbox item raised by a run they started (four-eyes approvals). An organisation may switch it off for itself, never on when the server has it off. |
| `AGENTCANVAS_SMTP_HOST` |  | the mail server approval notifications are sent through: its host (none: e-mail is not offered), port, how the connection is secured ("starttls", port 587; "tls", port 465; "none", only for a relay on a private network) and its sign-in. An organisation's admin switches e-mail on under Settings > Organisation > Notifications |
| `AGENTCANVAS_SMTP_PORT` | `587` | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_SMTP_SECURITY` | `"starttls"` | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_SMTP_USERNAME` |  | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_SMTP_PASSWORD` |  | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_SMTP_FROM` |  | the sender, e.g. "AgentCanvas <approvals@example.com>"; required with a host |
| `AGENTCANVAS_NOTIFY_INTERVAL_S` | `15` | how often each server looks for approvals to announce and for notifications to send or retry (seconds) |
| `AGENTCANVAS_NOTIFY_ALLOW_PRIVATE` | `False` | notification webhooks (Slack, an organisation's own) may reach private addresses, e.g. an internal relay; link-local and cloud metadata addresses never |
| `AGENTCANVAS_MAX_QUEUE_WAIT_S` | `3600` | how long a run may wait in that queue before it gives up with an error (seconds); 0 = as long as it takes |
| `AGENTCANVAS_RETENTION_INTERVAL_S` | `3600` | how often the retention job looks for expired conversations |
| `AGENTCANVAS_CRON_CATCHUP_S` | `3600` | a cron occurrence no scheduler ticked in (a restart, an outage) fires once when ticking resumes, if it is no older than this; 0 = only the minute a scheduler ticks in |
| `AGENTCANVAS_MAX_UPLOAD_MB` | `10` | the largest file a person may attach to a run (Run tab, share links, inline in API calls), in MB; at most 20, the largest request body the web server accepts (infra/nginx.conf) |
| `AGENTCANVAS_MAX_PROJECT_FILES_MB` | `1024` | all of a project's attached files; 0 = no limit |
| `AGENTCANVAS_DRAIN_GRACE_S` | `20.0` | safe redeploys: from SIGTERM, how long running runs get to finish before they are handed over (keep 5 + max(30, it) + 25 below the pod's terminationGracePeriodSeconds), and how often instances look for runs to continue |
| `AGENTCANVAS_HANDOVER_INTERVAL_S` | `10.0` | As the row above (one comment in settings.py covers these). |
| `AGENTCANVAS_ORPHAN_AFTER_S` | `60.0` | a running run whose instance has not refreshed it for this long lost its instance and is handed over |

## Read directly from the environment

These are read where they are used rather than through the server's settings:

| Variable | Read in |
|---|---|
| `AGENTCANVAS_ADAPTERS` | `agentcanvas/registry/adapters.py` |
| `AGENTCANVAS_CREWAI_PYTHON` | `agentcanvas/adapters/crewai/__init__.py` |
| `AGENTCANVAS_DOCS_DIR` | `agentcanvas/copilot/docs.py` |
| `AGENTCANVAS_GIT_SHA` | `agentcanvas/version.py` |
| `AGENTCANVAS_IN_DOCKER` | `agentcanvas/adapters/setup.py` |
| `AGENTCANVAS_KNOWLEDGE_DIR` | `agentcanvas/adapters/crewai/packager.py`, `agentcanvas/adapters/flowpackager.py`, `agentcanvas/adapters/google_adk/packager.py`, `agentcanvas/adapters/langchain/nodes.py`, `agentcanvas/adapters/mastra/packager.py`, `agentcanvas/adapters/openai_agents/packager.py` |
| `AGENTCANVAS_LOCAL_MODELS_HOST` | `agentcanvas/adapters/langchain/nodes.py`, `agentcanvas/copilot/evals.py`, `agentcanvas/registry/__init__.py` |
| `AGENTCANVAS_MASTRA_DIR` | `agentcanvas/adapters/mastra/__init__.py` |
| `AGENTCANVAS_MODEL_PRICES` | `agentcanvas/registry/__init__.py` |
| `AGENTCANVAS_NODE` | `agentcanvas/adapters/mastra/__init__.py` |
| `AGENTCANVAS_PERSISTENCE_POOL_SIZE` | `agentcanvas/adapters/langchain/persistence.py` |
| `AGENTCANVAS_SANDBOX_CONCURRENCY` | `agentcanvas/sandbox/service.py` |
| `AGENTCANVAS_SANDBOX_SWEEP` | `agentcanvas/sandbox/service.py` |
