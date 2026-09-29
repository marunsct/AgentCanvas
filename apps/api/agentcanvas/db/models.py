"""Control-database models (build-guide/05 §3; authoritative DDL mirrored in infra/schema.sql).

Portable across PostgreSQL (production) and SQLite (local edition, tests): JSON columns use
JSONB on Postgres, `inet` becomes text, `bytea` becomes a blob.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

Json = JSON().with_variant(JSONB(), "postgresql")
BigId = BigInteger().with_variant(Integer(), "sqlite")  # SQLite autoincrement needs INTEGER


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: Json, list[Any]: Json}


def _now() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False)


class Organization(Base):
    __tablename__ = "organization"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class AppUser(Base):
    __tablename__ = "app_user"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    oidc_subject: Mapped[str] = mapped_column(Text, unique=True)
    email: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class Membership(Base):
    __tablename__ = "membership"
    __table_args__ = (CheckConstraint("role IN ('admin','builder','reviewer','viewer')", name="membership_role"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(Text)


class Project(Base):
    __tablename__ = "project"
    __table_args__ = (UniqueConstraint("org_id", "name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class WorkflowRow(Base):
    __tablename__ = "workflow"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    draft_ir: Mapped[dict[str, Any]]
    layout: Mapped[dict[str, Any]] = mapped_column(Json, server_default=text("'{}'"), default=dict)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"), default=1)
    ir_hash: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), onupdate=func.now(), nullable=False
    )


class WorkflowVersion(Base):
    __tablename__ = "workflow_version"
    __table_args__ = (UniqueConstraint("workflow_id", "semver"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    semver: Mapped[str] = mapped_column(Text)
    ir: Mapped[dict[str, Any]]
    layout: Mapped[dict[str, Any]]
    ir_hash: Mapped[str] = mapped_column(Text)
    workspace_commit: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, server_default=text("''"), default="")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()


class TestCase(Base):
    """A saved test case of a workflow (FR-EVAL-01): an input and what the run must produce."""

    __tablename__ = "test_case"
    __test__ = False  # not a pytest class
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    input: Mapped[dict[str, Any]] = mapped_column(Json, server_default=text("'{}'"), default=dict)
    context: Mapped[dict[str, Any] | None] = mapped_column(Json)
    mocks: Mapped[dict[str, Any]] = mapped_column(Json, server_default=text("'{}'"), default=dict)
    pins: Mapped[dict[str, Any]] = mapped_column(Json, server_default=text("'{}'"), default=dict)
    expect: Mapped[dict[str, Any]] = mapped_column(Json, server_default=text("'{}'"), default=dict)
    last_result: Mapped[dict[str, Any] | None] = mapped_column(Json)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()


class Deployment(Base):
    __tablename__ = "deployment"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    version_id: Mapped[str] = mapped_column(ForeignKey("workflow_version.id"))
    environment: Mapped[str] = mapped_column(Text, server_default=text("'prod'"), default="prod")
    api_key_hash: Mapped[str] = mapped_column(Text)
    key_prefix: Mapped[str | None] = mapped_column(Text)  # shown in the UI to tell keys apart
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    created_at: Mapped[datetime] = _now()


class Thread(Base):
    __tablename__ = "thread"
    __table_args__ = (
        CheckConstraint("status IN ('idle','running','interrupted','error')", name="thread_status"),
        Index("thread_workflow_idx", "workflow_id", "updated_at"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)  # == LangGraph thread_id
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey("deployment.id"))
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'idle'"), default="idle")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), onupdate=func.now(), nullable=False
    )


class Run(Base):
    __tablename__ = "run"
    __table_args__ = (
        CheckConstraint("mode IN ('normal','debug')", name="run_mode"),
        CheckConstraint("status IN ('running','success','interrupted','error','cancelled')", name="run_status"),
        Index("run_thread_idx", "thread_id", "started_at"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("thread.id", ondelete="CASCADE"))
    workflow_version_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_version.id"))
    ir_hash: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text, server_default=text("'normal'"), default="normal")
    status: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"), default=Decimal(0))
    trace_url: Mapped[str | None] = mapped_column(Text)
    # run context (Runtime.context) — resumes reuse it: LangGraph does not checkpoint the context, and a
    # reviewer resuming someone else's run must not become the run's user (per-user memory namespaces)
    context: Mapped[dict[str, Any] | None] = mapped_column(Json)
    # FR-X-13 cassette of a fresh run: its input, each model resource's replies, status, route and output
    recording: Mapped[dict[str, Any] | None] = mapped_column(Json)
    started_at: Mapped[datetime] = _now()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InterruptTask(Base):
    __tablename__ = "interrupt_task"
    __table_args__ = (
        CheckConstraint("kind IN ('tool_approval','approval','input')", name="interrupt_kind"),
        CheckConstraint("status IN ('pending','decided','expired')", name="interrupt_status"),
        Index("interrupt_pending_idx", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)  # LangGraph interrupt id
    thread_id: Mapped[str] = mapped_column(ForeignKey("thread.id", ondelete="CASCADE"))
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"))
    canvas_node_id: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]]
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"), default="pending")
    assignee_role: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[dict[str, Any] | None] = mapped_column(Json)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Secret(Base):
    __tablename__ = "secret"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()


class Connection(Base):
    __tablename__ = "connection"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]]


class Trigger(Base):
    __tablename__ = "trigger"
    __table_args__ = (CheckConstraint("kind IN ('webhook','cron')", name="trigger_kind"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey("deployment.id"))
    kind: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]]
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    created_at: Mapped[datetime] = _now()


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("audit_org_time_idx", "org_id", "created_at"),)
    id: Mapped[int] = mapped_column(BigId, primary_key=True, autoincrement=True)
    org_id: Mapped[str | None] = mapped_column(ForeignKey("organization.id"))
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    action: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any]] = mapped_column(Json, server_default=text("'{}'"), default=dict)
    ip: Mapped[str | None] = mapped_column(String(45))
    created_at: Mapped[datetime] = _now()


class EventLog(Base):
    """Events delivered to a project's handlers (FR-X-22): what came in, what the handlers asked for."""

    __tablename__ = "event_log"
    __table_args__ = (Index("event_log_project_idx", "project_id", "created_at"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, server_default=text("'api'"), default="api")
    data: Mapped[dict[str, Any]] = mapped_column(Json, server_default=text("'{}'"), default=dict)
    status: Mapped[str] = mapped_column(Text)  # handled | ignored | error
    results: Mapped[list[Any]] = mapped_column(Json, server_default=text("'[]'"), default=list)
    created_at: Mapped[datetime] = _now()


class EventDedup(Base):
    """One row per (handler, dedup value): a redelivered event does not run the handler twice."""

    __tablename__ = "event_dedup"
    __table_args__ = (UniqueConstraint("project_id", "handler", "value"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    handler: Mapped[str] = mapped_column(Text)
    value: Mapped[str] = mapped_column(Text)
    event_id: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
