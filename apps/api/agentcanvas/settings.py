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
    recursion_limit: int = 50
    cors_origins: list[str] = ["http://localhost:5173"]
    log_json: bool = False


@cache
def get_settings() -> Settings:
    return Settings()
