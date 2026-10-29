"""Push to GitHub (S31): a workflow's export as one commit on a branch of a GitHub repository.

The export zip's files are written under a folder of the repository with GitHub's Git Data API (blobs, a tree on
top of the branch's tree, a commit, then the branch moved to it), so the push is a single commit that changes only
that folder. A branch that does not exist yet is created from the repository's default branch, ready for a pull
request. The token is a project secret (GITHUB_TOKEN by default): a fine-grained token with Contents read and write
on the repository is enough.
"""

from __future__ import annotations

import base64
import io
import re
import zipfile
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from agentcanvas.errors import InvalidInput

API = "https://api.github.com"
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH = re.compile(r"^(?!/)(?!.*\.\.)(?!.*//)[A-Za-z0-9._/-]+(?<!/)(?<!\.lock)$")
FOLDER = re.compile(r"^[A-Za-z0-9._ -]+(/[A-Za-z0-9._ -]+)*$")
MAX_FILES = 500


class GitHubError(InvalidInput):
    """GitHub refused the push (the message says what to fix)."""


@dataclass
class Pushed:
    commit: str
    url: str
    branch: str
    created_branch: bool
    files: int
    changed: bool = True  # False: `skip_unchanged` found nothing to commit


def files_of(zip_bytes: bytes) -> dict[str, bytes]:
    """The export's files by path, without the zip's single top-level folder."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        tops = {n.split("/", 1)[0] for n in names}
        strip = len(tops) == 1 and all("/" in n for n in names)
        return {(n.split("/", 1)[1] if strip else n): z.read(n) for n in names}


def check(repo: str, branch: str, folder: str) -> None:
    if not REPO.match(repo):
        raise InvalidInput("the repository is owner/name, e.g. acme/agents")
    if not BRANCH.match(branch):
        raise InvalidInput(f"'{branch}' is not a valid branch name")
    if folder and (not FOLDER.match(folder) or any(p in (".", "..", ".git") for p in folder.split("/"))):
        raise InvalidInput(f"'{folder}' is not a valid folder (letters, digits, . _ - and / between parts)")


class Client:
    """One repository's REST API for a token (`api` and `transport` are for tests: a mock transport, never GitHub)."""

    def __init__(self, repo: str, token: str, transport: httpx.AsyncBaseTransport | None = None, api: str = API):
        self.repo = repo
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "AgentCanvas"}  # fmt: skip
        self.http = httpx.AsyncClient(base_url=api, headers=headers, transport=transport, timeout=30)

    async def __aenter__(self) -> Client:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.http.aclose()

    async def call(
        self, method: str, path: str, json: Any = None, ok: tuple[int, ...] = (200, 201), missing: bool = False,
        params: dict[str, str] | None = None, accept: str | None = None,
    ) -> Any:  # fmt: skip
        """The answer of `/repos/<repo><path>`: JSON (or the body's bytes when `accept` asks for raw content);
        None for a 404 when `missing` says that is an answer, else a GitHubError that says what to fix."""
        try:
            r = await self.http.request(method, f"/repos/{self.repo}{path}", json=json, params=params,
                                        headers={"Accept": accept} if accept else None)  # fmt: skip
        except httpx.HTTPError as exc:
            raise GitHubError(f"GitHub could not be reached: {type(exc).__name__}") from exc
        if r.status_code in ok:
            return r.content if accept else r.json()
        if r.status_code == 404 and missing:
            return None
        raise GitHubError(_explain(r, self.repo))


async def push(
    files: dict[str, bytes], *, repo: str, branch: str, folder: str, message: str, token: str,
    transport: httpx.AsyncBaseTransport | None = None, api: str = API,
    root_files: dict[str, bytes] | None = None, prune: bool = False, skip_unchanged: bool = False,
) -> Pushed:  # fmt: skip
    """One commit putting `files` under `folder` of `repo` on `branch` (created from the default branch if new).

    `root_files` go at the repository's root (such as a CI file, which GitHub reads only from `.github/workflows`);
    `prune` deletes what the folder holds that `files` no longer has; `skip_unchanged` makes no commit when the
    result would be the tree the branch already has (`Pushed.changed` says whether there was one)."""
    check(repo, branch, folder)
    if len(files) + len(root_files or {}) > MAX_FILES:
        raise InvalidInput(f"the export has {len(files)} files; at most {MAX_FILES} can be pushed at once")
    async with Client(repo, token, transport, api) as gh:
        ref = await gh.call("GET", f"/git/ref/heads/{branch}", missing=True)
        created = ref is None
        if created:
            default = (await gh.call("GET", ""))["default_branch"]
            base = await gh.call("GET", f"/git/ref/heads/{default}", missing=True)
            if base is None:
                raise GitHubError(f"{repo} has no commits yet; add a first commit (a README) and push again")
            parent = base["object"]["sha"]
        else:
            parent = ref["object"]["sha"]
        base_tree = (await gh.call("GET", f"/git/commits/{parent}"))["tree"]["sha"]
        wanted = {f"{folder}/{path}" if folder else path: data for path, data in files.items()}
        wanted.update(root_files or {})
        tree: list[dict[str, Any]] = []
        for path, data in sorted(wanted.items()):
            content = base64.b64encode(data).decode()
            blob = await gh.call("POST", "/git/blobs", {"content": content, "encoding": "base64"})
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        if prune:  # what the folder holds that this export no longer has
            listing = await gh.call("GET", f"/git/trees/{base_tree}", params={"recursive": "1"})
            if listing.get("truncated"):
                raise GitHubError(f"{repo} is too large to compare its files; push without pruning")
            prefix = f"{folder}/" if folder else ""
            tree += [{"path": e["path"], "mode": "100644", "type": "blob", "sha": None} for e in listing["tree"]
                     if e["type"] == "blob" and e["path"].startswith(prefix) and e["path"] not in wanted]  # fmt: skip
        new_tree = await gh.call("POST", "/git/trees", {"base_tree": base_tree, "tree": tree})
        if skip_unchanged and new_tree["sha"] == base_tree and not created:
            return Pushed(commit=parent, url=_url(repo, branch, folder), branch=branch, created_branch=False,
                          files=len(files), changed=False)  # fmt: skip
        made = {"message": message, "tree": new_tree["sha"], "parents": [parent]}
        commit = await gh.call("POST", "/git/commits", made)
        if created:
            await gh.call("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
        else:
            await gh.call("PATCH", f"/git/refs/heads/{branch}", {"sha": commit["sha"], "force": False})
    return Pushed(commit=commit["sha"], url=_url(repo, branch, folder), branch=branch, created_branch=created,
                  files=len(files))  # fmt: skip


def _url(repo: str, branch: str, folder: str) -> str:
    return f"https://github.com/{repo}/tree/{branch}/{folder}".rstrip("/")


@dataclass
class PullRequest:
    number: int
    url: str
    created: bool  # opened by this call (False: an open one was updated)


async def default_branch(repo: str, token: str, transport: httpx.AsyncBaseTransport | None = None,
                         api: str = API) -> str:  # fmt: skip
    async with Client(repo, token, transport, api) as gh:
        return str((await gh.call("GET", ""))["default_branch"])


async def read_file(
    repo: str, path: str, ref: str, token: str, transport: httpx.AsyncBaseTransport | None = None, api: str = API
) -> bytes | None:  # fmt: skip
    """A file of `repo` at a branch, tag or commit; None when the file (or the branch) is not there."""
    async with Client(repo, token, transport, api) as gh:
        return await gh.call("GET", f"/contents/{quote(path, safe='/')}", params={"ref": ref}, missing=True,
                             accept="application/vnd.github.raw+json")  # fmt: skip


async def open_pull_request(
    *, repo: str, branch: str, base: str, title: str, body: str, token: str,
    transport: httpx.AsyncBaseTransport | None = None, api: str = API,
) -> PullRequest | None:  # fmt: skip
    """The open pull request from `branch` into `base`: its title and description are updated, or one is opened.
    None when GitHub sees nothing to compare (the branch holds what `base` does)."""
    check(repo, branch, "")
    async with Client(repo, token, transport, api) as gh:
        owner = repo.split("/", 1)[0]
        found = await gh.call("GET", "/pulls", params={"head": f"{owner}:{branch}", "base": base, "state": "open"})
        if found:
            number = found[0]["number"]
            done = await gh.call("PATCH", f"/pulls/{number}", {"title": title, "body": body})
            return PullRequest(number=number, url=done["html_url"], created=False)
        try:
            done = await gh.call("POST", "/pulls", {"title": title, "head": branch, "base": base, "body": body})
        except GitHubError as exc:
            if "No commits between" in str(exc):
                return None
            raise
        return PullRequest(number=done["number"], url=done["html_url"], created=True)


def _explain(r: httpx.Response, repo: str) -> str:
    try:
        detail = str(r.json().get("message") or "")
    except ValueError:
        detail = r.text[:200]
    if r.status_code == 401:
        return "GitHub did not accept the token; check the project secret holding it"
    if r.status_code in (403, 404):
        return (f"GitHub refused access to {repo} ({r.status_code}: {detail}); the token needs Contents read and "
                "write on that repository")  # fmt: skip
    if r.status_code == 422:
        return f"GitHub rejected the push: {detail} (if the branch moved meanwhile, push again)"
    return f"GitHub answered {r.status_code}: {detail}"
