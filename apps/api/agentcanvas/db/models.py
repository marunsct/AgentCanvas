"""Control-database models (build-guide/05 §3; authoritative DDL mirrored in infra/schema.sql).

Portable across PostgreSQL (production) and SQLite (local edition, tests): JSON columns use
JSONB on Postgres, `inet` becomes text, `bytea` becomes a blob.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Dialect,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """A timestamp read back in UTC with its timezone: SQLite stores none, and a time without one reads as local
    time in a browser (a workflow changed a minute ago showed as "9 hours ago" in Tokyo)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        # SQLite keeps the wall clock and drops the offset: `?since=10:00+02:00` would compare as 10:00 UTC
        return value.astimezone(UTC) if value is not None and value.tzinfo is not None else value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


# None is SQL NULL, not the JSON value null: else `column IS NOT NULL` matches rows that hold nothing (a resume's
# recording, which a "the conversation's last fresh run" lookup then took for the run with the question)
Json = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
BigId = BigInteger().with_variant(Integer(), "sqlite")  # SQLite autoincrement needs INTEGER


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: Json, list[Any]: Json, datetime: UtcDateTime()}


def _now() -> Mapped[datetime]:
    return mapped_column(UtcDateTime(), server_default=text("CURRENT_TIMESTAMP"), nullable=False)


class Organization(Base):
    __tablename__ = "organization"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    # days a conversation is kept after its last activity (M8-T4); NULL = AGENTCANVAS_RETENTION_DAYS, 0 = forever
    retention_days: Mapped[int | None] = mapped_column(Integer)
    # where workflows are published (dev, staging, prod...); None: any name, the web app offers the usual three
    environments: Mapped[list[str] | None] = mapped_column(Json, nullable=True)
    # its own values for server settings (services/org_settings.py): local MCP, rate limits, two retentions
    settings: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    # OIDC groups claim -> roles (G-13): {"claim", "roles": {"<group>": "<role>"}, "unmatched": "keep"|"remove"} (see
    # services/sso_groups.py); SQL NULL: roles are managed by hand
    sso_groups: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    # SCIM provisioning (G-13): the SHA-256 of the identity provider's bearer token (NULL: SCIM is off), and its start
    scim_token_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    scim_token_prefix: Mapped[str | None] = mapped_column(Text)
    scim_token_created_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    created_at: Mapped[datetime] = _now()


class OrgDataKey(Base):
    """An organisation's data key, wrapped by the master key (KMS envelope encryption, S11)."""

    __tablename__ = "org_data_key"
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), primary_key=True)
    master_key_id: Mapped[str] = mapped_column(Text)  # which master key wrapped it ("local:…", "aws:…", "gcp:…")
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _now()
    rotated_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class AppUser(Base):
    __tablename__ = "app_user"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    oidc_subject: Mapped[str] = mapped_column(Text, unique=True)
    email: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(Text)
    preferences: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)  # theme, Simple mode...
    created_at: Mapped[datetime] = _now()


class Membership(Base):
    __tablename__ = "membership"
    __table_args__ = (CheckConstraint("role IN ('admin','builder','reviewer','viewer')", name="membership_role"),)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(Text)
    # false: an invitation to someone who already belongs to another organisation, until they accept it (S11)
    accepted: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime())  # the default organisation
    # SCIM (G-13): `active: false` suspends the membership without deleting it; the provider's own id for the person
    suspended: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    scim_external_id: Mapped[str | None] = mapped_column(Text)


class Project(Base):
    __tablename__ = "project"
    __table_args__ = (UniqueConstraint("org_id", "name"), Index("project_workspace_idx", "org_id", "workspace_id"))
    id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    # the Matura workspace the project belongs to (TS26, Matura mode only); NULL = none, the whole organisation's
    workspace_id: Mapped[str | None] = mapped_column(String)
    # first-run setup: {"default_model": {"model", "base_url"?, "api_key"?}, "setup_done": bool}
    settings: Mapped[dict[str, Any]] = mapped_column(Json, server_default=text("'{}'"), default=dict)
    # grows with every secret change: each instance reloads its decrypted copy when it is behind (S22)
    secrets_version: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
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
        UtcDateTime(), server_default=text("CURRENT_TIMESTAMP"), onupdate=func.now(), nullable=False
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
    # the Copilot proposals accepted since the version before: [{summary, accepted_by, accepted_at, changes}]
    copilot_changes: Mapped[list[Any]] = mapped_column(Json, server_default=text("'[]'"), default=list)
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
    __table_args__ = (
        UniqueConstraint("workflow_id", "environment", name="deployment_once"),
        Index("deployment_version_idx", "version_id"),  # deleting a version (cascade)
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    version_id: Mapped[str] = mapped_column(ForeignKey("workflow_version.id", ondelete="CASCADE"))
    environment: Mapped[str] = mapped_column(Text, server_default=text("'prod'"), default="prod")
    api_key_hash: Mapped[str] = mapped_column(Text)
    key_prefix: Mapped[str | None] = mapped_column(Text)  # shown in the UI to tell keys apart
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    created_at: Mapped[datetime] = _now()


class DeploymentShare(Base):
    """A deployment's public share link and chat widget (S23): anyone with the link can chat with the deployment.

    The token is a capability link (like an unlisted document link): kept in clear so builders can copy it again,
    revoked by switching sharing off or regenerating it."""

    __tablename__ = "deployment_share"
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployment.id", ondelete="CASCADE"), primary_key=True)
    token: Mapped[str] = mapped_column(Text, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    title: Mapped[str] = mapped_column(Text, server_default=text("''"), default="")
    greeting: Mapped[str] = mapped_column(Text, server_default=text("''"), default="")
    suggestions: Mapped[list[Any]] = mapped_column(Json, server_default=text("'[]'"), default=list)
    input_field: Mapped[str | None] = mapped_column(Text)  # None: picked from the workflow's inputs
    frame_origins: Mapped[list[Any]] = mapped_column(Json, server_default=text("'[]'"), default=list)  # [] = any site
    updated_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), server_default=text("CURRENT_TIMESTAMP"), onupdate=func.now(), nullable=False
    )


class StoredFile(Base):
    """A file a person attached to a run (G-5): uploaded in the Run tab, through a share link, or sent inline to the
    API. Runs carry a reference (`agentcanvas/ir/files.py`), never the bytes; the bytes are read from here when a
    model is shown the file. Deleted with its project, or by the retention job once no run has used it for as long
    as the organisation keeps conversations."""

    __tablename__ = "stored_file"
    __table_args__ = (
        Index("stored_file_project_idx", "project_id", "last_used_at"),
        Index("stored_file_deployment_idx", "deployment_id"),  # deleting a deployment (cascade)
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)  # fil_<ulid>
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)  # the uploader's file name, without any folder
    media_type: Mapped[str] = mapped_column(Text)  # what the content is (sniffed), not what the uploader said
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(Text)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    source: Mapped[str] = mapped_column(Text, server_default=text("'upload'"), default="upload")  # upload|share|api
    # a share link's visitor (S23): only the visitor who uploaded it may use it, and only on that link
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey("deployment.id", ondelete="CASCADE"))
    visitor_hash: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    last_used_at: Mapped[datetime] = _now()


class ShareThread(Base):
    """A conversation a visitor of a share link started: only that visitor (holding the visitor secret whose hash
    is kept here) continues it, and only through the link it was started on."""

    __tablename__ = "share_thread"
    thread_id: Mapped[str] = mapped_column(ForeignKey("thread.id", ondelete="CASCADE"), primary_key=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployment.id", ondelete="CASCADE"), index=True)
    visitor_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    # what the visitor saw, so a reload brings the conversation back (G-15): [{role, text, run_id?, ...}]
    transcript: Mapped[list[Any]] = mapped_column(Json, server_default=text("'[]'"), default=list)
    closed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)  # "New conversation"


class Thread(Base):
    __tablename__ = "thread"
    __table_args__ = (
        CheckConstraint("status IN ('idle','running','interrupted','error')", name="thread_status"),
        Index("thread_workflow_idx", "workflow_id", "updated_at"),
        Index("thread_updated_idx", "updated_at"),
        Index("thread_created_idx", "created_at", "id"),  # the Conversations list, newest first
        Index("thread_workflow_created_idx", "workflow_id", "created_at", "id"),
        Index("thread_deployment_idx", "deployment_id"),  # deleting a deployment (SET NULL), the kill switch
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)  # == LangGraph thread_id
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey("deployment.id", ondelete="SET NULL"))
    title: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'idle'"), default="idle")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), server_default=text("CURRENT_TIMESTAMP"), onupdate=func.now(), nullable=False
    )
    # a paused run of a framework without its own checkpoints (runtime/paused.py), as that framework's JSON
    paused_state: Mapped[str | None] = mapped_column(Text)


class Run(Base):
    __tablename__ = "run"
    __table_args__ = (
        CheckConstraint("mode IN ('normal','debug')", name="run_mode"),
        CheckConstraint("status IN ('running','success','interrupted','error','cancelled')", name="run_status"),
        Index("run_thread_idx", "thread_id", "started_at"),
        Index("run_status_queued_idx", "status", "queued"),
        Index("run_started_idx", "started_at"),  # the spending report's period
        Index("run_version_idx", "workflow_version_id"),  # deleting a version (SET NULL)
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("thread.id", ondelete="CASCADE"))
    workflow_version_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_version.id", ondelete="SET NULL"))
    ir_hash: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text, server_default=text("'normal'"), default="normal")
    status: Mapped[str] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    tokens_in: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), server_default=text("0"), default=Decimal(0))
    # S26 model gateway: model answers taken from the answer cache, and model calls a backup model answered
    cache_hits: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    fallbacks: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    trace_url: Mapped[str | None] = mapped_column(Text)
    # run context (Runtime.context) — resumes reuse it: LangGraph does not checkpoint the context, and a
    # reviewer resuming someone else's run must not become the run's user (per-user memory namespaces)
    context: Mapped[dict[str, Any] | None] = mapped_column(Json)
    # FR-X-13 cassette of a fresh run: its input, each model resource's replies, status, route and output
    recording: Mapped[dict[str, Any] | None] = mapped_column(Json)
    # G-10: the run's events, compact (`runtime/trajectory.py`): the trajectory view once they left memory
    events: Mapped[list[Any] | None] = mapped_column(Json)
    # FR-X-10: stopped by a server shutdown before it finished; the next instance continues the thread
    handover: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    # someone asked to stop it (Stop, Delete conversation, the F-5 kill switch), maybe on another instance: the one
    # running it stops it at the next heartbeat, with `stop_reason` (NULL before it was kept: the kill switch's)
    stop_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    stop_reason: Mapped[str | None] = mapped_column(Text)
    # the run queue (with a concurrent-run limit): 1 high, 0 normal, -1 low; `queued` while it waits for a slot
    priority: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"), default=0)
    queued: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    # S12: refreshed while the run's instance is alive; a running run without a fresh heartbeat lost its instance
    heartbeat_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    # S13: what the deployment API caller tagged the run with (online evaluator filters, annotation rules)
    labels: Mapped[dict[str, Any] | None] = mapped_column(Json)
    # the runner's origin: "user", "test" (experiments, test cases, Copilot test runs), … NULL before it was kept
    origin: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = _now()
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class SpendArchive(Base):
    """What the runs of deleted conversations spent (retention, or deleted by hand), per workflow, UTC hour, deployment
    and person, so day and month budgets and the spending report still count it. Written by `delete_thread_rows`;
    it goes with its workflow. '' is no deployment or no person."""

    __tablename__ = "spend_archive"
    __table_args__ = (Index("spend_archive_hour_idx", "hour"),)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"), primary_key=True)
    hour: Mapped[datetime] = mapped_column(UtcDateTime(), primary_key=True)  # when the runs started, to the hour
    deployment_id: Mapped[str] = mapped_column(String, primary_key=True, default="")
    person: Mapped[str] = mapped_column(String, primary_key=True, default="")  # who started the conversation
    runs: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    tokens_in: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), default=0)
    tokens_out: Mapped[int] = mapped_column(BigInteger, server_default=text("0"), default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 6), server_default=text("0"), default=Decimal(0))
    cache_hits: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    fallbacks: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)


class RunSearch(Base):
    """G-10: a finished run's input and answer, embedded for "find conversations where …". One row per run; it goes
    with its run (retention, deleting the conversation or the workflow). `model` is the embedding model that made
    the vector: a search ranks only the rows of the project's current model, `reindex` redoes the others."""

    __tablename__ = "run_search"
    __table_args__ = (Index("run_search_project_idx", "project_id", "created_at"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), unique=True)
    thread_id: Mapped[str] = mapped_column(Text, index=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    text: Mapped[str] = mapped_column(Text)
    vector: Mapped[bytes | None] = mapped_column(LargeBinary)  # float32, little-endian
    model: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class Presence(Base):
    """G-4: a tab of the editor that has a workflow open (a session), refreshed by its heartbeat. Rows older than the
    TTL (`services/presence.py`) are not shown and are deleted by later heartbeats; the workflow's deletion takes
    them with it."""

    __tablename__ = "presence"
    __table_args__ = (Index("presence_seen_idx", "workflow_id", "seen_at"),)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"), primary_key=True)
    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(Text)
    selected: Mapped[str | None] = mapped_column(Text)  # the block (node or resource id) the tab has selected
    version: Mapped[int | None] = mapped_column(Integer)  # the workflow version the tab has
    seen_at: Mapped[datetime] = _now()


class InterruptTask(Base):
    __tablename__ = "interrupt_task"
    __table_args__ = (
        CheckConstraint("kind IN ('tool_approval','approval','input')", name="interrupt_kind"),
        CheckConstraint("status IN ('pending','decided','expired')", name="interrupt_status"),
        Index("interrupt_pending_idx", "status", "created_at"),
        Index("interrupt_thread_idx", "thread_id"),  # deleting a conversation or a run (cascade, retention)
        Index("interrupt_run_idx", "run_id"),
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
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class A2AReply(Base):
    """What a remote A2A agent answered one step so far (`adapters/langchain/interop.py`): a step paused for the
    agent's question continues on the same remote task, on any replica and after a restart (G-9)."""

    __tablename__ = "a2a_reply"
    thread_id: Mapped[str] = mapped_column(ForeignKey("thread.id", ondelete="CASCADE"), primary_key=True)
    step: Mapped[str] = mapped_column(Text, primary_key=True)  # checkpoint namespace | canvas node id
    replies: Mapped[list[Any]] = mapped_column(Json, server_default=text("'[]'"), default=list)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), server_default=text("CURRENT_TIMESTAMP"), onupdate=func.now(), nullable=False
    )


class AnswerCacheRow(Base):
    """A model answer kept by the gateway's answer cache (`adapters/langchain/answer_cache.py`), shared by replicas."""

    __tablename__ = "answer_cache"
    __table_args__ = (Index("answer_cache_expires_idx", "project_id", "expires_at"),)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)  # the request's hash
    answer: Mapped[dict[str, Any]] = mapped_column(Json)  # the AIMessage
    created_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime())


class Secret(Base):
    __tablename__ = "secret"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    # encrypted with the organisation's data key; false = written before envelope encryption (S10 and earlier)
    org_key: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
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


class Dataset(Base):
    """Examples to evaluate a workflow on (FR-EVAL-02). Every change makes a new immutable `DatasetVersion`;
    experiments pin the version they ran on."""

    __tablename__ = "dataset"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"), default="")
    # S13: the LangSmith dataset it is synced with {dataset_id, name, url, pushed_version, pulled_version, synced_at}
    langsmith: Mapped[dict[str, Any] | None] = mapped_column(Json)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()


class DatasetVersion(Base):
    __tablename__ = "dataset_version"
    __table_args__ = (UniqueConstraint("dataset_id", "number", name="dataset_version_number"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("dataset.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text, server_default=text("''"), default="")
    # [{id, input, context, reference, trajectory, split, metadata}] — the whole set, so a version never changes
    examples: Mapped[list[Any]]
    # counted when the version is written, so lists and the version history never load the examples to count them
    example_count: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    splits: Mapped[dict[str, Any]] = mapped_column(Json, server_default=text("'{}'"), default=dict)  # split -> count
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()


class Evaluator(Base):
    """How runs of a workflow are scored (FR-EVAL-03); kinds and configs in `agentcanvas.evaluation`."""

    __tablename__ = "evaluator"
    __table_args__ = (UniqueConstraint("workflow_id", "name", name="evaluator_name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]]
    threshold: Mapped[float | None] = mapped_column(Float)  # passes at score >= threshold; None = kind default
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()


class KnowledgeBase(Base):
    """Documents a workflow can search (S24 Knowledge Studio). `version` grows with every change, so each
    instance's search cache knows when to reload."""

    __tablename__ = "knowledge_base"
    __table_args__ = (UniqueConstraint("project_id", "name", name="knowledge_base_name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"), default="")
    embedding: Mapped[dict[str, Any]]  # {"model": "builtin" | "<provider>:<model>", "base_url"?, "api_key"?}
    chunking: Mapped[dict[str, Any]]  # {"strategy", "size", "overlap"}
    stale: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    version: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    # web re-crawl (G-6): {"every_hours", "sitemaps": {sitemap url: [page urls]}, "last": {at, added, updated, ...}}
    recrawl: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    crawled_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)  # claimed by the crawl job
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_document"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_base.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)  # upload | url | text
    url: Mapped[str | None] = mapped_column(Text)
    # the extracted text, pages separated by form feeds, kept so a change of chunking re-chunks without the file
    content: Mapped[str] = mapped_column(Text, server_default=text("''"), default="")
    sha256: Mapped[str] = mapped_column(Text, server_default=text("''"), default="")
    status: Mapped[str] = mapped_column(Text, server_default=text("'ready'"), default="ready")  # ready | error
    error: Mapped[str | None] = mapped_column(Text)
    chunks: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunk"
    __table_args__ = (Index("knowledge_chunk_kb", "kb_id", "doc_id", "ordinal"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_base.id", ondelete="CASCADE"))
    doc_id: Mapped[str] = mapped_column(ForeignKey("knowledge_document.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(Text)
    vector: Mapped[bytes | None] = mapped_column(LargeBinary)  # float32, little-endian


class Experiment(Base):
    """A workflow run over a pinned dataset version, once per target (the draft, a version, or either with model
    or prompt overrides), and scored by evaluators (FR-EVAL-04). Targets and evaluators are snapshots taken at
    creation, so later edits never change what an experiment measured."""

    __tablename__ = "experiment"
    __table_args__ = (
        CheckConstraint("status IN ('running','done','cancelled','error')", name="experiment_status"),
        Index("experiment_status_idx", "status", "heartbeat_at"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("dataset.id", ondelete="CASCADE"))
    dataset_version: Mapped[int] = mapped_column(Integer)
    split: Mapped[str | None] = mapped_column(Text)
    # [{label, version, version_id, overrides, ir}] — `ir` is the exact workflow each target runs
    targets: Mapped[list[Any]]
    evaluators: Mapped[list[Any]]  # [{name, kind, config, threshold}]
    concurrency: Mapped[int] = mapped_column(Integer, server_default=text("4"), default=4)
    # F-6: how many times each example runs per target (pass@k, pass^k, flaky examples)
    repetitions: Mapped[int] = mapped_column(Integer, server_default=text("1"), default=1)
    # the project's workspace head when it started: workspace code changes behaviour without changing the IR
    workspace_commit: Mapped[str | None] = mapped_column(Text)
    # S13 FR-EVAL-07: a simulated user drives each example as a conversation (services/simulation.py)
    simulation: Mapped[dict[str, Any] | None] = mapped_column(Json)
    status: Mapped[str] = mapped_column(Text, server_default=text("'running'"), default="running")
    error: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(Text)  # the instance working on it (S13); taking over changes it
    # refreshed by the instance working on it; a running experiment without a fresh heartbeat is taken over
    heartbeat_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()
    ended_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class ExperimentResult(Base):
    """One example run for one target of an experiment, with its scores."""

    __tablename__ = "experiment_result"
    __table_args__ = (
        CheckConstraint("status IN ('pending','running','done')", name="experiment_result_status"),
        UniqueConstraint("experiment_id", "target", "example_id", "repetition", name="experiment_result_example"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiment.id", ondelete="CASCADE"), index=True)
    target: Mapped[int] = mapped_column(Integer)  # index into Experiment.targets
    example_id: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)  # the example's place in the dataset version
    repetition: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)  # which run of the example
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"), default="pending")
    run_id: Mapped[str | None] = mapped_column(Text)
    thread_id: Mapped[str | None] = mapped_column(Text)
    run_status: Mapped[str | None] = mapped_column(Text)  # success / interrupted / error / cancelled / timeout
    error: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict[str, Any] | None] = mapped_column(Json)
    trajectory: Mapped[list[Any] | None] = mapped_column(Json)
    scores: Mapped[list[Any] | None] = mapped_column(Json)  # [{evaluator, score, passed, comment}]
    tokens_in: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    cost_usd: Mapped[float] = mapped_column(Float, server_default=text("0"), default=0.0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class ReleaseGate(Base):
    """FR-EVAL-05: publishing this workflow to `environment` must pass an evaluation on `dataset_id` first."""

    __tablename__ = "release_gate"
    __table_args__ = (UniqueConstraint("workflow_id", "environment", name="release_gate_environment"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"), index=True)
    environment: Mapped[str] = mapped_column(Text)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("dataset.id", ondelete="CASCADE"))
    min_scores: Mapped[dict[str, Any]]  # evaluator name -> minimum mean score; empty: every evaluator, its threshold
    max_regression: Mapped[float | None] = mapped_column(Float)  # largest allowed drop in pass rate vs. live
    # S13: what the gate measures with, fixed when an admin saves it (null on gates saved before: the latest
    # dataset version and the evaluators' current settings)
    dataset_version: Mapped[int | None] = mapped_column(Integer)
    evaluators: Mapped[list[Any] | None] = mapped_column(Json)  # [{name, kind, config, threshold}]
    updated_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    updated_at: Mapped[datetime] = _now()


class GitLink(Base):
    """G-14: a workflow connected to a branch of a GitHub repository (pull requests out, imports back)."""

    __tablename__ = "git_link"
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"), primary_key=True)
    repo: Mapped[str] = mapped_column(Text)  # owner/name
    branch: Mapped[str] = mapped_column(Text)
    folder: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    token_secret: Mapped[str] = mapped_column(Text, default="GITHUB_TOKEN", server_default=text("'GITHUB_TOKEN'"))
    target: Mapped[str] = mapped_column(Text, default="local", server_default=text("'local'"))
    auto: Mapped[str] = mapped_column(Text, default="publish", server_default=text("'publish'"))  # off|publish|version
    repo_hash: Mapped[str | None] = mapped_column(Text)  # hash of the IR the branch held when we last pushed or pulled
    pr_number: Mapped[int | None] = mapped_column(Integer)
    pr_url: Mapped[str | None] = mapped_column(Text)
    last_version: Mapped[str | None] = mapped_column(Text)  # the version (or "draft") last pushed
    last_synced_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


class OnlineEvaluator(Base):
    """FR-EVAL-06: an evaluator scoring a sample of a deployment's finished runs in the background (S13)."""

    __tablename__ = "online_evaluator"
    __table_args__ = (UniqueConstraint("deployment_id", "evaluator_id", name="online_evaluator_once"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployment.id", ondelete="CASCADE"), index=True)
    evaluator_id: Mapped[str] = mapped_column(ForeignKey("evaluator.id", ondelete="CASCADE"))
    sampling: Mapped[float] = mapped_column(Float)  # share of matching runs scored, 0-1
    filter: Mapped[dict[str, Any]]  # {"status": [..], "metadata": {key: value}}; empty: every run
    # drift rules: the mean over the last `window_minutes` below `alert_below`, or more than `alert_drop` below
    # the trailing 7-day mean, opens an alert (once `min_samples` runs were scored in the window)
    alert_below: Mapped[float | None] = mapped_column(Float)
    alert_drop: Mapped[float | None] = mapped_column(Float)
    window_minutes: Mapped[int] = mapped_column(Integer, server_default=text("60"), default=60)
    min_samples: Mapped[int] = mapped_column(Integer, server_default=text("5"), default=5)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()


class RunScore(Base):
    """A score given to a run after it finished: by an online evaluator, or by a person (S13)."""

    __tablename__ = "run_score"
    __table_args__ = (Index("run_score_deployment_idx", "deployment_id", "evaluator", "created_at"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"), index=True)
    thread_id: Mapped[str] = mapped_column(Text)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"), index=True)
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey("deployment.id", ondelete="CASCADE"))
    evaluator: Mapped[str] = mapped_column(Text)  # the evaluator's name, or "feedback" / a rubric criterion
    source: Mapped[str] = mapped_column(Text)  # "online" | "feedback" | "annotation"
    score: Mapped[float | None] = mapped_column(Float)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    comment: Mapped[str] = mapped_column(Text, default="")
    # what scoring itself cost (an LLM judge's tokens); never added to the run's own cost
    judge_tokens: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)
    judge_cost_usd: Mapped[float] = mapped_column(Float, server_default=text("0"), default=0.0)
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class EvalAlert(Base):
    """A drift alert of an online evaluator: open while its rule holds, resolved when it no longer does."""

    __tablename__ = "eval_alert"
    __table_args__ = (
        Index("eval_alert_open_idx", "deployment_id", "evaluator", "kind", "resolved_at"),
        # one open alert per rule, however many scoring tasks and pods see it break at once
        Index(
            "eval_alert_one_open",
            "deployment_id",
            "evaluator",
            "kind",
            unique=True,
            sqlite_where=text("resolved_at IS NULL"),
            postgresql_where=text("resolved_at IS NULL"),
        ),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"), index=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployment.id", ondelete="CASCADE"))
    evaluator: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)  # "below" | "drop"
    message: Mapped[str] = mapped_column(Text)
    value: Mapped[float] = mapped_column(Float)  # the window mean that broke the rule
    baseline: Mapped[float | None] = mapped_column(Float)  # the trailing mean ("drop")
    created_at: Mapped[datetime] = _now()
    resolved_at: Mapped[datetime | None] = mapped_column(UtcDateTime())


class CostAlert(Base):
    """A workflow's spending reached a share of its day or month budget (S26): raised once per period and share."""

    __tablename__ = "cost_alert"
    __table_args__ = (
        UniqueConstraint("workflow_id", "period", "period_start", "share", name="cost_alert_once"),
        Index("cost_alert_workflow_idx", "workflow_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    run_id: Mapped[str | None] = mapped_column(Text)  # the run whose spending crossed it
    period: Mapped[str] = mapped_column(Text)  # "day" | "month"
    period_start: Mapped[str] = mapped_column(String(10))  # UTC "2026-10-10" (day) or "2026-10" (month)
    share: Mapped[float] = mapped_column(Float)  # of the limit; 1.0 = the limit was reached and runs stop
    spent_usd: Mapped[float] = mapped_column(Float)
    limit_usd: Mapped[float] = mapped_column(Float)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class AnnotationQueue(Base):
    """A queue of runs for people to review (S13): fed by rules on production runs or by hand."""

    __tablename__ = "annotation_queue"
    __table_args__ = (UniqueConstraint("workflow_id", "name", name="annotation_queue_name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(Text)
    instructions: Mapped[str] = mapped_column(Text, default="")
    # [{"name", "description", "scale": points (2 = yes/no), "pass_at": normalised score that passes}]
    rubric: Mapped[list[Any]] = mapped_column(Json)
    # {"failed": bool, "thumbs_down": bool, "scores_below": {evaluator: threshold}}: production runs routed here
    rules: Mapped[dict[str, Any]]
    reviewers_per_item: Mapped[int] = mapped_column(Integer, server_default=text("1"), default=1)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()


class AnnotationItem(Base):
    """One run waiting in (or done with) a queue."""

    __tablename__ = "annotation_item"
    __table_args__ = (
        UniqueConstraint("queue_id", "run_id", name="annotation_item_once"),
        Index("annotation_item_queue_idx", "queue_id", "status", "created_at"),
        Index("annotation_item_run_idx", "run_id"),  # deleting a run (cascade)
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    queue_id: Mapped[str] = mapped_column(ForeignKey("annotation_queue.id", ondelete="CASCADE"))
    run_id: Mapped[str] = mapped_column(ForeignKey("run.id", ondelete="CASCADE"))
    thread_id: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)  # "manual" | "failed" | "thumbs_down" | "score:<evaluator>"
    status: Mapped[str] = mapped_column(Text, default="pending")  # "pending" | "done"
    added_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class Annotation(Base):
    """A reviewer's scores (per rubric criterion) and comment on a queue item; one per reviewer and item."""

    __tablename__ = "annotation"
    __table_args__ = (UniqueConstraint("item_id", "reviewer", name="annotation_once"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("annotation_item.id", ondelete="CASCADE"), index=True)
    reviewer: Mapped[str] = mapped_column(Text)
    scores: Mapped[dict[str, Any]]  # criterion -> points (1..scale)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _now()


class RateWindow(Base):
    """Requests counted per limit key and fixed window (S12, S30): shared by every API instance."""

    __tablename__ = "rate_window"
    key: Mapped[str] = mapped_column(Text, primary_key=True)  # "deployment:<id>", "project:<id>", "hook:<id>"
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)  # unix time // window length
    count: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)


class Trigger(Base):
    __tablename__ = "trigger"
    __table_args__ = (
        CheckConstraint("kind IN ('webhook','cron')", name="trigger_kind"),
        Index("trigger_workflow_idx", "workflow_id"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey("deployment.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(Text)
    config: Mapped[dict[str, Any]]
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    # cron: the UTC minute ("YYYY-mm-ddTHH:MM") it last fired for, claimed atomically so replicas fire it once
    last_fired: Mapped[str | None] = mapped_column(String(16))
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


class OrgPolicy(Base):
    """An organisation rule over workflows (FR-X-19): CEL predicates checked by the validator and at publish."""

    __tablename__ = "org_policy"
    __table_args__ = (
        UniqueConstraint("org_id", "name"),
        CheckConstraint("scope IN ('node','resource','workflow')", name="org_policy_scope"),
        CheckConstraint("severity IN ('error','warning')", name="org_policy_severity"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default=text("''"), default="")
    scope: Mapped[str] = mapped_column(Text)
    match: Mapped[str] = mapped_column(Text, server_default=text("'true'"), default="true")
    require: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(Text, server_default=text("'error'"), default="error")
    environments: Mapped[list[Any]] = mapped_column(Json, server_default=text("'[]'"), default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
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


class CopilotConversation(Base):
    """A person's Copilot chat about one workflow (S14). One is open per (workflow, user); "New chat" archives it,
    and an archived one can be reopened. `busy_until` is the lease of the turn answering now (one at a time)."""

    __tablename__ = "copilot_conversation"
    __table_args__ = (
        Index("copilot_conversation_open_once", "workflow_id", "user_id", unique=True,
              postgresql_where=text("archived_at IS NULL"), sqlite_where=text("archived_at IS NULL")),
        Index("copilot_conversation_by_person", "workflow_id", "user_id", "updated_at"),
    )  # fmt: skip
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(Text)
    messages: Mapped[list[Any]] = mapped_column(Json, server_default=text("'[]'"), default=list)
    brief: Mapped[dict[str, Any] | None] = mapped_column(Json, nullable=True)
    proposals: Mapped[list[Any]] = mapped_column(Json, server_default=text("'[]'"), default=list)
    usage: Mapped[dict[str, Any]] = mapped_column(Json, server_default=text("'{}'"), default=dict)
    title: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)  # chosen for this conversation (within policy); null: the project's
    archived_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    busy_until: Mapped[datetime | None] = mapped_column(UtcDateTime())
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), server_default=text("CURRENT_TIMESTAMP"), onupdate=func.now(), nullable=False
    )


class PersonalToken(Base):
    """A person's API token (G-13): `Authorization: Bearer act_…` acts as them in one organisation, with at most the
    role chosen when it was made. Only the SHA-256 of the token is kept; it is shown once."""

    __tablename__ = "personal_token"
    __table_args__ = (
        CheckConstraint("role IN ('admin','builder','reviewer','viewer')", name="personal_token_role"),
        Index("personal_token_owner_idx", "org_id", "user_id"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    prefix: Mapped[str] = mapped_column(Text)  # the first characters, shown to tell tokens apart
    role: Mapped[str] = mapped_column(Text)  # the most it may do; the person's role now applies when that is lower
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    created_at: Mapped[datetime] = _now()


class ScimGroup(Base):
    """A group the identity provider provisions over SCIM (G-13). Its `display_name` is looked up in the organisation's
    groups table (`organization.sso_groups`) for the role its members get."""

    __tablename__ = "scim_group"
    __table_args__ = (UniqueConstraint("org_id", "display_name"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    display_name: Mapped[str] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), server_default=text("CURRENT_TIMESTAMP"), onupdate=func.now(), nullable=False
    )


class ScimGroupMember(Base):
    __tablename__ = "scim_group_member"
    group_id: Mapped[str] = mapped_column(ForeignKey("scim_group.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), primary_key=True)


class OAuthConnection(Base):
    """A sign-in to an MCP server that needs one (G-7): one person's (`owner` is their user id) or the organisation's
    service identity (`owner = 'service'`). The tokens are encrypted with the organisation's data key."""

    __tablename__ = "oauth_connection"
    __table_args__ = (
        UniqueConstraint("org_id", "owner", "resource"),
        CheckConstraint("status IN ('active','reconnect')", name="oauth_connection_status"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    owner: Mapped[str] = mapped_column(Text)
    resource: Mapped[str] = mapped_column(Text)  # the MCP server's address, canonical
    issuer: Mapped[str] = mapped_column(Text)
    token_endpoint: Mapped[str] = mapped_column(Text)
    revocation_endpoint: Mapped[str | None] = mapped_column(Text)
    client_id: Mapped[str] = mapped_column(Text)
    client_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    access_token_enc: Mapped[bytes] = mapped_column(LargeBinary)
    refresh_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    scope: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"), default="active")
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime(), server_default=text("CURRENT_TIMESTAMP"), onupdate=func.now(), nullable=False
    )


class OAuthFlow(Base):
    """A sign-in in progress: made when the browser is sent to the authorization server, used up by the first answer
    that comes back (or discarded when it expires). `state_hash` is the SHA-256 of the `state` the browser carries."""

    __tablename__ = "oauth_flow"
    state_hash: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(Text)  # who started it: only they can finish it
    owner: Mapped[str] = mapped_column(Text)
    resource: Mapped[str] = mapped_column(Text)
    issuer: Mapped[str] = mapped_column(Text)
    token_endpoint: Mapped[str] = mapped_column(Text)
    revocation_endpoint: Mapped[str | None] = mapped_column(Text)
    client_id: Mapped[str] = mapped_column(Text)
    client_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    iss_in_response: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    verifier_enc: Mapped[bytes] = mapped_column(LargeBinary)  # the PKCE code verifier
    return_to: Mapped[str] = mapped_column(Text, server_default=text("'/'"), default="/")
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime())


class OAuthClient(Base):
    """AgentCanvas's client at an authorization server, once per organisation: a dynamic registration it made itself,
    or client credentials an admin entered (N-5). Entered credentials are for the MCP servers the admin entered them
    with (`resources`), never for another server that names the same sign-in server (CP-3)."""

    __tablename__ = "oauth_client"
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), primary_key=True)
    issuer: Mapped[str] = mapped_column(Text, primary_key=True)
    redirect_uri: Mapped[str] = mapped_column(Text, primary_key=True)
    client_id: Mapped[str] = mapped_column(Text)
    client_secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    # entered by an admin: the MCP servers (canonical addresses) they are for. None: AgentCanvas registered itself
    resources: Mapped[list[Any] | None] = mapped_column(Json, nullable=True)
    created_at: Mapped[datetime] = _now()
