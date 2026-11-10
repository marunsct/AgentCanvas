"""E-11, the rest of a deployment: its AG-UI endpoint and its deployment API (runs/wait and the others) take a person's
OAuth access token when the deployment has sign-in on, with the same checks as MCP and A2A; each person reaches only
their own conversations, runs and data, and the keys keep working."""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from test_agui import ag_ui, chat_deployment, run_input, user
from test_deployment_oauth import (
    ISSUER,
    _published,
    _turn_on,
    access_token,
    challenge_params,
    mcp_url,
    resource,
    settings,  # noqa: F401  (the fixture: the server's sign-in issuer)
    tokens,  # noqa: F401  (the fixture: the fake issuer's keys)
)
from test_protocols import _publish, rpc
from test_publish import bearer
from test_versions import HIGH_CASE, new_triage

from agentcanvas.db.models import AuditLog, Thread
from agentcanvas.services.deployment_oauth import DEFAULT_SCOPE, READ_SCOPE

pytestmark = pytest.mark.usefixtures("tokens")  # the fake issuer's keys, for every test
ALICE = f"oidc:{ISSUER}:alice"


def api_resource(dep: str) -> str:
    return f"http://test/v1/deployments/{dep}"


def person(dep: str, sub: str = "alice", **over: Any) -> dict[str, str]:
    """A signed-in person's header for the deployment API."""
    return bearer(access_token(api_resource(dep), sub=sub, **over))


def wait_url(dep: str) -> str:
    return f"/v1/deployments/{dep}/runs/wait"


GREET = {"input": {"who": "Ada"}}


async def test_the_deployment_api_takes_tokens_for_the_deployments_address(app, api_client):
    dep, key = await _published(api_client)
    off = await api_client.post(wait_url(dep), json=GREET, headers=person(dep))
    assert off.status_code == 401 and "www-authenticate" not in off.headers  # sign-in off: keys only, as before

    out = await _turn_on(api_client, dep)
    meta_url = f"http://test/.well-known/oauth-protected-resource/v1/deployments/{dep}"
    assert out["api_resource"] == api_resource(dep) and out["api_metadata"] == meta_url
    assert out["ag_ui_resource"] == f"{api_resource(dep)}/ag-ui" and out["ag_ui_metadata"] == f"{meta_url}/ag-ui"

    refused = await api_client.post(wait_url(dep), json=GREET)
    assert refused.status_code == 401
    assert challenge_params(refused.headers["www-authenticate"]) == {
        "scope": DEFAULT_SCOPE,
        "resource_metadata": meta_url,
    }
    meta = await api_client.get(meta_url.removeprefix("http://test"))
    assert meta.status_code == 200 and meta.json() == {
        "resource": api_resource(dep), "authorization_servers": [ISSUER], "bearer_methods_supported": ["header"],
        "scopes_supported": [DEFAULT_SCOPE, READ_SCOPE], "resource_name": "Greeter",
    }  # fmt: skip
    for path in ("runs", "runs/run_x", "end-users/erase", "threads/th_x/resume"):  # one resource for all of them
        assert (
            await api_client.get(f"/.well-known/oauth-protected-resource/v1/deployments/{dep}/{path}")
        ).status_code == 404

    done = await api_client.post(wait_url(dep), json=GREET, headers=person(dep))
    assert done.status_code == 200 and done.json()["output"]["out"] == "Hello Ada", done.text
    async with app.state.db.sessionmaker() as s:
        thread = await s.get(Thread, done.json()["thread_id"])
        assert thread is not None and thread.end_user == f"api:{dep}:{ALICE}"
    # a person's user_id in the body is ignored: the token says who they are
    spoofed = await api_client.post(wait_url(dep), json={**GREET, "user_id": "bob"}, headers=person(dep))
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(Thread, spoofed.json()["thread_id"])).end_user == f"api:{dep}:{ALICE}"

    # another endpoint's token is not for the API, nor the API's for another endpoint
    mcp_token = bearer(access_token(resource(dep)))
    wrong = await api_client.post(wait_url(dep), json=GREET, headers=mcp_token)
    assert wrong.status_code == 401 and challenge_params(wrong.headers["www-authenticate"])["error"] == "invalid_token"
    call = rpc("tools/call", {"name": "greeter", "arguments": {"who": "Ada"}})
    assert (await api_client.post(mcp_url(dep), json=call, headers=person(dep))).status_code == 401
    # every route of the API takes them, and the keys keep working
    started = await api_client.post(f"/v1/deployments/{dep}/runs", json=GREET, headers=person(dep))
    assert started.status_code == 202 and started.headers["location"].endswith(started.json()["run_id"])
    stream = await api_client.post(f"/v1/deployments/{dep}/runs/stream", json=GREET, headers=person(dep))
    assert stream.status_code == 200 and "RUN_FINISHED" in stream.text
    assert (await api_client.post(wait_url(dep), json=GREET, headers=bearer(key))).json()["status"] == "success"
    bad_key = await api_client.post(wait_url(dep), json=GREET, headers=bearer("ac_wrong"))
    assert bad_key.status_code == 401 and "resource_metadata" in bad_key.headers["www-authenticate"]


async def test_people_read_stop_and_rate_only_their_own_runs(api_client):
    dep, key = await _published(api_client)
    await _turn_on(api_client, dep)
    alice, bob = person(dep), person(dep, "bob")
    run = (await api_client.post(wait_url(dep), json=GREET, headers=alice)).json()
    base = f"/v1/deployments/{dep}/runs/{run['run_id']}"
    assert (await api_client.get(base, headers=alice)).json()["status"] == "success"
    assert (await api_client.get(base, headers=bearer(key))).status_code == 200  # a key reads any of them
    for method, path, body in [("GET", "", None), ("POST", "/cancel", None), ("POST", "/feedback", {"rating": "up"})]:
        theirs = await api_client.request(method, base + path, json=body, headers=bob)
        assert theirs.status_code == 404 and "not found" in theirs.text, (path, theirs.text)
    assert (await api_client.post(base + "/cancel", headers=alice)).json() == {"cancelled": False, "pending": False}
    assert (await api_client.post(base + "/feedback", json={"rating": "down"}, headers=alice)).status_code == 204
    # nor may Bob continue Alice's conversation
    again = {**GREET, "thread_id": run["thread_id"]}
    assert (await api_client.post(wait_url(dep), json=again, headers=bob)).status_code == 404
    assert (await api_client.post(wait_url(dep), json=again, headers=alice)).json()["status"] == "success"


async def test_reading_a_result_takes_the_read_scope_alone(api_client):
    dep, _ = await _published(api_client)
    await _turn_on(api_client, dep)
    run = (await api_client.post(wait_url(dep), json=GREET, headers=person(dep))).json()
    reader = person(dep, scope=f"openid {READ_SCOPE}")
    got = await api_client.get(f"/v1/deployments/{dep}/runs/{run['run_id']}", headers=reader)
    assert got.status_code == 200 and got.json()["output"]["out"] == "Hello Ada"
    export = await api_client.post(f"/v1/deployments/{dep}/end-users/export", json={}, headers=reader)
    assert export.status_code == 200 and len(export.json()["conversations"]) == 1
    for path in ("runs/wait", f"runs/{run['run_id']}/cancel", "end-users/erase"):
        r = await api_client.post(f"/v1/deployments/{dep}/{path}", json=GREET if "wait" in path else {}, headers=reader)
        params = challenge_params(r.headers["www-authenticate"])
        assert r.status_code == 403 and params["error"] == "insufficient_scope", (path, r.text)
    nothing = await api_client.get(f"/v1/deployments/{dep}/runs/{run['run_id']}", headers=person(dep, scope="openid"))
    assert nothing.status_code == 403 and READ_SCOPE in nothing.json()["detail"]


async def test_a_signed_in_person_exports_and_erases_only_their_own_data(app, api_client):
    dep, key = await _published(api_client)
    await _turn_on(api_client, dep)
    await api_client.post(wait_url(dep), json=GREET, headers=person(dep))
    await api_client.post(wait_url(dep), json=GREET, headers=person(dep, "bob"))
    export, erase = f"/v1/deployments/{dep}/end-users/export", f"/v1/deployments/{dep}/end-users/erase"
    mine = await api_client.post(export, json={"user_id": ALICE}, headers=person(dep))  # naming oneself is fine
    assert mine.status_code == 200 and len(mine.json()["conversations"]) == 1
    for url in (export, erase):
        r = await api_client.post(url, json={"user_id": f"oidc:{ISSUER}:bob"}, headers=person(dep))
        assert r.status_code == 403 and "only their own" in r.text
    assert (await api_client.post(erase, json={}, headers=bearer(key))).status_code == 422  # a key names whose
    gone = await api_client.post(erase, json={}, headers=person(dep))
    assert gone.status_code == 200 and gone.json()["conversations"] == 1
    left = await api_client.post(export, json={"user_id": f"oidc:{ISSUER}:bob"}, headers=bearer(key))
    assert len(left.json()["conversations"]) == 1  # Bob's are untouched
    async with app.state.db.sessionmaker() as s:
        row = (await s.scalars(select(AuditLog).where(AuditLog.action == "api_erase_end_user"))).all()[-1]
    assert row.detail["caller"] == "oauth" and row.detail["end_user"] == ALICE


async def test_a_paused_conversation_is_answered_only_by_the_person_whose_it_is(app, api_client):
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    dep, _ = await _publish(api_client, (await new_triage(api_client))["id"])
    await _turn_on(api_client, dep)
    paused = (await api_client.post(wait_url(dep), json={"input": {"ticket": "down"}}, headers=person(dep))).json()
    assert paused["status"] == "interrupted"
    resume = f"/v1/deployments/{dep}/threads/{paused['thread_id']}/resume"
    theirs = await api_client.post(resume, json={"value": {"approved": True}}, headers=person(dep, "bob"))
    assert theirs.status_code == 404 and "not found" in theirs.text
    done = await api_client.post(resume, json={"value": {"approved": True}}, headers=person(dep))
    assert done.status_code == 200 and done.json()["status"] == "success", done.text


async def test_a_callback_needs_a_key(app, api_client):
    """A callback is signed with the deployment's secret, which its own systems trust: never sent where a person
    chooses."""
    dep, _ = await _published(api_client)
    await _turn_on(api_client, dep)
    assert (await api_client.post(f"/v1/deployments/{dep}/callback-secret")).status_code == 200
    body = {**GREET, "callback_url": "https://hooks.example.com/done"}
    r = await api_client.post(f"/v1/deployments/{dep}/runs", json=body, headers=person(dep))
    assert r.status_code == 422 and "deployment key" in r.text


async def test_a_persons_uploads_are_theirs_alone(api_client):
    from test_files import PNG, make_photo_flow

    wf = await make_photo_flow(api_client)
    dep, key = await _publish(api_client, wf)
    await _turn_on(api_client, dep)
    url = f"/v1/deployments/{dep}/files"
    alices = (await api_client.post(url, files={"file": ("me.png", PNG, "image/png")}, headers=person(dep))).json()
    keys = (await api_client.post(url, files={"file": ("k.png", PNG, "image/png")}, headers=bearer(key))).json()

    async def run(who: dict[str, str], ref: dict) -> Any:
        return await api_client.post(wait_url(dep), json={"input": {"who": "Ada", "photo": ref}}, headers=who)

    assert (await run(person(dep), alices)).json()["status"] == "success"
    taken = await run(person(dep, "bob"), {"kind": "file", "id": alices["id"]})
    assert taken.status_code == 422 and "is not in this project" in taken.text
    assert (await run(person(dep, "bob"), keys)).json()["status"] == "success"  # the deployment's own are shared
    assert (await run(bearer(key), alices)).status_code == 422  # and a key does not reach a person's either


async def test_ag_ui_takes_a_persons_token_for_its_own_address(app, api_client):
    _, dep, key = await chat_deployment(app, api_client, ["Hello Alice", "Hello again", "Hi key"])
    await _turn_on(api_client, dep)
    url = f"/v1/deployments/{dep}/ag-ui"
    refused = await api_client.post(url, json=run_input("th-oauth-1", user("hi")))
    assert refused.status_code == 401
    meta_url = challenge_params(refused.headers["www-authenticate"])["resource_metadata"]
    assert meta_url == f"http://test/.well-known/oauth-protected-resource/v1/deployments/{dep}/ag-ui"
    meta = (await api_client.get(meta_url.removeprefix("http://test"))).json()
    assert meta["resource"] == f"{api_resource(dep)}/ag-ui" and meta["scopes_supported"] == [DEFAULT_SCOPE]
    assert (
        await api_client.post(url, json=run_input("th-oauth-1", user("hi")), headers=person(dep))
    ).status_code == 401

    alice = access_token(f"{api_resource(dep)}/ag-ui")
    events = await ag_ui(api_client, dep, alice, run_input("th-oauth-1", user("hi"), forwardedProps={"user_id": "x"}))
    assert events[-1].type == "RUN_FINISHED"
    async with app.state.db.sessionmaker() as s:
        assert (await s.get(Thread, "th-oauth-1")).end_user == f"api:{dep}:{ALICE}"
        plain = (await s.scalars(select(AuditLog).where(AuditLog.action == "api_ag_ui"))).all()
    assert plain == []  # a plain run is not in the audit log, signed in or not
    bob = access_token(f"{api_resource(dep)}/ag-ui", sub="bob")
    history = [user("hi"), {"id": "a1", "role": "assistant", "content": "Hello Alice"}, user("again", "u2")]
    taken = await api_client.post(url, json=run_input("th-oauth-1", *history), headers=bearer(bob))
    assert taken.status_code == 409 and "taken" in taken.text  # the same answer as another deployment's thread
    assert (await ag_ui(api_client, dep, alice, run_input("th-oauth-1", *history)))[-1].type == "RUN_FINISHED"
    assert (await ag_ui(api_client, dep, key, run_input("th-oauth-key", user("hi"))))[-1].type == "RUN_FINISHED"
