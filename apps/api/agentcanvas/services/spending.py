"""What an organisation's runs spent in a period, by project, workflow, person and deployment (G-8, N-9).

Read from the runs' own columns (`cost_usd`, tokens, `cache_hits`, `fallbacks`), so it counts what the budgets count:
the cost of models with a catalog price (a model with none adds tokens but no USD). A run belongs to the person who
started its conversation; conversations started by a deployment, a schedule or an event have no person, and the
per-deployment list says which deployment (a published workflow's environment) they came through.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import AppUser, Deployment, Project, Run, Thread, WorkflowRow


@dataclass
class Total:
    runs: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    cache_hits: int = 0
    fallbacks: int = 0

    def add(self, other: Total) -> None:
        self.runs += other.runs
        self.tokens_in += other.tokens_in
        self.tokens_out += other.tokens_out
        self.cost_usd += other.cost_usd
        self.cache_hits += other.cache_hits
        self.fallbacks += other.fallbacks

    def out(self) -> dict[str, Any]:
        return {**self.__dict__, "cost_usd": round(self.cost_usd, 6)}


async def spending(
    session: AsyncSession, org_id: str, since: datetime, until: datetime, *, limit: int = 100
) -> dict[str, Any]:
    """The organisation's runs started in [since, until), added up per project, workflow, person and deployment,
    each list biggest spender first and cut to `limit` entries (`total` always counts everything)."""
    person = Thread.created_by
    q = (
        select(
            Project.id, Project.name, WorkflowRow.id, WorkflowRow.name, person,
            Thread.deployment_id, Deployment.environment,
            func.count(Run.id), func.coalesce(func.sum(Run.tokens_in), 0), func.coalesce(func.sum(Run.tokens_out), 0),
            func.coalesce(func.sum(Run.cost_usd), 0), func.coalesce(func.sum(Run.cache_hits), 0),
            func.coalesce(func.sum(Run.fallbacks), 0),
        )
        .join(Thread, Thread.id == Run.thread_id)
        .join(WorkflowRow, WorkflowRow.id == Thread.workflow_id)
        .join(Project, Project.id == WorkflowRow.project_id)
        .outerjoin(Deployment, Deployment.id == Thread.deployment_id)
        .where(Project.org_id == org_id, Run.started_at >= since, Run.started_at < until)
        .group_by(Project.id, Project.name, WorkflowRow.id, WorkflowRow.name, person, Thread.deployment_id,
                  Deployment.environment)
    )  # fmt: skip
    total = Total()
    projects: dict[str, tuple[str, Total]] = {}
    workflows: dict[str, tuple[str, str, Total]] = {}
    people: dict[str | None, Total] = {}
    deployments: dict[str | None, tuple[str, str | None, Total]] = {}
    for pid, pname, wid, wname, who, dep, env, runs, tin, tout, cost, hits, fallbacks in (
        await session.execute(q)
    ).all():
        row = Total(int(runs), int(tin), int(tout), float(cost), int(hits), int(fallbacks))
        total.add(row)
        projects.setdefault(pid, (pname, Total()))[1].add(row)
        workflows.setdefault(wid, (wname, pid, Total()))[2].add(row)
        people.setdefault(who, Total()).add(row)
        label = f"{wname} ({env})" if dep else "Not through a deployment (the editor, the Playground)"
        deployments.setdefault(dep, (label, wid if dep else None, Total()))[2].add(row)
    names = {
        u.id: u
        for u in await session.scalars(select(AppUser).where(AppUser.id.in_([p for p in people if p is not None])))
    }

    def top(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(items, key=lambda i: (-i["cost_usd"], -i["runs"], i["name"]))[:limit]

    return {
        "since": since,
        "until": until,
        "total": total.out(),
        "by_project": top([{"id": i, "name": n, **t.out()} for i, (n, t) in projects.items()]),
        "by_workflow": top([{"id": i, "name": n, "project_id": p, **t.out()} for i, (n, p, t) in workflows.items()]),
        "by_deployment": top(
            [{"id": i, "name": n, "workflow_id": w, **t.out()} for i, (n, w, t) in deployments.items()]
        ),
        "by_person": top([{"id": who, **_person(who, names.get(who or "")), **t.out()} for who, t in people.items()]),
    }


def _person(user_id: str | None, user: AppUser | None) -> dict[str, Any]:
    if user_id is None:
        return {"name": "No person (deployments, schedules, events)", "email": None}
    if user is None:  # removed since
        return {"name": user_id, "email": None}
    return {"name": user.display_name or user.email, "email": user.email}
