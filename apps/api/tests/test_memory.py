"""Sprint 6 long-term memory: Recall / Remember / Forget nodes, memory tools and the memory inspector API."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from test_catalog import MESSAGES, cfg, ch, compiled, errors, workflow

from agentcanvas.adapters.langchain.codegen import generate
from agentcanvas.adapters.langchain.interpreter import build_graph
from agentcanvas.adapters.langchain.nodes import Resources
from agentcanvas.testing import scripted, tool_call

USER = [{"name": "user_id", "type": {"type": "string"}, "source": "user"}]
SPACE = {"space": "prefs", "scope": "user"}


def remember_wf():
    return workflow(
        channels=[ch("topic"), ch("fact")],
        nodes=[("save", "core.memory_remember", {**SPACE, "key": "about:{{ state.topic }}", "value": "fact"})],
        flow=[("START", "save"), ("save", "END")],
        context=USER,
    )


def recall_wf(query: str = "{{ state.question }}"):
    return workflow(
        channels=[ch("question"), ch("recalled", "array")],
        nodes=[("look", "core.memory_recall", {**SPACE, "query": query, "top_k": 3, "output": "recalled"})],
        flow=[("START", "look"), ("look", "END")],
        context=USER,
    )


def forget_wf():
    return workflow(
        channels=[ch("topic")],
        nodes=[("drop", "core.memory_forget", {**SPACE, "key": "about:{{ state.topic }}"})],
        flow=[("START", "drop"), ("drop", "END")],
        context=USER,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["interpreted", "generated"])
async def test_remembered_on_one_thread_recalled_on_another_for_the_same_user_only(tmp_path, mode):
    """M3-T14 AC: the namespace includes the user id, so memories never leak across users."""
    store = InMemoryStore()

    def graph(wf):
        if mode == "interpreted":
            return build_graph(wf, Resources(), checkpointer=InMemorySaver(), store=store)
        return compiled(wf, tmp_path).build_graph(Resources(), checkpointer=InMemorySaver(), store=store)

    save, look, drop = graph(remember_wf()), graph(recall_wf()), graph(forget_wf())
    await save.ainvoke({"topic": "drinks", "fact": "likes green tea"}, cfg("a"), context={"user_id": "u1"})
    await save.ainvoke({"topic": "music", "fact": "plays jazz piano"}, cfg("b"), context={"user_id": "u1"})

    mine = await look.ainvoke({"question": "what tea?"}, cfg("c"), context={"user_id": "u1"})
    assert [m["key"] for m in mine["recalled"]] == ["about:drinks"]  # keyword match without a semantic index
    assert mine["recalled"][0]["value"] == "likes green tea"
    theirs = await look.ainvoke({"question": "what tea?"}, cfg("d"), context={"user_id": "u2"})
    assert theirs["recalled"] == []

    await drop.ainvoke({"topic": "drinks"}, cfg("e"), context={"user_id": "u1"})
    after = await look.ainvoke({"question": ""}, cfg("f"), context={"user_id": "u1"})
    assert [m["key"] for m in after["recalled"]] == ["about:music"]  # empty query = most recent memories


@pytest.mark.asyncio
async def test_memory_tools_for_agents(tmp_path):
    wf = workflow(
        channels=[MESSAGES],
        resources=[("model", "model.chat", {"model": "fake:scripted"}), ("memory", "tool.memory", SPACE)],
        nodes=[("agent", "core.agent", {"approve_tools": ["save_memory"]})],
        flow=[("START", "agent"), ("agent", "END")],
        wiring=[("model", "agent", "model"), ("memory", "agent", "tools")],
        context=USER,
    )
    assert not errors(wf)  # E033 knows the tools a memory resource brings
    store = InMemoryStore()
    model = scripted(
        tool_call("save_memory", {"key": "coffee", "text": "takes it black"}),
        tool_call("search_memory", {"query": "coffee"}, "call_2"),
        "You take it black.",
    )
    from langgraph.types import Command

    g = build_graph(wf, Resources(overrides={"model": model}), checkpointer=InMemorySaver(), store=store)
    await g.ainvoke({"messages": [("user", "remember: black coffee")]}, cfg(), context={"user_id": "u1"})
    out = await g.ainvoke(Command(resume={"decisions": [{"type": "approve"}]}), cfg(), context={"user_id": "u1"})
    assert out["messages"][-1].content == "You take it black."
    assert "takes it black" in str([m.content for m in out["messages"] if m.type == "tool"][-1])
    assert (await store.aget(("prefs", "user", "u1"), "coffee")).value == {"text": "takes it black"}
    code = generate(wf).replace("\n", " ")
    assert "memory = ac.memory_tools(" in code and "tools=[*memory]" in code


def test_memory_rules():
    assert not errors(recall_wf())
    no_user = recall_wf()
    no_user.context.fields = []
    assert "E071" in errors(no_user)
    wf = recall_wf()
    wf.require_node("look").config["output"] = "missing"
    assert "E021" in errors(wf)


@pytest.mark.asyncio
async def test_memory_inspector_api(app, api_client):
    store = app.state.runner.adapter.store
    org = "org:org_default"  # runs keep each organisation's memories under its own root
    await store.aput((org, "prefs", "user", "usr_dev"), "about:drinks", {"value": "likes green tea"})
    await store.aput((org, "prefs", "user", "usr_dev"), "about:music", {"value": "jazz"})
    await store.aput((org, "prefs", "user", "usr_other"), "about:drinks", {"value": "coffee"})

    spaces = (await api_client.get("/v1/memory/namespaces", params={"prefix": "prefs"})).json()
    assert [s["namespace"] for s in spaces] == ["prefs/user/usr_dev", "prefs/user/usr_other"]
    ns = "prefs/user/usr_dev"
    found = (await api_client.get("/v1/memory/items", params={"namespace": ns, "q": "tea"})).json()
    assert [i["key"] for i in found] == ["about:drinks"]
    assert found[0]["namespace"] == ["prefs", "user", "usr_dev"]  # shown without the organisation root
    r = await api_client.delete("/v1/memory/items", params={"namespace": ns, "key": "about:drinks"})
    assert r.status_code == 204
    left = (await api_client.get("/v1/memory/items", params={"namespace": ns})).json()
    assert [i["key"] for i in left] == ["about:music"]
    erased = (await api_client.delete("/v1/memory/namespaces", params={"namespace": "prefs/user/usr_other"})).json()
    assert erased == {"deleted": 1}
    assert (await api_client.get("/v1/memory/items", params={"namespace": "prefs/user/usr_other"})).json() == []
    assert (await api_client.get("/v1/memory/items", params={"namespace": ""})).status_code == 422


@pytest.mark.asyncio
async def test_memory_inspector_stays_inside_the_organisation(app, api_client):
    from agentcanvas.api.deps import CurrentUser, current_user

    store = app.state.runner.adapter.store
    await store.aput(("org:org_default", "prefs", "user", "usr_dev"), "k", {"value": "mine"})
    await store.aput(("org:org_b", "prefs", "user", "usr_b"), "k", {"value": "theirs"})
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_b", org_id="org_b", role="admin")
    try:
        spaces = (await api_client.get("/v1/memory/namespaces")).json()
        assert [s["namespace"] for s in spaces] == ["prefs/user/usr_b"]
        assert (await api_client.get("/v1/memory/items", params={"namespace": "prefs/user/usr_dev"})).json() == []
        erased = await api_client.delete("/v1/memory/namespaces", params={"namespace": "prefs"})
        assert erased.json() == {"deleted": 1}
    finally:
        app.dependency_overrides.pop(current_user)
    assert (await store.aget(("org:org_default", "prefs", "user", "usr_dev"), "k")).value == {"value": "mine"}


@pytest.mark.asyncio
async def test_memory_inspector_lists_only_your_own_namespaces(app, api_client):
    from agentcanvas.api.deps import CurrentUser, current_user

    store = app.state.runner.adapter.store
    for who in ("usr_m", "usr_n"):
        await store.aput(("org:org_default", "prefs", "user", who), "k", {"value": who})
    app.dependency_overrides[current_user] = lambda: CurrentUser(id="usr_m", org_id="org_default", role="builder")
    try:
        for prefix in ("", "prefs"):  # no 403 without a user id in the prefix: the list is filtered instead
            r = await api_client.get("/v1/memory/namespaces", params={"prefix": prefix})
            assert r.status_code == 200 and [s["namespace"] for s in r.json()] == ["prefs/user/usr_m"], prefix
        r = await api_client.get("/v1/memory/items", params={"namespace": "prefs/user/usr_n"})
        assert r.status_code == 403  # reading another user's memories stays an admin's job
    finally:
        app.dependency_overrides.pop(current_user)


@pytest.mark.asyncio
async def test_memories_from_before_organisations_move_to_the_default_one(app):
    adapter = app.state.runner.adapter
    await adapter.store.adelete(*adapter.ADOPTED)  # startup already adopted the (empty) store: start over
    await adapter.store.aput(("prefs", "user", "usr_dev"), "old", {"value": "from v0.1"})
    await adapter.store.aput(("prefs", "user", "usr_dev", "deeper"), "older", {"value": "nested"})
    assert await adapter.memory_adopt("org_default") == 2
    assert (await adapter.store.aget(("org:org_default", "prefs", "user", "usr_dev"), "old")).value == {
        "value": "from v0.1"
    }
    assert await adapter.store.aget(("prefs", "user", "usr_dev"), "old") is None
    # it runs once: namespaces written later without an organisation root are not misfiled on the next start
    await adapter.store.aput(("exported",), "k", {"value": "an exported app sharing the store"})
    assert await adapter.memory_adopt("org_default") == 0
    assert await adapter.store.aget(("exported",), "k") is not None
