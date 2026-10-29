"""G-14 Git round trip: connect a workflow to a branch, pull requests with the IR diff, CI, and import back.

GitHub is a stateful fake behind httpx's mock transport (branches, trees, commits, file contents, pull requests): no
request leaves the process, and the token is checked to never come back out.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

import httpx
import pytest
import yaml
from test_publish import greeter, make_greeter, set_greeting

from agentcanvas.db.models import GitLink
from agentcanvas.ir.diff import diff_workflows, markdown
from agentcanvas.ir.models import Workflow
from agentcanvas.services import gitsync

TOKEN = "ghp_test_token_value_123"
REPO = "acme/agents"


class FakeGitHub:
    """GitHub's REST API for one repository with a `main` branch holding a README."""

    def __init__(self, status: int | None = None):
        self.blobs: dict[str, bytes] = {}
        self.trees: dict[str, dict[str, str]] = {}  # tree sha -> {path: blob sha}
        self.commits: dict[str, dict] = {}
        self.branches: dict[str, str] = {}
        self.pulls: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []
        self.auth: set[str] = set()
        self.status = status
        self.commit({"README.md": b"# agents\n"}, [], "init", "main")

    def blob(self, data: bytes) -> str:
        sha = "b" + hashlib.sha1(data).hexdigest()[:12]
        self.blobs[sha] = data
        return sha

    def tree(self, files: dict[str, str]) -> str:
        sha = "t" + hashlib.sha1(json.dumps(sorted(files.items())).encode()).hexdigest()[:12]
        self.trees[sha] = dict(files)
        return sha

    def commit(self, files: dict[str, bytes], parents: list[str], message: str, branch: str) -> str:
        """A commit that holds exactly `files` (a person's edit on GitHub), moving `branch` to it."""
        tree = self.tree({p: self.blob(d) for p, d in files.items()})
        sha = f"c{len(self.commits)}"
        self.commits[sha] = {"tree": tree, "parents": parents, "message": message}
        self.branches[branch] = sha
        return sha

    def files(self, branch: str) -> dict[str, bytes]:
        return {p: self.blobs[s] for p, s in self.trees[self.commits[self.branches[branch]]["tree"]].items()}

    def edit(self, branch: str, path: str, data: bytes | None) -> None:
        files = self.files(branch)
        if data is None:
            files.pop(path)
        else:
            files[path] = data
        self.commit(files, [self.branches[branch]], f"edit {path}", branch)

    def merge(self, branch: str) -> None:
        """The pull request is merged: main takes the branch, the pull request closes."""
        self.branches["main"] = self.branches[branch]
        for pr in self.pulls:
            pr["state"] = "closed"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        self.calls.append((request.method, path))
        self.auth.add(request.headers["Authorization"])
        if self.status:
            return httpx.Response(self.status, json={"message": "Bad credentials"})
        body = json.loads(request.content) if request.content else {}
        q = dict(request.url.params)
        m = request.method
        if m == "GET" and path == "":
            return httpx.Response(200, json={"default_branch": "main"})
        if m == "GET" and path.startswith("/git/ref/heads/"):
            sha = self.branches.get(path.removeprefix("/git/ref/heads/"))
            return httpx.Response(200, json={"object": {"sha": sha}}) if sha else httpx.Response(404, json={})
        if m == "GET" and path.startswith("/git/commits/"):
            return httpx.Response(200, json={"tree": {"sha": self.commits[path.rsplit("/", 1)[1]]["tree"]}})
        if m == "GET" and path.startswith("/git/trees/"):
            files = self.trees[path.rsplit("/", 1)[1]]
            listing = [{"path": p, "type": "blob", "sha": s} for p, s in files.items()]
            return httpx.Response(200, json={"truncated": False, "tree": listing})
        if path == "/git/blobs":
            return httpx.Response(201, json={"sha": self.blob(base64.b64decode(body["content"]))})
        if path == "/git/trees":
            files = dict(self.trees[body["base_tree"]])
            for e in body["tree"]:
                if e["sha"] is None:
                    files.pop(e["path"], None)
                else:
                    files[e["path"]] = e["sha"]
            return httpx.Response(201, json={"sha": self.tree(files)})
        if path == "/git/commits":
            sha = f"c{len(self.commits)}"
            self.commits[sha] = body
            return httpx.Response(201, json={"sha": sha})
        if m == "POST" and path == "/git/refs":
            self.branches[body["ref"].removeprefix("refs/heads/")] = body["sha"]
            return httpx.Response(201, json={})
        if m == "PATCH" and path.startswith("/git/refs/heads/"):
            self.branches[path.removeprefix("/git/refs/heads/")] = body["sha"]
            return httpx.Response(200, json={})
        if m == "GET" and path.startswith("/contents/"):
            assert "raw" in request.headers["Accept"]
            branch = self.branches.get(q["ref"])
            files = self.trees[self.commits[branch]["tree"]] if branch else {}
            sha = files.get(path.removeprefix("/contents/"))
            return httpx.Response(200, content=self.blobs[sha]) if sha else httpx.Response(404, json={})
        if m == "GET" and path == "/pulls":
            head = q["head"].split(":", 1)[1]
            found = [p for p in self.pulls if p["head"] == head and p["base"] == q["base"] and p["state"] == "open"]
            return httpx.Response(200, json=found)
        if m == "POST" and path == "/pulls":
            same = (
                self.commits[self.branches[body["head"]]]["tree"] == self.commits[self.branches[body["base"]]]["tree"]
            )
            if same:
                return httpx.Response(422, json={"message": f"No commits between {body['base']} and {body['head']}"})
            pr = {**body, "number": len(self.pulls) + 1, "state": "open"}
            pr["html_url"] = f"https://github.com/{REPO}/pull/{pr['number']}"
            self.pulls.append(pr)
            return httpx.Response(201, json=pr)
        if m == "PATCH" and path.startswith("/pulls/"):
            pr = self.pulls[int(path.rsplit("/", 1)[1]) - 1]
            pr.update(body)
            return httpx.Response(200, json=pr)
        return httpx.Response(500, json={"message": f"unexpected {m} {path}"})


@pytest.fixture
def gh(app):
    fake = FakeGitHub()
    app.state.github_transport = httpx.MockTransport(fake.handler)
    yield fake
    del app.state.github_transport


async def connect(api, wf: str, **kw) -> dict:
    body = {"repo": REPO, "branch": "agentcanvas/greeter", "folder": "agents/greeter", **kw}
    assert (await api.put(f"/v1/workflows/{wf}/git", json=body)).status_code == 200
    return (await api.get(f"/v1/workflows/{wf}/git")).json()


async def ready(api) -> str:
    wf = await make_greeter(api)
    assert (await api.put("/v1/projects/prj_default/secrets/GITHUB_TOKEN", json={"value": TOKEN})).status_code == 204
    await connect(api, wf)
    return wf


async def save_version(api, wf: str, message: str = "") -> dict:
    r = await api.post(f"/v1/workflows/{wf}/versions", json={"message": message})
    assert r.status_code == 201, r.text
    return r.json()


# ------------------------------------------------------------------ connect ----
async def test_a_workflow_is_connected_read_and_disconnected(api_client):
    wf = await make_greeter(api_client)
    assert (await api_client.get(f"/v1/workflows/{wf}/git")).json() is None
    link = await connect(api_client, wf, folder="/agents/greeter/")
    assert link["folder"] == "agents/greeter" and link["auto"] == "publish" and link["token_secret"] == "GITHUB_TOKEN"
    assert link["pr_url"] is None and link["last_synced_at"] is None
    for bad in ({"repo": "acme"}, {"branch": "a..b"}, {"folder": "../up"}, {"auto": "always"}, {"target": "mars"}):
        body = {"repo": REPO, "branch": "x", **bad}
        assert (await api_client.put(f"/v1/workflows/{wf}/git", json=body)).status_code == 422, bad
    assert (await api_client.delete(f"/v1/workflows/{wf}/git")).status_code == 204
    assert (await api_client.get(f"/v1/workflows/{wf}/git")).json() is None
    r = await api_client.post(f"/v1/workflows/{wf}/git/push", json={})
    assert r.status_code == 422 and "not connected" in r.text


async def test_pushing_needs_the_token_secret_and_a_branch_that_is_not_the_default(api_client, gh):
    wf = await make_greeter(api_client)
    await connect(api_client, wf)
    r = await api_client.post(f"/v1/workflows/{wf}/git/push", json={})
    assert r.status_code == 422 and "GITHUB_TOKEN" in r.text and not gh.calls
    await api_client.put("/v1/projects/prj_default/secrets/GITHUB_TOKEN", json={"value": TOKEN})
    await connect(api_client, wf, branch="main")
    r = await api_client.post(f"/v1/workflows/{wf}/git/push", json={})
    assert r.status_code == 422 and "default branch" in r.text and gh.branches["main"] == "c0"
    assert TOKEN not in r.text


# ------------------------------------------------------------------ out ----
async def test_a_push_commits_the_export_with_ci_and_opens_a_pull_request(api_client, gh):
    wf = await ready(api_client)
    await save_version(api_client, wf, "first cut")
    r = await api_client.post(f"/v1/workflows/{wf}/git/push", json={"version": "1.0.0"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["created_branch"] is True and out["changed"] is True and out["pr_created"] is True
    assert out["label"] == "v1.0.0" and out["base"] == "main" and out["pr_url"] == f"https://github.com/{REPO}/pull/1"
    assert gh.auth == {f"Bearer {TOKEN}"} and TOKEN not in r.text
    files = gh.files("agentcanvas/greeter")
    assert files["README.md"] == b"# agents\n" and gh.branches["main"] == "c0"  # built on main, which is untouched
    ir = Workflow.model_validate_json(files["agents/greeter/workflow.ir.json"])
    assert ir.id == wf and ir.nodes[0].name == "greet"
    assert "agents/greeter/pyproject.toml" in files and "agents/greeter/tests/test_smoke.py" in files  # the export
    # the CI file: GitHub reads workflows only from the repository's .github/workflows
    ci = yaml.safe_load(files[gitsync.ci_path(wf)])
    assert ci[True]["pull_request"]["branches"] == ["main"]  # YAML 1.1: the key `on` loads as True
    job = ci["jobs"]["export-tests"]
    assert job["defaults"]["run"]["working-directory"] == "agents/greeter"
    assert [s.get("run") for s in job["steps"] if "run" in s] == ["uv sync", "uv run pytest -q"]
    pr = gh.pulls[0]
    assert (
        pr["head"] == "agentcanvas/greeter"
        and pr["base"] == "main"
        and pr["title"] == "Greeter: v1.0.0 from AgentCanvas"
    )
    assert "whole workflow is new" in pr["body"] and "> first cut" in pr["body"] and TOKEN not in pr["body"]
    assert "uv run pytest" in pr["body"]
    link = (await api_client.get(f"/v1/workflows/{wf}/git")).json()
    assert link["pr_url"] == out["pr_url"] and link["last_version"] == "v1.0.0" and link["last_error"] is None


async def test_the_pull_request_description_is_the_diff_against_the_base_branch(api_client, gh):
    wf = await ready(api_client)
    await save_version(api_client, wf)
    await api_client.post(f"/v1/workflows/{wf}/git/push", json={"version": "1.0.0"})
    gh.merge("agentcanvas/greeter")  # v1.0.0 is on main now
    await set_greeting(api_client, wf, "Howdy")
    await save_version(api_client, wf, "friendlier")
    out = (await api_client.post(f"/v1/workflows/{wf}/git/push", json={"version": "1.1.0"})).json()
    assert out["pr_created"] is True and out["pr_number"] == 2
    body = gh.pulls[1]["body"]
    assert "1 step changed" in body and "**Changed** `greet` (`core.set`)" in body
    assert "| `config.values.out` | `Hello {{ state.who }}` | `Howdy {{ state.who }}` |" in body
    assert "whole workflow is new" not in body


async def test_pushing_again_updates_the_open_pull_request_and_unchanged_makes_no_commit(api_client, gh):
    wf = await ready(api_client)
    await save_version(api_client, wf)
    await api_client.post(f"/v1/workflows/{wf}/git/push", json={"version": "1.0.0"})
    head = gh.branches["agentcanvas/greeter"]
    again = (await api_client.post(f"/v1/workflows/{wf}/git/push", json={"version": "1.0.0"})).json()
    assert again["changed"] is False and again["pr_created"] is False and again["pr_number"] == 1
    assert gh.branches["agentcanvas/greeter"] == head and len(gh.pulls) == 1  # no empty commit, no second PR
    assert ("PATCH", "/pulls/1") in gh.calls
    await set_greeting(api_client, wf, "Hey")
    await save_version(api_client, wf)
    newer = (await api_client.post(f"/v1/workflows/{wf}/git/push", json={"version": "1.1.0"})).json()
    assert newer["changed"] is True and newer["pr_number"] == 1 and len(gh.pulls) == 1
    assert gh.commits[gh.branches["agentcanvas/greeter"]]["parents"] == [head]
    assert gh.pulls[0]["title"] == "Greeter: v1.1.0 from AgentCanvas"  # the same pull request, brought up to date
    assert b"Hey {{ state.who }}" in gh.files("agentcanvas/greeter")["agents/greeter/workflow.ir.json"]


async def test_files_the_export_no_longer_has_are_removed_from_the_folder(api_client, gh):
    wf = await ready(api_client)
    await api_client.post(f"/v1/workflows/{wf}/git/push", json={})  # the draft
    # a stale file in the folder, and one elsewhere that is not ours; the branch's workflow file is as it was
    gh.edit("agentcanvas/greeter", "agents/greeter/old_module.py", b"print('stale')\n")
    gh.edit("agentcanvas/greeter", "docs/notes.md", b"mine\n")
    assert (await api_client.get(f"/v1/workflows/{wf}/git")).json()["last_version"] == "the draft"
    await api_client.post(f"/v1/workflows/{wf}/git/push", json={})
    after = gh.files("agentcanvas/greeter")
    assert "agents/greeter/old_module.py" not in after and after["docs/notes.md"] == b"mine\n"


async def test_nothing_to_review_when_the_branch_holds_what_main_has(api_client, gh):
    wf = await ready(api_client)
    await api_client.post(f"/v1/workflows/{wf}/git/push", json={})
    gh.merge("agentcanvas/greeter")  # main == the branch, the pull request is closed
    out = (await api_client.post(f"/v1/workflows/{wf}/git/push", json={})).json()
    assert out["changed"] is False and out["pr_number"] is None and "nothing to review" in out["note"]


async def test_a_push_refuses_to_overwrite_what_someone_changed_on_the_branch(api_client, gh):
    wf = await ready(api_client)
    await api_client.post(f"/v1/workflows/{wf}/git/push", json={})
    theirs = Workflow.model_validate_json(gh.files("agentcanvas/greeter")["agents/greeter/workflow.ir.json"])
    theirs.nodes[0].config["values"] = {"out": "Hi from GitHub {{ state.who }}"}
    gh.edit("agentcanvas/greeter", "agents/greeter/workflow.ir.json", gitsync.ir_bytes(theirs, wf))
    head = gh.branches["agentcanvas/greeter"]
    r = await api_client.post(f"/v1/workflows/{wf}/git/push", json={})
    assert (
        r.status_code == 409
        and "Import from branch" in r.json()["detail"]
        and gh.branches["agentcanvas/greeter"] == head
    )
    assert (await api_client.get(f"/v1/workflows/{wf}/git")).json()["last_error"].startswith("the branch")
    r = await api_client.post(f"/v1/workflows/{wf}/git/push", json={"overwrite": True})
    assert r.status_code == 200 and r.json()["changed"] is True
    link = (await api_client.get(f"/v1/workflows/{wf}/git")).json()
    assert link["last_error"] is None


async def test_github_refusals_are_explained_and_remembered(api_client, app):
    wf = await ready(api_client)
    app.state.github_transport = httpx.MockTransport(FakeGitHub(status=401).handler)
    try:
        r = await api_client.post(f"/v1/workflows/{wf}/git/push", json={})
    finally:
        del app.state.github_transport
    assert r.status_code == 422 and "did not accept the token" in r.text and TOKEN not in r.text
    assert "did not accept the token" in (await api_client.get(f"/v1/workflows/{wf}/git")).json()["last_error"]


# ------------------------------------------------------------------ back ----
async def pushed(api_client, gh) -> str:
    wf = await ready(api_client)
    await api_client.post(f"/v1/workflows/{wf}/git/push", json={})
    return wf


def edit_on_github(gh: FakeGitHub, wf: str, greeting: str) -> None:
    theirs = Workflow.model_validate_json(gh.files("agentcanvas/greeter")["agents/greeter/workflow.ir.json"])
    theirs.nodes[0].config["values"] = {"out": greeting + " {{ state.who }}"}
    gh.edit("agentcanvas/greeter", "agents/greeter/workflow.ir.json", gitsync.ir_bytes(theirs, wf))


async def greeting(api, wf: str) -> str:
    return (await api.get(f"/v1/workflows/{wf}")).json()["ir"]["nodes"][0]["config"]["values"]["out"]


async def test_importing_the_branch_updates_the_draft(api_client, gh):
    wf = await pushed(api_client, gh)
    r = await api_client.post(f"/v1/workflows/{wf}/git/pull", json={})
    assert r.json()["outcome"] == "same"  # the branch is what the draft is
    version = (await api_client.get(f"/v1/workflows/{wf}")).json()["version"]
    edit_on_github(gh, wf, "Hi")
    r = await api_client.post(f"/v1/workflows/{wf}/git/pull", json={})
    assert r.status_code == 200, r.text
    assert r.json()["outcome"] == "applied" and r.json()["version"] == version + 1
    assert "1 step changed" in r.json()["summary"]
    assert await greeting(api_client, wf) == "Hi {{ state.who }}"
    assert (await api_client.post(f"/v1/workflows/{wf}/git/pull", json={})).json()["outcome"] == "same"


async def test_an_import_is_a_conflict_when_the_draft_and_the_branch_both_moved(api_client, gh):
    wf = await pushed(api_client, gh)
    await set_greeting(api_client, wf, "Howdy")  # the draft moves on the canvas...
    edit_on_github(gh, wf, "Hi")  # ...and the branch moves on GitHub
    r = await api_client.post(f"/v1/workflows/{wf}/git/pull", json={})
    assert r.status_code == 409 and "both changed" in r.json()["detail"]
    assert await greeting(api_client, wf) == "Howdy {{ state.who }}"  # the draft is untouched
    r = await api_client.post(f"/v1/workflows/{wf}/git/pull", json={"overwrite": True})
    assert r.status_code == 200 and r.json()["outcome"] == "applied"
    assert await greeting(api_client, wf) == "Hi {{ state.who }}"


async def test_an_unchanged_branch_leaves_a_draft_that_moved_alone(api_client, gh):
    wf = await pushed(api_client, gh)
    await set_greeting(api_client, wf, "Howdy")
    r = await api_client.post(f"/v1/workflows/{wf}/git/pull", json={})
    assert r.json()["outcome"] == "unchanged" and await greeting(api_client, wf) == "Howdy {{ state.who }}"


async def test_importing_needs_a_workflow_file_on_the_branch(api_client, gh):
    wf = await pushed(api_client, gh)
    gh.edit("agentcanvas/greeter", "agents/greeter/workflow.ir.json", b"{not json")
    r = await api_client.post(f"/v1/workflows/{wf}/git/pull", json={})
    assert r.status_code == 422 and "not a valid AgentCanvas workflow" in r.json()["detail"]
    gh.edit("agentcanvas/greeter", "agents/greeter/workflow.ir.json", None)
    r = await api_client.post(f"/v1/workflows/{wf}/git/pull", json={})
    assert r.status_code == 422 and "is not on the branch" in r.json()["detail"]
    assert await greeting(api_client, wf) == "Hello {{ state.who }}"


# ------------------------------------------------------------------ on publish ----
async def test_a_publish_opens_the_pull_request_and_a_failed_sync_does_not_stop_it(api_client, gh, app):
    wf = await ready(api_client)
    pub = (await api_client.post(f"/v1/workflows/{wf}/publish", json={"message": "go live"})).json()
    assert pub["deployment"]["semver"] == "1.0.0" and pub["git"]["pr_url"] == f"https://github.com/{REPO}/pull/1"
    assert gh.pulls[0]["title"] == "Greeter: v1.0.0 from AgentCanvas" and "> go live" in gh.pulls[0]["body"]
    await set_greeting(api_client, wf, "Howdy")
    app.state.github_transport = httpx.MockTransport(FakeGitHub(status=401).handler)
    r = await api_client.post(f"/v1/workflows/{wf}/publish", json={})
    assert r.status_code == 200 and r.json()["deployment"]["semver"] == "1.1.0"  # the publish stands
    assert "did not accept the token" in r.json()["git"]["error"] and TOKEN not in r.text


async def test_auto_decides_what_opens_a_pull_request(api_client, gh):
    wf = await ready(api_client)  # auto: publish
    await save_version(api_client, wf)
    assert not gh.calls  # a saved version alone does nothing
    await connect(api_client, wf, auto="off")
    assert (await api_client.post(f"/v1/workflows/{wf}/publish", json={})).json()["git"] is None and not gh.calls
    await connect(api_client, wf, auto="version")
    await set_greeting(api_client, wf, "Hey")
    await save_version(api_client, wf, "second")
    assert gh.pulls and gh.pulls[0]["title"] == "Greeter: v1.1.0 from AgentCanvas"


# ------------------------------------------------------------------ pieces ----
def test_what_an_import_does_follows_who_moved():
    assert gitsync.pull_decision("a", "a", "z") == "same"
    assert gitsync.pull_decision("b", "a", "a") == "unchanged"  # only the draft moved
    assert gitsync.pull_decision("a", "b", "a") == "apply"  # only the branch moved
    assert gitsync.pull_decision("c", "b", "a") == "conflict"
    assert gitsync.pull_decision("c", "b", None) == "conflict"  # never synced and they differ


def _wf(**values: str) -> Workflow:
    ir = greeter("wf_x", "Hello")
    ir["nodes"][0]["config"]["values"] = {"out": "Hello", **values}
    return Workflow.model_validate(ir)


def test_the_diff_as_markdown_keeps_user_text_in_code_spans():
    hostile = "@octocat |x| `y`\n# not a heading\n[click](http://evil)"
    before, after = _wf(), _wf(out=hostile)
    after.nodes[0].name = "greet @everyone"
    text = markdown(diff_workflows(before, after), before, after)
    assert "**Changed** `greet @everyone` (`core.set`)" in text
    assert "| `config.values.out` | `Hello` | `@octocat \\|x\\| 'y' # not a heading [click](http://evil)` |" in text
    assert "\n# not a heading" not in text and "\n@octocat" not in text  # nothing starts a line or leaves a span
    assert markdown(diff_workflows(before, before), before, before) == "No changes to the workflow.\n"


def test_a_long_diff_is_cut_and_the_description_fits_github():
    before = _wf()
    after = Workflow.model_validate({**greeter("wf_x", "Hello"), "nodes": [
        {"id": f"n_{i}", "type": "core.set", "name": f"step_{i}", "config": {"values": {"out": "x" * 500}}}
        for i in range(60)]})  # fmt: skip
    text = markdown(diff_workflows(before, after), before, after)
    assert "and more added or removed" in text and text.count("- added") == 20 and "x" * 100 not in text
    link = GitLink(workflow_id="wf_x", repo=REPO, branch="b", folder="")
    body = gitsync.pr_body(link, "Big", "v2.0.0", "m" * 100_000, before, after)
    assert len(body) <= gitsync.MAX_BODY + 100


def test_the_ci_file_is_valid_yaml_for_any_folder():
    for folder in ("", "agents/my agent", "a-b/c.d"):
        link = GitLink(workflow_id="wf_x", repo=REPO, branch="feat/a b", folder=folder)
        ci = yaml.safe_load(gitsync.ci_file(link, "main", 'Name: "quoted"\n# hash'))
        assert ci["jobs"]["export-tests"]["defaults"]["run"]["working-directory"] == (folder or ".")
        assert ci[True]["push"]["branches"] == ["feat/a b"]
        assert ci[True]["pull_request"]["paths"][0] == (f"{folder}/**" if folder else "**")
