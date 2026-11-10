"""A deployment's AI-system record (ISO/IEC 42001 Annex A, NIST AI 600-1, the AI Act's deployer duties): one page an
auditor or a risk owner reads to know what a published AI system is for, who answers for it, how risky it is and what
evidence backs it.

People write a few fields (`RecordFields`, kept on `Deployment.system_record`): the purpose, the owner (a member),
the risk class with its rationale, the kinds of data it handles, whether its end users are the public, and when it
is next reviewed. Everything else is read, when the record is
asked for, from what the server already keeps, so it cannot fall out of date:

- the published version and what changed since the version before it;
- the models and their providers, and every other service its runs send data to (subprocessors);
- its tools, app connectors with the tool fingerprints pinned at publish, and remote agents;
- its guardrails (safety steps, agent add-ons, the workflow's budget) and the organisation's policies in force;
- where a person approves, and which steps act outside without anyone approving;
- evaluation evidence: the release gate, the latest experiment and red-team run, online evaluators' scores;
- who published, unpublished and described it (the audit log);
- retention and data handling.

`notes` are reminders, never errors: a high-risk system without evaluation evidence or an approval step, an owner who
left, a review date that passed. Publishing shows them too.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import (
    AppUser,
    AuditLog,
    Dataset,
    Deployment,
    DeploymentShare,
    EvalAlert,
    Evaluator,
    Experiment,
    Membership,
    OnlineEvaluator,
    Organization,
    OrgPolicy,
    Project,
    ReleaseGate,
    RunScore,
    TestCase,
    WorkflowRow,
    WorkflowVersion,
)
from agentcanvas.errors import InvalidInput, NotFound
from agentcanvas.ir.diff import diff_workflows
from agentcanvas.ir.models import Node, Resource, Workflow
from agentcanvas.registry import Registry, default_registry
from agentcanvas.services import model_defaults, org_settings, trace_export
from agentcanvas.services.approvers import Approvers
from agentcanvas.services.experiments import ExperimentService
from agentcanvas.settings import Settings

RiskClass = Literal["minimal", "limited", "high"]
RISK_CLASSES: dict[str, str] = {
    "minimal": "Minimal: no particular risk to people (spam filters, internal drafting aids)",
    "limited": "Limited: people must be told they deal with an AI (chat assistants, generated content)",
    "high": "High: decisions about people's access to work, credit, education, essential services or safety",
}
# what people usually pick; any short label is accepted
DATA_CATEGORIES = (
    "public information", "internal business data", "customer contact details", "personal data",
    "special-category personal data", "children's data", "financial data", "health data", "credentials or secrets",
)  # fmt: skip
ONLINE_WINDOW = timedelta(days=30)  # how far back online evaluators' scores are summed up
HISTORY = 50  # audit entries listed
HISTORY_ACTIONS = ("publish", "unpublish", "update_system_record")
GUARDRAIL_ADDONS = ("mw.pii", "mw.rubric", "mw.model_call_limit", "mw.tool_call_limit")
APPROVAL_STEPS = ("core.approval", "core.human_input")
REMOTE_AGENTS = ("agent.a2a", "agent.mcp")
TOOLS = ("mcp.server", "tool.http", "tool.python", "tool.memory")
LOCAL_PROCESS = "a local process"  # an app connector started on the server (stdio)

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class RecordFields(BaseModel):
    """What the person responsible writes down; every field may stay empty."""

    purpose: str = Field(
        default="", max_length=2000, description="what the system is for, and what it must not be used for"
    )
    owner_id: str | None = Field(default=None, description="the member who answers for it (a user id)")
    risk_class: RiskClass | None = Field(default=None, description="minimal, limited or high; with a rationale")
    risk_rationale: str = Field(default="", max_length=2000, description="why it has this risk class")
    data_categories: list[Label] = Field(
        default_factory=list, max_length=30, description="kinds of data it handles, e.g. personal data, health data"
    )
    end_user_facing: bool | None = Field(
        default=None, description="its end users are the public (customers, visitors); false: only your own staff"
    )
    review_date: date | None = Field(default=None, description="when the record is next reviewed")

    @field_validator("purpose", "risk_rationale")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("data_categories")
    @classmethod
    def _unique(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))


class RecordOut(RecordFields):
    owner_email: str | None = Field(
        default=None, description="the owner's email; none when they are no longer a member"
    )
    owner_member: bool = Field(default=True, description="false: the owner left the organisation")
    updated_by_email: str | None = None
    updated_at: datetime | None = None


class VersionInfo(BaseModel):
    id: str
    semver: str
    message: str
    created_at: datetime
    created_by: str | None = Field(description="the email of who made the version, or their id")
    workspace_commit: str | None


class Changes(BaseModel):
    previous: str | None = Field(description="the semver of the version before; none: the first version")
    summary: str
    added: list[str] = Field(default_factory=list, description="steps and resources added, by name")
    removed: list[str] = Field(default_factory=list)
    changed: list[str] = Field(default_factory=list)
    copilot: list[str] = Field(default_factory=list, description="Copilot proposals accepted for this version")


class ModelUse(BaseModel):
    block: str = Field(description="the AI model block")
    model: str = Field(description="provider:model")
    provider: str
    provider_label: str
    role: Literal["model", "backup", "cheapest of", "project default"] = "model"
    address: str | None = Field(default=None, description="the model server's address when the block sets one")
    used_by: list[str] = Field(default_factory=list, description="the steps it is wired to")


class Recipient(BaseModel):
    """A service its runs, or the scoring of its runs, send data to."""

    name: str
    kind: Literal["model provider", "judge model", "app connector", "web API", "remote agent", "trace export"]
    detail: str = ""
    local: bool = Field(default=False, description="runs on your own machines or network (a local model server)")


class PinnedTool(BaseModel):
    name: str
    fingerprint: str | None = Field(description="the hash recorded when its tools were browsed; none: by name only")


class ToolInfo(BaseModel):
    name: str
    type: str
    label: str
    address: str | None = None
    pinned: list[PinnedTool] = Field(default_factory=list, description="app connectors: the tools it may offer")
    pinning: Literal["pinned", "names only", "not pinned", ""] = ""
    on_tool_change: str | None = Field(default=None, description="app connectors: drop a changed tool, or fail the run")
    drift: dict[str, Any] | None = Field(default=None, description="what the server changed since publishing")


class Guardrail(BaseModel):
    name: str
    label: str
    detail: str = ""


class PolicyInForce(BaseModel):
    name: str
    severity: str
    description: str


class Approval(BaseModel):
    step: str
    label: str
    what: str = Field(description="what a person approves or answers")
    deciders: str = Field(description="who may decide")


class GateEvidence(BaseModel):
    dataset: str
    dataset_version: int | None
    min_scores: dict[str, Any]
    max_regression: float | None
    evaluators: list[str]


class TargetResult(BaseModel):
    label: str
    version: str | None = Field(description="the semver it ran, or 'draft'")
    this_version: bool = Field(description="it ran exactly the published version")
    pass_rate: float | None
    completed: int
    evaluators: dict[str, float | None] = Field(description="evaluator -> mean score")


class ExperimentEvidence(BaseModel):
    id: str
    name: str
    status: str
    dataset: str
    dataset_version: int
    ended_at: datetime | None
    targets: list[TargetResult]


class OnlineEvidence(BaseModel):
    evaluator: str
    sampling: float
    scored: int = Field(description=f"runs scored in the last {ONLINE_WINDOW.days} days")
    mean: float | None
    pass_rate: float | None
    open_alerts: int


class Evaluation(BaseModel):
    release_gate: GateEvidence | None = None
    experiment: ExperimentEvidence | None = Field(default=None, description="the latest finished experiment")
    red_team: ExperimentEvidence | None = Field(default=None, description="the latest finished red-team run")
    online: list[OnlineEvidence] = Field(default_factory=list)
    test_cases: int = 0

    @property
    def any(self) -> bool:
        return bool(self.release_gate or self.experiment or self.red_team or self.online)


class HistoryEntry(BaseModel):
    at: datetime
    action: str
    who: str | None
    environment: str | None = None
    version: str | None = None
    detail: str = ""


class DataHandling(BaseModel):
    conversation_days: int = Field(description="conversations are deleted this many days after their last activity "
                                               "(0: kept)")  # fmt: skip
    audit_days: int = Field(description="audit entries are kept this many days (0: forever)")
    memory_days: int = Field(description="long-term memories not updated for this long are deleted (0: kept)")
    approval_expiry_days: int
    trace_redact: list[str] = Field(description="fields whose values never reach a trace")
    trace_export: Recipient | None = None
    trace_content: bool | None = Field(default=None, description="the trace export sends inputs and outputs (masked)")
    ai_notice: str = Field(description="what the share link's chat page and widget tell people")
    share_link: bool = Field(description="a share link is switched on")
    output_marked: bool = Field(default=True, description="its answers are marked as AI-generated (X-AI-Generated)")


class SystemRecordOut(BaseModel):
    generated_at: datetime
    organisation: str
    project: str
    workflow_id: str
    workflow: str
    description: str
    deployment_id: str
    environment: str
    active: bool
    published_at: datetime
    record: RecordOut
    version: VersionInfo
    changes: Changes
    models: list[ModelUse]
    subprocessors: list[Recipient]
    tools: list[ToolInfo]
    guardrails: list[Guardrail]
    policies: list[PolicyInForce]
    approvals: list[Approval]
    ungated: list[str] = Field(description="steps that act outside (write, send, run code) without a person approving")
    evaluation: Evaluation
    history: list[HistoryEntry]
    data_handling: DataHandling
    notes: list[str] = Field(description="reminders, not errors: what an auditor would ask about")


# ---------------------------------------------------------------------------------------- the fields ----
def stored(dep: Deployment) -> RecordFields:
    """The fields as kept on the deployment; anything unreadable counts as empty."""
    raw = {k: v for k, v in (dep.system_record or {}).items() if k in RecordFields.model_fields}
    try:
        return RecordFields.model_validate(raw)
    except ValueError:
        return RecordFields()


async def save(session: AsyncSession, dep: Deployment, org_id: str, fields: RecordFields, user_id: str) -> None:
    """Keep the fields on the deployment. The owner must be a member of the organisation, and a risk class needs a
    rationale (an auditor's first question). Commits."""
    if fields.owner_id is not None:
        member = await session.get(Membership, (org_id, fields.owner_id))
        if member is None or not member.accepted or member.suspended:
            raise InvalidInput(f"the owner '{fields.owner_id}' is not a member of this organisation")
    if fields.risk_class is not None and not fields.risk_rationale:
        raise InvalidInput(f"say why the risk class is '{fields.risk_class}': the rationale is part of the record")
    dep.system_record = {
        **fields.model_dump(mode="json"), "updated_by": user_id, "updated_at": datetime.now(UTC).isoformat(),
    }  # fmt: skip
    await session.commit()


# ------------------------------------------------------------------------------------- the evidence ----
def _flat(wf: Workflow, path: str = "") -> tuple[list[tuple[str, Node]], list[tuple[str, Resource]], Workflow]:
    """(display name, element) of every enabled step and every resource, subgraphs included as "group/inner"."""
    nodes = [(path + n.name, n) for n in wf.nodes if not n.disabled]
    resources = [(path + r.name, r) for r in wf.resources]
    for n in wf.nodes:
        if n.type == "core.subgraph" and not n.disabled:
            with contextlib.suppress(Exception):  # an invalid subgraph never runs
                inner_nodes, inner_resources, _ = _flat(wf.subworkflow(n), f"{path}{n.name}/")
                nodes += inner_nodes
                resources += inner_resources
    return nodes, resources, wf


def _wired(wf: Workflow, path: str = "") -> dict[str, list[str]]:
    """Resource display name -> the steps it is wired to, subgraphs included."""
    names = {n.id: path + n.name for n in wf.nodes}
    res = {r.id: path + r.name for r in wf.resources}
    out: dict[str, list[str]] = {}
    for e in wf.edges:
        if e.kind == "wiring" and e.source.node in res and e.target.node in names:
            out.setdefault(res[e.source.node], []).append(names[e.target.node])
    for n in wf.nodes:
        if n.type == "core.subgraph":
            with contextlib.suppress(Exception):
                for k, v in _wired(wf.subworkflow(n), f"{path}{n.name}/").items():
                    out.setdefault(k, []).extend(v)
    return out


def host(url: Any) -> str | None:
    """The scheme and host of an address (never its path, query or credentials)."""
    if not isinstance(url, str) or not url.strip():
        return None
    try:
        parts = urlsplit(url.strip())
        name = parts.hostname
    except ValueError:
        return None
    if not name:
        return None
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{name}{port}" if parts.scheme else name


def _label(registry: Registry, type_: str) -> str:
    m = registry.get(type_)
    return m.label if m else type_


def models_of(wf: Workflow, project_settings: dict[str, Any] | None, registry: Registry) -> list[ModelUse]:
    """Every model its runs may call: each AI model block's model, backups and cheapest-of choices."""
    _, resources, _ = _flat(wf)
    wired = _wired(wf)
    out: list[ModelUse] = []

    def use(block: str, model: str, role: Any, address: str | None) -> None:
        provider = model.partition(":")[0] if ":" in model else ""
        info = registry.provider(provider) if provider else None
        out.append(ModelUse(block=block, model=model, provider=provider or "unknown",
                            provider_label=info.label if info else (provider or "unknown"), role=role,
                            address=address, used_by=wired.get(block, [])))  # fmt: skip

    for name, r in resources:
        if r.type != "model.chat":
            continue
        address = host(r.config.get("base_url"))
        model = str(r.config.get("model") or "")
        if model:
            use(name, model, "model", address)
        elif (default := model_defaults.resolve(project_settings, "chat").model) is not None:
            use(name, default, "project default", address)
        for m in r.config.get("fallbacks") or []:
            if isinstance(m, str) and m:
                use(name, m, "backup", None)
        for m in r.config.get("cheapest_of") or []:
            if isinstance(m, str) and m:
                use(name, m, "cheapest of", None)
    return out


def tools_of(wf: Workflow, dep: Deployment, registry: Registry) -> list[ToolInfo]:
    nodes, resources, _ = _flat(wf)
    drift = dep.tool_drift or {}
    out: list[ToolInfo] = []
    for name, r in resources:
        if r.type not in TOOLS:
            continue
        info = ToolInfo(name=name, type=r.type, label=_label(registry, r.type))
        if r.type == "mcp.server":
            stdio = r.config.get("transport") == "stdio"
            info.address = LOCAL_PROCESS if stdio else host(r.config.get("url"))
            wanted = set(r.config.get("tool_filter") or [])
            found = [
                d for d in r.config.get("discovered") or [] if isinstance(d, dict) and isinstance(d.get("name"), str)
            ]
            # what the runs may offer (the runtime's pins, `mcp_pins`): browsed tools by fingerprint, else by name
            info.pinned = [
                PinnedTool(name=d["name"], fingerprint=d["hash"] if isinstance(d.get("hash"), str) else None)
                for d in found
                if not wanted or d["name"] in wanted
            ] or [PinnedTool(name=str(t), fingerprint=None) for t in sorted(wanted)]
            if not info.pinned:
                info.pinning = "not pinned"
            elif all(p.fingerprint for p in info.pinned):
                info.pinning = "pinned"
            else:
                info.pinning = "names only"
            info.on_tool_change = str(r.config.get("on_tool_change") or "drop")
            info.drift = drift.get(r.name) if isinstance(drift.get(r.name), dict) else None
        elif r.type == "tool.http":
            info.address = host(r.config.get("url"))
        out.append(info)
    for name, n in nodes:
        if n.type in REMOTE_AGENTS:
            address = host(n.config.get("url")) if n.type == "agent.a2a" else None
            out.append(ToolInfo(name=name, type=n.type, label=_label(registry, n.type), address=address))
    return out


def guardrails_of(wf: Workflow, registry: Registry) -> list[Guardrail]:
    nodes, resources, _ = _flat(wf)
    out: list[Guardrail] = []
    for name, n in nodes:
        m = registry.get(n.type)
        if m is not None and m.category == "Safety":
            how = n.config.get("on_fail")
            out.append(Guardrail(name=name, label=m.label, detail=f"on failure: {how}" if how else ""))
    for name, r in resources:
        if r.type in GUARDRAIL_ADDONS:
            out.append(Guardrail(name=name, label=_label(registry, r.type)))
    budget = wf.settings.budget
    if budget is not None and (limits := budget.model_dump(exclude_none=True)):
        out.append(Guardrail(name="budget", label="Workflow budget",
                             detail=", ".join(f"{k.replace('_', ' ')} {v}" for k, v in limits.items())))  # fmt: skip
    return out


def _deciders(stored_setting: Any) -> str:
    a = Approvers.of(stored_setting)
    if not a.named:
        return "anyone who answers the Inbox" + (" or the caller" if not a.routed else "")
    parts = [f"roles {', '.join(a.roles)}"] if a.roles else []
    parts += [f"groups {', '.join(a.groups)}"] if a.groups else []
    parts += [f"people {', '.join(a.people)}"] if a.people else []
    if a.caller:
        parts.append("the caller")
    if a.not_initiator:
        parts.append("not the person who started the run")
    if a.escalate_to:
        parts.append(f"escalates to {a.escalate_to} after {a.escalate_after_hours or '?'} h")
    return "; ".join(parts)


def approvals_of(wf: Workflow, registry: Registry) -> list[Approval]:
    nodes, _, _ = _flat(wf)
    out: list[Approval] = []
    for name, n in nodes:
        label = _label(registry, n.type)
        if n.type in APPROVAL_STEPS:
            what = "approves before the flow goes on" if n.type == "core.approval" else "answers a question"
            out.append(Approval(step=name, label=label, what=what, deciders=_deciders(n.config.get("approvers"))))
        elif tools := [str(t) for t in n.config.get("approve_tools") or []]:
            out.append(Approval(step=name, label=label, what=f"approves the tools {', '.join(tools)} before they run",
                                deciders=_deciders(n.config.get("approvers"))))  # fmt: skip
        elif n.type in REMOTE_AGENTS and n.config.get("approve"):
            out.append(Approval(step=name, label=label, what="approves what is sent before it runs",
                                deciders=_deciders(n.config.get("approvers"))))  # fmt: skip
    return out


def ungated_of(wf: Workflow, registry: Registry) -> list[str]:
    from agentcanvas.validation.blast import blast_radius

    try:
        radius = blast_radius(wf, registry)
    except ValueError:  # an invalid subgraph (reported at publish); never fail the record over it
        return []
    names = {n.id: n.name for n in wf.nodes}
    return [names.get(i, i) for i in radius.ungated]


def _changes(version: WorkflowVersion, previous: WorkflowVersion | None) -> Changes:
    copilot = [str(c.get("summary") or "") for c in version.copilot_changes or [] if isinstance(c, dict)]
    if previous is None:
        return Changes(previous=None, summary="the first version", copilot=copilot)
    before, after = Workflow.model_validate(previous.ir), Workflow.model_validate(version.ir)
    diff = diff_workflows(before, after)
    was = {x.id: x.name for x in [*before.nodes, *before.resources]}
    now = {x.id: x.name for x in [*after.nodes, *after.resources]}
    pick = (diff.nodes, diff.resources)
    return Changes(
        previous=previous.semver, summary=diff.describe(),
        added=[now.get(i, i) for d in pick for i in d.added], removed=[was.get(i, i) for d in pick for i in d.removed],
        changed=[c.name or c.id for d in pick for c in d.changed], copilot=copilot,
    )  # fmt: skip


async def _experiments(session: AsyncSession, wf_id: str, version_id: str) -> tuple[Any, Any]:
    """The latest finished experiment and the latest finished red-team run (one whose evaluators include a red-team
    judge), each as evidence."""
    q = (
        select(Experiment)
        .where(Experiment.workflow_id == wf_id, Experiment.status == "done")
        .order_by(Experiment.id.desc())
        .limit(50)
    )
    found = list((await session.scalars(q)).all())

    def red(e: Experiment) -> bool:
        return any(isinstance(s, dict) and s.get("kind") == "red_team" for s in e.evaluators or [])

    picked = [next((e for e in found if not red(e)), None), next((e for e in found if red(e)), None)]
    wanted = [e for e in picked if e is not None]
    summaries = await ExperimentService(session).summaries(wanted)
    names = dict((await session.execute(select(Dataset.id, Dataset.name).where(
        Dataset.id.in_([e.dataset_id for e in wanted])))).all()) if wanted else {}  # fmt: skip

    def evidence(e: Experiment | None) -> ExperimentEvidence | None:
        if e is None:
            return None
        targets = []
        for s in summaries[e.id]:
            t = e.targets[s.target] if s.target < len(e.targets) else {}
            targets.append(TargetResult(
                label=s.label, version=t.get("version"), pass_rate=s.pass_rate, completed=s.completed,
                this_version=t.get("version_id") == version_id and not t.get("overrides"),
                evaluators={x.evaluator: x.mean for x in s.evaluators},
            ))  # fmt: skip
        return ExperimentEvidence(id=e.id, name=e.name, status=e.status, dataset=names.get(e.dataset_id, e.dataset_id),
                                  dataset_version=e.dataset_version, ended_at=e.ended_at, targets=targets)  # fmt: skip

    return evidence(picked[0]), evidence(picked[1])


async def _evaluation(session: AsyncSession, dep: Deployment, now: datetime) -> tuple[Evaluation, list[str]]:
    """The evidence, and the judge models its online evaluators send runs to."""
    ev = Evaluation()
    gate = await session.scalar(
        select(ReleaseGate).where(
            ReleaseGate.workflow_id == dep.workflow_id, ReleaseGate.environment == dep.environment
        )
    )
    if gate is not None:
        dataset = await session.get(Dataset, gate.dataset_id)
        ev.release_gate = GateEvidence(
            dataset=dataset.name if dataset else gate.dataset_id, dataset_version=gate.dataset_version,
            min_scores=dict(gate.min_scores or {}), max_regression=gate.max_regression,
            evaluators=[str(e.get("name")) for e in gate.evaluators or [] if isinstance(e, dict)],
        )  # fmt: skip
    ev.experiment, ev.red_team = await _experiments(session, dep.workflow_id, dep.version_id)
    ev.test_cases = await session.scalar(
        select(func.count()).select_from(TestCase).where(TestCase.workflow_id == dep.workflow_id)
    ) or 0  # fmt: skip
    online = (
        await session.execute(
            select(OnlineEvaluator, Evaluator)
            .join(Evaluator, Evaluator.id == OnlineEvaluator.evaluator_id)
            .where(OnlineEvaluator.deployment_id == dep.id)
            .order_by(Evaluator.name)
        )
    ).all()
    judges: list[str] = []
    if online:
        names = [e.name for _, e in online]
        scores = {
            row.evaluator: row
            for row in (
                await session.execute(
                    select(
                        RunScore.evaluator, func.count().label("n"), func.avg(RunScore.score).label("mean"),
                        func.sum(case((RunScore.passed, 1), else_=0)).label("passed"),
                    )
                    .where(RunScore.deployment_id == dep.id, RunScore.source == "online",
                           RunScore.evaluator.in_(names), RunScore.created_at >= now - ONLINE_WINDOW)
                    .group_by(RunScore.evaluator)
                )
            ).all()
        }  # fmt: skip
        alerts = dict(
            (
                await session.execute(
                    select(EvalAlert.evaluator, func.count())
                    .where(EvalAlert.deployment_id == dep.id, EvalAlert.resolved_at.is_(None))
                    .group_by(EvalAlert.evaluator)
                )
            ).all()
        )
        for oe, e in online:
            s = scores.get(e.name)
            n = int(s.n) if s else 0
            ev.online.append(OnlineEvidence(
                evaluator=e.name, sampling=oe.sampling, scored=n,
                mean=round(float(s.mean), 4) if s and s.mean is not None else None,
                pass_rate=round(int(s.passed or 0) / n, 4) if s and n else None, open_alerts=int(alerts.get(e.name, 0)),
            ))  # fmt: skip
            if isinstance(model := (e.config or {}).get("model"), str) and model:
                judges.append(model)
    return ev, judges


async def _history(session: AsyncSession, org_id: str, dep: Deployment) -> list[HistoryEntry]:
    """Who published the workflow to this environment, unpublished it or changed its record, newest first."""
    q = (
        select(AuditLog, AppUser.email)
        .outerjoin(AppUser, AppUser.id == AuditLog.actor_id)
        .where(
            AuditLog.org_id == org_id,
            AuditLog.target.in_([f"wf_id={dep.workflow_id}", f"dep_id={dep.id}"]),
            AuditLog.action.in_(HISTORY_ACTIONS),
        )
        .order_by(AuditLog.id.desc())
        .limit(HISTORY * 4)  # publishes to other environments are read and left out
    )
    out: list[HistoryEntry] = []
    for row, email in (await session.execute(q)).all():
        d = row.detail or {}
        env = d.get("environment") if isinstance(d.get("environment"), str) else None
        if row.action == "publish" and env not in (None, dep.environment):
            continue
        detail = f"over a failed release gate: {d['gate_override']}" if d.get("gate_override") else ""
        if row.action == "update_system_record" and d.get("risk_class") is not None:
            detail = f"risk class {d['risk_class']}"
        out.append(HistoryEntry(at=row.created_at, action=row.action, who=email or row.actor_id,
                                environment=env, version=d.get("version"), detail=detail))  # fmt: skip
        if len(out) >= HISTORY:
            break
    return out


def notes_for(fields: RecordFields, evaluation: Evaluation, approvals: list[Approval], owner_member: bool,
              today: date, environment: str) -> list[str]:  # fmt: skip
    out: list[str] = []
    if fields.risk_class == "high":
        if not evaluation.any:
            out.append(
                "Marked high risk, but there is no evaluation evidence: run an experiment or a red team, add a "
                f"release gate for {environment}, or score its runs with an online evaluator."
            )
        if not approvals:
            out.append(
                "Marked high risk, but no step asks a person: consider an Ask for approval step, or tool approvals on "
                "its agents, before it acts."
            )
        if fields.owner_id is None:
            out.append("Marked high risk without an owner: name the member who answers for it.")
    if fields.owner_id is not None and not owner_member:
        out.append("Its owner is no longer a member of the organisation: name a new owner.")
    if fields.review_date is not None and fields.review_date < today:
        out.append(f"Its review date ({fields.review_date.isoformat()}) has passed: review the record.")
    if not (fields.purpose or fields.owner_id or fields.risk_class):
        out.append("No purpose, owner or risk class is recorded yet.")
    return out


async def _owner(session: AsyncSession, org_id: str, fields: RecordFields) -> tuple[str | None, bool]:
    if fields.owner_id is None:
        return None, True
    member = await session.get(Membership, (org_id, fields.owner_id))
    if member is None:
        return None, False
    user = await session.get(AppUser, fields.owner_id)
    return (user.email if user else None), True


async def notes_at_publish(session: AsyncSession, dep: Deployment, wf: Workflow, org_id: str) -> list[str]:
    """The record's reminders for the workflow about to be published to `dep`'s environment (only for a deployment
    whose record was filled in: a record nobody started says nothing at publish)."""
    if not dep.system_record:
        return []
    fields = stored(dep)
    evaluation, _ = await _evaluation(session, dep, datetime.now(UTC))
    _, member = await _owner(session, org_id, fields)
    found = notes_for(fields, evaluation, approvals_of(wf, default_registry()), member, datetime.now(UTC).date(),
                      dep.environment)  # fmt: skip
    return [n for n in found if not n.startswith("No purpose")]


async def build(session: AsyncSession, server: Settings, dep: Deployment, org_id: str) -> SystemRecordOut:
    """The whole record of `dep`, read now."""
    now = datetime.now(UTC)
    registry = default_registry()
    version = await session.get(WorkflowVersion, dep.version_id)
    row = await session.get(WorkflowRow, dep.workflow_id)
    if version is None or row is None:
        raise NotFound(f"the version of deployment '{dep.id}' is missing")
    project = await session.get(Project, row.project_id)
    org = await session.get(Organization, org_id)
    previous = await session.scalar(
        select(WorkflowVersion)
        .where(WorkflowVersion.workflow_id == dep.workflow_id, WorkflowVersion.id < version.id)
        .order_by(WorkflowVersion.id.desc())
        .limit(1)
    )
    wf = Workflow.model_validate(version.ir)
    fields = stored(dep)
    owner_email, owner_member = await _owner(session, org_id, fields)
    raw = dep.system_record or {}
    people = {i for i in (raw.get("updated_by"), version.created_by) if isinstance(i, str)}
    emails = dict((await session.execute(select(AppUser.id, AppUser.email).where(AppUser.id.in_(people)))).all())
    updated_at = None
    with contextlib.suppress(TypeError, ValueError):
        updated_at = datetime.fromisoformat(raw["updated_at"]) if raw.get("updated_at") else None
    record = RecordOut(**fields.model_dump(), owner_email=owner_email, owner_member=owner_member,
                       updated_by_email=emails.get(str(raw.get("updated_by"))), updated_at=updated_at)  # fmt: skip

    settings = await org_settings.of_project(session, server, row.project_id)
    models = models_of(wf, project.settings if project else None, registry)
    tools = tools_of(wf, dep, registry)
    evaluation, judges = await _evaluation(session, dep, now)
    approvals = approvals_of(wf, registry)
    policies = [
        PolicyInForce(name=p.name, severity=p.severity, description=p.description)
        for p in (await session.scalars(
            select(OrgPolicy).where(OrgPolicy.org_id == org_id, OrgPolicy.enabled).order_by(OrgPolicy.name))).all()
        if not p.environments or dep.environment in p.environments
    ]  # fmt: skip

    recipients: dict[tuple[str, str], Recipient] = {}
    for m in models:  # one per provider and address, naming the models it serves
        info = registry.provider(m.provider)
        name = m.provider_label + (f" at {m.address}" if m.address else "")
        served = sorted({x.model for x in models if x.provider == m.provider and x.address == m.address})
        recipients.setdefault(("model provider", name), Recipient(
            name=name, kind="model provider", detail=", ".join(served), local=bool(info and info.local),
        ))  # fmt: skip
    for j in judges:
        provider = j.partition(":")[0]
        info = registry.provider(provider)
        recipients.setdefault(("judge model", j), Recipient(name=info.label if info else provider, kind="judge model",
                                                            detail=j, local=bool(info and info.local)))  # fmt: skip
    kinds = {"mcp.server": "app connector", "tool.http": "web API", "agent.a2a": "remote agent"}
    for t in tools:
        kind = kinds.get(t.type)
        if kind is not None and t.address and t.address != LOCAL_PROCESS:
            recipients.setdefault((kind, t.address), Recipient(name=t.address, kind=kind, detail=t.name))  # type: ignore[arg-type]
    exporter = trace_export.load(project.settings if project else None)
    trace = None
    if exporter is not None and exporter.enabled:
        address = host(exporter.endpoint or trace_export.DEFAULTS.get(exporter.kind, "")) or exporter.kind
        trace = Recipient(name=address, kind="trace export", detail=exporter.kind)
        recipients.setdefault(("trace export", address), trace)

    share = await session.get(DeploymentShare, dep.id)
    from agentcanvas.services.share import ai_notice

    org_days = org.retention_days if org is not None else None
    handling = DataHandling(
        conversation_days=org_days if org_days is not None else settings.retention_days,
        audit_days=settings.audit_retention_days, memory_days=settings.memory_retention_days,
        approval_expiry_days=settings.approval_expiry_days, trace_redact=list(wf.settings.trace_redact),
        trace_export=trace, trace_content=exporter.include_content if trace is not None and exporter else None,
        ai_notice=ai_notice(share), share_link=bool(share and share.enabled),
    )  # fmt: skip
    return SystemRecordOut(
        generated_at=now, organisation=org.name if org else org_id, project=project.name if project else row.project_id,
        workflow_id=row.id, workflow=row.name, description=wf.description, deployment_id=dep.id,
        environment=dep.environment, active=dep.active, published_at=dep.created_at, record=record,
        version=VersionInfo(id=version.id, semver=version.semver, message=version.message,
                            created_at=version.created_at, workspace_commit=version.workspace_commit,
                            created_by=emails.get(version.created_by or "") or version.created_by),
        changes=_changes(version, previous), models=models, subprocessors=list(recipients.values()), tools=tools,
        guardrails=guardrails_of(wf, registry), policies=policies, approvals=approvals,
        ungated=ungated_of(wf, registry), evaluation=evaluation, history=await _history(session, org_id, dep),
        data_handling=handling,
        notes=notes_for(fields, evaluation, approvals, owner_member, now.date(), dep.environment),
    )  # fmt: skip
