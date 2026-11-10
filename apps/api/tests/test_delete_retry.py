"""Deletes right after a run: a run's finish hooks and its search indexing write after its last event reached the
client, and on Postgres a delete at that moment could lose a deadlock to them (or to another server writing under
the same rows). The delete paths wait for this server's runs to finish their hooks, and run the delete transaction
again when Postgres picks it as a deadlock's victim."""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_publish import make_greeter
from test_retention import converse
from test_strict_resume import make, parallel, paused

from agentcanvas.db import retry
from agentcanvas.db.models import InterruptTask, Thread
from agentcanvas.errors import Unavailable

POSTGRES = (os.getenv("TEST_DATABASE_URL") or "").startswith("postgres")


class Refused(Exception):
    def __init__(self, sqlstate: str):
        super().__init__(f"sqlstate {sqlstate}")
        self.sqlstate = sqlstate


class FakeSession:
    def __init__(self):
        self.rollbacks = 0

    async def rollback(self):
        self.rollbacks += 1


def refused(sqlstate: str) -> DBAPIError:
    return DBAPIError("DELETE FROM run", {}, Refused(sqlstate))


@pytest.fixture
def no_wait(monkeypatch):
    monkeypatch.setattr(retry, "backoff", lambda attempt: 0)


@pytest.mark.parametrize("sqlstate", ["40P01", "40001"])
async def test_a_deadlock_or_serialization_victim_runs_again(no_wait, sqlstate):
    session, tries = FakeSession(), []

    async def work():
        tries.append(1)
        if len(tries) < 3:
            raise refused(sqlstate)
        return "done"

    assert await retry.retrying(session, work, what="test delete") == "done"  # type: ignore[arg-type]
    assert len(tries) == 3 and session.rollbacks == 2


async def test_it_gives_up_after_a_few_tries_with_a_503_that_says_why(no_wait):
    session, tries = FakeSession(), []

    async def work():
        tries.append(1)
        raise refused("40P01")

    with pytest.raises(Unavailable) as caught:
        await retry.retrying(session, work, what="conversation delete")  # type: ignore[arg-type]
    assert len(tries) == retry.ATTEMPTS and session.rollbacks == retry.ATTEMPTS
    assert "conversation delete" in caught.value.detail and "deadlock" in caught.value.detail
    assert caught.value.extra["retry_after"] == retry.RETRY_AFTER_S


async def test_other_database_errors_are_not_retried(no_wait):
    session, tries = FakeSession(), []

    async def work():
        tries.append(1)
        raise IntegrityError("INSERT", {}, Refused("23505"))

    with pytest.raises(IntegrityError):
        await retry.retrying(session, work, what="test delete")  # type: ignore[arg-type]
    assert len(tries) == 1 and session.rollbacks == 0


def test_the_wait_grows_with_jitter_up_to_a_cap():
    for attempt in range(1, 8):
        full = min(retry.CAP_S, retry.BASE_S * 2 ** (attempt - 1))
        waits = {retry.backoff(attempt) for _ in range(20)}
        assert all(full / 2 <= w <= full for w in waits) and len(waits) > 1


async def test_a_delete_waits_for_the_finish_hooks_of_a_run_that_just_ended(app, api_client):
    """The run's last event reaches the client before its finish hooks run: the delete lets them end first."""
    order: list[str] = []
    hooks_began = asyncio.Event()

    async def slow_hook(h, *_):
        if h.thread_id == "th_hooks":
            assert any(e["type"] == "RUN_FINISHED" for e in h.events)  # the client can have its answer by now
            hooks_began.set()
            await asyncio.sleep(0.5)  # the search index, the notifier: writing under the conversation's rows
            async with app.state.db.sessionmaker() as s:
                assert await s.get(Thread, "th_hooks") is not None  # not deleted under it
            order.append("hook")

    app.state.runner.finish_hooks.append(slow_hook)
    wf = await make_greeter(api_client)
    talk = asyncio.create_task(converse(api_client, wf, "th_hooks"))
    await hooks_began.wait()
    assert (await api_client.delete("/v1/threads/th_hooks")).status_code == 204
    order.append("deleted")
    await talk
    assert order == ["hook", "deleted"]


async def _collide(app, thread_id: str, holding: asyncio.Event) -> None:
    """Another server writing under the conversation as it is deleted, in the other order: it holds the run's row,
    then (once the delete waits for that row) updates the Inbox rows the delete has already taken."""
    engine = app.state.db.engine
    async with engine.connect() as other:
        await other.execute(text("UPDATE run SET error = error WHERE thread_id = :t"), {"t": thread_id})
        holding.set()
        async with engine.connect() as watch:
            while not await watch.scalar(text("SELECT count(*) FROM pg_locks WHERE NOT granted")):
                await asyncio.sleep(0.02)
                await watch.rollback()
        notice = text("UPDATE interrupt_task SET noticed = 'waiting' WHERE thread_id = :t")  # as the notifier does
        await other.execute(notice, {"t": thread_id})
        await other.commit()


async def _delete_during_collision(app, api_client, thread_id: str):
    holding = asyncio.Event()
    other = asyncio.create_task(_collide(app, thread_id, holding))
    await holding.wait()
    r = await api_client.delete(f"/v1/threads/{thread_id}")
    await other  # the other side went on once the delete gave way
    return r


@pytest.mark.skipif(not POSTGRES, reason="deadlocks need Postgres (SQLite has one writer at a time)")
async def test_a_conversation_delete_that_loses_a_deadlock_runs_again(app, api_client, monkeypatch):
    wf = await make(api_client, parallel())
    await paused(api_client, wf, "th_deadlock")

    monkeypatch.setattr(retry, "ATTEMPTS", 1)  # as before: the deadlock's victim fails the request
    r = await _delete_during_collision(app, api_client, "th_deadlock")
    assert r.status_code == 503 and "deadlock" in r.json()["detail"], r.text
    assert r.headers["Retry-After"] == str(retry.RETRY_AFTER_S)
    async with app.state.db.sessionmaker() as s:
        assert await s.get(Thread, "th_deadlock") is not None  # nothing was changed

    monkeypatch.setattr(retry, "ATTEMPTS", 4)
    r = await _delete_during_collision(app, api_client, "th_deadlock")
    assert r.status_code == 204, r.text
    async with app.state.db.sessionmaker() as s:
        assert await s.get(Thread, "th_deadlock") is None
        assert not (await s.scalars(select(InterruptTask).where(InterruptTask.thread_id == "th_deadlock"))).all()


async def test_workflow_deletes_and_the_retention_job_run_again_after_a_deadlock(app, api_client, monkeypatch, no_wait):
    """Wired in: a workflow delete and the retention job's delete, each ended once as a deadlock's victim."""
    from datetime import UTC, datetime

    from test_retention import age

    from agentcanvas.db.repositories import WorkflowRepo
    from agentcanvas.services import retention

    def once(original):
        calls = []

        async def flaky(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise refused("40P01")
            return await original(*args, **kwargs)

        return flaky, calls

    wf = await make_greeter(api_client)
    await converse(api_client, wf, "th_old")
    await age(app, "th_old", 400)
    flaky, calls = once(retention.delete_thread_rows)
    monkeypatch.setattr(retention, "delete_thread_rows", flaky)
    assert "th_old" in await app.state.retention.tick(datetime.now(UTC))
    assert len(calls) == 2

    flaky, calls = once(WorkflowRepo.delete)
    monkeypatch.setattr(WorkflowRepo, "delete", flaky)
    assert (await api_client.delete(f"/v1/workflows/{wf}")).status_code == 204
    assert len(calls) == 2 and (await api_client.get(f"/v1/workflows/{wf}")).status_code == 404
