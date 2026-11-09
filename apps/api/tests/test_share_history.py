"""G-15: a share link visitor's conversation comes back after a reload."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import update
from test_files import PNG
from test_publish import make_greeter
from test_share import ADA, BO, events, publish, say, share_on
from test_share_files import ask, attach, shared_looker

from agentcanvas.db.models import Thread


def history(api, token: str, visitor: str = ADA):
    return api.get(f"/v1/share/{token}/conversation", headers={"x-visitor": visitor})


async def test_a_reload_shows_the_conversation_again_in_the_same_thread(api_client):
    _, dep, _ = await publish(api_client)
    token = (await share_on(api_client, dep))["token"]
    empty = (await history(api_client, token)).json()
    assert empty == {"thread_id": None, "busy": False, "messages": []}  # a new visitor

    first = events((await say(api_client, token, "Ada")).text)
    thread = first[0]["threadId"]
    await say(api_client, token, "Bo", thread_id=thread)

    saved = (await history(api_client, token)).json()
    assert saved["thread_id"] == thread and saved["busy"] is False
    assert [(m["role"], m["text"]) for m in saved["messages"]] == [
        ("user", "Ada"), ("assistant", "Hello Ada"), ("user", "Bo"), ("assistant", "Hello Bo"),
    ]  # fmt: skip
    assert saved["messages"][1]["run_id"] == first[-1]["runId"]  # the answers can be rated again
    # and it carries on: the next message continues the saved thread
    again = events((await say(api_client, token, "Cy", thread_id=saved["thread_id"])).text)
    assert again[0]["threadId"] == thread and again[-1]["answer"] == "Hello Cy"
    assert len((await history(api_client, token)).json()["messages"]) == 6


async def test_visitors_see_only_their_own_conversation(api_client):
    _, dep, _ = await publish(api_client)
    token = (await share_on(api_client, dep))["token"]
    await say(api_client, token, "Ada")
    assert (await history(api_client, token, BO)).json()["messages"] == []
    assert (await history(api_client, token, ADA)).json()["messages"][0]["text"] == "Ada"
    # another link of another deployment shows nothing of it either
    _, dep2, _ = await publish(api_client)
    token2 = (await share_on(api_client, dep2))["token"]
    assert (await history(api_client, token2, ADA)).json()["messages"] == []


async def test_the_secret_travels_in_a_header_and_is_checked(api_client):
    _, dep, _ = await publish(api_client)
    token = (await share_on(api_client, dep))["token"]
    assert (await api_client.get(f"/v1/share/{token}/conversation")).status_code == 422  # no secret
    assert (await history(api_client, token, "short")).status_code == 422
    assert (await api_client.get(f"/v1/share/{token}/conversation?visitor={ADA}")).status_code == 422  # not in the URL
    assert (await history(api_client, "sh_unknown")).status_code == 404


async def test_new_conversation_closes_the_saved_one_for_good(api_client):
    _, dep, _ = await publish(api_client)
    token = (await share_on(api_client, dep))["token"]
    await say(api_client, token, "Ada")
    other = await say(api_client, token, "Bo", visitor=BO)
    assert other.status_code == 200
    gone = await api_client.delete(f"/v1/share/{token}/conversation", headers={"x-visitor": ADA})
    assert gone.status_code == 204
    assert (await history(api_client, token)).json()["messages"] == []
    assert (await history(api_client, token, BO)).json()["messages"][0]["text"] == "Bo"  # Bo's stays
    fresh = events((await say(api_client, token, "Cy")).text)  # the next message starts a new conversation
    assert (await history(api_client, token)).json()["thread_id"] == fresh[0]["threadId"]


async def test_regenerating_the_link_ends_saved_conversations(api_client):
    _, dep, _ = await publish(api_client)
    token = (await share_on(api_client, dep))["token"]
    await say(api_client, token, "Ada")
    fresh = (await api_client.post(f"/v1/deployments/{dep}/share/regenerate")).json()["token"]
    assert (await history(api_client, token)).status_code == 404
    assert (await history(api_client, fresh)).json()["messages"] == []


async def test_a_conversation_still_being_answered_says_so(app, api_client):
    _, dep, _ = await publish(api_client)
    token = (await share_on(api_client, dep))["token"]
    thread = events((await say(api_client, token, "Ada")).text)[0]["threadId"]
    async with app.state.db.sessionmaker() as s:
        await s.execute(update(Thread).where(Thread.id == thread).values(status="running"))
        await s.commit()
    assert (await history(api_client, token)).json()["busy"] is True


async def test_saved_conversations_follow_conversation_retention(app, api_client):
    _, dep, _ = await publish(api_client)
    token = (await share_on(api_client, dep))["token"]
    thread = events((await say(api_client, token, "Ada")).text)[0]["threadId"]
    async with app.state.db.sessionmaker() as s:
        old = datetime.now(UTC) - timedelta(days=91)
        await s.execute(update(Thread).where(Thread.id == thread).values(updated_at=old))
        await s.commit()
    assert await app.state.retention.tick(datetime.now(UTC)) == [thread]
    assert (await history(api_client, token)).json() == {"thread_id": None, "busy": False, "messages": []}


async def test_attached_files_are_listed_by_name_and_the_page_info_names_the_allowed_sites(api_client):
    token = await shared_looker(api_client)
    ref = (await attach(api_client, token, "receipt.png", PNG)).json()
    await ask(api_client, token, "what is this?", [ref["id"]])
    [user, answer] = (await history(api_client, token)).json()["messages"]
    assert user["attached"] == ["receipt.png"] and user["text"] == "what is this?"
    assert answer["role"] == "assistant" and answer["text"].startswith("what is this? / receipt.png")
    assert (await api_client.get(f"/v1/share/{token}")).json()["frame_origins"] == []


async def make_asker(api) -> str:
    """The greeter, asking 'What should I call you?' first."""
    wf = await make_greeter(api)
    cur = (await api.get(f"/v1/workflows/{wf}")).json()
    ir = cur["ir"]
    ir["state"]["channels"].append({"name": "nick", "type": {"type": "string"}, "io": "out"})
    ir["nodes"].append({"id": "n_ask", "type": "core.human_input", "name": "ask",
                        "config": {"prompt": "What should I call you?", "output": "nick"}})  # fmt: skip
    for e in ir["edges"]:
        if e["kind"] == "flow" and e["source"]["node"] == "START":
            first, e["target"]["node"] = e["target"]["node"], "n_ask"
    ir["edges"].append({"id": "e_ask", "kind": "flow", "source": {"node": "n_ask"}, "target": {"node": first}})
    r = await api.put(f"/v1/workflows/{wf}", json={"ir": ir}, headers={"If-Match": str(cur["version"])})
    assert r.status_code == 200, r.text
    return wf


async def test_a_question_the_assistant_asked_is_still_open_after_a_reload(api_client):
    wf = await make_asker(api_client)
    dep = (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()["deployment"]["id"]
    token = (await share_on(api_client, dep, frame_origins=["https://shop.example.com"]))["token"]
    assert (await api_client.get(f"/v1/share/{token}")).json()["frame_origins"] == ["https://shop.example.com"]
    asked = events((await say(api_client, token, "Ada")).text)
    question = asked[-1]["interrupts"][0]
    saved = (await history(api_client, token)).json()
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    assert (
        saved["messages"][1]["question"]["id"] == question["id"]
        and saved["messages"][1]["text"] == question["question"]
    )

    resume = f"/v1/share/{token}/threads/{saved['thread_id']}/resume"
    await api_client.post(resume, json={"visitor": ADA, "text": "Ace"})
    after = (await history(api_client, token)).json()["messages"]
    assert [m["role"] for m in after[2:]] == ["user", "assistant"] and after[2]["text"] == "Ace"
    assert "Hello Ada" in after[3]["text"]
    assert after[3]["question"] is None  # answered: the page asks nothing more
