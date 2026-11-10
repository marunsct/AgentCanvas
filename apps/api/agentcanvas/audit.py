"""The audit log from the command line, for operators (the API's environment: DATABASE_URL and the audit settings).

    python -m agentcanvas.audit verify [--org org_123]            check each organisation's hash chain (exit code 1
                                                                  when an entry was changed or removed)
    python -m agentcanvas.audit outbox                            what waits for delivery, by destination and state
    python -m agentcanvas.audit retry [--destination splunk]      send entries given up on (dead) again
    python -m agentcanvas.audit discard --destination splunk [--pending]
                                                                  drop a destination's dead entries (and, with
                                                                  --pending, the waiting ones: one no longer used)

Retrying and discarding are themselves recorded in each organisation's audit log.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update

from agentcanvas.db.models import AuditLog, AuditOutbox, Organization
from agentcanvas.db.repositories import AuditRepo
from agentcanvas.db.session import Database
from agentcanvas.services import audit_chain, audit_sink
from agentcanvas.settings import Settings


async def verify(db: Database, org_id: str | None = None) -> list[audit_chain.Report]:
    async with db.sessionmaker() as s:
        org_ids = (
            [org_id] if org_id else list((await s.scalars(select(Organization.id).order_by(Organization.id))).all())
        )
        return [await audit_chain.verify(s, o) for o in org_ids]


async def outbox(db: Database) -> list[tuple[str, str, int]]:
    async with db.sessionmaker() as s:
        rows = await s.execute(
            select(AuditOutbox.destination, AuditOutbox.state, func.count())
            .group_by(AuditOutbox.destination, AuditOutbox.state)
            .order_by(AuditOutbox.destination, AuditOutbox.state)
        )
        return [(d, st, n) for d, st, n in rows.all()]


async def _record(db: Database, action: str, by_org: dict[str | None, int], **detail: Any) -> None:
    for org_id, n in by_org.items():
        async with db.sessionmaker() as s:
            row = AuditLog(org_id=org_id, actor_id=None, action=action, target="audit_outbox",
                           detail={"caller": "command line", "entries": n, **detail})  # fmt: skip
            await AuditRepo(s).add(row)


async def _change(db: Database, where: list[Any], values: dict[str, Any] | None) -> dict[str | None, int]:
    """Update (or, with no values, delete) the outbox rows matching `where`; how many per organisation."""
    async with db.sessionmaker() as s:
        counted = await s.execute(select(AuditOutbox.org_id, func.count()).where(*where).group_by(AuditOutbox.org_id))
        by_org = {org: n for org, n in counted.all()}
        if values is None:
            await s.execute(delete(AuditOutbox).where(*where))
        else:
            await s.execute(update(AuditOutbox).where(*where).values(**values))
        await s.commit()
    return by_org


async def retry(db: Database, destination: str | None = None) -> int:
    where: list[Any] = [AuditOutbox.state == "dead"]
    if destination:
        where.append(AuditOutbox.destination == destination)
    values = {"state": "pending", "attempts": 0, "next_attempt_at": datetime.now(UTC), "last_error": None}
    by_org = await _change(db, where, values)
    await _record(db, "audit_outbox_retried", by_org, destination=destination or "all")
    return sum(by_org.values())


async def discard(db: Database, destination: str, pending: bool = False) -> int:
    where: list[Any] = [AuditOutbox.destination == destination]
    if not pending:
        where.append(AuditOutbox.state == "dead")
    by_org = await _change(db, where, None)
    await _record(db, "audit_outbox_discarded", by_org, destination=destination, pending=pending)
    return sum(by_org.values())


async def _main(args: argparse.Namespace, settings: Settings) -> int:
    destinations = audit_sink.destinations_for(settings)  # what this server forwards: the records above go there too
    audit_sink.install(audit_sink.Forwarding(tuple(d.name for d in destinations), settings.audit_forward_all))
    db = Database(settings.database_url)
    try:
        if args.command == "verify":
            reports = await verify(db, args.org)
            for r in reports:
                state = "ok" if r.ok else "BROKEN"
                print(f"{r.org_id}\t{state}\tchecked={r.checked}\tunchained={r.unchained}\thead={r.head_hash or '-'}")
                for p in r.problems:
                    print(f"  entry {p['id']}: {p['kind']}: {p['message']}")
            return 0 if all(r.ok for r in reports) else 1
        if args.command == "outbox":
            rows = await outbox(db)
            for d, st, n in rows:
                print(f"{d}\t{st}\t{n}")
            if not rows:
                print("the outbox is empty: every forwarded entry was delivered", file=sys.stderr)
        elif args.command == "retry":
            print(f"{await retry(db, args.destination)} entries will be sent again", file=sys.stderr)
        elif args.command == "discard":
            print(f"{await discard(db, args.destination, args.pending)} entries discarded", file=sys.stderr)
    finally:
        await db.dispose()
        for d in destinations:
            await d.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agentcanvas.audit", description=(__doc__ or "").split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verify", help="check each organisation's hash chain")
    v.add_argument("--org", help="only this organisation")
    sub.add_parser("outbox", help="what waits for delivery, by destination and state")
    r = sub.add_parser("retry", help="send entries given up on (dead) again")
    r.add_argument("--destination", help="only this destination (webhook, splunk, matura)")
    d = sub.add_parser("discard", help="drop a destination's dead entries")
    d.add_argument("--destination", required=True)
    d.add_argument("--pending", action="store_true", help="the waiting ones too (a destination no longer used)")
    args = parser.parse_args(argv)
    return asyncio.run(_main(args, Settings()))


if __name__ == "__main__":
    raise SystemExit(main())
