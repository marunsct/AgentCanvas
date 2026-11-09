"""Sprint 23 share links (S23): a published deployment as a public chat page and chat widget."""

from __future__ import annotations

import json

from test_publish import bearer, make_greeter
from test_ratelimit import limits  # noqa: F401 - fixture
from test_versions import HIGH_CASE, new_triage

from agentcanvas.ir.models import Workflow
from agentcanvas.services.share import DEFAULT_AI_NOTICE, InputShape, answer_text, input_shape, text_inputs

ADA = "visitor-ada-0123456789"
BO = "visitor-bo-0123456789ab"


def events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


async def publish(api) -> tuple[str, str, str]:
    wf = await make_greeter(api)
    pub = (await api.post(f"/v1/workflows/{wf}/publish", json={})).json()
    return wf, pub["deployment"]["id"], pub["api_key"]


async def share_on(api, dep: str, **settings) -> dict:
    r = await api.put(f"/v1/deployments/{dep}/share", json={"enabled": True, **settings})
    assert r.status_code == 200, r.text
    return r.json()


async def say(api, token: str, message: str, visitor: str = ADA, thread_id: str | None = None):
    body = {"message": message, "visitor": visitor, **({"thread_id": thread_id} if thread_id else {})}
    return await api.post(f"/v1/share/{token}/runs/stream", json=body)


async def test_enable_share_and_chat(api_client):
    """S23 AC: a builder switches the link on; a visitor (no sign-in, no key) chats and continues the conversation."""
    _, dep, _ = await publish(api_client)
    off = (await api_client.get(f"/v1/deployments/{dep}/share")).json()
    assert off["enabled"] is False and off["token"] is None and off["path"] is None  # no link until one is made
    assert off["title"] == "Greeter" and off["input"] == {"field": "who", "chat": False}
    assert (await api_client.get(f"/v1/deployments/{dep}/share")).json() == off  # reading it makes nothing
    saved = (await api_client.put(f"/v1/deployments/{dep}/share", json={"title": "Say hi"})).json()
    assert saved["enabled"] is False and saved["token"].startswith("sh_") and saved["path"] == f"/s/{saved['token']}"
    assert (await api_client.get(f"/v1/share/{saved['token']}")).status_code == 404  # off until switched on

    share = await share_on(
        api_client, dep, title="Say hi", greeting="Tell me your name.", suggestions=["Ada", " ", "Bo"]
    )
    token = share["token"]
    assert share["token"] == saved["token"] and share["suggestions"] == ["Ada", "Bo"]

    info = await api_client.get(f"/v1/share/{token}")
    assert info.status_code == 200
    assert info.json() == {
        "title": "Say hi", "greeting": "Tell me your name.", "suggestions": ["Ada", "Bo"],
        "input": {"field": "who", "chat": False}, "attachments": None, "frame_origins": [],
        "ai_notice": DEFAULT_AI_NOTICE,
    }  # fmt: skip
    # nothing internal: no workflow, deployment, key or version
    assert all(s not in info.text for s in ("wf_", "dep_", "ac_", "ver_", "n_g", "core.set"))

    r = await say(api_client, token, "Ada")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = events(r.text)
    assert [e["type"] for e in evs] == ["RUN_STARTED", "RUN_FINISHED"]
    thread = evs[0]["threadId"]
    assert evs[-1]["answer"] == "Hello Ada" and evs[-1]["status"] == "success" and "output" not in evs[-1]

    again = events((await say(api_client, token, "Bo", thread_id=thread)).text)
    assert again[0]["threadId"] == thread and again[-1]["answer"] == "Hello Bo"

    # the conversation is an ordinary deployment run, with its end user under the deployment
    assert (await api_client.get(f"/v1/threads/{thread}")).status_code == 200
    run = (await api_client.get(f"/v1/runs/{again[-1]['runId']}")).json()
    assert run["thread_id"] == thread

    # thumbs: by the visitor of the conversation only
    fb = f"/v1/share/{token}/runs/{again[-1]['runId']}/feedback"
    assert (await api_client.post(fb, json={"rating": "down", "visitor": BO})).status_code == 404
    assert (await api_client.post(fb, json={"rating": "up", "visitor": ADA})).status_code == 204


async def test_visitors_keep_to_their_own_conversations(api_client):
    """A conversation continues only for the visitor who started it, on the link it was started on; another
    deployment's (or the key API's) conversations are out of reach."""
    _, dep, key = await publish(api_client)
    token = (await share_on(api_client, dep))["token"]
    thread = events((await say(api_client, token, "Ada")).text)[0]["threadId"]
    assert (await say(api_client, token, "Bo", visitor=BO, thread_id=thread)).status_code == 404

    # a conversation started with the deployment key is not a share conversation
    api_thread = (
        await api_client.post(f"/v1/deployments/{dep}/runs/wait", json={"input": {"who": "Cy"}}, headers=bearer(key))
    ).json()["thread_id"]
    assert (await say(api_client, token, "Cy", thread_id=api_thread)).status_code == 404

    # another deployment's link cannot reach this conversation
    _, dep2, _ = await publish(api_client)
    token2 = (await share_on(api_client, dep2))["token"]
    assert (await say(api_client, token2, "Ada", thread_id=thread)).status_code == 404
    assert (await say(api_client, token, "Ada", thread_id=thread)).status_code == 200

    # a short or odd visitor secret is refused
    bad = await api_client.post(f"/v1/share/{token}/runs/stream", json={"message": "x", "visitor": "short"})
    assert bad.status_code == 422


async def test_disabled_regenerated_or_unpublished_links_are_404(api_client):
    _, dep, _ = await publish(api_client)
    token = (await share_on(api_client, dep))["token"]
    thread = events((await say(api_client, token, "Ada")).text)[0]["threadId"]

    await api_client.put(f"/v1/deployments/{dep}/share", json={"enabled": False})
    assert (await api_client.get(f"/v1/share/{token}")).status_code == 404
    assert (await say(api_client, token, "Ada")).status_code == 404
    await api_client.put(f"/v1/deployments/{dep}/share", json={"enabled": True})
    assert (await say(api_client, token, "Ada", thread_id=thread)).status_code == 200  # back on: same link works

    fresh = (await api_client.post(f"/v1/deployments/{dep}/share/regenerate")).json()
    assert fresh["token"] != token and fresh["enabled"] is True
    assert (await api_client.get(f"/v1/share/{token}")).status_code == 404
    assert (await say(api_client, fresh["token"], "Ada", thread_id=thread)).status_code == 404  # old conversations end
    assert (await say(api_client, fresh["token"], "Ada")).status_code == 200

    await api_client.delete(f"/v1/deployments/{dep}")  # unpublished
    assert (await api_client.get(f"/v1/share/{fresh['token']}")).status_code == 404
    assert (await api_client.get("/v1/share/sh_nope")).status_code == 404


async def test_publishing_again_does_not_revive_the_old_link_or_its_conversations(api_client):
    wf, dep, _ = await publish(api_client)
    token = (await share_on(api_client, dep))["token"]
    thread = events((await say(api_client, token, "Ada")).text)[0]["threadId"]
    assert (await api_client.delete(f"/v1/deployments/{dep}")).status_code == 204
    again = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()
    assert again["deployment"]["id"] == dep
    assert (await api_client.get(f"/v1/share/{token}")).status_code == 404
    share = (await api_client.get(f"/v1/deployments/{dep}/share")).json()
    assert share["enabled"] is False and share["token"] != token
    fresh = (await share_on(api_client, dep))["token"]
    assert (await say(api_client, fresh, "Ada", thread_id=thread)).status_code == 404  # the old conversation ended
    assert (await api_client.get(f"/v1/threads/{thread}")).status_code == 200  # its run stays for the builders


async def test_settings_validation_and_roles(api_client, app):
    from agentcanvas.api.deps import CurrentUser, current_user

    _, dep, _ = await publish(api_client)
    r = await api_client.put(f"/v1/deployments/{dep}/share", json={"input_field": "nope"})
    assert r.status_code == 422
    share = (await api_client.get(f"/v1/deployments/{dep}/share")).json()
    assert share["inputs"] == ["who", "out"] and share["input_field"] is None  # what the page offers; None: picked
    chosen = (await api_client.put(f"/v1/deployments/{dep}/share", json={"input_field": "who"})).json()
    assert chosen["input_field"] == "who" and chosen["input"]["field"] == "who"
    cleared = (await api_client.put(f"/v1/deployments/{dep}/share", json={"input_field": ""})).json()
    assert cleared["input_field"] is None
    r = await api_client.put(f"/v1/deployments/{dep}/share", json={"suggestions": ["a"] * 7})
    assert r.status_code == 422
    app.dependency_overrides[current_user] = lambda: CurrentUser(
        id="usr_v", org_id="org_default", role="viewer", memberships={"org_default": "viewer"}
    )
    try:  # the link is a capability: viewers neither see nor change it
        assert (await api_client.get(f"/v1/deployments/{dep}/share")).status_code == 403
        assert (await api_client.put(f"/v1/deployments/{dep}/share", json={"enabled": True})).status_code == 403
    finally:
        app.dependency_overrides.pop(current_user, None)


async def test_share_calls_count_against_the_deployment_limit(api_client, limits):  # noqa: F811
    limits.rate_limit_per_deployment, limits.rate_limit_per_project = 2, 0
    _, dep, _ = await publish(api_client)
    token = (await share_on(api_client, dep))["token"]
    for _ in range(2):
        assert (await say(api_client, token, "Ada")).status_code == 200
    refused = await say(api_client, token, "Ada")
    assert refused.status_code == 429


async def test_a_paused_conversation_waits_for_the_builders_approval(app, api_client):
    """A run that pauses for approval tells the visitor it waits; a visitor cannot approve it (anyone with the
    link could), the builders do."""
    from agentcanvas.testing import DemoResources

    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(HIGH_CASE["mocks"])
    wf = await new_triage(api_client)
    dep = (await api_client.post(f"/v1/workflows/{wf['id']}/publish", json={})).json()["deployment"]["id"]
    share = await share_on(api_client, dep)
    assert share["input"] == {"field": "ticket", "chat": False}
    evs = events((await say(api_client, share["token"], "Site down")).text)
    done = evs[-1]
    assert done["status"] == "interrupted" and done["interrupts"] == [] and "needs approval" in done["answer"]
    thread = evs[0]["threadId"]
    resume = f"/v1/share/{share['token']}/threads/{thread}/resume"
    assert (await api_client.post(resume, json={"visitor": BO, "approve": True})).status_code == 404
    for body in ({"approve": True}, {"text": "yes"}):
        refused = await api_client.post(resume, json={"visitor": ADA, **body})
        assert refused.status_code == 422 and "cannot be approved from a share link" in refused.text
    inbox = (await api_client.get("/v1/inbox")).json()
    assert [i for i in inbox if i["thread_id"] == thread]  # still waiting for a builder


async def test_a_visitor_answers_a_question(app, api_client):
    """An 'Ask a person' step is the visitor's to answer."""
    wf = await make_greeter(api_client)
    cur = (await api_client.get(f"/v1/workflows/{wf}")).json()
    ir = cur["ir"]
    ir["state"]["channels"].append({"name": "nick", "type": {"type": "string"}, "io": "out"})
    ir["nodes"].append({"id": "n_ask", "type": "core.human_input", "name": "ask",
                        "config": {"prompt": "What should I call you?", "output": "nick"}})  # fmt: skip
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
    done = evs[-1]
    assert done["status"] == "interrupted" and done["interrupts"][0]["kind"] == "input"
    assert done["interrupts"][0]["question"] == "What should I call you?"
    resume = f"/v1/share/{token}/threads/{evs[0]['threadId']}/resume"
    after = events((await api_client.post(resume, json={"visitor": ADA, "text": "Ace"})).text)
    assert after[-1]["type"] == "RUN_FINISHED" and after[-1]["status"] == "success", after
    again = await api_client.post(resume, json={"visitor": ADA, "text": "Ace"})
    assert again.status_code == 422  # nothing is waiting any more


async def test_visitors_see_only_the_answer_steps_text(app, api_client):
    """Text an earlier step writes (a classifier's label) never streams to a visitor; the final step's does,
    and nothing streams when the workflow has a safety block (the text before the check must not leak)."""
    from agentcanvas.api.v1.share import _answer_steps
    from agentcanvas.testing import DemoResources

    mocks = {"fast_model": ["low"], "smart_model": ["Use the reset link."]}
    app.state.resources_factory = lambda thread_id, wf: DemoResources().with_mocks(mocks)
    wf = await new_triage(api_client)
    dep = (await api_client.post(f"/v1/workflows/{wf['id']}/publish", json={})).json()["deployment"]["id"]
    token = (await share_on(api_client, dep))["token"]
    evs = events((await say(api_client, token, "How do I reset my password?")).text)
    streamed = "".join(e["delta"] for e in evs if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert streamed == "Use the reset link." == evs[-1]["answer"]  # the classifier's "low" stays inside

    triage = Workflow.model_validate(wf["ir"])
    assert _answer_steps(triage) == {"n_ag"}
    guarded = triage.model_dump(mode="json")
    guarded["nodes"].append({"id": "n_g", "type": "guard.pii", "name": "pii", "config": {}})
    assert _answer_steps(Workflow.model_validate(guarded)) == set()


async def test_one_visitor_cannot_use_up_the_deployment_limit(api_client, limits):  # noqa: F811
    """Share requests also count per client address on each link, so one visitor (whatever secrets they send)
    runs out before the deployment does."""
    limits.rate_limit_per_deployment, limits.rate_limit_per_project, limits.rate_limit_per_share_client = 10, 0, 2
    _, dep, key = await publish(api_client)
    token = (await share_on(api_client, dep))["token"]
    assert (await say(api_client, token, "Ada")).status_code == 200
    assert (await say(api_client, token, "Ada", visitor=BO)).status_code == 200
    refused = await say(api_client, token, "Ada", visitor="visitor-cy-0123456789ab")
    assert refused.status_code == 429 and "visitor" in refused.text
    run = await api_client.post(f"/v1/deployments/{dep}/runs/wait", json={"input": {"who": "Cy"}}, headers=bearer(key))
    assert run.status_code == 200  # the deployment itself still has room


async def test_concurrent_first_reads_of_a_share_create_it_once(app, api_client):
    """Two first GETs at once: the one that loses the insert race reads the other's row (no 500)."""
    import asyncio

    from agentcanvas.services.share import ShareService

    _, dep, _ = await publish(api_client)
    async with app.state.db.sessionmaker() as s1, app.state.db.sessionmaker() as s2:
        first = await ShareService(s1).ensure(dep)
        await s1.commit()
        late = ShareService(s2)

        async def missed(_dep_id: str) -> None:  # it looked before the other request's insert was committed
            return None

        late.get = missed  # type: ignore[method-assign]
        second = await late.ensure(dep)
        await s2.commit()
        assert second.token == first.token
    gets = await asyncio.gather(*(api_client.get(f"/v1/deployments/{dep}/share") for _ in range(4)))
    assert {g.status_code for g in gets} == {200} and len({g.json()["token"] for g in gets}) == 1


def test_input_shape_and_answer():
    chat = Workflow.model_validate({
        "id": "wf_c", "name": "Chat", "nodes": [],
        "state": {"channels": [{"name": "messages", "type": {"type": "array"}, "reducer": "add_messages"}]},
    })  # fmt: skip
    shape = input_shape(chat, None)
    assert shape == InputShape(field="messages", chat=True)
    out = {
        "messages": [{"type": "human", "content": "hi"}, {"type": "ai", "content": [{"type": "text", "text": "yo"}]}]
    }
    assert answer_text(out, shape) == "yo"
    qa = Workflow.model_validate({
        "id": "wf_q", "name": "QA", "nodes": [],
        "state": {"channels": [{"name": "question"}, {"name": "answer"},
                               {"name": "sources", "type": {"type": "array"}}]},
    })  # fmt: skip
    shape = input_shape(qa, None)
    assert shape == InputShape(field="question")
    assert answer_text({"question": "q", "answer": "a", "sources": []}, shape) == "a"
    assert answer_text({"question": "q", "n": 1}, shape) == '{\n  "n": 1\n}'
    triage = Workflow.model_validate({
        "id": "wf_t", "name": "T", "nodes": [],
        "state": {"channels": [{"name": "messages", "type": {"type": "array"}, "reducer": "add_messages"},
                               {"name": "ticket"}, {"name": "summary"}]},
    })  # fmt: skip
    shape = input_shape(triage, None)
    assert shape == InputShape(field="ticket")  # a question-like text input wins over the message list
    assert text_inputs(triage) == ["messages", "ticket", "summary"]
    assert input_shape(triage, "messages") == InputShape(field="messages", chat=True)
    reply = {"messages": [{"type": "ai", "content": "Sorry about that."}], "summary": "ESCALATED", "ticket": "x"}
    assert answer_text(reply, shape) == "Sorry about that."


async def test_a_link_says_which_sites_may_frame_its_chat_page(api_client):
    """Review follow-up: the chat page was framable by any site. A builder can list the sites; none listed keeps the
    old behaviour; a link that is unknown or off allows only the app itself."""
    _, dep, _ = await publish(api_client)
    off = (await api_client.put(f"/v1/deployments/{dep}/share", json={"enabled": False})).json()  # a link, off
    token = off["token"]
    assert off["frame_origins"] == []

    async def ancestors(tok: str) -> str:
        r = await api_client.get(f"/v1/share/{tok}/frame")
        assert r.status_code == 204
        return r.headers["x-frame-ancestors"]

    assert await ancestors(token) == "'self'"  # off
    assert await ancestors("sh_unknown") == "'self'"

    await share_on(api_client, dep)
    assert await ancestors(token) == "*"  # nothing listed: any site, as before

    share = await share_on(
        api_client, dep, frame_origins=["https://Example.com/", "https://*.shop.example", "https://example.com"]
    )
    assert share["frame_origins"] == ["https://example.com", "https://*.shop.example"]
    assert await ancestors(token) == "'self' https://example.com https://*.shop.example"

    for bad in [
        "example.com",
        "https://example.com/path",
        "https://a b.com",
        "*",
        "javascript:alert(1)",
        "https://x;y",
    ]:
        r = await api_client.put(f"/v1/deployments/{dep}/share", json={"frame_origins": [bad]})
        assert r.status_code == 422, bad
    assert (await api_client.put(f"/v1/deployments/{dep}/share", json={"frame_origins": []})).json()[
        "frame_origins"
    ] == []
    assert await ancestors(token) == "*"


async def test_the_chat_page_always_tells_visitors_they_are_chatting_with_an_ai(api_client):
    """EU AI Act Art. 50(1): the notice is on every link by default; a builder may reword it but never remove it, and
    the audit log keeps the wording visitors were shown."""
    _, dep, _ = await publish(api_client)
    share = await share_on(api_client, dep)
    assert share["ai_notice"] == DEFAULT_AI_NOTICE
    assert (await api_client.get(f"/v1/share/{share['token']}")).json()["ai_notice"] == DEFAULT_AI_NOTICE

    own = "  Sie chatten mit einem KI-Assistenten.  "
    assert (await share_on(api_client, dep, ai_notice=own))["ai_notice"] == own.strip()
    assert (await api_client.get(f"/v1/share/{share['token']}")).json()["ai_notice"] == own.strip()
    assert (await share_on(api_client, dep, title="Hi"))["ai_notice"] == own.strip()  # left out: kept
    entries = (await api_client.get("/v1/org/audit", params={"action": "update_share"})).json()
    assert [e["detail"].get("ai_notice") for e in entries][:2] == [None, own.strip()]  # newest first

    for removed in ["", "   ", "x" * 201]:
        r = await api_client.put(f"/v1/deployments/{dep}/share", json={"ai_notice": removed})
        assert r.status_code == 422, removed
    assert (await api_client.get(f"/v1/deployments/{dep}/share")).json()["ai_notice"] == own.strip()
    assert (await share_on(api_client, dep, ai_notice=DEFAULT_AI_NOTICE))["ai_notice"] == DEFAULT_AI_NOTICE
