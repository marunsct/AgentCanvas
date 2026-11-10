"""Process configuration from environment variables / `.env` (build-guide/05 §2)."""

from __future__ import annotations

from functools import cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="AGENTCANVAS_")

    env: Literal["dev", "test", "staging", "prod"] = "dev"  # staging and prod refuse development defaults
    # hosted = a multi-tenant service: blocks that reach the server or private networks are refused
    edition: Literal["dev", "hosted"] = "dev"
    # SQLAlchemy URL of the control DB. postgresql://… (production) or sqlite+aiosqlite:///… (local edition).
    database_url: str = Field(default="sqlite+aiosqlite:///./agentcanvas.db", validation_alias="DATABASE_URL")
    # LangGraph checkpointer/store location; defaults to the control DB ("memory" = ephemeral, tests only)
    persistence_url: str | None = None
    auto_migrate: bool = True  # run Alembic `upgrade head` at startup; off: refuse a database not at head
    seed_dev_data: bool = True  # create the default organisation/project/user when missing
    secret_key: str = "dev-only-change-me"  # signs sessions and links; staging and prod refuse the default
    # master key that wraps each organisation's data key (see agentcanvas/security/kms.py): "" derives it from
    # secret_key (development), or file:<path>, aws:<key ARN>, gcp:<key name>; previous keys still unwrap
    kms_key: str = ""
    kms_previous_keys: list[str] = []  # earlier master keys, still used to unwrap data keys after a rotation
    # Authentication. dev = every request is the seeded development admin (refused in staging/prod);
    # oidc = bearer JWTs from the identity provider, checked against its JWKS.
    # matura = bearer JWTs from Matura (oidc_issuer = its realm, oidc_audience = this app's client id): the token
    # names the organisation (`tid`) and its permissions (`perms`); members and roles are managed in Matura.
    auth_mode: Literal["dev", "oidc", "matura"] = "dev"
    oidc_issuer: str | None = None  # e.g. https://login.example.com/realms/acme (the token's `iss`)
    oidc_audience: str | None = None  # the token's `aud` the API accepts
    oidc_client_id: str | None = None  # public SPA client id, handed to the web app by /v1/auth/config
    oidc_jwks_url: str | None = None  # None = from the issuer's discovery document
    oidc_scopes: str = "openid profile email offline_access"  # offline_access: silent token refresh
    oidc_email_claim: str = "email"  # the token claim that holds the person's email
    # treat the email claim as verified when the provider sends no `email_verified` (e.g. a corporate IdP that
    # owns the domain); leave off for providers where users choose their own email
    oidc_trust_email: bool = False
    oidc_jwks_cache_s: int = 3600  # how long the provider's signing keys are kept before they are fetched again
    oidc_leeway_s: int = 30  # clock skew tolerated on exp/nbf/iat
    # what every sign-in must show about how the person signed in: an `acr` (authentication level, as the identity
    # provider names it, e.g. Keycloak's "2") on this list, or an `amr` (methods, e.g. "mfa", "hwk") on that one. Empty
    # (default): no requirement. A sign-in without it is asked to sign in again at the provider, with these acr_values
    oidc_required_acr: list[str] = []
    oidc_required_amr: list[str] = []
    # what counts as multi-factor for an organisation that requires it (Settings > Organisation > Security): an `acr`
    # on the first list or an `amr` on the second. Entra ID, Okta and Auth0 put "mfa" in `amr`; Keycloak needs an
    # acr level configured (and listed here)
    oidc_mfa_acr: list[str] = []
    oidc_mfa_amr: list[str] = ["mfa"]
    # Matura (auth_mode = matura). The API URL, app slug and client secret read the change feed; the webhook
    # secret checks POST /v1/matura/events; the portal is where admins manage members and roles.
    matura_url: str | None = None  # e.g. https://matura.example.com
    matura_app: str = "agentcanvas"
    matura_client_secret: str | None = None
    matura_webhook_secret: str | None = None
    matura_portal_url: str | None = None  # overrides the portal's address; default <matura_public_url>/portal/
    matura_public_url: str | None = None  # where people reach Matura (its MATURA_PUBLIC_URL), for portal links
    # where security-relevant audit entries are POSTed, signed with the webhook secret (placeholder until
    # Matura has an audit ingest API); unset = kept in AgentCanvas's own audit log only
    matura_audit_url: str | None = None
    # security-relevant audit entries, also POSTed here as signed CloudEvents (any sign-in mode); each one is also
    # always logged as `audit.event`. The secret signs them (AgentCanvas-Signature) and is needed with the URL.
    audit_sink_url: str | None = None
    audit_sink_secret: str | None = None
    # security-relevant audit entries, also sent to Splunk's HTTP Event Collector: the full URL (e.g.
    # https://splunk.example.com:8088/services/collector/event) and an HEC token, needed with the URL
    audit_splunk_hec_url: str | None = None
    audit_splunk_hec_token: str | None = None
    # forward every audit entry (every change anyone makes), not only the security-relevant ones
    audit_forward_all: bool = False
    # a delivery that keeps failing is retried with a growing delay (up to an hour) this many times, about 15 hours
    # in all with the default, then kept as undelivered (dead) for an operator to retry
    audit_forward_max_attempts: int = Field(default=24, ge=1)
    audit_forward_interval_s: float = Field(default=2.0, gt=0)  # how often each server looks for entries to deliver
    matura_sync_interval_s: int = 3600  # how often the change feed is read (webhooks bring changes in between)
    # emails that become admins of the default organisation on first sign-in (bootstraps an empty install)
    bootstrap_admin_emails: list[str] = []
    allow_local_mcp: bool = False  # app connectors that start a local process (stdio MCP servers); local edition
    workspaces_dir: str = "./workspaces"  # one git repository per project
    # how workspace code runs: in the API process (dev), a child process, a Docker container or a LangSmith
    # Sandbox (credentials from LANGSMITH_API_KEY)
    script_execution: Literal["inprocess", "subprocess", "docker", "langsmith", "remote"] = "inprocess"
    # staging and prod refuse to run workspace code as the API's own user (inprocess, subprocess) unless this says so:
    # for a server only trusted people edit
    allow_unisolated_scripts: bool = False
    sandbox_url: str | None = (
        None  # "remote": the sandbox service (`agentcanvas.sandbox.service`), e.g. http://sandbox:8090
    )
    sandbox_token: str | None = None  # the bearer token the service expects
    sandbox_image: str = "python:3.12-slim"  # "docker": the image workspace code runs in
    # local (stdio) app connectors run in the sandbox instead of on the API server: a sandbox of their own for each
    # run (or conversation), with the project's sandbox.json egress list and limits. Needs script execution docker
    # or remote, and AGENTCANVAS_ALLOW_LOCAL_MCP; the hosted edition allows only the gallery's connectors marked to
    # run in the sandbox
    sandbox_mcp: bool = False
    # "docker": the image connectors run in (unset: AGENTCANVAS_SANDBOX_IMAGE); it needs Python 3 and the programs
    # the connectors start, such as the API image built with WITH_BROWSER=true
    sandbox_mcp_image: str | None = None
    sandbox_mcp_url: str | None = None  # "remote": a sandbox service for connectors (unset: AGENTCANVAS_SANDBOX_URL)
    sandbox_mcp_sessions: int = Field(default=8, ge=1, le=1000)  # connector sandboxes one API server runs at once
    # sandbox.json egress allow-lists (docker, remote): sandboxed code reaches the network only through this proxy
    # (`agentcanvas.sandbox.egress`), which admits the hosts of the call's signed list. "remote": its address as the
    # sandbox sees it (e.g. egress:3128); "docker": unset starts and uses the `agentcanvas-egress` container
    egress_proxy: str | None = None
    # "remote": hand the sandbox the proxy's IPv4 address instead of its name, so the sandbox needs no DNS (Kubernetes:
    # a Service's ClusterIP is the same from every pod; not in compose, where the API sees the proxy on another network)
    egress_proxy_resolve: bool = False
    egress_secret: str | None = None  # signs each call's list; shared with the proxy (docker: derived when unset)
    # the proxy reaches private addresses (10.x, 172.16.x, the database, 127.x) only when the operator says so: a
    # project's sandbox.json cannot open them. Link-local and cloud metadata addresses are never reached.
    egress_allow_private: bool = False
    langsmith_snapshot: str | None = None  # snapshot name/id to boot from; None = LangSmith's default snapshot
    langsmith_sandbox_endpoint: str | None = None  # None = the SDK default (LANGSMITH_ENDPOINT)
    langsmith_idle_s: int = 600  # idle sandboxes stop after this (a multiple of 60)
    recursion_limit: int = 50  # steps one run may take before it stops (LangGraph's recursion limit)
    # the Copilot's engine: our own tool loop, or LangChain's Deep Agents harness (the loop stays the default until Deep
    # Agents has passed the same suite and the real-model evaluation)
    copilot_engine: Literal["loop", "deepagents"] = "loop"
    cors_origins: list[str] = ["http://localhost:5173"]  # browser origins allowed to call the API
    # where people reach the web app, e.g. https://canvas.example.com: OAuth sign-ins to app connectors return to
    # <public_url>/oauth/callback and (https only) authorization servers fetch our client metadata document from
    # <public_url>/api/v1/oauth/client-metadata.json. Unset: the first CORS origin (the development server).
    public_url: str | None = None
    # networks whose requests get 403: the one the API shares with the sandbox service (compose: sandbox-control), so
    # workspace code there cannot call the API from inside
    refuse_clients: list[str] = []
    # an emergency switch: true ignores every organisation's IP allow-list, for an admin locked out by their own (say,
    # their address changed). Switch it off again once the list is fixed.
    ignore_org_networks: bool = False
    log_json: bool = False  # one JSON object per log line (for a log collector) instead of readable text
    metrics_enabled: bool = True  # Prometheus at GET /metrics
    # conversations (threads, their checkpoints and runs) are deleted this many days after their last activity
    # unless the organisation sets its own; 0 keeps them forever
    retention_days: int = 90
    audit_retention_days: int = 400  # audit-log rows older than this are deleted; 0 keeps them forever
    memory_retention_days: int = 0  # long-term memories not updated for this long are deleted; 0 (default): kept
    # an event handler's dedup values (`@on(dedup_key=...)`) are kept this many days: the same value delivered again
    # later runs the handler again. Never longer than the organisation's conversation retention; 0 = only that
    event_dedup_days: int = Field(default=30, ge=0)
    # a decision nobody gave for this many days expires: its run is cancelled and the conversation then follows the
    # conversation retention like any other, unless its step escalates it to a role (then it waits as long again).
    # 0 = approvals wait forever (and keep their conversations forever)
    approval_expiry_days: int = Field(default=90, ge=0)
    # an organisation an admin deletes is suspended at once and deleted for good this many days later; until then an
    # admin can take the deletion back
    org_deletion_grace_days: int = Field(default=7, ge=1, le=365)
    # how often each server looks for organisations due for deletion and exports to build (a server also starts an
    # export it was asked for at once)
    offboarding_interval_s: float = Field(default=60, gt=0)
    # an organisation export is kept this many hours after it is built, then deleted; it can be downloaded once
    org_export_keep_hours: int = Field(default=24, ge=1, le=720)
    # long-term memory's semantic index: "builtin" or "<provider>:<model>"; empty (default): by words.
    # One size of vector per server (the pgvector column), so one model for every project; a model other than the
    # built-in or a well-known hosted one needs its size in `memory_embedding_dims` (0: measured at startup)
    memory_embedding: str = ""
    memory_embedding_dims: int = 0
    memory_embedding_base_url: str = ""  # a local server's address (LM Studio, Ollama) for `memory_embedding`
    # whether an organisation's groups-to-roles table may add people who are not its members; unset: only
    # when the server has one organisation, so one tenant's admin cannot enrol every person of another
    sso_groups_add_members: bool | None = None
    # OCR for scanned PDFs and pictures added to a knowledge base: "auto" (default) uses Tesseract when it is installed
    # (the Docker image has it), "off" never runs it. Languages are Tesseract codes joined by "+", e.g. "eng+deu".
    ocr: str = "auto"
    ocr_languages: str = "eng"
    ocr_max_pages: int = 100  # pages OCR reads in one file; the rest are left out and the title says so
    ocr_page_timeout_s: int = 60
    # control-database connections per API process (Postgres): pool_size kept open, up to max_overflow more
    db_pool_size: int = 10
    db_max_overflow: int = 20
    # requests per minute to the public API, counted across instances; 0 = no limit. Per deployment
    # (its API key) and per webhook trigger, and per project over all its deployments and webhooks together.
    # `rate_limit_by_environment` overrides the per-deployment limit for a deployment environment, e.g. {"dev": 30}.
    rate_limit_per_deployment: int = 120
    rate_limit_per_webhook: int = 120
    rate_limit_per_project: int = 600
    rate_limit_by_environment: dict[str, int] = {}
    # share links: requests per minute from one client address to one link, on top of the deployment's and
    # project's limits, so one anonymous visitor cannot use up a deployment's whole budget
    rate_limit_per_share_client: int = 30
    # runs in flight at once in one organisation, across every server; past it a run waits in the organisation's
    # queue (highest priority, then oldest, first). 0 = no limit. An organisation may set a lower one.
    max_concurrent_runs: int = Field(default=0, ge=0)
    # spending caps for each organisation, in USD per UTC day and per calendar month (UTC): what all its runs (drafts,
    # debug runs, experiments, deployments, triggers), the Copilot and AI judges spent on models with a price. At a
    # cap, new runs are refused (429) and runs in flight stop. 0 = no cap. An organisation may set a lower one, and
    # one per project.
    max_cost_usd_day: float = Field(default=0, ge=0)
    max_cost_usd_month: float = Field(default=0, ge=0)
    # the share of a spending cap, in percent, at which a cost alert is raised (reaching the cap always raises one).
    # An organisation may choose its own.
    cost_warn_percent: int = Field(default=80, ge=1, le=100)
    # the longest an API token may live, in days; 0 = no limit (a token may also never expire). An organisation may set
    # a shorter one.
    max_token_days: int = Field(default=0, ge=0, le=3650)
    # false: nobody may approve or answer an Inbox item raised by a run they started (four-eyes approvals). An
    # organisation may switch it off for itself, never on when the server has it off.
    approve_own_runs: bool = True
    # the mail server approval notifications are sent through: its host (none: e-mail is not offered), port, how the
    # connection is secured ("starttls", port 587; "tls", port 465; "none", only for a relay on a private network) and
    # its sign-in. An organisation's admin switches e-mail on under Settings > Organisation > Notifications
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: Literal["starttls", "tls", "none"] = "starttls"
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None  # the sender, e.g. "AgentCanvas <approvals@example.com>"; required with a host
    # how often each server looks for approvals to announce and for notifications to send or retry (seconds)
    notify_interval_s: float = Field(default=15, gt=0)
    # an approval or question still waiting this many hours before it expires or escalates is announced once more,
    # on the same channels; 0 = no reminders. An item that waits less than twice this in all gets none (its first
    # message is recent)
    approval_reminder_hours: int = Field(default=24, ge=0, le=24 * 365)
    # notification webhooks (Slack, an organisation's own) may reach private addresses, e.g. an internal relay;
    # link-local and cloud metadata addresses never
    notify_allow_private: bool = False
    # how long a run may wait in that queue before it gives up with an error (seconds); 0 = as long as it takes
    max_queue_wait_s: float = Field(default=3600, ge=0)
    retention_interval_s: int = 3600  # how often the retention job looks for expired conversations
    # how often each server looks for projects whose weekly run digest (last week's failures, spend and latency, on
    # the Runs page) is due, in seconds; 0 = no digests are made
    digest_interval_s: float = Field(default=3600, ge=0)
    # a cron occurrence no scheduler ticked in (a restart, an outage) fires once when ticking resumes, if it is no
    # older than this; 0 = only the minute a scheduler ticks in
    cron_catchup_s: int = Field(default=3600, ge=0)
    # the largest file a person may attach to a run (Run tab, share links, inline in API calls), in MB; at most 20,
    # the largest request body the web server accepts (infra/nginx.conf)
    max_upload_mb: float = Field(default=10, gt=0, le=20)
    max_project_files_mb: float = Field(default=1024, ge=0)  # all of a project's attached files; 0 = no limit
    # safe redeploys: from SIGTERM, how long running runs get to finish before they are handed over (keep
    # 5 + max(30, it) + 25 below the pod's terminationGracePeriodSeconds), and how often instances look for runs
    # to continue
    drain_grace_s: float = 20.0
    handover_interval_s: float = 10.0
    # a running run whose instance has not refreshed it for this long lost its instance and is handed over
    orphan_after_s: float = 60.0

    @property
    def isolates_workspace_code(self) -> bool:
        """Workspace code runs outside the API process (a sandbox), so blocks that would import it into the API are
        refused (E041). Staging and prod count as isolating unless the operator trusts every builder (S-8)."""
        if self.script_execution != "inprocess":
            return True
        return self.env in ("staging", "prod") and not self.allow_unisolated_scripts

    @field_validator("refuse_clients")
    @classmethod
    def _networks(cls, value: list[str]) -> list[str]:
        import ipaddress

        for network in value:
            ipaddress.ip_network(network, strict=False)  # a typo stops the start, not a request
        return value


@cache
def get_settings() -> Settings:
    return Settings()
