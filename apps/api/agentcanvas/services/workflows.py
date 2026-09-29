"""Workflow use-cases: create, edit (whole IR or commands), layout, validate, generate code."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import WorkflowRow
from agentcanvas.db.repositories import ProjectRepo, WorkflowRepo
from agentcanvas.errors import InvalidInput, NotFound
from agentcanvas.ids import new_id
from agentcanvas.ir.canonical import ir_hash
from agentcanvas.ir.commands import Command, CommandError, apply_batch
from agentcanvas.ir.models import Workflow
from agentcanvas.settings import Settings
from agentcanvas.validation import Diagnostic, validate

TEMPLATES = Path(__file__).parents[1] / "registry" / "templates"
DEV_TEMPLATES = Path(__file__).parents[2] / "examples"  # demo workflows with dev-only imports


@dataclass
class WorkflowView:
    row: WorkflowRow
    workflow: Workflow
    diagnostics: list[Diagnostic]


def list_templates(settings: Settings) -> dict[str, dict[str, Any]]:
    dirs = [TEMPLATES] + ([DEV_TEMPLATES] if settings.env in ("dev", "test") else [])
    out: dict[str, dict[str, Any]] = {}
    for d in dirs:
        for p in sorted(d.glob("*.ir.json")):
            out[p.name.removesuffix(".ir.json")] = json.loads(p.read_text())
    return out


class WorkflowService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.repo = WorkflowRepo(session)
        self.projects = ProjectRepo(session)
        self.settings = settings

    def _validate(self, wf: Workflow) -> list[Diagnostic]:
        return validate(wf, edition=self.settings.edition, allow_local_mcp=self.settings.allow_local_mcp)

    def _view(self, row: WorkflowRow) -> WorkflowView:
        wf = Workflow.model_validate(row.draft_ir)
        return WorkflowView(row, wf, self._validate(wf))

    async def create(self, project_id: str, name: str, template: str | None, created_by: str | None) -> WorkflowView:
        await self.projects.require(project_id)
        wf_id = new_id("wf")
        if template:
            doc = list_templates(self.settings).get(template)
            if doc is None:
                raise NotFound(f"template '{template}' not found")
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
        return self._view(await self.repo.create(row))

    async def get(self, wf_id: str) -> WorkflowView:
        return self._view(await self.repo.require(wf_id))

    async def list(self, project_id: str, limit: int, cursor: str | None) -> list[WorkflowRow]:
        await self.projects.require(project_id)
        return await self.repo.list(project_id, limit, cursor)

    async def replace(self, wf_id: str, raw_ir: dict[str, Any], expected_version: int) -> WorkflowView:
        await self.repo.require(wf_id)
        try:
            wf = Workflow.model_validate(raw_ir)
        except ValidationError as exc:
            raise InvalidInput("the IR document is not valid", errors=_pydantic_errors(exc)) from exc
        if wf.id != wf_id:
            raise InvalidInput(f"IR id '{wf.id}' does not match workflow '{wf_id}'")
        return await self._save(wf_id, wf, expected_version)

    async def apply_commands(
        self, wf_id: str, commands: list[Command], expected_version: int
    ) -> tuple[WorkflowView, list[Command]]:
        current = Workflow.model_validate((await self.repo.require(wf_id)).draft_ir)
        try:
            new, inverse = apply_batch(current, commands)
        except CommandError as exc:
            raise InvalidInput(str(exc)) from exc
        return await self._save(wf_id, new, expected_version), inverse

    async def _save(self, wf_id: str, wf: Workflow, expected_version: int) -> WorkflowView:
        ir = wf.model_dump(mode="json")
        await self.repo.save_ir(wf_id, ir, ir_hash(ir), wf.name, expected_version)
        row = await self.repo.require(wf_id)
        await self.repo.s.refresh(row)
        return self._view(row)

    async def set_layout(self, wf_id: str, positions: dict[str, dict[str, float]], replace: bool) -> dict[str, Any]:
        row = await self.repo.require(wf_id)
        wf = Workflow.model_validate(row.draft_ir)
        known = {n.id for n in wf.nodes} | {r.id for r in wf.resources} | {"START", "END"}
        layout = {} if replace else dict(row.layout or {})
        layout.update(positions)
        layout = {k: v for k, v in layout.items() if k in known}  # drop positions of deleted elements
        await self.repo.save_layout(wf_id, layout)
        return layout

    async def delete(self, wf_id: str) -> None:
        await self.repo.delete(wf_id)


def _pydantic_errors(exc: ValidationError) -> list[dict[str, Any]]:
    return [{"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()]
