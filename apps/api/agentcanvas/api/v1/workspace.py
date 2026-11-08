"""Workspace files (M5-T1/T2/T3): browse, read, save (with ruff diagnostics), delete, scaffold scripts.

S17: `?workflow_id=` scopes a request to one workflow's own code folder (`flows/<workflow>/`, created on first
use) next to the project's shared code at the root.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import get_session
from agentcanvas.db.repositories import ProjectRepo, WorkflowRepo
from agentcanvas.errors import Conflict, InvalidInput, NotFound
from agentcanvas.sandbox.policy import (
    POLICY_FILE,
    PolicyError,
    SandboxPolicy,
    check_policy,
    load_policy,
    parse_policy,
)
from agentcanvas.workspace import Workspace, WorkspaceError
from agentcanvas.workspace.store import workflow_key

router = APIRouter(prefix="/projects/{project_id}", tags=["workspace"])


class FileOut(BaseModel):
    path: str
    size: int
    modified: float


class LintItem(BaseModel):
    line: int
    column: int
    end_line: int
    end_column: int
    code: str
    message: str


class FileContent(BaseModel):
    path: str
    content: str
    diagnostics: list[LintItem] = Field(default_factory=list)


class FileSave(BaseModel):
    content: str


class SavedFile(FileOut):
    diagnostics: list[LintItem] = Field(default_factory=list)


class NewScript(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    writes: list[str] = Field(default_factory=list)  # channels the scaffold returns
    workflow_id: str | None = Field(None, description="create it in this workflow's own folder (S17)")


class FilesOut(BaseModel):
    files: list[FileOut]
    folder: str | None = Field(None, description="the workflow's own code folder, when `workflow_id` was given")


async def workspace(project_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> Workspace:
    await ProjectRepo(session).require(project_id)
    return request.app.state.projects.workspace(project_id)


_LOCKS: dict[str, asyncio.Lock] = {}


async def _off_loop(ws: Workspace, fn: Any) -> Any:
    """A workspace change (git add and commit) in a worker thread, one at a time per workspace: it used to run on
    the event loop and stall every stream and request of this server while git and ruff ran."""
    lock = _LOCKS.setdefault(str(ws.root), asyncio.Lock())
    async with lock:
        return await asyncio.to_thread(_guard, fn)


def _guard(fn):
    try:
        return fn()
    except WorkspaceError as exc:
        raise InvalidInput(str(exc)) from exc
    except FileNotFoundError as exc:
        raise NotFound(f"file '{exc.args[0]}' not found") from exc


async def _own_folder(session: AsyncSession, project_id: str, ws: Workspace, wf_id: str) -> str:
    """The workflow's code folder, created on first use; the workflow must belong to this project."""
    row = await WorkflowRepo(session).require(wf_id)
    if row.project_id != project_id:
        raise NotFound(f"workflow '{wf_id}' not found")
    return await _off_loop(ws, lambda: ws.ensure_workflow(wf_id))  # a first use commits the new folder


@router.get("/files", response_model=list[FileOut])
async def list_files(ws: Workspace = Depends(workspace)) -> list[FileOut]:
    return [FileOut(path=f.path, size=f.size, modified=f.modified) for f in ws.list()]


@router.get("/workflow-files/{wf_id}", response_model=FilesOut)
async def list_workflow_files(
    project_id: str, wf_id: str, session: AsyncSession = Depends(get_session), ws: Workspace = Depends(workspace)
) -> FilesOut:
    """One workflow's view of the workspace: its own folder (created on first use) and the shared code, without
    other workflows' folders."""
    folder = await _own_folder(session, project_id, ws, wf_id)
    files = [
        FileOut(path=f.path, size=f.size, modified=f.modified)
        for f in ws.list()
        if not f.path.startswith("flows/") or f.path.startswith(f"{folder}/")
    ]
    return FilesOut(files=files, folder=folder)


def _lint(ws: Workspace, path: str, request: Request, project_id: str) -> list[LintItem]:
    """ruff for Python files; the sandbox policy checks (E082-E085, W082, W083, W074) for `sandbox.json`."""
    items = [LintItem(**d) for d in ws.lint(path)]
    if path.strip("/") == POLICY_FILE:
        projects = request.app.state.projects
        mode, secrets = projects.settings.script_execution, set(projects.secrets.get(project_id, {}))
        items += [
            LintItem(line=1, column=1, end_line=1, end_column=1, code=p["code"], message=p["message"])
            for p in check_policy(ws.root, mode, secrets, projects.settings.env)
        ]
    return items


@router.get("/files/{path:path}", response_model=FileContent)
async def read_file(project_id: str, path: str, request: Request, ws: Workspace = Depends(workspace)) -> FileContent:
    content = _guard(lambda: ws.read(path))
    diagnostics = await asyncio.to_thread(_lint, ws, path, request, project_id)  # ruff runs as a process
    return FileContent(path=path, content=content, diagnostics=diagnostics)


@router.put("/files/{path:path}", response_model=SavedFile)
async def save_file(
    project_id: str, path: str, body: FileSave, request: Request, ws: Workspace = Depends(workspace)
) -> SavedFile:
    info = await _off_loop(ws, lambda: ws.write(path, body.content))
    diagnostics = await asyncio.to_thread(_lint, ws, path, request, project_id)
    return SavedFile(path=info.path, size=info.size, modified=info.modified, diagnostics=diagnostics)


class FileMove(BaseModel):
    path: str = Field(min_length=1, max_length=500, description="the file to move")
    to: str = Field(min_length=1, max_length=500, description="its new path (a new name, or another folder)")


@router.post("/files-move", response_model=FileOut)
async def move_file(body: FileMove, ws: Workspace = Depends(workspace)) -> FileOut:
    """Rename or move a file. Blocks and workflows that name it by its old import path stop finding it."""
    info = await _off_loop(ws, lambda: _guard(lambda: ws.move(body.path, body.to)))
    return FileOut(path=info.path, size=info.size, modified=info.modified)


@router.delete("/files/{path:path}", status_code=204)
async def delete_file(path: str, ws: Workspace = Depends(workspace)) -> Response:
    await _off_loop(ws, lambda: ws.delete(path))
    return Response(status_code=204)


SCRIPT_TEMPLATE = '''"""Script step `{name}`: return the fields to update."""


def run(state: dict) -> dict:
{body}
'''


@router.post("/scripts", status_code=201)
async def new_script(
    project_id: str, body: NewScript, session: AsyncSession = Depends(get_session), ws: Workspace = Depends(workspace)
) -> dict[str, Any]:
    """Scaffold `scripts/<name>.py` for a Script node (FR-SCR-01), or `flows/<workflow>/scripts/<name>.py`
    with `workflow_id` (S17); returns its import path."""
    folder = await _own_folder(session, project_id, ws, body.workflow_id) if body.workflow_id else ""
    path = f"{folder}/scripts/{body.name}.py" if folder else f"scripts/{body.name}.py"
    module = path.removesuffix(".py").replace("/", ".")
    if (ws.root / path).exists():
        raise Conflict(f"'{path}' already exists", import_path=f"{module}:run")
    writes = [w for w in body.writes if re.fullmatch(r"[a-z][a-z0-9_]*", w)]
    lines = ["    # read inputs with state.get(...)"] + (
        ["    return {" + ", ".join(f'"{w}": None' for w in writes) + "}"] if writes else ["    return {}"]
    )
    await _off_loop(ws, lambda: ws.write(path, SCRIPT_TEMPLATE.format(name=body.name, body="\n".join(lines))))
    return {"path": path, "import_path": f"{module}:run"}


class CatalogOut(BaseModel):
    tools: list[dict[str, Any]]
    nodes: list[str]  # custom node types (their manifests are in /node-types?project_id=)
    scripts: list[dict[str, Any]]
    handlers: list[dict[str, Any]]
    problems: list[dict[str, str]]
    schemas: list[str] = Field(default_factory=list)  # event types with a schema in events/schemas/


@router.get("/workspace/catalog", response_model=CatalogOut)
async def workspace_catalog(
    project_id: str,
    request: Request,
    _: Workspace = Depends(workspace),
    workflow_id: str | None = Query(None, description="only shared code and this workflow's own (S17)"),
) -> CatalogOut:
    """What the workspace contributes to the palette: tools, custom nodes, scripts, event handlers (AST scan)."""
    projects = request.app.state.projects
    cat = projects.catalog(project_id)
    key = _guard(lambda: workflow_key(workflow_id)) if workflow_id else None

    def visible(item: dict[str, Any]) -> bool:
        return workflow_id is None or item.get("workflow") in (None, key)

    policy = check_policy(
        projects.workspace(project_id).root,
        projects.settings.script_execution,
        set(projects.secrets.get(project_id, {})),
        projects.settings.env,
    )
    return CatalogOut(
        tools=[t for t in cat.tools if visible(t)],
        nodes=[m.type for m in cat.nodes],
        scripts=[x for x in cat.scripts if visible(x)],
        handlers=cat.handlers,
        problems=[*cat.problems, *policy],
        schemas=sorted(
            p.stem
            for p in (request.app.state.projects.workspace(project_id).root / "events" / "schemas").glob("*.json")
        ),
    )


# ------------------------------------------------------------------ sandbox policy (Settings > Sandbox) ----
class SandboxOut(BaseModel):
    mode: str = Field(description="how this server runs workspace code (AGENTCANVAS_SCRIPT_EXECUTION)")
    enforces_egress: bool = Field(description="an egress list gives code exactly those hosts")
    refuses_egress: bool = Field(description="an egress list makes this server refuse to run the code")
    applies_inject: bool = Field(description="inject rules add their headers (LangSmith only)")
    policy: SandboxPolicy | None = Field(description="the saved policy; null when sandbox.json is invalid")
    error: str | None = None
    problems: list[LintItem] = []


def _sandbox_out(ws: Workspace, request: Request, project_id: str) -> SandboxOut:
    settings = request.app.state.projects.settings
    mode = settings.script_execution
    enforces = mode in ("docker", "langsmith") or (mode == "remote" and bool(settings.egress_proxy))
    try:
        policy, error = load_policy(ws.root), None
    except PolicyError as exc:
        policy, error = None, str(exc)
    return SandboxOut(
        mode=mode, enforces_egress=enforces, applies_inject=mode == "langsmith",
        refuses_egress=not enforces and mode in ("inprocess", "subprocess") and settings.env in ("staging", "prod"),
        policy=policy, error=error, problems=_lint(ws, POLICY_FILE, request, project_id),
    )  # fmt: skip


@router.get("/sandbox", response_model=SandboxOut)
async def get_sandbox(project_id: str, request: Request, ws: Workspace = Depends(workspace)) -> SandboxOut:
    """The project's sandbox policy (`sandbox.json`) and what this server does with it."""
    return await asyncio.to_thread(_sandbox_out, ws, request, project_id)


@router.put("/sandbox", response_model=SandboxOut)
async def put_sandbox(
    project_id: str, body: SandboxPolicy, request: Request, ws: Workspace = Depends(workspace)
) -> SandboxOut:
    """Save the sandbox policy as `sandbox.json` (the same checks as the file: hostnames, secrets, limits)."""
    text = json.dumps(body.model_dump(exclude_defaults=True), indent=2) + "\n"
    try:
        parse_policy(text)
    except PolicyError as exc:
        raise InvalidInput(str(exc)) from None
    await _off_loop(ws, lambda: ws.write(POLICY_FILE, text))
    return await asyncio.to_thread(_sandbox_out, ws, request, project_id)
