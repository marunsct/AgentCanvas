"""Catalog endpoints: node types, models, templates, IR JSON Schema, expression checks."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel, Field

from agentcanvas.api.deps import get_settings_dep
from agentcanvas.expr import ExpressionError, check_condition
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import (
    ModelInfo,
    NodeManifest,
    ProviderInfo,
    adapters,
    default_registry,
    installed_providers,
    load_gallery,
)
from agentcanvas.registry.adapters import AdapterManifest
from agentcanvas.services import template_kit
from agentcanvas.services.workflows import list_templates, runs_in, template_gallery
from agentcanvas.settings import Settings

router = APIRouter(tags=["catalog"])
_jinja = SandboxedEnvironment()


@router.get("/node-types", response_model=list[NodeManifest])
def node_types(request: Request, project_id: str | None = None) -> list[NodeManifest]:
    """Built-in node types, plus the project's workspace custom nodes when `project_id` is given."""
    if project_id:
        return request.app.state.projects.registry(project_id).all()
    return default_registry().all()


class FrameworkOut(AdapterManifest):
    ready: bool = Field(True, description="its runtime is set up here, so its workflows run (else see `setup`)")
    setup: str | None = Field(None, description="what to do so its workflows run on this server")


@router.get("/frameworks", response_model=list[FrameworkOut])
def frameworks() -> list[FrameworkOut]:
    """The frameworks this server can run workflows on (S20): abilities and which blocks each runs, natively or by
    emulation, and whether each one's runtime is set up here. The first one is the default for new workflows."""
    from agentcanvas.adapters import installed_adapters
    from agentcanvas.adapters.setup import setup_hint

    ready = installed_adapters()
    out = []
    for m in adapters.installed():
        hint = setup_hint(ready[m.id]) if m.id in ready else None
        out.append(FrameworkOut(**m.model_dump(), ready=hint is None, setup=hint))
    return out


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
    # S23 gallery (from the template's `metadata.gallery`; absent on templates that have none)
    block_count: int = 0  # steps and resources (models, tools, middleware) on the canvas
    category: str | None = None
    tags: list[str] = []
    difficulty: Literal["beginner", "intermediate", "advanced"] | None = None
    sample_input: dict[str, Any] | None = None
    kit: list[str] = Field(
        default_factory=list, description="what it adds beside the workflow, e.g. '2 workspace files', 'a dataset'"
    )


class ConnectorSecret(BaseModel):
    name: str
    what: str = ""
    where: str = ""


class McpConnector(BaseModel):
    id: str
    name: str
    category: str
    description: str
    publisher: Literal["official", "vendor", "community"] = "community"
    config: dict[str, Any]
    secrets: list[ConnectorSecret] = []
    setup: str = ""
    auth: Literal["none", "key", "oauth"] = "none"
    docs: str = ""
    source: str = ""
    verified: str = ""
    local: bool = False  # starts a process on the server (stdio): local edition, enabled by an admin


@router.get("/mcp-connectors", response_model=list[McpConnector])
def mcp_connectors() -> list[McpConnector]:
    """The one-click connector gallery (S15): curated MCP servers with their settings and the secrets they need."""
    return [McpConnector(**c, local=c["config"].get("transport") == "stdio") for c in load_gallery()["connectors"]]


@router.get("/templates", response_model=list[TemplateInfo])
def templates(settings: Settings = Depends(get_settings_dep)) -> list[TemplateInfo]:
    """The template gallery (S23): each starter template with its category, tags, difficulty and a sample input; on
    the hosted service or a server that runs workspace code in a sandbox, not those with a block that runs workspace
    code inside the server (E041)."""
    return [_template_info(k, v) for k, v in list_templates(settings).items() if runs_in(settings, v)]


def _template_info(key: str, doc: dict[str, Any]) -> TemplateInfo:
    g = template_gallery(doc)
    return TemplateInfo(
        id=key, name=doc["name"], description=doc.get("description", ""), node_count=len(doc.get("nodes", [])),
        block_count=len(doc.get("nodes", [])) + len(doc.get("resources", [])), category=g.get("category"),
        tags=g.get("tags") or [], difficulty=g.get("difficulty"), sample_input=g.get("sample_input"),
        kit=template_kit.summary(template_kit.kit_of(doc)),
    )  # fmt: skip


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
