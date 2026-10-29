"""Agents designed for other frameworks (S21): the frameworks, an Agent block's generated code, and its export."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from agentcanvas import designs
from agentcanvas.api.deps import attachment
from agentcanvas.api.v1.workflows import service
from agentcanvas.designs.export import export_zip
from agentcanvas.errors import InvalidInput, NotFound
from agentcanvas.ir.models import Node, Workflow
from agentcanvas.services.workflows import WorkflowService

router = APIRouter(tags=["workflows"])


class AgentFrameworkOut(BaseModel):
    id: str
    label: str
    packages: list[str]
    api: str
    structured_output: bool
    installed: bool  # on this server: drawn agents for it run here
    own_env: bool  # S28: its packages clash with LangGraph's, so it runs as a part with an environment of its own


@router.get("/agent-frameworks", response_model=list[AgentFrameworkOut])
async def list_agent_frameworks() -> list[AgentFrameworkOut]:
    """Frameworks an Agent block can be designed for: generated in that framework's own code (S21)."""
    return [
        AgentFrameworkOut(
            id=f.id,
            label=f.label,
            packages=list(f.packages),
            api=f.api,
            structured_output=f.structured_output,
            installed=designs.installed(f.id),
            own_env=f.own_env,
        )
        for f in designs.AGENT_FRAMEWORKS.values()
    ]


def _agent(wf: Workflow, node_id: str, framework: str | None) -> tuple[Node, str]:
    node = next((n for n in wf.nodes if n.id == node_id), None)
    if node is None or node.type not in designs.DESIGN_TYPES:
        raise NotFound(f"no Agent block '{node_id}' in this workflow")
    chosen = framework or node.engine
    if chosen not in designs.AGENT_FRAMEWORKS:
        names = ", ".join(designs.AGENT_FRAMEWORKS)
        raise InvalidInput(f"choose the framework to design this agent for (one of {names})")
    return node, chosen


class AgentCodeOut(BaseModel):
    framework: str
    label: str
    code: str
    problems: list[str]  # why it cannot run in AgentCanvas as designed
    export_problems: list[str]  # why it cannot be exported (includes `problems`)


@router.get("/workflows/{wf_id}/agents/{node_id}/code", response_model=AgentCodeOut)
async def agent_code(
    wf_id: str, node_id: str, framework: str | None = None, svc: WorkflowService = Depends(service)
) -> AgentCodeOut:
    """An Agent block as a native agent for `framework` (default: the block's engine): the code that runs."""
    wf = (await svc.get(wf_id)).workflow
    node, fw = _agent(wf, node_id, framework)
    design = designs.design_for(wf, node)
    return AgentCodeOut(
        framework=fw,
        label=designs.AGENT_FRAMEWORKS[fw].label,
        code=designs.generate(fw, design),
        problems=designs.problems(fw, design),
        export_problems=designs.export_problems(fw, design),
    )


@router.get("/workflows/{wf_id}/agents/{node_id}/code.py", response_class=PlainTextResponse)
async def agent_code_file(
    wf_id: str, node_id: str, framework: str | None = None, svc: WorkflowService = Depends(service)
) -> str:
    wf = (await svc.get(wf_id)).workflow
    node, fw = _agent(wf, node_id, framework)
    return designs.generate(fw, designs.design_for(wf, node))


@router.get("/workflows/{wf_id}/agents/{node_id}/export.zip", response_class=Response)
async def agent_export(
    wf_id: str, node_id: str, request: Request, framework: str | None = None, svc: WorkflowService = Depends(service)
) -> Response:
    """One Agent block as a native project for its framework: runner, pinned packages, README and tests."""
    view = await svc.get(wf_id)
    node, fw = _agent(view.workflow, node_id, framework)
    name, data = export_zip(
        view.workflow,
        designs.design_for(view.workflow, node),
        fw,
        workspace=request.app.state.projects.workspace(view.row.project_id).root,
    )
    return Response(data, media_type="application/zip", headers=attachment(name))
