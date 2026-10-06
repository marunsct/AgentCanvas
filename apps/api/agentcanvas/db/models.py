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
    Float,
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
    # days a conversation is kept after its last activity (M8-T4); NULL = AGENTCANVAS_RETENTION_DAYS, 0 = forever
    retention_days: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _now()


class OrgDataKey(Base):
    """An organisation's data key, wrapped by the master key (KMS envelope encryption, S11)."""

    __tablename__ = "org_data_key"
    org_id: Mapped[str] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"), primary_key=True)
    master_key_id: Mapped[str] = mapped_column(Text)  # which master key wrapped it ("local:…", "aws:…", "gcp:…")
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = _now()
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    # false: an invitation to someone who already belongs to another organisation, until they accept it (S11)
    accepted: Mapped[bool] = mapped_column(Boolean, server_default=text("true"), default=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # the default organisation


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
        Index("thread_updated_idx", "updated_at"),
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
    # FR-X-10: stopped by a server shutdown before it finished; the next instance continues the thread
    handover: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), default=False)
    # S12: refreshed while the run's instance is alive; a running run without a fresh heartbeat lost its instance
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # S13: what the deployment API caller tagged the run with (online evaluator filters, annotation rules)
    labels: Mapped[dict[str, Any] | None] = mapped_column(Json)
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
    # the project's workspace head when it started: workspace code changes behaviour without changing the IR
    workspace_commit: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'running'"), default="running")
    error: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(Text)  # the instance working on it (S13); taking over changes it
    # refreshed by the instance working on it; a running experiment without a fresh heartbeat is taken over
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(ForeignKey("app_user.id"))
    created_at: Mapped[datetime] = _now()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExperimentResult(Base):
    """One example run for one target of an experiment, with its scores."""

    __tablename__ = "experiment_result"
    __table_args__ = (
        CheckConstraint("status IN ('pending','running','done')", name="experiment_result_status"),
        UniqueConstraint("experiment_id", "target", "example_id", name="experiment_result_example"),
    )
    id: Mapped[str] = mapped_column(String, primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiment.id", ondelete="CASCADE"), index=True)
    target: Mapped[int] = mapped_column(Integer)  # index into Experiment.targets
    example_id: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)  # the example's place in the dataset version
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
    __table_args__ = (Index("eval_alert_open_idx", "deployment_id", "evaluator", "kind", "resolved_at"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"), index=True)
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployment.id", ondelete="CASCADE"))
    evaluator: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)  # "below" | "drop"
    message: Mapped[str] = mapped_column(Text)
    value: Mapped[float] = mapped_column(Float)  # the window mean that broke the rule
    baseline: Mapped[float | None] = mapped_column(Float)  # the trailing mean ("drop")
    created_at: Mapped[datetime] = _now()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RateWindow(Base):
    """Requests counted per limit key and fixed window (S12, S30): shared by every API instance."""

    __tablename__ = "rate_window"
    key: Mapped[str] = mapped_column(Text, primary_key=True)  # "deployment:<id>", "project:<id>", "hook:<id>"
    slot: Mapped[int] = mapped_column(Integer, primary_key=True)  # unix time // window length
    count: Mapped[int] = mapped_column(Integer, server_default=text("0"), default=0)


class Trigger(Base):
    __tablename__ = "trigger"
    __table_args__ = (CheckConstraint("kind IN ('webhook','cron')", name="trigger_kind"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow.id", ondelete="CASCADE"))
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey("deployment.id"))
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
