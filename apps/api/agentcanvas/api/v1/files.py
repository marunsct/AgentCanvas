"""Files for runs (G-5): attach an image, a PDF or a document to a file field of a workflow's input.

Upload with `POST /v1/projects/{project_id}/uploads` (multipart field `file`); the answer is the reference to put in
the run's input, e.g. `{"photo": {"kind": "file", "id": "fil_…", …}}`. See `services/files.py` for the limits.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note
from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.models import StoredFile
from agentcanvas.db.repositories import ProjectRepo
from agentcanvas.services.files import SERVE_HEADERS, FileService

router = APIRouter(prefix="/projects/{project_id}/uploads", tags=["files"])


class FileOut(BaseModel):
    """A file reference, as a run's input carries it."""

    kind: str = "file"
    id: str
    name: str
    media_type: str = Field(description="what the content is, read from its first bytes")
    size: int


def file_out(row: StoredFile) -> FileOut:
    return FileOut(id=row.id, name=row.name, media_type=row.media_type, size=row.size)


def file_service(request: Request, session: AsyncSession) -> FileService:
    settings = request.app.state.settings
    return FileService(
        session, int(settings.max_upload_mb * 1024 * 1024), int(settings.max_project_files_mb * 1024 * 1024)
    )


def service(request: Request, session: AsyncSession = Depends(get_session)) -> FileService:
    return file_service(request, session)


async def read_upload(upload: UploadFile, files: FileService) -> bytes:
    """The upload's bytes, reading one byte past the limit at most."""
    data = await upload.read(files.max_bytes + 1)
    if len(data) > files.max_bytes:
        raise files.too_large(upload.filename or "file")
    return data


def serve(row: StoredFile) -> Response:
    """A stored file as a download: never shown as a page, whatever it contains."""
    disposition = f"attachment; filename*=UTF-8''{quote(row.name, safe='')}"
    return Response(
        content=bytes(row.data), media_type=row.media_type,
        headers={**SERVE_HEADERS, "Content-Disposition": disposition},
    )  # fmt: skip


@router.post("", response_model=FileOut, status_code=201)
async def upload_file(
    project_id: str,
    request: Request,
    file: Annotated[UploadFile, File(description="an image (PNG, JPEG, GIF, WebP), a PDF, a .docx or a text file")],
    files: FileService = Depends(service),
    session: AsyncSession = Depends(get_session),
    user: CurrentUser = Depends(current_user),
) -> FileOut:
    """Upload a file for a run's file field (10 MB at most by default). Its type is read from its content."""
    await ProjectRepo(session).require(project_id)
    row = await files.add(project_id, file.filename, await read_upload(file, files), created_by=user.id)
    note(request, file=row.id, media_type=row.media_type, size=row.size)
    return file_out(row)


@router.get("/{file_id}", response_class=Response, responses={200: {"content": {"application/octet-stream": {}}}})
async def download_file(
    project_id: str, file_id: str, files: FileService = Depends(service), session: AsyncSession = Depends(get_session)
) -> Response:
    """The file's bytes, as a download."""
    await ProjectRepo(session).require(project_id)
    return serve(await files.get(project_id, file_id, with_data=True))
