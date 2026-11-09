"""Workflow use-cases: create, edit (whole IR or commands), layout, validate, generate code."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import WorkflowRow
from agentcanvas.db.repositories import PolicyRepo, ProjectRepo, WorkflowRepo
from agentcanvas.errors import InvalidInput, NotFound
from agentcanvas.ids import new_id
from agentcanvas.ir.canonical import ir_hash
from agentcanvas.ir.commands import Command, CommandError, apply_batch
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import Registry, without_sampling
from agentcanvas.services import org_settings
from agentcanvas.services.projects import PROJECT_OF
from agentcanvas.settings import Settings
from agentcanvas.validation import Diagnostic, validate
from agentcanvas.validation.policies import Policy

TEMPLATES = Path(__file__).parents[1] / "registry" / "templates"
DEV_TEMPLATES = Path(__file__).parents[2] / "examples"  # demo workflows with dev-only imports


@dataclass
class WorkflowView:
    row: WorkflowRow
    workflow: Workflow
    diagnostics: list[Diagnostic]


def with_default_model(doc: Any, default: dict[str, Any]) -> Any:
    """A template whose chat models (subgraphs included) are the project's default model from first-run setup;
    their other settings (temperature, max tokens…) stay."""
    if isinstance(doc, list):
        return [with_default_model(x, default) for x in doc]
    if not isinstance(doc, dict):
        return doc
    out = {k: with_default_model(v, default) for k, v in doc.items()}
    if out.get("type") == "model.chat" and isinstance(out.get("config"), dict):
        kept = {k: v for k, v in out["config"].items() if k not in ("model", "base_url", "api_key")}
        out["config"], _ = without_sampling(str(default.get("model", "")), {**kept, **default})  # W020
    return out


GALLERY = "gallery"  # a template's `metadata.gallery`: category, tags, difficulty, sample_input (S23)
GALLERY_CATEGORIES = (
    "Getting started", "Customer support", "Content", "Sales", "Data extraction", "Research", "HR", "Engineering",
    "Operations",
)  # fmt: skip


def template_gallery(doc: dict[str, Any]) -> dict[str, Any]:
    """The gallery metadata of a template document ({} when it has none)."""
    g = (doc.get("metadata") or {}).get(GALLERY)
    return g if isinstance(g, dict) else {}


def block_types(doc: Any) -> set[str]:
    """Block types in a workflow document, groups of steps (subgraphs) included."""
    out: set[str] = set()
    if isinstance(doc, dict):
        for key in ("nodes", "resources"):
            out |= {i["type"] for i in doc.get(key) or [] if isinstance(i, dict) and isinstance(i.get("type"), str)}
        for value in doc.values():
            out |= block_types(value)
    elif isinstance(doc, list):
        for value in doc:
            out |= block_types(value)
    return out


def runs_in(edition: str, doc: dict[str, Any]) -> bool:
    """A template the edition can run: the hosted service refuses blocks that execute on the host (E041)."""
    if edition != "hosted":
        return True
    from agentcanvas.registry import default_registry

    registry = default_registry()
    return not any((m := registry.get(t)) is not None and m.hosted_forbidden for t in block_types(doc))


def without_gallery(doc: dict[str, Any]) -> dict[str, Any]:
    """A template document as a workflow: the gallery metadata only describes the template, and its kit (what it
    brings beside the workflow) is added once, so both are dropped."""
    out = {k: v for k, v in doc.items() if k != "metadata"}
    meta = {k: v for k, v in (doc.get("metadata") or {}).items() if k not in (GALLERY, "kit")}
    return {**out, "metadata": meta} if meta else out


def list_templates(settings: Settings) -> dict[str, dict[str, Any]]:
    dirs = [TEMPLATES] + ([DEV_TEMPLATES] if settings.env in ("dev", "test") else [])
    out: dict[str, dict[str, Any]] = {}
    for d in dirs:
        for p in sorted(d.glob("*.ir.json")):
            out[p.name.removesuffix(".ir.json")] = json.loads(p.read_text())
    return out


class WorkflowService:
    def __init__(
        self, session: AsyncSession, settings: Settings, registry_for: Callable[[str], Registry] | None = None
    ):
        self.repo = WorkflowRepo(session)
        self.projects = ProjectRepo(session)
        self.settings = settings
        self.registry_for = registry_for  # project id -> registry incl. workspace custom nodes
        self._policies: dict[str, list[Policy]] = {}  # project id -> its organisation's policies (FR-X-19)
        self._org: dict[str, Settings] = {}  # project id -> the server's settings as its organisation has them

    def registry(self, project_id: str) -> Registry | None:
        from agentcanvas.services import projects

        provider = self.registry_for or (projects.CURRENT.registry if projects.CURRENT else None)
        return provider(project_id) if provider else None

    async def policies(self, project_id: str) -> list[Policy]:
        if project_id not in self._policies:
            rows = await PolicyRepo(self.repo.s).of_project(project_id)
            self._policies[project_id] = [Policy.model_validate(r, from_attributes=True) for r in rows]
        return self._policies[project_id]

    async def _validate(
        self, wf: Workflow, project_id: str | None = None, *, environment: str | None = None
    ) -> list[Diagnostic]:
        """Validation diagnostics, with the organisation's policies for `environment` (None: as the editor sees
        them)."""
        registry = self.registry(project_id) if project_id else None
        if project_id and project_id not in self._org:
            self._org[project_id] = await org_settings.of_project(self.repo.s, self.settings, project_id)
        settings = self._org.get(project_id or "", self.settings)
        return validate(
            wf, registry, edition=settings.edition, allow_local_mcp=settings.allow_local_mcp,
            policies=await self.policies(project_id) if project_id else None, environment=environment,
        )  # fmt: skip

    async def _view(self, row: WorkflowRow) -> WorkflowView:
        wf = Workflow.model_validate(row.draft_ir)
        PROJECT_OF[wf.id] = row.project_id  # lets the runner resolve workspace code for this workflow
        return WorkflowView(row, wf, await self._validate(wf, row.project_id))

    async def create(
        self, project_id: str, name: str, template: str | None, created_by: str | None, wf_id: str | None = None
    ) -> WorkflowView:
        project = await self.projects.require(project_id)
        wf_id = wf_id or new_id("wf")
        if template:
            doc = list_templates(self.settings).get(template)
            if doc is None:
                raise NotFound(f"template '{template}' not found")
            doc = without_gallery(doc)
            default = (project.settings or {}).get("default_model")
            if default:
                doc = with_default_model(doc, default)
            wf = Workflow.model_validate({**doc, "id": wf_id, "name": name or doc["name"]})
        else:
            wf = Workflow(id=wf_id, name=name)
        ir = wf.model_dump(mode="json")
        row = WorkflowRow(
            id=wf_id,
            project_id=project_id,
            name=wf.name,
            draft_ir=ir,
            layout={},
            version=1,
            ir_hash=ir_hash(ir),
            created_by=created_by,
        )
        return await self._view(await self.repo.create(row))

    async def create_from_ir(
        self, project_id: str, raw_ir: dict[str, Any], layout: dict[str, Any], created_by: str | None
    ) -> WorkflowView:
        """A new workflow from an IR document built elsewhere (an Agent Spec import, S16)."""
        await self.projects.require(project_id)
        try:
            wf = Workflow.model_validate(raw_ir)
        except ValidationError as exc:
            raise InvalidInput("the imported workflow is not valid", errors=_pydantic_errors(exc)) from exc
        ir = wf.model_dump(mode="json")
        known = {n.id for n in wf.nodes} | {r.id for r in wf.resources} | {"START", "END"}
        row = WorkflowRow(
            id=wf.id,
            project_id=project_id,
            name=wf.name,
            draft_ir=ir,
            layout={k: v for k, v in layout.items() if k in known},
            version=1,
            ir_hash=ir_hash(ir),
            created_by=created_by,
        )
        return await self._view(await self.repo.create(row))

    async def get(self, wf_id: str) -> WorkflowView:
        return await self._view(await self.repo.require(wf_id))

    async def list(self, project_id: str, limit: int, cursor: str | None) -> list[WorkflowRow]:
        await self.projects.require(project_id)
        return await self.repo.list(project_id, limit, cursor)

    async def replace(
        self, wf_id: str, raw_ir: dict[str, Any], expected_version: int, layout: dict[str, Any] | None = None
    ) -> WorkflowView:
        await self.repo.require(wf_id)
        try:
            wf = Workflow.model_validate(raw_ir)
        except ValidationError as exc:
            raise InvalidInput("the IR document is not valid", errors=_pydantic_errors(exc)) from exc
        if wf.id != wf_id:
            raise InvalidInput(f"IR id '{wf.id}' does not match workflow '{wf_id}'")
        return await self._save(wf_id, wf, expected_version, layout)

    async def apply_commands(
        self, wf_id: str, commands: list[Command], expected_version: int
    ) -> tuple[WorkflowView, list[Command]]:
        current = Workflow.model_validate((await self.repo.require(wf_id)).draft_ir)
        try:
            new, inverse = apply_batch(current, commands)
        except CommandError as exc:
            raise InvalidInput(str(exc)) from exc
        return await self._save(wf_id, new, expected_version), inverse

    async def _save(
        self, wf_id: str, wf: Workflow, expected_version: int, layout: dict[str, Any] | None = None
    ) -> WorkflowView:
        ir = wf.model_dump(mode="json")
        await self.repo.save_ir(wf_id, ir, ir_hash(ir), wf.name, expected_version, layout)
        row = await self.repo.require(wf_id)
        await self.repo.s.refresh(row)
        return await self._view(row)

    async def set_layout(self, wf_id: str, positions: dict[str, dict[str, float]], replace: bool) -> dict[str, Any]:
        row = await self.repo.require(wf_id)
        wf = Workflow.model_validate(row.draft_ir)
        known = {n.id for n in wf.nodes} | {r.id for r in wf.resources} | {"START", "END"}
        layout = {} if replace else dict(row.layout or {})
        layout.update(positions)
        layout = {k: v for k, v in layout.items() if k in known}  # drop positions of deleted elements
        await self.repo.save_layout(wf_id, layout)
        return layout

    async def rename(self, wf_id: str, name: str) -> WorkflowView:
        """A new name for the workflow (its IR and its row), as one more version of the draft."""
        row = await self.repo.require(wf_id)
        wf = Workflow.model_validate(row.draft_ir).model_copy(update={"name": name})
        return await self._save(wf_id, wf, row.version)

    async def duplicate(self, wf_id: str, name: str | None, created_by: str | None) -> WorkflowView:
        """A copy of the draft in the same project, with its layout; versions, runs and deployments stay with the
        original. Code in the original's own workspace folder is shared, not copied (imports name that folder)."""
        row = await self.repo.require(wf_id)
        copy_id = new_id("wf")
        ir = {**row.draft_ir, "id": copy_id, "name": name or f"{row.name} (copy)"}
        return await self.create_from_ir(row.project_id, ir, dict(row.layout or {}), created_by)

    async def delete(self, wf_id: str) -> None:
        await self.repo.delete(wf_id)


def _pydantic_errors(exc: ValidationError) -> list[dict[str, Any]]:
    return [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
