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


async def push(
    files: dict[str, bytes], *, repo: str, branch: str, folder: str, message: str, token: str,
    transport: httpx.AsyncBaseTransport | None = None, api: str = API,
) -> Pushed:  # fmt: skip
    """One commit putting `files` under `folder` of `repo` on `branch` (created from the default branch if new)."""
    check(repo, branch, folder)
    if len(files) > MAX_FILES:
        raise InvalidInput(f"the export has {len(files)} files; at most {MAX_FILES} can be pushed at once")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "AgentCanvas"}  # fmt: skip
    async with httpx.AsyncClient(base_url=api, headers=headers, transport=transport,
                                 timeout=30) as gh:  # fmt: skip

        async def call(method: str, path: str, json: Any = None, ok: tuple[int, ...] = (200, 201)) -> Any:
            try:
                r = await gh.request(method, f"/repos/{repo}{path}", json=json)
            except httpx.HTTPError as exc:
                raise GitHubError(f"GitHub could not be reached: {type(exc).__name__}") from exc
            if r.status_code in ok:
                return r.json()
            if r.status_code == 404 and path == "/git/ref/heads/" + branch:
                return None
            raise GitHubError(_explain(r, repo))

        ref = await call("GET", f"/git/ref/heads/{branch}")
        created = ref is None
        if created:
            default = (await call("GET", ""))["default_branch"]
            base = await call("GET", f"/git/ref/heads/{default}")
            if base is None:
                raise GitHubError(f"{repo} has no commits yet; add a first commit (a README) and push again")
            parent = base["object"]["sha"]
        else:
            parent = ref["object"]["sha"]
        base_tree = (await call("GET", f"/git/commits/{parent}"))["tree"]["sha"]
        tree = []
        for path, data in sorted(files.items()):
            blob = await call("POST", "/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
            tree.append({"path": f"{folder}/{path}" if folder else path, "mode": "100644", "type": "blob",
                         "sha": blob["sha"]})  # fmt: skip
        new_tree = await call("POST", "/git/trees", {"base_tree": base_tree, "tree": tree})
        commit = await call("POST", "/git/commits", {"message": message, "tree": new_tree["sha"], "parents": [parent]})
        if created:
            await call("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
        else:
            await call("PATCH", f"/git/refs/heads/{branch}", {"sha": commit["sha"], "force": False})
    return Pushed(commit=commit["sha"], url=f"https://github.com/{repo}/tree/{branch}/{folder}".rstrip("/"),
                  branch=branch, created_branch=created, files=len(files))  # fmt: skip


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
