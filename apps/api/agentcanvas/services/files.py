"""Files people attach to runs (G-5): upload, what a file really is, references in run input, retention.

Uploads are untrusted. A file is at most `AGENTCANVAS_MAX_UPLOAD_MB` (10 MB by default); its type is read from its
first bytes, never taken from the upload's `Content-Type` or the name alone; only images (PNG, JPEG, GIF, WebP),
PDF, Word (.docx) and UTF-8 text are kept, and a name keeps no folder. The bytes are stored per project (a row of
`stored_file`, which has the tenant policy of every project table); runs and checkpoints hold a reference
(`agentcanvas/ir/files.py`). Files are served back only as downloads (`Content-Disposition: attachment`) with a
policy that runs nothing, so a file can never become a page of the app.

Retention follows conversations: a file no run has used for as long as the organisation keeps conversations
(`retention_days`) is deleted by the retention job, and every file goes with its project.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import unicodedata
import zipfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import Project, StoredFile
from agentcanvas.errors import InvalidInput, NotFound
from agentcanvas.ids import new_id
from agentcanvas.ir.files import PDF_TYPE, accepts, file_refs, is_file_ref, is_file_schema, type_matches

DEFAULT_MAX_BYTES = 10 * 1024 * 1024
MAX_NAME = 200
MAX_FILES_PER_RUN = 20
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_TYPES = {".md": "text/markdown", ".markdown": "text/markdown", ".csv": "text/csv", ".json": "application/json"}
KINDS = "images (PNG, JPEG, GIF, WebP), PDF, Word (.docx) and text files"
# served files: never a page, never a script, whatever a browser would guess
SERVE_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private, no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
}
FileReader = Callable[[dict[str, Any]], Awaitable[tuple[bytes, str, str]]]  # ref -> (bytes, media type, name)


def safe_name(name: str | None) -> str:
    """The file's own name, as a label: no folders (`../`, `C:\\`), no control characters, at most 200 characters."""
    raw = (name or "").replace("\x00", "")
    base = PureWindowsPath(PurePosixPath(raw).name).name  # both separators
    clean = "".join(ch for ch in unicodedata.normalize("NFC", base) if unicodedata.category(ch)[0] != "C").strip()
    clean = clean.lstrip(".") or "file"
    if len(clean) > MAX_NAME:
        stem, dot, ext = clean.rpartition(".")
        clean = (stem[: MAX_NAME - len(ext) - 1] + dot + ext) if dot and len(ext) <= 10 else clean[:MAX_NAME]
    return clean


def sniff(name: str, data: bytes) -> str:
    """What the file is, from its content: one of the kept kinds, else InvalidInput."""
    head = data[:16]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head.startswith(b"%PDF-"):
        return PDF_TYPE
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" in z.namelist():
                    return DOCX_TYPE
        except zipfile.BadZipFile:
            pass
        raise InvalidInput(f"'{name}': archives are not accepted; attach {KINDS}")
    if b"\x00" not in data:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return TEXT_TYPES.get(PurePosixPath(name.lower()).suffix, "text/plain")
    raise InvalidInput(f"'{name}': this kind of file is not accepted; attach {KINDS}")


def ref_of(row: StoredFile) -> dict[str, Any]:
    """The reference a run carries for a stored file."""
    return {"kind": "file", "id": row.id, "name": row.name, "media_type": row.media_type, "size": row.size}


class FileService:
    def __init__(self, session: AsyncSession, max_bytes: int = DEFAULT_MAX_BYTES, quota_bytes: int = 0):
        self.s, self.max_bytes, self.quota_bytes = session, max_bytes, quota_bytes  # quota 0: no limit per project

    def too_large(self, name: str) -> InvalidInput:
        return InvalidInput(f"'{name}' is larger than {self.max_bytes / (1024 * 1024):.3g} MB")

    async def add(
        self,
        project_id: str,
        name: str | None,
        data: bytes,
        *,
        source: str = "upload",
        created_by: str | None = None,
        deployment_id: str | None = None,
        visitor_hash: str | None = None,
        accept: str | None = None,
        commit: bool = True,
    ) -> StoredFile:
        clean = safe_name(name)
        if len(data) > self.max_bytes:
            raise self.too_large(clean)
        if not data:
            raise InvalidInput(f"'{clean}' is empty")
        if self.quota_bytes:
            kept = await self.s.scalar(
                select(func.coalesce(func.sum(StoredFile.size), 0)).where(StoredFile.project_id == project_id)
            )
            if (kept or 0) + len(data) > self.quota_bytes:
                raise InvalidInput(
                    f"this project keeps at most {self.quota_bytes / (1024 * 1024):.4g} MB of attached files; "
                    "they are deleted when no conversation has used them for the retention time"
                )
        media_type = sniff(clean, data)
        if not type_matches(media_type, accept):
            raise InvalidInput(f"'{clean}' is {media_type}; this takes {accept}")
        row = StoredFile(
            id=new_id("fil"), project_id=project_id, name=clean, media_type=media_type, size=len(data),
            sha256=hashlib.sha256(data).hexdigest(), data=data, source=source, created_by=created_by,
            deployment_id=deployment_id, visitor_hash=visitor_hash,
        )  # fmt: skip
        self.s.add(row)
        if commit:
            await self.s.commit()
        else:
            await self.s.flush()
        return row

    async def get(self, project_id: str, file_id: str, *, with_data: bool = False) -> StoredFile:
        q = select(StoredFile).where(StoredFile.id == file_id, StoredFile.project_id == project_id)
        if with_data:
            from sqlalchemy.orm import undefer

            q = q.options(undefer(StoredFile.data))
        row = await self.s.scalar(q)
        if row is None:
            raise NotFound(f"file '{file_id}' not found")
        return row

    async def of_visitor(self, deployment_id: str, visitor_hash: str, file_ids: list[str]) -> list[StoredFile]:
        """Files a share link's visitor uploaded on that link, in the order asked (404 for any other)."""
        rows = (
            await self.s.scalars(
                select(StoredFile).where(StoredFile.id.in_(file_ids), StoredFile.deployment_id == deployment_id)
            )
        ).all()
        mine = {r.id: r for r in rows if r.visitor_hash == visitor_hash}
        if missing := [f for f in file_ids if f not in mine]:
            raise NotFound(f"file '{missing[0]}' not found")
        return [mine[f] for f in file_ids]

    async def claim(
        self,
        project_id: str,
        values: dict[str, Any],
        schemas: dict[str, dict[str, Any]] | None = None,
        *,
        deployment_id: str | None = None,
        visitor_hash: str | None = None,
    ) -> dict[str, Any]:
        """A run's input with every file reference checked against the project's files and written as the server
        knows the file (its real type and size, whatever the caller said). Bytes sent inline (`data`) are stored
        first, so checkpoints keep only the reference. A file field that takes only some types refuses others.

        A run of a deployment (its key, a share link, a trigger) uses only files given to that deployment: uploaded
        with its key, or by the same share-link visitor (`visitor_hash`), never the project's other files."""
        schemas = schemas or {}
        out = dict(values)
        seen = 0
        now = datetime.now(UTC)
        used: list[str] = []
        for key, value in values.items():
            refs = file_refs(value)
            if not refs:
                if is_file_schema(schemas.get(key)) and value not in (None, [], {}):
                    raise InvalidInput(f"'{key}' takes a file: upload it and send its reference")
                continue
            seen += len(refs)
            if seen > MAX_FILES_PER_RUN:
                raise InvalidInput(f"a run takes at most {MAX_FILES_PER_RUN} files")
            fixed = [await self._one(project_id, key, ref, deployment_id, visitor_hash) for ref in refs]
            accept = accepts(schemas.get(key))
            for ref in fixed:
                if not type_matches(ref["media_type"], accept):
                    raise InvalidInput(f"'{key}' takes {accept}; '{ref['name']}' is {ref['media_type']}")
            used += [r["id"] for r in fixed]
            out[key] = fixed[0] if is_file_ref(value) else fixed
        if used:
            await self.s.execute(update(StoredFile).where(StoredFile.id.in_(used)).values(last_used_at=now))
        return out

    async def _one(
        self, project_id: str, key: str, ref: dict[str, Any], deployment_id: str | None, visitor: str | None
    ) -> dict[str, Any]:
        if isinstance(ref.get("id"), str) and ref["id"]:
            try:
                row = await self.get(project_id, ref["id"])
                if deployment_id is not None and (
                    row.deployment_id != deployment_id or (row.visitor_hash is not None and row.visitor_hash != visitor)
                ):
                    raise NotFound(ref["id"])  # another caller's file: the same answer as no such file
                return ref_of(row)
            except NotFound:
                raise InvalidInput(f"'{key}': file '{ref['id']}' is not in this project (or no longer kept)") from None
        if isinstance(ref.get("data"), str):
            if len(ref["data"]) > self.max_bytes * 4 // 3 + 4:
                raise self.too_large(safe_name(ref.get("name")))
            try:
                data = base64.b64decode(ref["data"], validate=True)
            except (binascii.Error, ValueError):
                raise InvalidInput(f"'{key}': `data` must be the file's bytes in base64") from None
            row = await self.add(
                project_id, ref.get("name"), data, source="api", deployment_id=deployment_id, visitor_hash=visitor,
                commit=False,
            )  # fmt: skip
            return ref_of(row)
        if ref.get("url"):
            raise InvalidInput(
                f"'{key}': the server reads files it keeps; upload the file (or send its bytes as `data`). "
                "A `url` works in exported projects only"
            )
        raise InvalidInput(f"'{key}': a file reference needs the `id` of an uploaded file, or `data`")


async def expire_files(session: AsyncSession, org_id: str, before: datetime) -> int:
    """Delete the organisation's files no run has used since `before` (the retention job)."""
    projects = select(Project.id).where(Project.org_id == org_id)
    res = await session.execute(
        delete(StoredFile).where(StoredFile.project_id.in_(projects), StoredFile.last_used_at < before)
    )
    return res.rowcount or 0  # type: ignore[attr-defined]


class FileStore:
    """What runs read files with on this server: a project's files only, by id."""

    def __init__(self, db: Any):
        self.db = db

    def for_project(self, project_id: str) -> FileReader:
        async def read(ref: dict[str, Any]) -> tuple[bytes, str, str]:
            file_id = ref.get("id")
            if not isinstance(file_id, str) or not file_id:
                raise ValueError(f"the file '{ref.get('name', 'file')}' has no id on this server")
            async with self.db.sessionmaker() as s:
                try:
                    row = await FileService(s).get(project_id, file_id, with_data=True)
                except NotFound:
                    raise ValueError(
                        f"the file '{ref.get('name', file_id)}' is no longer kept (files go when no conversation "
                        "has used them for the organisation's retention time); attach it again"
                    ) from None
                return bytes(row.data), row.media_type, row.name

        return read
