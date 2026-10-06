"""Catalog endpoints: node types, models, templates, IR JSON Schema, expression checks."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel

from agentcanvas.api.deps import get_settings_dep
from agentcanvas.expr import ExpressionError, check_condition
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import ModelInfo, NodeManifest, ProviderInfo, default_registry, installed_providers
from agentcanvas.services.workflows import list_templates
from agentcanvas.settings import Settings

router = APIRouter(tags=["catalog"])
_jinja = SandboxedEnvironment()


@router.get("/node-types", response_model=list[NodeManifest])
def node_types(request: Request, project_id: str | None = None) -> list[NodeManifest]:
    """Built-in node types, plus the project's workspace custom nodes when `project_id` is given."""
    if project_id:
        return request.app.state.projects.registry(project_id).all()
    return default_registry().all()


@router.get("/models", response_model=list[ModelInfo])
def models() -> list[ModelInfo]:
    return default_registry().models()


@router.get("/model-providers", response_model=list[ProviderInfo])
def model_providers() -> list[ProviderInfo]:
    """Model providers: the `provider:` prefixes a chat model can use, where each one's key comes from, and whether
    this server has its integration installed."""
    return installed_providers()


class TemplateInfo(BaseModel):
    id: str
    name: str
    description: str
    node_count: int


@router.get("/templates", response_model=list[TemplateInfo])
def templates(settings: Settings = Depends(get_settings_dep)) -> list[TemplateInfo]:
    return [
        TemplateInfo(id=k, name=v["name"], description=v.get("description", ""), node_count=len(v.get("nodes", [])))
        for k, v in list_templates(settings).items()
    ]


@router.get("/ir-schema")
def ir_schema() -> dict[str, Any]:
    """JSON Schema of the Graph IR (the web app generates its TypeScript types from it)."""
    return Workflow.model_json_schema()


class ExpressionCheck(BaseModel):
    kind: Literal["cel", "template"]
    source: str


class ExpressionResult(BaseModel):
    ok: bool
    error: str | None = None


@router.post("/expressions/check", response_model=ExpressionResult)
def check_expression(body: ExpressionCheck) -> ExpressionResult:
    try:
        if body.kind == "cel":
            check_condition(body.source)
        else:
            _jinja.parse(body.source)
    except (ExpressionError, TemplateSyntaxError) as exc:
        return ExpressionResult(ok=False, error=str(exc))
    return ExpressionResult(ok=True)
