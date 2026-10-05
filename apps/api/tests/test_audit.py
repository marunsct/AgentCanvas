"""Audit log (M7-T4): edits, publishes, approvals and secret changes are recorded with actor; reads are not."""

import pytest
from test_publish import make_greeter
from test_runs_v1 import events, triage_api  # noqa: F401 - fixture

from agentcanvas.api.audit import payload_hash
from agentcanvas.api.deps import CurrentUser, current_user


async def log(api, **params) -> list[dict]:
    r = await api.get("/v1/org/audit", params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def test_every_approval_is_logged_with_actor_and_payload_hash(triage_api):  # noqa: F811
    """M7-T4 AC: every approval is logged with actor and payload hash."""
    api, wf = triage_api, triage_api.wf_id
    await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": "Invoice?"}, "thread_id": "th_a"})
    item = (await api.get("/v1/inbox")).json()[0]
    value = {"decisions": [{"type": "approve"}]}
    r = await api.post(f"/v1/inbox/{item['id']}/decision", params={"stream": "true"}, json={"value": value})
    assert events(r.text)[-1]["outcome"] == "success"
    [entry] = await log(api, action="decide")
    assert entry["actor_id"] == "usr_dev" and entry["actor_email"] == "dev@localhost"
    assert entry["target"] == f"interrupt_id={item['id']}"
    assert entry["detail"]["payload_sha256"] == payload_hash(value)
    assert entry["detail"]["kind"] == "tool_approval"


async def test_edits_publishes_and_secrets_are_logged_reads_are_not(api_client):
    wf = await make_greeter(api_client)
    await api_client.get(f"/v1/workflows/{wf}")
    assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).status_code == 200
    assert (await api_client.put("/v1/projects/prj_default/secrets/TOKEN", json={"value": "s3cret"})).status_code == 204
    assert (
        await api_client.delete(f"/v1/workflows/{wf}/test-cases/tc_missing")
    ).status_code == 404  # failed: not logged
    actions = [e["action"] for e in await log(api_client)]
    assert actions[:2] == ["put_secret", "publish"]
    assert "create_workflow" in actions and "delete_test_case" not in actions
    assert not any(a.startswith(("get", "list")) for a in actions)
    secret = (await log(api_client, action="put_secret"))[0]
    assert secret["target"] == "name=TOKEN" and "s3cret" not in str(secret)
    assert (await log(api_client, target=wf))[0]["action"] == "publish"


async def test_public_resume_is_logged_against_the_deployments_org(api_client):
    wf = await make_greeter(api_client)
    pub = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()
    key, dep = pub["api_key"], pub["deployment"]["id"]
    headers = {"Authorization": f"Bearer {key}"}
    run = (
        await api_client.post(f"/v1/deployments/{dep}/runs/wait", json={"input": {"who": "Ada"}}, headers=headers)
    ).json()
    r = await api_client.post(
        f"/v1/deployments/{dep}/threads/{run['thread_id']}/resume", json={"value": 1}, headers=headers
    )
    assert r.status_code == 200, r.text
    assert not await log(api_client, action="api_run_wait")  # run calls are not audited
    [entry] = await log(api_client, action="api_resume")
    assert entry["actor_id"] is None and entry["detail"]["caller"] == "deployment key"
    assert entry["detail"]["payload_sha256"] == payload_hash(1)


@pytest.mark.parametrize("role", ["builder", "viewer"])
async def test_only_admins_read_the_log(app, api_client, role):
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_dev", org_id="org_default", role=role)
    try:
        assert (await api_client.get("/v1/org/audit")).status_code == 403
    finally:
        app.dependency_overrides.pop(current_user)


async def test_log_is_per_organisation(app, api_client):
    await make_greeter(api_client)
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_b", org_id="org_b", role="admin")
    try:
        assert (await api_client.get("/v1/org/audit")).json() == []
    finally:
        app.dependency_overrides.pop(current_user)


async def test_mutations_without_an_id_in_the_path_name_their_target(api_client):
    """Member and memory changes carry their target in the body or query: the log still names it."""
    carol = (await api_client.put("/v1/org/members", json={"email": "carol@example.com", "role": "viewer"})).json()
    [entry] = await log(api_client, action="set_member")
    assert entry["target"] == f"user_id={carol['user_id']}"
    assert entry["detail"]["email"] == "carol@example.com" and entry["detail"]["role"] == "viewer"
    ns = "memories/user/usr_dev"
    r = await api_client.delete("/v1/memory/items", params={"namespace": ns, "key": "about:drinks"})
    assert r.status_code == 204
    [entry] = await log(api_client, action="delete_item")
    assert entry["target"] == f"namespace={ns}" and entry["detail"]["key"] == "about:drinks"
