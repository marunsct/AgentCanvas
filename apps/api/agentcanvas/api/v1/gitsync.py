"""Git round trip (G-14): connect a workflow to a GitHub branch, open pull requests from it, import it back."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.api.v1.versions import _export, _export_source
from agentcanvas.api.v1.workflows import service
from agentcanvas.db.models import GitLink, WorkflowVersion
from agentcanvas.errors import Conflict, DomainError, InvalidInput
from agentcanvas.ir.diff import diff_workflows
from agentcanvas.services import github, gitsync
from agentcanvas.services.versions import VersionService
from agentcanvas.services.workflows import WorkflowService

router = APIRouter(tags=["git"])
Auto = Literal["off", "publish", "version"]


class GitLinkIn(BaseModel):
    repo: str = Field(max_length=200, description="owner/name of the repository")
    branch: str = Field(min_length=1, max_length=200, description="the branch the pull requests come from")
    folder: str = Field("", max_length=200, description="where the workflow goes in the repository ('' = the root)")
    token_secret: str = Field("GITHUB_TOKEN", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$", description="project secret")
    target: Literal["local", "server", "kubernetes", "cloudrun", "aws", "azure"] = "local"
    auto: Auto = Field(
        "publish", description="open or update the pull request on every publish, every version, or never"
    )


class GitLinkOut(BaseModel):
    repo: str
    branch: str
    folder: str
    token_secret: str
    target: str
    auto: Auto
    pr_number: int | None = None
    pr_url: str | None = None
    last_version: str | None = Field(None, description="the version (or 'draft') the branch was last given")
    last_synced_at: datetime | None = None
    last_error: str | None = None


class PushIn(BaseModel):
    version: str | None = Field(None, description="a version's semver; the draft when left out")
    message: str | None = Field(None, max_length=2000, description="the commit message")
    overwrite: bool = Field(False, description="replace changes made on the branch since the last sync")


class PushOut(BaseModel):
    commit: str
    branch: str
    base: str
    changed: bool = Field(description="false: the branch already held exactly this export")
    created_branch: bool
    pr_number: int | None = None
    pr_url: str | None = None
    pr_created: bool = False
    note: str | None = None
    label: str


class PullIn(BaseModel):
    overwrite: bool = Field(False, description="replace the draft even though it changed since the last sync")


class PullOut(BaseModel):
    outcome: Literal["applied", "same", "unchanged"]
    version: int = Field(description="the draft's version after the import")
    summary: str = Field(description="what the import changed in the draft")


class GitSyncOut(BaseModel):
    """What opening the pull request after a publish or a saved version did (`error`: it did not, and why)."""

    pr_url: str | None = None
    pr_number: int | None = None
    error: str | None = None


def _out(link: GitLink) -> GitLinkOut:
    return GitLinkOut.model_validate(link, from_attributes=True)


async def _require(session: AsyncSession, wf_id: str) -> GitLink:
    link = await gitsync.GitLinks(session).get(wf_id)
    if link is None:
        raise InvalidInput("this workflow is not connected to a Git branch yet: connect it first")
    return link


def _token(request: Request, project_id: str, link: GitLink) -> str:
    token = request.app.state.projects.secrets.get(project_id, {}).get(link.token_secret)
    if not token:
        raise InvalidInput(
            f"add a project secret named {link.token_secret} holding a GitHub token that can write to {link.repo} "
            "(Contents and Pull requests: read and write, and Workflows for the CI file)"
        )
    return token


@router.get("/workflows/{wf_id}/git", response_model=GitLinkOut | None)
async def get_link(wf_id: str, session: AsyncSession = Depends(get_session), svc: WorkflowService = Depends(service)):
    """The workflow's Git connection (null when it has none)."""
    await svc.get(wf_id)
    link = await gitsync.GitLinks(session).get(wf_id)
    return _out(link) if link else None


@router.put("/workflows/{wf_id}/git", response_model=GitLinkOut)
async def connect(
    wf_id: str,
    body: GitLinkIn,
    session: AsyncSession = Depends(get_session),
    svc: WorkflowService = Depends(service),
    user: CurrentUser = Depends(current_user),
) -> GitLinkOut:
    """Connect the workflow to a branch of a GitHub repository (or change where it is connected to)."""
    await svc.get(wf_id)
    link = await gitsync.GitLinks(session).save(
        wf_id, repo=body.repo.strip(), branch=body.branch.strip(), folder=body.folder.strip().strip("/"),
        token_secret=body.token_secret, target=body.target, auto=body.auto, user_id=user.id,
    )  # fmt: skip
    return _out(link)


@router.delete("/workflows/{wf_id}/git", status_code=204)
async def disconnect(
    wf_id: str, session: AsyncSession = Depends(get_session), svc: WorkflowService = Depends(service)
) -> Response:
    """Forget the connection (the branch and its pull requests stay on GitHub)."""
    await svc.get(wf_id)
    await gitsync.GitLinks(session).delete(wf_id)
    return Response(status_code=204)


async def push_link(
    request: Request, session: AsyncSession, svc: WorkflowService, wf_id: str, link: GitLink,
    *, version: str | None, message: str | None, overwrite: bool,
) -> PushOut:  # fmt: skip
    """Export `version` (the draft when None) to the link's branch and open or update its pull request."""
    view, wf, semver = await _export_source(session, svc, wf_id, version)
    token = _token(request, view.row.project_id, link)
    _, _, data = await _export(request, session, svc, wf_id, version, link.target)
    saved = await VersionService(session).get(wf_id, version) if version else None
    label = f"v{semver}" if version else "the draft"
    note = message or (saved.message if saved else "") or ""
    pushed = await gitsync.push(
        link, wf, github.files_of(data), name=view.row.name, label=label, token=token, overwrite=overwrite,
        message=message or f"{view.row.name}: export of {label} from AgentCanvas",
        transport=getattr(request.app.state, "github_transport", None), note=note,
    )  # fmt: skip
    await session.commit()
    return PushOut(**vars(pushed), label=label)


@router.post("/workflows/{wf_id}/git/push", response_model=PushOut)
async def push(
    wf_id: str,
    body: PushIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    svc: WorkflowService = Depends(service),
) -> PushOut:
    """Commit a version (or the draft) to the branch and open or update the pull request: its description is the
    workflow's diff against the default branch, and a CI file runs the export's tests."""
    link = await _require(session, wf_id)
    try:
        return await push_link(request, session, svc, wf_id, link, version=body.version, message=body.message,
                               overwrite=body.overwrite)  # fmt: skip
    except DomainError as exc:
        await _remember(session, wf_id, exc)
        raise


async def _remember(session: AsyncSession, wf_id: str, exc: DomainError) -> None:
    """The last sync's failure, kept on the link so the editor can show it (nothing but the message: no token)."""
    await session.rollback()
    link = await gitsync.GitLinks(session).get(wf_id)
    if link is not None:
        link.last_error = exc.detail[:1000]
        await session.commit()


@router.post("/workflows/{wf_id}/git/pull", response_model=PullOut)
async def pull(
    wf_id: str,
    body: PullIn,
    request: Request,
    session: AsyncSession = Depends(get_session),
    svc: WorkflowService = Depends(service),
) -> PullOut:
    """Import the workflow on the branch into the draft. Refused (409) when the draft and the branch both changed
    since the last sync, unless `overwrite`; save a version first to keep the draft."""
    link = await _require(session, wf_id)
    view = await svc.get(wf_id)
    token = _token(request, view.row.project_id, link)
    remote = await gitsync.fetch(link, token, getattr(request.app.state, "github_transport", None))
    draft_hash, remote_hash = gitsync.hash_of(view.workflow, wf_id), gitsync.hash_of(remote, wf_id)
    decision = gitsync.pull_decision(draft_hash, remote_hash, link.repo_hash)
    changes = diff_workflows(view.workflow, remote)
    if decision == "conflict" and not body.overwrite:
        raise Conflict(
            f"the draft and the branch '{link.branch}' have both changed since the last sync "
            f"(importing would change: {changes.describe()}): save a version to keep the draft, then import with "
            "overwrite",
            reason="draft_changed", draft_version=view.row.version, summary=changes.describe(),
        )  # fmt: skip
    if decision in ("same", "unchanged"):
        if decision == "same":
            link.repo_hash = remote_hash
            await session.commit()
        return PullOut(outcome=decision, version=view.row.version,
                       summary="the draft already matches the branch" if decision == "same"
                       else "the branch has not changed since the last sync")  # fmt: skip
    done = await svc.replace(wf_id, gitsync.normalised(remote, wf_id).model_dump(mode="json"), view.row.version)
    link.repo_hash, link.last_synced_at, link.last_error = remote_hash, datetime.now(UTC), None
    await session.commit()
    return PullOut(outcome="applied", version=done.row.version, summary=changes.describe())


async def sync_after_version(
    request: Request, session: AsyncSession, wf_id: str, saved: WorkflowVersion, trigger: Literal["publish", "version"]
) -> GitSyncOut | None:
    """After a publish or a saved version: open or update the pull request when the link asks for it. Never raises:
    a failed sync is remembered on the link and reported, and the publish stands."""
    link = await gitsync.GitLinks(session).get(wf_id)
    if link is None or link.auto == "off" or (link.auto == "publish" and trigger == "version"):
        return None
    svc = WorkflowService(session, request.app.state.settings, request.app.state.projects.registry)
    try:
        out = await push_link(request, session, svc, wf_id, link, version=saved.semver, message=None, overwrite=False)
    except DomainError as exc:
        await _remember(session, wf_id, exc)
        return GitSyncOut(error=exc.detail)
    return GitSyncOut(pr_url=out.pr_url, pr_number=out.pr_number)
