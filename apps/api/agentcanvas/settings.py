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
    allow_local_mcp: bool = False
    workspaces_dir: str = "./workspaces"  # one git repository per project (M5-T1)
    # how workspace code runs (M5-T4): in the API process (dev), a child process, a Docker container or a LangSmith
    # Sandbox (spike SP-2; credentials from LANGSMITH_API_KEY)
    script_execution: Literal["inprocess", "subprocess", "docker", "langsmith"] = "inprocess"
    sandbox_image: str = "python:3.12-slim"
    langsmith_snapshot: str | None = None  # snapshot name/id to boot from; None = LangSmith's default snapshot
    langsmith_sandbox_endpoint: str | None = None  # None = the SDK default (LANGSMITH_ENDPOINT)
    langsmith_idle_s: int = 600  # idle sandboxes stop after this (a multiple of 60)
    recursion_limit: int = 50
    cors_origins: list[str] = ["http://localhost:5173"]
    log_json: bool = False


@cache
def get_settings() -> Settings:
    return Settings()
