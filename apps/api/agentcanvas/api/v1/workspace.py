"""Workspace files (M5-T1/T2/T3): browse, read, save (with ruff diagnostics), delete, scaffold scripts."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import get_session
from agentcanvas.db.repositories import ProjectRepo
from agentcanvas.errors import Conflict, InvalidInput, NotFound
from agentcanvas.workspace import Workspace, WorkspaceError

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


async def workspace(project_id: str, request: Request, session: AsyncSession = Depends(get_session)) -> Workspace:
    await ProjectRepo(session).require(project_id)
    return request.app.state.projects.workspace(project_id)


def _guard(fn):
    try:
        return fn()
    except WorkspaceError as exc:
        raise InvalidInput(str(exc)) from exc
    except FileNotFoundError as exc:
        raise NotFound(f"file '{exc.args[0]}' not found") from exc


@router.get("/files", response_model=list[FileOut])
async def list_files(ws: Workspace = Depends(workspace)) -> list[FileOut]:
    return [FileOut(path=f.path, size=f.size, modified=f.modified) for f in ws.list()]


@router.get("/files/{path:path}", response_model=FileContent)
async def read_file(path: str, ws: Workspace = Depends(workspace)) -> FileContent:
    content = _guard(lambda: ws.read(path))
    return FileContent(path=path, content=content, diagnostics=[LintItem(**d) for d in ws.lint(path)])


@router.put("/files/{path:path}", response_model=SavedFile)
async def save_file(path: str, body: FileSave, ws: Workspace = Depends(workspace)) -> SavedFile:
    info = _guard(lambda: ws.write(path, body.content))
    return SavedFile(path=info.path, size=info.size, modified=info.modified, diagnostics=ws.lint(path))  # type: ignore[arg-type]


@router.delete("/files/{path:path}", status_code=204)
async def delete_file(path: str, ws: Workspace = Depends(workspace)) -> Response:
    _guard(lambda: ws.delete(path))
    return Response(status_code=204)


SCRIPT_TEMPLATE = '''"""Script node `{name}`: receives the workflow state, returns the channels to update."""


def run(state: dict) -> dict:
{body}
'''


@router.post("/scripts", status_code=201)
async def new_script(body: NewScript, ws: Workspace = Depends(workspace)) -> dict[str, Any]:
    """Scaffold `scripts/<name>.py` for a Script node (FR-SCR-01); returns its import path."""
    path = f"scripts/{body.name}.py"
    if (ws.root / path).exists():
        raise Conflict(f"'{path}' already exists", import_path=f"scripts.{body.name}:run")
    writes = [w for w in body.writes if re.fullmatch(r"[a-z][a-z0-9_]*", w)]
    lines = ["    # read inputs with state.get(...)"] + (
        ["    return {" + ", ".join(f'"{w}": None' for w in writes) + "}"] if writes else ["    return {}"]
    )
    _guard(lambda: ws.write(path, SCRIPT_TEMPLATE.format(name=body.name, body="\n".join(lines))))
    return {"path": path, "import_path": f"scripts.{body.name}:run"}
