"""The audit log's hash chain: each organisation's entries are linked, so a changed or removed entry shows.

Every entry of an organisation stores `prev_hash`, the `row_hash` of the entry written before it, and `row_hash`,
the SHA-256 of its own canonical form (below) including `prev_hash`. `AuditRepo.add` computes both while it holds the
organisation's row lock, so entries written at the same time by several servers still form one line.

Verifying walks the entries in order and recomputes each hash: an entry edited after it was written no longer
matches its `row_hash`, and an entry deleted from the middle leaves the next one's `prev_hash` pointing at nothing.
Retention deletes the oldest entries on purpose; it records the last deleted entry's hash in `audit_anchor`, where
the remaining chain starts. Entries written before the chain existed have no hash and are reported as such.

What it cannot show on its own: someone able to rewrite the whole table could recompute every hash. Forwarded entries
carry their hashes, so the copy in the SIEM anchors the chain against that.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import AuditAnchor, AuditLog, Organization

BATCH = 1000
COLUMNS = (
    AuditLog.id, AuditLog.org_id, AuditLog.actor_id, AuditLog.action, AuditLog.target, AuditLog.detail, AuditLog.ip,
    AuditLog.created_at, AuditLog.prev_hash, AuditLog.row_hash,
)  # fmt: skip
MAX_PROBLEMS = 50


def _normal(value: Any) -> Any:
    """JSON as the database gives it back: numbers as floats (Postgres's JSONB turns 1e20 into 100000000000000000000
    and 1.0 into 1.0, SQLite keeps what it got), keys as strings."""
    if isinstance(value, dict):
        return {str(k): _normal(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_normal(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float):
        try:
            return float(value)
        except OverflowError:
            return repr(value)
    return str(value)


def _time(at: datetime) -> str:
    return (at if at.tzinfo else at.replace(tzinfo=UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical(row: Any) -> bytes:
    """The bytes an entry's hash covers: every column but its id and its own hash (`row`: an AuditLog, or a result
    row with its columns)."""
    body = {
        "org_id": row.org_id, "actor_id": row.actor_id, "action": row.action, "target": row.target,
        "detail": _normal(row.detail or {}), "ip": row.ip, "created_at": _time(row.created_at),
        "prev_hash": row.prev_hash,
    }  # fmt: skip
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def row_hash(row: Any) -> str:
    return hashlib.sha256(canonical(row)).hexdigest()


async def lock(session: AsyncSession, org_id: str) -> None:
    """Hold the organisation's chain until the transaction ends. Postgres: its row, in the mode that does not stop
    rows that refer to it from being written (FOR NO KEY UPDATE), and is taken after the membership lock's FOR UPDATE
    without a deadlock. SQLite: the database's write lock (a write that changes nothing), so the head read next is the
    last one written."""
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        q = select(Organization.id).where(Organization.id == org_id).with_for_update(key_share=True)
        await session.execute(q)
    else:
        await session.execute(text("UPDATE organization SET id = id WHERE id = :o"), {"o": org_id})


async def head(session: AsyncSession, org_id: str) -> str | None:
    """The hash the organisation's next entry links to: its last chained entry's, else where retention left the
    chain (the last deleted entry's), else none (the chain starts)."""
    last = await session.scalar(
        select(AuditLog.row_hash)
        .where(AuditLog.org_id == org_id, AuditLog.row_hash.is_not(None))
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    if last is not None:
        return last
    return await session.scalar(
        select(AuditAnchor.last_hash).where(AuditAnchor.org_id == org_id).order_by(AuditAnchor.id.desc()).limit(1)
    )


async def link(session: AsyncSession, row: AuditLog) -> None:
    """Give a new entry its place in its organisation's chain (call before inserting it, in its transaction)."""
    if row.org_id is None:
        return
    await lock(session, row.org_id)
    row.created_at = datetime.now(UTC)  # under the lock: the chain's order is also the order of times
    row.detail = _plain(row.detail or {})
    row.prev_hash = await head(session, row.org_id)
    row.row_hash = row_hash(row)


def _plain(detail: dict[str, Any]) -> dict[str, Any]:
    """The detail as JSON would store it (tuples as lists, other values as text), so what is hashed is what is kept."""
    return json.loads(json.dumps(detail, default=str))


@dataclass
class Report:
    org_id: str
    ok: bool = True
    checked: int = 0  # chained entries checked
    unchained: int = 0  # entries written before the chain existed
    first_id: int | None = None
    last_id: int | None = None
    head_hash: str | None = None  # compare with the last forwarded entry's `row_hash` in the SIEM
    anchor: dict[str, Any] | None = None  # where retention left the chain
    problems: list[dict[str, Any]] = field(default_factory=list)

    def problem(self, row_id: int, kind: str, message: str) -> None:
        self.ok = False
        if len(self.problems) < MAX_PROBLEMS:
            self.problems.append({"id": row_id, "kind": kind, "message": message})


async def verify(session: AsyncSession, org_id: str) -> Report:
    """Recompute an organisation's chain from its start (a batch of entries at a time)."""
    report = Report(org_id)
    anchor = (
        await session.scalars(
            select(AuditAnchor).where(AuditAnchor.org_id == org_id).order_by(AuditAnchor.id.desc()).limit(1)
        )
    ).first()
    if anchor is not None:
        report.anchor = {
            "through_id": anchor.through_id, "last_hash": anchor.last_hash, "rows": anchor.rows,
            "expired_at": anchor.created_at.isoformat(),
        }  # fmt: skip
    expected = anchor.last_hash if anchor is not None else None
    started, after = False, 0
    while True:
        rows = (
            await session.execute(
                select(*COLUMNS)
                .where(AuditLog.org_id == org_id, AuditLog.id > after)
                .order_by(AuditLog.id)
                .limit(BATCH)
            )
        ).all()  # columns, not entities: a long log is not kept in the session
        if not rows:
            break
        for row in rows:
            after = row.id
            if row.row_hash is None:
                if started:
                    report.problem(row.id, "unchained", "this entry has no hash: it was added outside AgentCanvas")
                else:
                    report.unchained += 1
                continue
            started = True
            report.checked += 1
            report.first_id = report.first_id or row.id
            report.last_id = row.id
            if row_hash(row) != row.row_hash:
                report.problem(row.id, "changed", "this entry was changed after it was written")
            if row.prev_hash != expected:
                report.problem(
                    row.id, "missing", "the entry written before this one is missing (deleted) or was replaced"
                )
            expected = row.row_hash
    report.head_hash = expected
    return report
