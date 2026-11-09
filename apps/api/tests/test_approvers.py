"""E-6: a step names who may decide it (roles, SCIM groups, people, not the person who started the run), and the
caller of a published workflow answers only when the step allows it."""

from __future__ import annotations

from test_events import P as EVENTS
from test_events import delivered, finished, linear, make_wf
from test_protocols import _publish, rpc
from test_publish import bearer, make_greeter
from test_roles import act_as
from test_runs_v1 import events, triage_api  # noqa: F401 - fixture
from test_share import ADA, say, share_on
from test_versions import HIGH_CASE, edit_node, new_triage

from agentcanvas.api.deps import current_user
from agentcanvas.db.models import AppUser, InterruptTask, Membership, ScimGroup, ScimGroupMember
from agentcanvas.ir.models import Workflow
from agentcanvas.services.approvers import node_approvers

APPROVE = {"value": {"decisions": [{"type": "approve"}]}}


async def _member(app, user_id: str, email: str, role: str, groups: tuple[str, ...] = ()) -> None:
    async with app.state.db.sessionmaker() as s:
        s.add(AppUser(id=user_id, oidc_subject=user_id, email=email))
        await s.flush()
        s.add(Membership(org_id="org_default", user_id=user_id, role=role, accepted=True))
        for g in groups:
            s.add(ScimGroup(id=f"grp_{g}", org_id="org_default", display_name=g))
            await s.flush()
            s.add(ScimGroupMember(group_id=f"grp_{g}", user_id=user_id))
        await s.commit()


async def _waiting(api, wf: str, thread: str) -> dict:
    r = await api.post(f"/v1/workflows/{wf}/runs/stream", json={"input": {"ticket": "Invoice?"}, "thread_id": thread})
    assert events(r.text)[-1]["status"] == "interrupted", r.text
    return next(i for i in (await api.get("/v1/inbox")).json() if i["thread_id"] == thread)


async def test_a_step_that_names_people_is_decided_only_by_them(app, triage_api):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    await edit_node(api, wf, "n_ag", approvers={"people": ["Lee@Example.com"]})
    item = await _waiting(api, wf, "th_people")
    async with app.state.db.sessionmaker() as s:  # the step's setting as it was when the run paused
        assert (await s.get(InterruptTask, item["id"])).approvers == {"people": ["Lee@Example.com"]}
    assert item["deciders"] == "Lee@Example.com" and item["may_decide"] is False
    r = await api.post(f"/v1/inbox/{item['id']}/decision", json=APPROVE)
    assert r.status_code == 403 and "Lee@Example.com" in r.json()["detail"], r.text
    assert (await api.post("/v1/threads/th_people/resume", json=APPROVE)).status_code == 403  # the other way in
    await _member(app, "usr_lee", "lee@example.com", "reviewer")
    act_as(app, "reviewer", "usr_lee")
    try:
        [mine] = [i for i in (await api.get("/v1/inbox")).json() if i["id"] == item["id"]]
        assert mine["may_decide"] is True
        r = await api.post(f"/v1/inbox/{item['id']}/decision", params={"stream": "true"}, json=APPROVE)
        assert events(r.text)[-1]["outcome"] == "success", r.text
    finally:
        app.dependency_overrides.pop(current_user, None)


async def test_roles_groups_and_an_escalation_role_may_decide(app, triage_api):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    await edit_node(api, wf, "n_ag", approvers={"roles": ["admin"], "groups": ["Finance"]})
    first = await _waiting(api, wf, "th_roles1")
    second = await _waiting(api, wf, "th_roles2")
    assert first["deciders"] == "admin role; group Finance"
    await _member(app, "usr_rev", "rev@example.com", "reviewer")
    await _member(app, "usr_fin", "fin@example.com", "reviewer", groups=("finance",))  # names match ignoring case
    try:
        act_as(app, "reviewer", "usr_rev")
        r = await api.post(f"/v1/inbox/{first['id']}/decision", json=APPROVE)
        assert r.status_code == 403 and "admin role; group Finance" in r.text, r.text
        async with app.state.db.sessionmaker() as s:  # escalated to reviewers: they may decide it now
            (await s.get(InterruptTask, second["id"])).assignee_role = "reviewer"
            await s.commit()
        r = await api.post(f"/v1/inbox/{second['id']}/decision", params={"stream": "true"}, json=APPROVE)
        assert events(r.text)[-1]["outcome"] == "success", r.text
        act_as(app, "reviewer", "usr_fin")
        r = await api.post(f"/v1/inbox/{first['id']}/decision", params={"stream": "true"}, json=APPROVE)
        assert events(r.text)[-1]["outcome"] == "success", r.text
    finally:
        app.dependency_overrides.pop(current_user, None)
    third = await _waiting(api, wf, "th_roles3")  # an admin (the default user here) by role
    r = await api.post(f"/v1/inbox/{third['id']}/decision", params={"stream": "true"}, json=APPROVE)
    assert events(r.text)[-1]["outcome"] == "success", r.text


async def test_not_the_person_who_started_the_run(app, triage_api):  # noqa: F811
    api, wf = triage_api, triage_api.wf_id
    await edit_node(api, wf, "n_ag", approvers={"not_initiator": True})
    item = await _waiting(api, wf, "th_four_eyes")
    assert item["may_decide"] is False and item["deciders"] is None  # anyone else may: it names nobody
    r = await api.post(f"/v1/inbox/{item['id']}/decision", json=APPROVE)
    assert r.status_code == 403 and "started the run" in r.text
    await _member(app, "usr_two", "two@example.com", "reviewer")
    act_as(app, "reviewer", "usr_two")
    try:
        r = await api.post("/v1/threads/th_four_eyes/resume", json=APPROVE)
        assert events(r.text)[-1]["outcome"] == "success", r.text
    finally:
        app.dependency_overrides.pop(current_user, None)


async def test_the_caller_answers_only_when_the_step_allows_it(app, api_client):
    """A deployment's key, MCP `answer`, an A2A reply: refused for a step that names its deciders, unless it also
    allows the caller. A step that names nobody stays the caller's to answer (test_protocols)."""
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    wf = await new_triage(api_client)
    await edit_node(api_client, wf["id"], "n_ok", approvers={"roles": ["reviewer"]})
    dep, key = await _publish(api_client, wf["id"])
    auth = bearer(key)

    run = (
        await api_client.post(f"/v1/deployments/{dep}/runs/wait", json={"input": {"ticket": "down"}}, headers=auth)
    ).json()
    assert run["status"] == "interrupted", run
    resume = f"/v1/deployments/{dep}/threads/{run['thread_id']}/resume"
    r = await api_client.post(resume, json={"value": {"approved": True}}, headers=auth)
    assert r.status_code == 403 and "Inbox" in r.json()["detail"], r.text

    call = rpc("tools/call", {"name": "triage", "arguments": {"ticket": "down"}})
    paused = (await api_client.post(f"/v1/deployments/{dep}/mcp", json=call, headers=auth)).json()["result"]
    thread = paused["structuredContent"]["thread_id"]
    answer = rpc("tools/call", {"name": "answer", "arguments": {"thread_id": thread, "approve": True}})
    refused = (await api_client.post(f"/v1/deployments/{dep}/mcp", json=answer, headers=auth)).json()["result"]
    assert refused["isError"] is True and "Inbox" in refused["content"][0]["text"]

    a2a = f"/v1/deployments/{dep}/a2a"
    msg = {"message": {"role": "user", "messageId": "m1", "parts": [{"kind": "text", "text": "down"}]}}
    task = (await api_client.post(a2a, json=rpc("message/send", msg), headers=auth)).json()["result"]
    reply = {"message": {"role": "user", "messageId": "m2", "taskId": task["id"], "contextId": task["contextId"],
                         "parts": [{"kind": "text", "text": "yes"}]}}  # fmt: skip
    err = (await api_client.post(a2a, json=rpc("message/send", reply), headers=auth)).json()
    assert err["error"]["code"] == -32602 and "Inbox" in err["error"]["message"]

    item = next(i for i in (await api_client.get("/v1/inbox")).json() if i["thread_id"] == run["thread_id"])
    await _member(app, "usr_rev", "rev@example.com", "reviewer")
    act_as(app, "reviewer", "usr_rev")
    try:  # a reviewer decides it in the Inbox instead
        r = await api_client.post(f"/v1/inbox/{item['id']}/decision", params={"stream": "true"}, json=APPROVE)
        assert events(r.text)[-1]["outcome"] == "success", r.text
    finally:
        app.dependency_overrides.pop(current_user, None)

    await edit_node(api_client, wf["id"], "n_ok", approvers={"roles": ["reviewer"], "caller": True})
    await _publish(api_client, wf["id"])  # the same deployment, now on the new version
    run = (
        await api_client.post(f"/v1/deployments/{dep}/runs/wait", json={"input": {"ticket": "x"}}, headers=auth)
    ).json()
    resume = f"/v1/deployments/{dep}/threads/{run['thread_id']}/resume"
    done = (await api_client.post(resume, json={"value": {"approved": True}}, headers=auth)).json()
    assert done["status"] == "success", done


async def test_a_share_visitor_cannot_answer_a_question_that_names_its_deciders(app, api_client):
    wf = await make_greeter(api_client)
    cur = (await api_client.get(f"/v1/workflows/{wf}")).json()
    ir = cur["ir"]
    ir["state"]["channels"].append({"name": "nick", "type": {"type": "string"}, "io": "out"})
    ask = {"prompt": "What should I call you?", "output": "nick", "approvers": {"people": ["ops@example.com"]}}
    ir["nodes"].append({"id": "n_ask", "type": "core.human_input", "name": "ask", "config": ask})
    first = ""
    for e in ir["edges"]:
        if e["kind"] == "flow" and e["source"]["node"] == "START":
            first, e["target"]["node"] = e["target"]["node"], "n_ask"
    ir["edges"].append({"id": "e_ask", "kind": "flow", "source": {"node": "n_ask"}, "target": {"node": first}})
    r = await api_client.put(f"/v1/workflows/{wf}", json={"ir": ir}, headers={"If-Match": str(cur["version"])})
    assert r.status_code == 200, r.text
    dep = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()["deployment"]["id"]
    token = (await share_on(api_client, dep))["token"]
    evs = events((await say(api_client, token, "Ada")).text)
    assert evs[-1]["status"] == "interrupted"
    r = await api_client.post(
        f"/v1/share/{token}/threads/{evs[0]['threadId']}/resume", json={"visitor": ADA, "text": "Ace"}
    )
    assert r.status_code == 403 and "Inbox" in r.text


async def test_an_event_handler_cannot_answer_a_step_that_names_its_deciders(app, api_client):
    src = (
        "from agentcanvas.sdk import on\n\n\n"
        '@on("contract.signed")\n'
        'def signed(event, act):\n    act.resume(event.data["thread_id"], {"approved": True})\n'
    )
    await api_client.put(f"{EVENTS}/files/events/sign.py", json={"content": src})
    ask = {"id": "n_a", "type": "core.approval", "name": "sign",
           "config": {"show": "out", "output": "ok", "approvers": {"roles": ["admin"]}}}  # fmt: skip
    wf = await make_wf(api_client, linear("Contract", [ask], ["out", "ok"]))
    r = await api_client.post(f"/v1/workflows/{wf}/runs/stream", json={"thread_id": "th_signed"})
    assert events(r.text)[-1]["status"] == "interrupted"
    await delivered(app)
    res = (
        await api_client.post(f"{EVENTS}/events", json={"type": "contract.signed", "data": {"thread_id": "th_signed"}})
    ).json()
    assert res["status"] == "error" and "cannot answer" in res["handlers"][0]["error"], res
    await finished(app, res["handlers"][0]["runs"])
    assert (await api_client.get("/v1/threads/th_signed")).json()["status"] == "interrupted"


def _wf(name: str, nodes: list[dict], channels: list[str]) -> Workflow:
    return Workflow.model_validate({**linear(name, nodes, channels), "id": f"wf_{name}"})


def test_steps_inside_a_subgraph_name_their_deciders_too():
    inner = {
        "name": "inner", "state": {"channels": [{"name": "x"}]}, "edges": [],
        "nodes": [{"id": "n_in", "type": "core.approval", "name": "in",
                   "config": {"show": "x", "output": "x", "approvers": {"roles": ["admin"]}}}],
    }  # fmt: skip
    wf = _wf(
            "outer",
            [
                {"id": "n_sub", "type": "core.subgraph", "name": "sub",
                 "config": {"workflow": inner, "inputs": [], "outputs": []}},
                {"id": "n_ok", "type": "core.approval", "name": "ok",
                 "config": {"show": "out", "output": "ok", "approvers": {}}},
            ],
            ["out", "ok"],
    )  # fmt: skip
    assert node_approvers(wf) == {"n_in": {"roles": ["admin"]}}  # an empty setting names nobody


def test_the_validator_explains_the_setting():
    from agentcanvas.registry import default_registry
    from agentcanvas.validation.engine import validate

    ask = {"id": "n_a", "type": "core.approval", "name": "sign",
           "config": {"show": "out", "output": "ok", "approvers": {"caller": True, "not_initiator": True}}}  # fmt: skip
    agent_cfg = {"system_prompt": "", "approvers": {"roles": ["admin"]}}
    agent = {"id": "n_ag", "type": "core.agent", "name": "agent", "config": agent_cfg}
    found = {(d.code, d.node_id) for d in validate(_wf("v", [ask, agent], ["out", "ok"]), default_registry())}
    assert {("E037", "n_a"), ("I011", "n_a"), ("W027", "n_ag"), ("I011", "n_ag")} <= found
