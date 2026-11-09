"""Agents designed for other frameworks (S21): the frameworks, an Agent block's generated code, and its export.

G-12: Claude Managed Agents is listed with them (`hosted`): its "code" is the agent file `ant apply` reads, and its
export is an `ant apply` bundle (`designs.managed`).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from agentcanvas import designs
from agentcanvas.api.deps import attachment
from agentcanvas.api.v1.workflows import service
from agentcanvas.designs import managed
from agentcanvas.designs.export import all_export_problems, export_zip
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
    hosted: bool = False  # G-12: a platform that runs the agent (Claude Managed Agents); here it runs as the
    # workflow's own agent, and the export is that platform's files


@router.get("/agent-frameworks", response_model=list[AgentFrameworkOut])
async def list_agent_frameworks() -> list[AgentFrameworkOut]:
    """Frameworks an Agent block can be designed for: generated in that framework's own code (S21), and the agent
    platforms it can be exported to (G-12)."""
    out = [
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
    p = managed.PLATFORM
    out.append(AgentFrameworkOut(id=p.id, label=p.label, packages=list(p.packages), api=p.api,
                                 structured_output=p.structured_output, installed=True, own_env=False,
                                 hosted=True))  # fmt: skip
    return out


def _agent(wf: Workflow, node_id: str, framework: str | None) -> tuple[Node, str]:
    node = next((n for n in wf.nodes if n.id == node_id), None)
    if node is None or node.type not in designs.DESIGN_TYPES:
        raise NotFound(f"no Agent block '{node_id}' in this workflow")
    chosen = framework or node.engine
    if chosen not in designs.AGENT_FRAMEWORKS and chosen != managed.ID:
        names = ", ".join([*designs.AGENT_FRAMEWORKS, managed.ID])
        raise InvalidInput(f"choose the framework to design this agent for (one of {names})")
    return node, chosen


class DesignGapOut(BaseModel):
    blocks: bool  # the export cannot be made until this is dealt with
    text: str


class AgentCodeOut(BaseModel):
    framework: str
    label: str
    code: str
    file: str = "agent.py"  # where `code` goes in the export
    problems: list[str]  # why it cannot run in AgentCanvas as designed
    export_problems: list[str]  # why it cannot be exported (includes `problems`)
    gaps: list[DesignGapOut] = []  # G-12: what does not carry over to an agent platform


@router.get("/workflows/{wf_id}/agents/{node_id}/code", response_model=AgentCodeOut)
async def agent_code(
    wf_id: str, node_id: str, request: Request, framework: str | None = None, svc: WorkflowService = Depends(service)
) -> AgentCodeOut:
    """An Agent block as a native agent for `framework` (default: the block's engine): the code that runs."""
    view = await svc.get(wf_id)
    wf = view.workflow
    node, fw = _agent(wf, node_id, framework)
    design = designs.design_for(wf, node)
    if fw == managed.ID:
        workspace = request.app.state.projects.workspace(view.row.project_id).root
        return AgentCodeOut(
            framework=fw, label=managed.PLATFORM.label, code=_agent_file(design, workspace, wf.id),
            file=f"agents/{managed.slug(design.name)}/agent.md", problems=[],
            export_problems=managed.export_problems(design),
            gaps=[DesignGapOut(blocks=g.blocks, text=g.text) for g in managed.gaps(design, workspace, wf.id)],
        )  # fmt: skip
    return AgentCodeOut(
        framework=fw,
        label=designs.AGENT_FRAMEWORKS[fw].label,
        code=designs.generate(fw, design),
        problems=designs.problems(fw, design),
        export_problems=all_export_problems(fw, design),
    )


def _agent_file(design: designs.AgentDesign, workspace: Path, wf_id: str) -> str:
    return managed.agent_md(design, workspace, sorted(managed.skills(workspace, wf_id)[0]))


@router.get("/workflows/{wf_id}/agents/{node_id}/code.py", response_class=PlainTextResponse)
async def agent_code_file(
    wf_id: str, node_id: str, request: Request, framework: str | None = None, svc: WorkflowService = Depends(service)
) -> str:
    view = await svc.get(wf_id)
    wf = view.workflow
    node, fw = _agent(wf, node_id, framework)
    if fw == managed.ID:  # the agent file, as `ant apply` reads it
        workspace = request.app.state.projects.workspace(view.row.project_id).root
        return _agent_file(designs.design_for(wf, node), workspace, wf.id)
    return designs.generate(fw, designs.design_for(wf, node))


@router.get("/workflows/{wf_id}/agents/{node_id}/export.zip", response_class=Response)
async def agent_export(
    wf_id: str, node_id: str, request: Request, framework: str | None = None, svc: WorkflowService = Depends(service)
) -> Response:
    """One Agent block as a native project for its framework: runner, pinned packages, README and tests; for Claude
    Managed Agents, the files `ant apply` reads with a session runner, `make doctor` and tests (G-12)."""
    view = await svc.get(wf_id)
    node, fw = _agent(view.workflow, node_id, framework)
    workspace = request.app.state.projects.workspace(view.row.project_id).root
    if fw == managed.ID:  # G-12: an `ant apply` bundle
        name, data = managed.export_zip(view.workflow, designs.design_for(view.workflow, node), workspace)
        return Response(data, media_type="application/zip", headers=attachment(name))
    name, data = export_zip(
        view.workflow,
        designs.design_for(view.workflow, node),
        fw,
        workspace=workspace,
    )
    return Response(data, media_type="application/zip", headers=attachment(name))
