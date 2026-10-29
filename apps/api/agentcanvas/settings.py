"""Process configuration from environment variables / `.env` (build-guide/05 §2)."""

from __future__ import annotations

from functools import cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="AGENTCANVAS_")

    env: Literal["dev", "test", "staging", "prod"] = "dev"  # AGENTCANVAS_ENV
    edition: Literal["dev", "hosted"] = "dev"  # hosted = multi-tenant service (E041 etc.)
    # SQLAlchemy URL of the control DB. postgresql://… (production) or sqlite+aiosqlite:///… (local edition).
    database_url: str = Field(default="sqlite+aiosqlite:///./agentcanvas.db", validation_alias="DATABASE_URL")
    # LangGraph checkpointer/store location; defaults to the control DB ("memory" = ephemeral, tests only)
    persistence_url: str | None = None
    auto_migrate: bool = True  # run Alembic `upgrade head` at startup
    seed_dev_data: bool = True  # create the default organisation/project/user when missing
    secret_key: str = "dev-only-change-me"
    # master key that wraps each organisation's data key (see agentcanvas/security/kms.py): "" derives it from
    # secret_key (development), or file:<path>, aws:<key ARN>, gcp:<key name>; previous keys still unwrap
    kms_key: str = ""
    kms_previous_keys: list[str] = []
    # Authentication (M7-T1). dev = every request is the seeded development admin (refused in staging/prod);
    # oidc = bearer JWTs from the identity provider, checked against its JWKS.
    # matura = bearer JWTs from Matura (oidc_issuer = its realm, oidc_audience = this app's client id): the token
    # names the organisation (`tid`) and its permissions (`perms`); members and roles are managed in Matura (TS9a).
    auth_mode: Literal["dev", "oidc", "matura"] = "dev"
    oidc_issuer: str | None = None  # e.g. https://login.example.com/realms/acme (the token's `iss`)
    oidc_audience: str | None = None  # the token's `aud` the API accepts
    oidc_client_id: str | None = None  # public SPA client id, handed to the web app by /v1/auth/config
    oidc_jwks_url: str | None = None  # None = from the issuer's discovery document
    oidc_scopes: str = "openid profile email offline_access"  # offline_access: silent token refresh
    oidc_email_claim: str = "email"
    # treat the email claim as verified when the provider sends no `email_verified` (e.g. a corporate IdP that
    # owns the domain); leave off for providers where users choose their own email
    oidc_trust_email: bool = False
    oidc_jwks_cache_s: int = 3600
    oidc_leeway_s: int = 30  # clock skew tolerated on exp/nbf/iat
    # Matura (auth_mode = matura). The API URL, app slug and client secret read the change feed; the webhook
    # secret checks POST /v1/matura/events; the portal is where admins manage members and roles.
    matura_url: str | None = None  # e.g. https://matura.example.com
    matura_app: str = "agentcanvas"
    matura_client_secret: str | None = None
    matura_webhook_secret: str | None = None
    matura_portal_url: str | None = None  # overrides the portal's address; default <matura_public_url>/portal/
    matura_public_url: str | None = None  # where people reach Matura (its MATURA_PUBLIC_URL), for portal links
    # TS26: where security-relevant audit entries are POSTed, signed with the webhook secret (placeholder until
    # Matura has an audit ingest API); unset = kept in AgentCanvas's own audit log only
    matura_audit_url: str | None = None
    matura_sync_interval_s: int = 3600  # how often the change feed is read (webhooks bring changes in between)
    # emails that become admins of the default organisation on first sign-in (bootstraps an empty install)
    bootstrap_admin_emails: list[str] = []
    allow_local_mcp: bool = False
    workspaces_dir: str = "./workspaces"  # one git repository per project (M5-T1)
    # how workspace code runs (M5-T4): in the API process (dev), a child process, a Docker container or a LangSmith
    # Sandbox (spike SP-2; credentials from LANGSMITH_API_KEY)
    script_execution: Literal["inprocess", "subprocess", "docker", "langsmith", "remote"] = "inprocess"
    sandbox_url: str | None = (
        None  # "remote": the sandbox service (`agentcanvas.sandbox.service`), e.g. http://sandbox:8090
    )
    sandbox_token: str | None = None  # the bearer token the service expects
    sandbox_image: str = "python:3.12-slim"
    # sandbox.json egress allow-lists (docker, remote): sandboxed code reaches the network only through this proxy
    # (`agentcanvas.sandbox.egress`), which admits the hosts of the call's signed list. "remote": its address as the
    # sandbox sees it (e.g. egress:3128); "docker": unset starts and uses the `agentcanvas-egress` container
    egress_proxy: str | None = None
    egress_secret: str | None = None  # signs each call's list; shared with the proxy (docker: derived when unset)
    # the proxy reaches private addresses (10.x, 172.16.x, the database, 127.x) only when the operator says so: a
    # project's sandbox.json cannot open them. Link-local and cloud metadata addresses are never reached.
    egress_allow_private: bool = False
    langsmith_snapshot: str | None = None  # snapshot name/id to boot from; None = LangSmith's default snapshot
    langsmith_sandbox_endpoint: str | None = None  # None = the SDK default (LANGSMITH_ENDPOINT)
    langsmith_idle_s: int = 600  # idle sandboxes stop after this (a multiple of 60)
    recursion_limit: int = 50
    # the Copilot's engine: our own tool loop, or LangChain's Deep Agents harness (PLAN.md section 2; the loop stays the
    # default until Deep Agents has passed the same suite and the real-model evaluation)
    copilot_engine: Literal["loop", "deepagents"] = "loop"
    cors_origins: list[str] = ["http://localhost:5173"]
    log_json: bool = False
    metrics_enabled: bool = True  # Prometheus at GET /metrics (M8-T3)
    # conversations (threads, their checkpoints and runs) are deleted this many days after their last activity
    # unless the organisation sets its own (M8-T4); 0 keeps them forever
    retention_days: int = 90
    audit_retention_days: int = 400  # audit-log rows older than this are deleted; 0 keeps them forever (S12)
    memory_retention_days: int = 0  # long-term memories not updated for this long are deleted; 0 (default): kept
    # control-database connections per API process (Postgres): pool_size kept open, up to max_overflow more
    db_pool_size: int = 10
    db_max_overflow: int = 20
    # S12 (S30): requests per minute to the public API, counted across instances; 0 = no limit. Per deployment
    # (its API key) and per webhook trigger, and per project over all its deployments and webhooks together.
    # `rate_limit_by_environment` overrides the per-deployment limit for a deployment environment, e.g. {"dev": 30}.
    rate_limit_per_deployment: int = 120
    rate_limit_per_webhook: int = 120
    rate_limit_per_project: int = 600
    rate_limit_by_environment: dict[str, int] = {}
    # share links (S23): requests per minute from one client address to one link, on top of the deployment's and
    # project's limits, so one anonymous visitor cannot use up a deployment's whole budget
    rate_limit_per_share_client: int = 30
    retention_interval_s: int = 3600  # how often the retention job looks for expired conversations
    # FR-X-10 safe redeploy: on shutdown, how long running runs get to finish before they are handed over (keep it
    # below the pod's terminationGracePeriodSeconds), and how often instances look for runs to continue
    drain_grace_s: float = 20.0
    handover_interval_s: float = 10.0
    # a running run whose instance has not refreshed it for this long lost its instance and is handed over
    orphan_after_s: float = 60.0


@cache
def get_settings() -> Settings:
    return Settings()
