"""Organisation export (E-7, GDPR Art. 20 and 28(3)(g)): everything an organisation holds, for it to take away.

The archive is a zip: `manifest.json` (what is in it), `organisation.json`, `members.ndjson`, one NDJSON file per
table of the organisation's own data (every table with a row-level security policy, `db/rls.py`, so a table added
later is exported without anyone remembering to), `memories.ndjson`, the files people attached under `files/` and
each project's workspace under `workspaces/`. Credentials never leave: key and token hashes, encrypted secrets and
sign-ins, share-link tokens, webhook signing secrets and the data key are left out, and so are embedding vectors
(they can be made again from the text).

An admin asks for one; the background job (`OffboardingJob`) builds it on whichever replica claims it first and
stores it in the database in parts, so any replica can serve it. It can be downloaded once, through a link that
works for `LINK_TTL`, and it is deleted `AGENTCANVAS_ORG_EXPORT_KEEP_HOURS` after it was built.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import tempfile
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from sqlalchemy import LargeBinary, Table, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.adapters.spi import MemoryAbility, supports
from agentcanvas.db import rls
from agentcanvas.db.models import (
    AppUser,
    Base,
    Membership,
    Organization,
    OrgExport,
    OrgExportPart,
    Project,
    StoredFile,
)
from agentcanvas.errors import Conflict, NotFound
from agentcanvas.ids import new_id
from agentcanvas.log import get_logger
from agentcanvas.services.data_subjects import memories_in

log = get_logger(__name__)

FORMAT = 1
PART_BYTES = 4 * 1024 * 1024  # an export is stored in parts this size
PAGE = 200  # rows read at a time
LINK_TTL = timedelta(minutes=15)
LEASE = timedelta(minutes=10)
LINK_PREFIX = "acx_"
# tables not exported, and why
SKIPPED = {
    "org_data_key": "the organisation's data key: it never leaves the server",
    "oauth_flow": "sign-ins in progress, with their PKCE verifiers",
    "answer_cache": "a cache of model answers, made again on use",
    "presence": "who has the editor open right now",
    "event_dedup": "an event handler's duplicate check",
    "org_export": "the exports themselves",
    "org_export_part": "the exports themselves",
}
# columns never exported (credentials); every binary column is left out too (vectors, ciphertext, file bytes)
SECRET_COLUMNS = {
    "personal_token": {"token_hash"},
    "deployment": {"api_key_hash"},
    "deployment_share": {"token"},
    "share_thread": {"visitor_hash"},
    "stored_file": {"visitor_hash"},
    "connection": {"config"},
}


def owned(table: str, org_id: str) -> Any:
    """The rows of `table` that belong to `org_id`, following the row-level security chain up to the organisation."""
    column, parent = rls.TENANT_TABLES[table]
    t = Base.metadata.tables[table]
    if parent is None:
        return t.c[column] == org_id
    p = Base.metadata.tables[parent]
    return t.c[column].in_(select(p.c.id).where(owned(parent, org_id)))


def _columns(table: Table) -> list[Any]:
    hidden = SECRET_COLUMNS.get(table.name, set())
    return [c for c in table.columns if c.name not in hidden and not isinstance(c.type, LargeBinary)]


def _clean(table: str, row: dict[str, Any]) -> dict[str, Any]:
    if table == "trigger" and isinstance(row.get("config"), dict):  # a webhook's signing secret, encrypted
        row["config"] = {k: v for k, v in row["config"].items() if k != "secret"}
    return row


def _line(row: dict[str, Any]) -> bytes:
    return json.dumps(row, default=str, ensure_ascii=False).encode() + b"\n"


async def request_export(session: AsyncSession, org_id: str, user_id: str) -> OrgExport:
    """Queue an export (one at a time per organisation). Commits."""
    await session.execute(select(Organization.id).where(Organization.id == org_id).with_for_update())
    busy = await session.scalar(
        select(OrgExport.id).where(OrgExport.org_id == org_id, OrgExport.status.in_(("queued", "building")))
    )
    if busy is not None:
        raise Conflict(f"an export is already being made: '{busy}'", export_id=busy)
    row = OrgExport(id=new_id("oex"), org_id=org_id, status="queued", requested_by=user_id)
    session.add(row)
    await session.commit()
    return row


async def get_export(session: AsyncSession, org_id: str, export_id: str) -> OrgExport:
    row = await session.get(OrgExport, export_id)
    if row is None or row.org_id != org_id:
        raise NotFound(f"export '{export_id}' not found")
    return row


async def make_link(session: AsyncSession, org_id: str, export_id: str) -> tuple[str, datetime]:
    """A new download link for a built export (the one before it stops working). Commits."""
    row = await get_export(session, org_id, export_id)
    if row.status != "ready":
        raise Conflict(
            {"queued": "the export is not built yet", "building": "the export is not built yet",
             "downloaded": "the export was downloaded already: make a new one",
             "expired": "the export expired: make a new one",
             "failed": f"the export failed: {row.error}"}.get(row.status, row.status)
        )  # fmt: skip
    token = LINK_PREFIX + secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + LINK_TTL
    row.link_hash, row.link_expires_at = hash_link(token), expires
    await session.commit()
    return token, expires


def hash_link(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def use_link(session: AsyncSession, token: str) -> OrgExport:
    """The export a link is for, now used up: a second download, an old or expired link is a 404. Commits."""
    gone = NotFound("this download link does not work any more: an admin can make a new one")
    if not token.startswith(LINK_PREFIX):
        raise gone
    row = await session.scalar(select(OrgExport).where(OrgExport.link_hash == hash_link(token)))
    now = datetime.now(UTC)
    if row is None:
        raise gone
    res = await session.execute(
        update(OrgExport)
        .where(OrgExport.id == row.id, OrgExport.status == "ready", OrgExport.link_expires_at > now,
               OrgExport.link_hash == hash_link(token))
        .values(status="downloaded", downloaded_at=now, link_hash=None)
    )  # fmt: skip
    await session.commit()
    if res.rowcount != 1:  # type: ignore[attr-defined]  # expired, or a parallel request used it first
        raise gone
    return row


async def stream_parts(db: Any, export_id: str) -> AsyncIterator[bytes]:
    """The archive's bytes, a part at a time (each read in its own short session); its parts are deleted once the
    whole archive was sent."""
    seq = 0
    while True:
        async with db.sessionmaker() as s:
            data = await s.scalar(
                select(OrgExportPart.data).where(OrgExportPart.export_id == export_id, OrgExportPart.seq == seq)
            )
        if data is None:
            break
        yield data
        seq += 1
    async with db.sessionmaker() as s:
        await s.execute(delete(OrgExportPart).where(OrgExportPart.export_id == export_id))
        await s.commit()


class ExportBuilder:
    """Builds queued exports and deletes expired ones; part of the offboarding job."""

    def __init__(self, db: Any, runner: Any, workspaces_dir: str, keep_hours: int = 24):
        self.db, self.runner, self.workspaces_dir, self.keep_hours = db, runner, Path(workspaces_dir), keep_hours

    async def tick(self, now: datetime) -> list[str]:
        """Build the exports waiting (and those whose builder stopped); returns those built here."""
        await self.expire(now)
        async with self.db.sessionmaker() as s:
            waiting = list(
                (
                    await s.scalars(
                        select(OrgExport.id)
                        .where(
                            or_(
                                OrgExport.status == "queued",
                                (OrgExport.status == "building") & (OrgExport.lease_until < now),
                            )
                        )
                        .order_by(OrgExport.created_at)
                    )
                ).all()
            )
        built = []
        for export_id in waiting:
            if await self._claim(export_id, now):
                await self.build(export_id)
                built.append(export_id)
        return built

    async def _claim(self, export_id: str, now: datetime) -> bool:
        async with self.db.sessionmaker() as s:
            res = await s.execute(
                update(OrgExport)
                .where(
                    OrgExport.id == export_id,
                    or_(OrgExport.status == "queued", (OrgExport.status == "building") & (OrgExport.lease_until < now)),
                )
                .values(status="building", lease_until=now + LEASE, started_at=now)
            )
            await s.commit()
        return res.rowcount == 1  # type: ignore[attr-defined]

    async def _renew(self, export_id: str) -> None:
        async with self.db.sessionmaker() as s:
            await s.execute(
                update(OrgExport).where(OrgExport.id == export_id).values(lease_until=datetime.now(UTC) + LEASE)
            )
            await s.commit()

    async def expire(self, now: datetime) -> int:
        """Delete the archives kept long enough (built or downloaded); the rows stay as a record."""
        async with self.db.sessionmaker() as s:
            ids = list(
                (
                    await s.scalars(
                        select(OrgExport.id).where(
                            OrgExport.expires_at < now, OrgExport.status.in_(("ready", "downloaded", "failed"))
                        )
                    )
                ).all()
            )
            if ids:
                await s.execute(delete(OrgExportPart).where(OrgExportPart.export_id.in_(ids)))
                await s.execute(update(OrgExport).where(OrgExport.id.in_(ids)).values(status="expired", link_hash=None))
                await s.commit()
        return len(ids)

    async def build(self, export_id: str) -> None:
        async with self.db.sessionmaker() as s:
            row = await s.get(OrgExport, export_id)
            assert row is not None
            org_id = row.org_id
        tmp = tempfile.TemporaryFile()
        try:
            counts = await self._write(export_id, org_id, tmp)
            await self._store(export_id, tmp, counts)
            log.info("org_export.built", export_id=export_id, org_id=org_id)
        except Exception as exc:
            log.exception("org_export.failed", export_id=export_id, org_id=org_id)
            async with self.db.sessionmaker() as s:
                await s.execute(delete(OrgExportPart).where(OrgExportPart.export_id == export_id))
                await s.execute(
                    update(OrgExport)
                    .where(OrgExport.id == export_id)
                    .values(status="failed", error=str(exc)[:500] or type(exc).__name__, lease_until=None,
                            finished_at=datetime.now(UTC), expires_at=datetime.now(UTC))
                )  # fmt: skip
                await s.commit()
        finally:
            tmp.close()

    async def _write(self, export_id: str, org_id: str, tmp: Any) -> dict[str, int]:
        counts: dict[str, int] = {}
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:

            async def ndjson(name: str, rows: AsyncIterator[list[dict[str, Any]]]) -> None:
                n = 0
                with zf.open(name, "w", force_zip64=True) as fh:
                    async for page in rows:
                        await asyncio.to_thread(fh.write, b"".join(_line(r) for r in page))
                        n += len(page)
                counts[name] = n

            async with self.db.sessionmaker() as s:
                org = await s.get(Organization, org_id)
                assert org is not None
                about = {"id": org.id, "name": org.name, "retention_days": org.retention_days,
                         "environments": org.environments, "settings": org.settings, "sso_groups": org.sso_groups,
                         "created_at": org.created_at}  # fmt: skip
                members = [
                    {"user_id": u.id, "email": u.email, "display_name": u.display_name, "role": m.role,
                     "accepted": m.accepted, "deactivated": m.suspended}
                    for u, m in (await s.execute(
                        select(AppUser, Membership).join(Membership, Membership.user_id == AppUser.id)
                        .where(Membership.org_id == org_id)
                    )).all()
                ]  # fmt: skip
            zf.writestr("organisation.json", json.dumps(about, default=str, ensure_ascii=False, indent=2))
            await ndjson("members.ndjson", _once(members))
            for table in rls.TENANT_TABLES:
                if table not in SKIPPED:
                    await ndjson(f"{table}.ndjson", self._rows(table, org_id))
                    await self._renew(export_id)
            await ndjson("memories.ndjson", self._memories(org_id))
            counts["files"] = await self._files(zf, org_id)
            async with self.db.sessionmaker() as s:
                projects = list((await s.scalars(select(Project.id).where(Project.org_id == org_id))).all())
            counts["workspace_files"] = await asyncio.to_thread(self._workspaces, zf, projects)
            manifest = {
                "format": FORMAT, "organisation": org_id, "export_id": export_id,
                "exported_at": datetime.now(UTC).isoformat(), "counts": counts,
                "left_out": {**SKIPPED, "credentials": "key and token hashes, encrypted secrets and sign-ins, share "
                             "link tokens and webhook signing secrets", "vectors": "embeddings: made again from text"},
            }  # fmt: skip
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        return counts

    async def _rows(self, table: str, org_id: str) -> AsyncIterator[list[dict[str, Any]]]:
        t = Base.metadata.tables[table]
        cols = _columns(t)
        keyset = [c.name for c in t.primary_key.columns] == ["id"]  # one id column: pages by it, else by offset
        after: Any = None
        offset = 0
        while True:
            q = select(*cols).where(owned(table, org_id))
            if keyset:
                q = q.order_by(t.c.id)
                if after is not None:
                    q = q.where(t.c.id > after)
            else:
                q = q.order_by(*t.primary_key.columns).offset(offset)
            async with self.db.sessionmaker() as s:
                page = [dict(r._mapping) for r in (await s.execute(q.limit(PAGE))).all()]
            if not page:
                return
            yield [_clean(table, r) for r in page]
            if len(page) < PAGE:
                return
            after, offset = page[-1].get("id"), offset + len(page)

    async def _memories(self, org_id: str) -> AsyncIterator[list[dict[str, Any]]]:
        if not supports(self.runner.adapter, "memory_store"):
            return
        memory = cast(MemoryAbility, self.runner.adapter)
        namespaces = [tuple(ns) for ns in await memory.memory_namespaces((f"org:{org_id}",), limit=100_000)]
        for i in range(0, len(namespaces), 50):
            yield await memories_in(self.runner, org_id, namespaces[i : i + 50])

    async def _files(self, zf: zipfile.ZipFile, org_id: str) -> int:
        n, after = 0, ""
        while True:
            async with self.db.sessionmaker() as s:
                rows = (
                    await s.execute(
                        select(StoredFile.id, StoredFile.name, StoredFile.data)
                        .where(owned("stored_file", org_id), StoredFile.id > after)
                        .order_by(StoredFile.id)
                        .limit(20)
                    )
                ).all()
            for file_id, name, data in rows:
                safe = os.path.basename(name.replace("\\", "/")) or "file"
                await asyncio.to_thread(zf.writestr, f"files/{file_id}/{safe}", data)
                n += 1
            if len(rows) < 20:
                return n
            after = rows[-1][0]

    def _workspaces(self, zf: zipfile.ZipFile, project_ids: list[str]) -> int:
        """Each project's workspace files (not its git history); runs in a worker thread."""
        n = 0
        for project_id in project_ids:
            root = self.workspaces_dir / project_id
            if not root.is_dir():
                continue
            for path in sorted(root.rglob("*")):
                rel = path.relative_to(root)
                if path.is_file() and not path.is_symlink() and ".git" not in rel.parts:
                    zf.write(path, f"workspaces/{project_id}/{rel.as_posix()}")
                    n += 1
        return n

    async def _store(self, export_id: str, tmp: Any, counts: dict[str, int]) -> None:
        """Copy the archive into the database in parts, then mark the export ready."""
        tmp.seek(0)
        digest, size, seq = hashlib.sha256(), 0, 0
        async with self.db.sessionmaker() as s:
            await s.execute(delete(OrgExportPart).where(OrgExportPart.export_id == export_id))  # a cut-short try's
            await s.commit()
        while chunk := await asyncio.to_thread(tmp.read, PART_BYTES):
            digest.update(chunk)
            size += len(chunk)
            async with self.db.sessionmaker() as s:
                s.add(OrgExportPart(export_id=export_id, seq=seq, data=chunk))
                await s.commit()
            seq += 1
            await self._renew(export_id)
        now = datetime.now(UTC)
        async with self.db.sessionmaker() as s:
            await s.execute(
                update(OrgExport)
                .where(OrgExport.id == export_id)
                .values(status="ready", size=size, sha256=digest.hexdigest(), finished_at=now, lease_until=None,
                        expires_at=now + timedelta(hours=self.keep_hours))
            )  # fmt: skip
            await s.commit()


async def _once(rows: list[dict[str, Any]]) -> AsyncIterator[list[dict[str, Any]]]:
    yield rows
