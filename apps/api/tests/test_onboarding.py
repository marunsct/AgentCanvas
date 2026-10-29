"""S31 onboarding: the playground (chat with a model, then make it a workflow) and Push to GitHub."""

from __future__ import annotations

import base64
import json

import httpx
from langchain_core.messages import AIMessage

from agentcanvas.adapters.langchain.testing import DemoResources, scripted
from agentcanvas.services.github import files_of

P = "/v1/projects/prj_default"
TOKEN = "ghp_test_token_value_123"


def _script(app, *replies: str, seen: list | None = None) -> None:
    def factory(thread_id, wf):
        model = scripted(*[AIMessage(content=r) for r in replies])
        if seen is not None:
            original = model.ainvoke

            async def spy(messages, *a, **kw):
                seen.append(messages)
                return await original(messages, *a, **kw)

            object.__setattr__(model, "ainvoke", spy)
        return DemoResources(overrides={"copilot": model})

    app.state.resources_factory = factory


async def _set_default_model(api_client) -> None:
    r = await api_client.put(f"{P}/setup", json={"model": "anthropic:claude-haiku-4-5"})
    assert r.status_code == 200, r.text


async def test_playground_needs_a_model(app, api_client):
    _script(app, "hi")
    r = await api_client.post(f"{P}/playground/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 422 and "Settings > Models" in r.text


async def test_playground_chat_uses_the_default_model_and_the_whole_conversation(app, api_client):
    await _set_default_model(api_client)
    seen: list = []
    _script(app, "Bonjour !", seen=seen)
    body = {
        "system_prompt": "Answer in French.",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Salut"},
            {"role": "user", "content": "how are you?"},
        ],
        "temperature": 0.2,
    }
    r = await api_client.post(f"{P}/playground/chat", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["reply"] == "Bonjour !" and r.json()["model"] == "anthropic:claude-haiku-4-5"
    sent = seen[0]
    assert [type(m).__name__ for m in sent] == ["SystemMessage", "HumanMessage", "AIMessage", "HumanMessage"]
    assert sent[0].content == "Answer in French." and sent[-1].content == "how are you?"
    other = await api_client.post(
        f"{P}/playground/chat", json={"model": "openai:gpt-5.5", "messages": [{"role": "user", "content": "x"}]}
    )
    assert other.status_code == 200 and other.json()["model"] == "openai:gpt-5.5"


async def test_playground_model_errors_are_readable(app, api_client):
    await _set_default_model(api_client)

    async def broken(*a, **kw):
        raise RuntimeError("rate limited")

    original = app.state.models.chat
    app.state.models.chat = broken
    try:
        r = await api_client.post(f"{P}/playground/chat", json={"messages": [{"role": "user", "content": "x"}]})
    finally:
        app.state.models.chat = original
    assert r.status_code == 422 and "RuntimeError: rate limited" in r.text


async def test_playground_becomes_a_workflow_that_runs(app, api_client):
    await _set_default_model(api_client)
    body = {"name": "French helper", "system_prompt": "Answer in French.", "temperature": 0.3}
    r = await api_client.post(f"{P}/playground/workflow", json=body)
    assert r.status_code == 201, r.text
    wf = r.json()
    assert not [d for d in wf["diagnostics"] if d["severity"] == "error"], wf["diagnostics"]
    (agent,) = wf["ir"]["nodes"]
    assert agent["type"] == "core.agent" and agent["config"] == {"system_prompt": "Answer in French."}
    assert wf["ir"]["resources"][0]["config"] == {"model": "anthropic:claude-haiku-4-5", "temperature": 0.3}
    demo = DemoResources()
    app.state.resources_factory = lambda thread_id, w: demo
    run = await api_client.post(
        f"/v1/workflows/{wf['id']}/runs/stream", json={"input": {"messages": [{"role": "user", "content": "hi"}]}}
    )
    events = [json.loads(ln[6:]) for ln in run.text.splitlines() if ln.startswith("data: ")]
    assert events[-1]["type"] == "RUN_FINISHED", events[-1]


async def test_playground_workflow_leaves_out_a_temperature_the_model_refuses(app, api_client):
    """W020: a model the catalog marks `sampling: false` gets no temperature in the workflow made from the chat."""
    await _set_default_model(api_client)
    body = {"model": "anthropic:claude-haiku-5-5", "temperature": 0.5}
    wf = (await api_client.post(f"{P}/playground/workflow", json=body)).json()
    assert wf["ir"]["resources"][0]["config"] == {"model": "anthropic:claude-haiku-5-5"}
    assert not [d for d in wf["diagnostics"] if d["code"] == "W020"], wf["diagnostics"]


# ------------------------------------------------------------------ Push to GitHub ----
class FakeGitHub:
    """GitHub's Git Data API for one repository with a `main` branch."""

    def __init__(self, branches: dict[str, str] | None = None, status: int | None = None):
        self.branches = branches if branches is not None else {"main": "c0"}
        self.blobs: dict[str, bytes] = {}
        self.trees: dict[str, dict] = {}
        self.commits: dict[str, dict] = {"c0": {"tree": "t0"}}
        self.calls: list[tuple[str, str]] = []
        self.auth: set[str] = set()
        self.status = status

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/repos/acme/agents")
        self.calls.append((request.method, path))
        self.auth.add(request.headers["Authorization"])
        if self.status:
            return httpx.Response(self.status, json={"message": "Bad credentials"})
        body = json.loads(request.content) if request.content else {}
        if request.method == "GET" and path == "":
            return httpx.Response(200, json={"default_branch": "main"})
        if request.method == "GET" and path.startswith("/git/ref/heads/"):
            sha = self.branches.get(path.removeprefix("/git/ref/heads/"))
            return httpx.Response(200, json={"object": {"sha": sha}}) if sha else httpx.Response(404, json={})
        if request.method == "GET" and path.startswith("/git/commits/"):
            return httpx.Response(200, json={"tree": {"sha": self.commits[path.rsplit("/", 1)[1]]["tree"]}})
        if path == "/git/blobs":
            sha = f"b{len(self.blobs)}"
            self.blobs[sha] = base64.b64decode(body["content"])
            return httpx.Response(201, json={"sha": sha})
        if path == "/git/trees":
            sha = f"t{len(self.trees) + 1}"
            self.trees[sha] = body
            return httpx.Response(201, json={"sha": sha})
        if path == "/git/commits":
            sha = f"c{len(self.commits)}"
            self.commits[sha] = body
            return httpx.Response(201, json={"sha": sha})
        if request.method == "POST" and path == "/git/refs":
            self.branches[body["ref"].removeprefix("refs/heads/")] = body["sha"]
            return httpx.Response(201, json={})
        if request.method == "PATCH" and path.startswith("/git/refs/heads/"):
            self.branches[path.removeprefix("/git/refs/heads/")] = body["sha"]
            return httpx.Response(200, json={})
        return httpx.Response(500, json={"message": f"unexpected {request.method} {path}"})


async def _workflow(api_client) -> str:
    r = await api_client.post(f"{P}/workflows", json={"name": "Triage", "template": "ticket_triage"})
    return r.json()["id"]


async def test_push_needs_the_token_secret(app, api_client):
    wf = await _workflow(api_client)
    r = await api_client.post(f"/v1/workflows/{wf}/github", json={"repo": "acme/agents", "branch": "main"})
    assert r.status_code == 422 and "GITHUB_TOKEN" in r.text
    for bad in ({"repo": "acme", "branch": "main"}, {"repo": "acme/agents", "branch": "a..b"},
                {"repo": "acme/agents", "branch": "main", "folder": "../up"}):  # fmt: skip
        assert (await api_client.post(f"/v1/workflows/{wf}/github", json=bad)).status_code == 422, bad


async def test_push_the_export_as_one_commit_on_a_new_branch(app, api_client):
    wf = await _workflow(api_client)
    assert (await api_client.put(f"{P}/secrets/GITHUB_TOKEN", json={"value": TOKEN})).status_code == 204
    gh = FakeGitHub()
    app.state.github_transport = httpx.MockTransport(gh.handler)
    try:
        body = {"repo": "acme/agents", "branch": "agentcanvas/triage", "folder": "agents/triage/"}
        r = await api_client.post(f"/v1/workflows/{wf}/github", json=body)
    finally:
        del app.state.github_transport
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["created_branch"] is True and out["branch"] == "agentcanvas/triage"
    assert out["url"] == "https://github.com/acme/agents/tree/agentcanvas/triage/agents/triage"
    assert gh.branches["agentcanvas/triage"] == out["commit"] and gh.branches["main"] == "c0"
    assert gh.auth == {f"Bearer {TOKEN}"} and TOKEN not in r.text
    commit = gh.commits[out["commit"]]
    assert commit["parents"] == ["c0"] and commit["message"].startswith("Triage: export of the draft")
    tree = gh.trees[commit["tree"]]
    assert tree["base_tree"] == "t0"
    paths = {e["path"] for e in tree["tree"]}
    assert out["files"] == len(paths) and all(p.startswith("agents/triage/") for p in paths)
    zipped = (await api_client.get(f"/v1/workflows/{wf}/export.zip")).content
    expected = files_of(zipped)
    assert paths == {f"agents/triage/{p}" for p in expected}
    readme = next(e["sha"] for e in tree["tree"] if e["path"].endswith("/README.md"))
    assert gh.blobs[readme] == expected["README.md"]


async def test_push_to_an_existing_branch_moves_it(app, api_client):
    wf = await _workflow(api_client)
    await api_client.put(f"{P}/secrets/GITHUB_TOKEN", json={"value": TOKEN})
    gh = FakeGitHub()
    app.state.github_transport = httpx.MockTransport(gh.handler)
    try:
        body = {"repo": "acme/agents", "branch": "main", "message": "Ship triage"}
        out = (await api_client.post(f"/v1/workflows/{wf}/github", json=body)).json()
    finally:
        del app.state.github_transport
    assert out["created_branch"] is False and gh.branches["main"] == out["commit"]
    assert ("PATCH", "/git/refs/heads/main") in gh.calls and ("GET", "") not in gh.calls
    assert gh.commits[out["commit"]]["message"] == "Ship triage"


async def test_push_explains_github_refusals(app, api_client):
    wf = await _workflow(api_client)
    await api_client.put(f"{P}/secrets/GITHUB_TOKEN", json={"value": TOKEN})
    app.state.github_transport = httpx.MockTransport(FakeGitHub(status=401).handler)
    try:
        r = await api_client.post(f"/v1/workflows/{wf}/github", json={"repo": "acme/agents", "branch": "main"})
    finally:
        del app.state.github_transport
    assert r.status_code == 422 and "did not accept the token" in r.text and TOKEN not in r.text


async def test_push_needs_the_builder_role(app, api_client):
    from test_roles import act_as

    from agentcanvas.api.deps import current_user

    wf = await _workflow(api_client)
    act_as(app, "viewer")
    try:
        r = await api_client.post(f"/v1/workflows/{wf}/github", json={"repo": "acme/agents", "branch": "main"})
        chat = await api_client.post(f"{P}/playground/chat", json={"messages": [{"role": "user", "content": "x"}]})
    finally:
        app.dependency_overrides.pop(current_user, None)
    assert r.status_code == 403 and chat.status_code == 403


async def test_playground_streams_the_answer(app, api_client):
    """POST .../chat/stream: the answer as delta events, then done with the whole reply; a failure is an event."""
    import json

    await _set_default_model(api_client)
    _script(app, "Bonjour tout le monde !")
    r = await api_client.post(f"{P}/playground/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "Bonjour tout le monde !", events
    assert events[-1] == {**events[-1], "type": "done", "reply": "Bonjour tout le monde !"}

    async def broken(*a, **kw):
        raise RuntimeError("rate limited")
        yield ""

    app.state.models.stream = broken
    r = await api_client.post(f"{P}/playground/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]})
    last = json.loads([line for line in r.text.splitlines() if line.startswith("data:")][-1][5:])
    assert last["type"] == "error" and "rate limited" in last["message"]
