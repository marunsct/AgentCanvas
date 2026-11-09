"""Indexes behind the hot and the cascading queries: a foreign key a delete cascades through leads an index, the
Conversations list reads in order, and a day or month budget check reads only this month's conversations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint, event, select, update
from test_publish import make_greeter
from test_retention import converse

from agentcanvas.db.models import Base, Run, Thread
from agentcanvas.runtime.runner import periods, workflow_spend

# small tables, or parents deleted rarely (a person, a dataset): a scan per deleted row costs nothing there
SMALL = {
    ("membership", "user_id"),
    ("personal_token", "user_id"),
    ("scim_group_member", "user_id"),
    ("experiment", "dataset_id"),
    ("release_gate", "dataset_id"),
    ("online_evaluator", "evaluator_id"),
    ("trigger", "deployment_id"),
}


def test_every_foreign_key_a_delete_goes_through_leads_an_index():
    """Postgres does not index foreign keys: each row a cascade (or SET NULL) touches scans the child table without
    one, so deleting a workflow, a conversation or a run grew with the whole table."""
    missing = []
    for table in Base.metadata.sorted_tables:
        leads = {next(iter(ix.columns)).name for ix in table.indexes}
        leads |= {c.name for c in table.columns if c.unique or c.index}
        keys = [c for c in table.constraints if isinstance(c, PrimaryKeyConstraint | UniqueConstraint) and c.columns]
        leads |= {next(iter(c.columns)).name for c in keys}
        for fk in table.foreign_keys:
            name = fk.parent.name
            if fk.ondelete in ("CASCADE", "SET NULL") and name not in leads and (table.name, name) not in SMALL:
                missing.append(f"{table.name}.{name}")
    assert missing == []


@contextmanager
def executed(app) -> Iterator[list[tuple[str, tuple]]]:
    seen: list[tuple[str, tuple]] = []

    def record(_conn, _cursor, statement, parameters, *_args) -> None:
        seen.append((statement, tuple(parameters or ())))

    engine = app.state.db.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def plan(app, statement: str, parameters: tuple) -> str:
    async with app.state.db.engine.connect() as c:
        rows = (await c.exec_driver_sql(f"EXPLAIN QUERY PLAN {statement}", parameters)).all()
    return "\n".join(str(r[-1]) for r in rows)


@pytest.fixture
def sqlite_only(app):
    if app.state.db.is_postgres:
        pytest.skip("SQLite query plans")


async def test_the_conversations_list_reads_threads_in_order(app, api_client, sqlite_only):
    wf = await make_greeter(api_client)
    await converse(api_client, wf, "th_a")
    with executed(app) as seen:  # a workflow's conversations (its run history)
        assert (await api_client.get("/v1/threads", params={"workflow_id": wf})).status_code == 200
    [(listed, args)] = [(q, a) for q, a in seen if "FROM thread JOIN workflow" in q and "ORDER BY" in q]
    assert "TEMP B-TREE FOR ORDER BY" not in await plan(app, listed, args)
    # a project's or the organisation's can walk thread_created_idx newest first: Postgres does when it pays off


async def test_the_conversations_list_adds_up_every_run(app, api_client):
    """A conversation's runs, tokens and cost, however many runs it has."""
    wf = await make_greeter(api_client)
    for _ in range(3):
        await converse(api_client, wf, "th_many")
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Run).where(Run.thread_id == "th_many").values(cost_usd=0.5, tokens_in=10, tokens_out=5))
        ids = (await s.scalars(select(Run.id).where(Run.thread_id == "th_many").order_by(Run.id))).all()
        for n, run_id in enumerate(ids):  # one second apart, the second one last
            when = datetime(2026, 1, 1, 0, 0, (n + 1) % 3, tzinfo=UTC)
            await s.execute(update(Run).where(Run.id == run_id).values(started_at=when))
        await s.commit()
    last = ids[1]
    [item] = (await api_client.get("/v1/threads", params={"workflow_id": wf})).json()["items"]
    assert (item["runs"], item["tokens"], item["cost_usd"]) == (3, 45, 1.5)
    assert item["last_run_id"] == last


async def test_a_budget_check_reads_only_this_months_conversations(app, api_client, sqlite_only):
    wf = await make_greeter(api_client)
    await converse(api_client, wf, "th_now")
    with executed(app) as seen:
        async with app.state.db.sessionmaker() as s:
            await workflow_spend(s, wf)
    [(q, args)] = [(q, a) for q, a in seen if "sum(" in q.lower() and "FROM run" in q]
    assert "thread_workflow_idx (workflow_id=? AND updated_at>?)" in await plan(app, q, args)


async def test_a_budget_counts_this_months_runs_of_older_conversations(app, api_client):
    """Starting a run touches its conversation, so one begun last month with a run this month still counts."""
    wf = await make_greeter(api_client)
    await converse(api_client, wf, "th_old")  # begun last month, only old runs
    await converse(api_client, wf, "th_back")  # begun last month, a run this month
    month = periods()["month"][1]
    long_ago = month - timedelta(days=40)
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Run).values(cost_usd=2))
        await s.execute(update(Thread).values(created_at=long_ago, updated_at=long_ago))
        await s.execute(update(Run).where(Run.thread_id == "th_old").values(started_at=long_ago))
        await s.commit()
    await converse(api_client, wf, "th_back")
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Run).where(Run.thread_id == "th_back").values(cost_usd=2))
        await s.commit()
        today, this_month = await workflow_spend(s, wf)
    assert (today, this_month) == (4, 4)  # th_back's two runs, not th_old's
