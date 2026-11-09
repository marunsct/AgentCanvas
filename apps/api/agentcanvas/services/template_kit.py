"""What a template brings beside its workflow (`metadata.kit`): workspace code, an evaluation dataset with its
evaluators, saved tests and a cron trigger, so a template can show a feature that lives outside the canvas (event
handlers, custom blocks, evaluation) and still work the moment it is opened.

    "metadata": {"kit": {
        "files": {"events/{{workflow_key}}_new_ticket.py": "...act.start_workflow(\"{{workflow_id}}\")..."},
        "dataset": {"name": "Tickets", "examples": [{"input": {...}, "reference": {...}}]},
        "evaluators": [{"name": "has a reply", "kind": "regex", "config": {...}}],
        "tests": [{"name": "urgent goes to a manager", "input": {...}, "expect": {"status": "interrupted"}}],
        "triggers": [{"kind": "cron", "config": {"schedule": "0 9 * * 1-5"}}]
    }}

`{{workflow_id}}` and `{{workflow_key}}` (the id in lower case, fit for a module name) are filled in, in file paths
and contents. A file already in the workspace is kept as it is (the person's code wins). Evaluators are the kinds
that need no model, so a template works before a model is chosen; triggers are created switched off, since a
template should not start running on its own.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.services.testcases import Expect

KIT = "kit"


class KitDataset(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    examples: list[dict[str, Any]] = Field(min_length=1)


class KitEvaluator(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: Literal["exact", "regex", "json_schema", "trajectory", "code"]
    config: dict[str, Any] = Field(default_factory=dict)
    threshold: float | None = None


class KitTest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    input: dict[str, Any] = Field(default_factory=dict)
    expect: Expect = Field(default_factory=Expect)
    mocks: dict[str, Any] = Field(default_factory=dict)


class KitTrigger(BaseModel):
    kind: Literal["cron"] = "cron"
    config: dict[str, Any]


class Kit(BaseModel):
    files: dict[str, str] = Field(default_factory=dict, description="workspace path -> content")
    dataset: KitDataset | None = None
    evaluators: list[KitEvaluator] = Field(default_factory=list)
    tests: list[KitTest] = Field(default_factory=list)
    triggers: list[KitTrigger] = Field(default_factory=list)


class KitOut(BaseModel):
    """What creating the workflow added beside it."""

    files_written: list[str] = Field(default_factory=list)
    files_kept: list[str] = Field(default_factory=list, description="already in the workspace: left as they were")
    dataset_id: str | None = None
    evaluators: int = 0
    tests: int = 0
    triggers: int = 0


def kit_of(doc: dict[str, Any] | None) -> Kit:
    raw = ((doc or {}).get("metadata") or {}).get(KIT)
    return Kit.model_validate(raw) if isinstance(raw, dict) else Kit()


def summary(kit: Kit) -> list[str]:
    """The kit in the gallery's words: "2 workspace files", "a dataset of 4 examples", …"""
    out = []
    if kit.files:
        n = len(kit.files)
        out.append(f"{n} workspace file{'s' if n != 1 else ''}")
    if kit.dataset:
        n = len(kit.dataset.examples)
        out.append(f"a dataset of {n} example{'s' if n != 1 else ''}")
    if kit.evaluators:
        out.append(f"{len(kit.evaluators)} evaluator{'s' if len(kit.evaluators) != 1 else ''}")
    if kit.tests:
        out.append(f"{len(kit.tests)} saved test{'s' if len(kit.tests) != 1 else ''}")
    if kit.triggers:
        out.append("a schedule (off)")
    return out


def fill(text: str, wf_id: str) -> str:
    return text.replace("{{workflow_id}}", wf_id).replace("{{workflow_key}}", wf_id.lower())


def check(kit: Kit) -> None:
    """Every part of the kit is valid, before anything is created (InvalidInput otherwise)."""
    from agentcanvas.errors import InvalidInput
    from agentcanvas.evaluation import check_config
    from agentcanvas.services.datasets import Example
    from agentcanvas.services.triggers import CronConfig

    try:
        for e in kit.evaluators:
            check_config(e.kind, e.config)
        for t in kit.triggers:
            CronConfig.model_validate(t.config)
        for x in kit.dataset.examples if kit.dataset else []:
            Example.model_validate(x)
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        raise InvalidInput(f"the template's kit is not valid: {exc}") from exc


def write_files(ws: Any, kit: Kit, wf_id: str, template: str) -> KitOut:
    """Write the kit's files (blocking: git commits; run it off the event loop). A file already there is kept."""
    out = KitOut()
    for path, content in kit.files.items():
        rel = fill(path, wf_id)
        if ws.resolve(rel).exists():
            out.files_kept.append(rel)
            continue
        ws.write(rel, fill(content, wf_id), f"template {template}: add {rel}")
        out.files_written.append(rel)
    return out


async def add_records(session: AsyncSession, kit: Kit, wf_id: str, user_id: str | None, out: KitOut) -> KitOut:
    """The dataset, evaluators, saved tests and (switched off) schedule of a workflow just created."""
    from agentcanvas.db.models import Evaluator, Trigger
    from agentcanvas.evaluation import check_config
    from agentcanvas.ids import new_id
    from agentcanvas.services.datasets import DatasetService, Example
    from agentcanvas.services.testcases import TestCaseService
    from agentcanvas.services.triggers import CronConfig

    if kit.dataset:
        datasets = DatasetService(session)
        version = await datasets.create(wf_id, kit.dataset.name, kit.dataset.description, user_id)
        examples = [Example.model_validate(e) for e in kit.dataset.examples]
        await datasets.add(version.dataset_id, examples, "examples from the template", user_id)
        out.dataset_id = version.dataset_id
    for e in kit.evaluators:
        session.add(Evaluator(
            id=new_id("evl"), workflow_id=wf_id, name=e.name, kind=e.kind, config=check_config(e.kind, e.config),
            threshold=e.threshold, created_by=user_id,
        ))  # fmt: skip
        out.evaluators += 1
    tests = TestCaseService(session)
    for t in kit.tests:
        await tests.add(wf_id, name=t.name, input=t.input, expect=t.expect, mocks=t.mocks, created_by=user_id)
        out.tests += 1
    for t in kit.triggers:
        config = CronConfig.model_validate(t.config).model_dump()
        session.add(Trigger(id=new_id("trg"), workflow_id=wf_id, kind="cron", config=config, enabled=False))
        out.triggers += 1
    await session.commit()
    return out


def kit_registry(wf: Any, registry: Any, root: Any) -> tuple[Any, Any]:
    """The registry that knows a template's own blocks (its kit's `nodes/` code, written under `root` as a project
    that opened the template would have it), and that workspace root; (registry, None) when the template has none.
    Used where templates are checked or exported without a project: the export script and the Copilot evaluation."""
    from pathlib import Path

    from agentcanvas.workspace.catalog import scan

    files = ((wf.metadata or {}).get(KIT) or {}).get("files") or {}
    if not files:
        return registry, None
    base = Path(root)
    for rel, text in files.items():
        path = base / fill(rel, wf.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fill(text, wf.id), encoding="utf-8")
    builtin = {m.type for m in registry.all()}
    custom = [m for m in scan(base).nodes if m.type not in builtin]
    return registry.with_manifests(custom), base
