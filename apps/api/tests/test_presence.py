"""Presence in the editor (G-4): who has a workflow open and what they select, over heartbeats and an SSE stream."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from test_org_scope import act_as_b, org_b  # noqa: F401 - fixture
from test_publish import make_greeter

from agentcanvas.api.deps import CurrentUser, current_user
from agentcanvas.db.models import AppUser, Presence
from agentcanvas.services.presence import Beat, PresenceHub

S1, S2, S3 = "session-one-aaaa", "session-two-bbbb", "session-three-cc"


def url(wf: str, tail: str = "") -> str:
    return f"/v1/workflows/{wf}/presence{tail}"


def act_as(app, user_id: str, role: str = "builder") -> None:
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id=user_id, org_id="org_default", role=role, memberships={"org_default": role}
    )


async def person(app, user_id: str, name: str | None) -> None:
    async with app.state.db.sessionmaker() as s:
        s.add(AppUser(id=user_id, oidc_subject=user_id, email=f"{user_id}@example.com", display_name=name))
        await s.commit()


async def test_tabs_see_each_other_and_what_they_select(app, api_client):
    wf = await make_greeter(api_client)
    await person(app, "usr_ada", "Ada Lovelace")
    first = await api_client.post(url(wf), json={"session": S1, "selected": "n_greet", "version": 3})
    assert first.status_code == 200, first.text
    assert [(p["name"], p["selected"], p["version"]) for p in first.json()["peers"]] == [("Developer", "n_greet", 3)]

    act_as(app, "usr_ada")
    try:
        second = await api_client.post(url(wf), json={"session": S2})
        peers = {p["session"]: p for p in second.json()["peers"]}
        assert peers[S2]["name"] == "Ada Lovelace" and peers[S2]["selected"] is None and peers[S2]["version"] is None
        assert peers[S1]["selected"] == "n_greet" and peers[S1]["user_id"] == "usr_dev"
        # she selects a block: everyone sees it on the next read
        await api_client.post(url(wf), json={"session": S2, "selected": "n_end", "version": 3})
    finally:
        app.dependency_overrides.pop(current_user, None)
    now = (await api_client.get(url(wf))).json()["peers"]
    assert [(p["name"], p["selected"]) for p in now] == [("Ada Lovelace", "n_end"), ("Developer", "n_greet")]

    left = await api_client.delete(url(wf, f"/{S1}"))
    assert left.status_code == 204
    assert [p["session"] for p in (await api_client.get(url(wf))).json()["peers"]] == [S2]


async def test_a_tab_that_stops_beating_disappears_and_a_session_stays_one_persons(app, api_client):
    wf = await make_greeter(api_client)
    hub: PresenceHub = app.state.presence
    then = datetime.now(UTC) - timedelta(seconds=hub.ttl_s + 5)
    await hub.beat(wf, "usr_dev", Beat(session=S1), now=then)
    assert await hub.peers(wf) == []  # its last beat is older than the TTL: not shown
    await api_client.post(url(wf), json={"session": S2})
    assert [p.session for p in await hub.peers(wf)] == [S2]

    await person(app, "usr_eve", "Eve")
    act_as(app, "usr_eve")
    try:  # taking over someone else's session id is refused
        stolen = await api_client.post(url(wf), json={"session": S2})
        assert stolen.status_code == 409
        assert (await api_client.delete(url(wf, f"/{S2}"))).status_code == 204  # and cannot remove it either
    finally:
        app.dependency_overrides.pop(current_user, None)
    assert [p.session for p in await hub.peers(wf)] == [S2]
    for bad in ({"session": "short"}, {"session": "has spaces in it!"}, {"session": S1, "version": -1}):
        assert (await api_client.post(url(wf), json=bad)).status_code == 422


async def test_old_rows_are_cleaned_up_by_later_heartbeats_and_the_workflow_takes_its_rows_with_it(app, api_client):
    wf = await make_greeter(api_client)
    hub: PresenceHub = app.state.presence
    await hub.beat(wf, "usr_dev", Beat(session=S1), now=datetime.now(UTC) - timedelta(seconds=hub.ttl_s * 5))
    await hub.beat(wf, "usr_dev", Beat(session=S2))
    async with app.state.db.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(Presence)) == 1  # S1's row was deleted by S2's beat
    assert (await api_client.delete(f"/v1/workflows/{wf}")).status_code == 204
    async with app.state.db.sessionmaker() as s:
        assert await s.scalar(select(func.count()).select_from(Presence)) == 0


async def frames(stream, n: int, timeout: float = 5.0) -> list[dict]:
    out = []

    async def read() -> None:
        async for frame in stream:
            if frame.startswith("data: "):
                out.append(json.loads(frame[6:]))
                if len(out) == n:
                    return

    await asyncio.wait_for(read(), timeout)
    return out


async def test_the_stream_sends_the_list_and_again_when_it_changes_here_or_on_another_replica(app, api_client):
    wf = await make_greeter(api_client)
    mine = PresenceHub(app.state.db, poll_s=0.05)  # this replica
    other = PresenceHub(app.state.db, poll_s=0.05)  # another one, with its own memory and the same database
    stream = mine.stream(wf)
    assert (await frames(stream, 1))[0] == {"peers": []}

    async def later() -> None:
        await asyncio.sleep(0.1)
        await other.beat(wf, "usr_dev", Beat(session=S1, selected="n_greet", version=2))  # heard through the database
        await asyncio.sleep(0.1)
        await mine.beat(wf, "usr_dev", Beat(session=S1, selected="n_end", version=2))  # heard at once on this replica

    task = asyncio.create_task(later())
    got = await frames(stream, 2)
    await task
    assert [p["selected"] for f in got for p in f["peers"]] == ["n_greet", "n_end"]
    assert all("seen_at" not in p for f in got for p in f["peers"])  # a refreshed beat alone is no change
    await stream.aclose()


async def test_the_endpoints_check_the_workflow_the_role_and_the_organisation(app, api_client, org_b):  # noqa: F811
    wf = await make_greeter(api_client)
    assert (await api_client.post(url("wf_nope"), json={"session": S1})).status_code == 404
    assert (await api_client.get(url("wf_nope", "/stream"))).status_code == 404
    act_as(app, "usr_dev", "viewer")
    try:  # viewers watch too
        assert (await api_client.post(url(wf), json={"session": S1})).status_code == 200
        assert (await api_client.delete(url(wf, f"/{S1}"))).status_code == 204
    finally:
        app.dependency_overrides.pop(current_user, None)
    await api_client.post(url(wf), json={"session": S1})
    act_as_b(app)
    try:
        for r in (
            await api_client.post(url(wf), json={"session": S3}),
            await api_client.get(url(wf)),
            await api_client.get(url(wf, "/stream")),
            await api_client.delete(url(wf, f"/{S1}")),
        ):
            assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()
    assert [p["session"] for p in (await api_client.get(url(wf))).json()["peers"]] == [S1]


async def test_a_tabs_first_heartbeats_arriving_together_make_one_row(app, api_client, monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession

    wf = await make_greeter(api_client)
    hub: PresenceHub = app.state.presence
    await hub.beat(wf, "usr_dev", Beat(session=S3, selected="n_first"))  # the winner's row is already there ...
    real_get, stale = AsyncSession.get, {"left": 1}

    async def get(self, entity, ident, *args, **kwargs):
        if entity is Presence and stale["left"]:  # ... but the loser read before it was written
            stale["left"] -= 1
            return None
        return await real_get(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", get)
    await hub.beat(wf, "usr_dev", Beat(session=S3, selected="n_second"))  # used to fail with a UNIQUE error
    assert stale["left"] == 0
    async with app.state.db.sessionmaker() as s:
        rows = (await s.execute(select(Presence).where(Presence.session_id == S3))).scalars().all()
    assert [r.selected for r in rows] == ["n_second"]
