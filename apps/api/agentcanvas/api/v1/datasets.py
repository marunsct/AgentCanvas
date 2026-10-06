"""/v1 datasets (FR-EVAL-02): examples a workflow is evaluated on, versioned on every change."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note
from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.models import Dataset, DatasetVersion
from agentcanvas.errors import InvalidInput
from agentcanvas.services import gates, langsmith_sync
from agentcanvas.services.datasets import DatasetService, Example, ExamplePatch, Mapping, to_jsonl


async def gated(
    request: Request, user: CurrentUser = Depends(current_user), session: AsyncSession = Depends(get_session)
) -> None:
    """Changing a dataset a release gate measures with needs an admin (the gate itself does)."""
    if request.method != "GET" and (ds_id := request.path_params.get("ds_id")):
        await gates.guard(
            session, user.role, dataset_id=ds_id, delete=request.method == "DELETE" and request.url.path.endswith(ds_id)
        )


router = APIRouter(tags=["datasets"], dependencies=[Depends(gated)])


def svc(session: AsyncSession = Depends(get_session)) -> DatasetService:
    return DatasetService(session)


class DatasetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class DatasetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class LangSmithLink(BaseModel):
    dataset_id: str
    name: str
    url: str | None
    pushed_version: int | None = Field(default=None, description="the version last pushed")
    pulled_version: int | None = Field(default=None, description="the version the last pull made here")
    synced_at: datetime


class DatasetOut(BaseModel):
    id: str
    workflow_id: str
    name: str
    description: str
    version: int
    examples: int
    splits: dict[str, int] = Field(description="examples per split ('' = none)")
    langsmith: LangSmithLink | None = Field(default=None, description="the LangSmith dataset it is synced with")
    created_at: datetime
    updated_at: datetime


class PushIn(BaseModel):
    version: int | None = Field(default=None, description="default: the latest version")


class DatasetDetail(DatasetOut):
    version_id: str
    items: list[Example]


class DatasetVersionOut(BaseModel):
    id: str
    number: int
    message: str
    examples: int
    created_by: str | None
    created_at: datetime


class ExamplesIn(BaseModel):
    examples: list[Example] = Field(min_length=1)
    message: str = ""


class FromTestCasesIn(BaseModel):
    ids: list[str] | None = Field(default=None, description="these test cases (default: all of the workflow's)")


class FromRunsIn(BaseModel):
    run_ids: list[str] = Field(min_length=1, max_length=500)


class ImportIn(BaseModel):
    format: Literal["csv", "jsonl"]
    content: str = Field(description="the file's text")
    mapping: Mapping | None = Field(default=None, description="CSV only: columns -> fields")
    split: str | None = Field(default=None, description="split for examples that name none")


def _out(ds: Dataset, v: DatasetVersion) -> DatasetOut:
    splits: dict[str, int] = {}
    for e in v.examples:
        splits[e.get("split", "")] = splits.get(e.get("split", ""), 0) + 1
    return DatasetOut(
        id=ds.id, workflow_id=ds.workflow_id, name=ds.name, description=ds.description, version=v.number,
        examples=len(v.examples), splits=splits, created_at=ds.created_at, updated_at=v.created_at,
        langsmith=LangSmithLink.model_validate(ds.langsmith) if ds.langsmith else None,
    )  # fmt: skip


async def _detail(s: DatasetService, ds_id: str, number: int | None = None) -> DatasetDetail:
    ds, v = await s.get(ds_id), await s.version(ds_id, number)
    items = [Example.model_validate(e) for e in v.examples]
    return DatasetDetail(**_out(ds, v).model_dump(), version_id=v.id, items=items)


@router.get("/workflows/{wf_id}/datasets", response_model=list[DatasetOut])
async def list_datasets(wf_id: str, s: DatasetService = Depends(svc)) -> list[DatasetOut]:
    return [_out(d, v) for d, v in await s.list(wf_id)]


@router.post("/workflows/{wf_id}/datasets", response_model=DatasetDetail, status_code=201)
async def create_dataset(
    wf_id: str, body: DatasetIn, s: DatasetService = Depends(svc), user: CurrentUser = Depends(current_user)
) -> DatasetDetail:
    v = await s.create(wf_id, body.name, body.description, user.id)
    return await _detail(s, v.dataset_id)


@router.get("/datasets/{ds_id}", response_model=DatasetDetail)
async def get_dataset(ds_id: str, version: int | None = None, s: DatasetService = Depends(svc)) -> DatasetDetail:
    """The dataset with its examples, at its latest version or at `version`."""
    return await _detail(s, ds_id, version)


@router.patch("/datasets/{ds_id}", response_model=DatasetDetail)
async def update_dataset(ds_id: str, body: DatasetPatch, s: DatasetService = Depends(svc)) -> DatasetDetail:
    await s.rename(ds_id, body.name, body.description)
    return await _detail(s, ds_id)


@router.delete("/datasets/{ds_id}", status_code=204)
async def delete_dataset(ds_id: str, s: DatasetService = Depends(svc)) -> Response:
    await s.delete(ds_id)
    return Response(status_code=204)


@router.get("/datasets/{ds_id}/versions", response_model=list[DatasetVersionOut])
async def list_dataset_versions(ds_id: str, s: DatasetService = Depends(svc)) -> list[DatasetVersionOut]:
    await s.get(ds_id)
    return [
        DatasetVersionOut(
            id=v.id,
            number=v.number,
            message=v.message,
            examples=len(v.examples),
            created_by=v.created_by,
            created_at=v.created_at,
        )
        for v in await s.versions(ds_id)
    ]


@router.get("/datasets/{ds_id}/export")
async def export_dataset(ds_id: str, version: int | None = None, s: DatasetService = Depends(svc)) -> Response:
    """The examples as JSONL (one example per line), importable again."""
    ds, v = await s.get(ds_id), await s.version(ds_id, version)
    return Response(
        to_jsonl(v.examples),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="{ds.name}-v{v.number}.jsonl"'},
    )


@router.post("/datasets/{ds_id}/examples", response_model=DatasetDetail)
async def add_examples(
    ds_id: str, body: ExamplesIn, s: DatasetService = Depends(svc), user: CurrentUser = Depends(current_user)
) -> DatasetDetail:
    v = await s.add(ds_id, body.examples, body.message or f"added {len(body.examples)} example(s)", user.id)
    return await _detail(s, ds_id, v.number)


@router.post("/datasets/{ds_id}/examples/from-test-cases", response_model=DatasetDetail)
async def add_test_cases(
    ds_id: str, body: FromTestCasesIn, s: DatasetService = Depends(svc), user: CurrentUser = Depends(current_user)
) -> DatasetDetail:
    v = await s.from_test_cases(ds_id, body.ids, user.id)
    return await _detail(s, ds_id, v.number)


@router.post("/datasets/{ds_id}/examples/from-runs", response_model=DatasetDetail)
async def add_runs(
    ds_id: str, body: FromRunsIn, s: DatasetService = Depends(svc), user: CurrentUser = Depends(current_user)
) -> DatasetDetail:
    v = await s.from_runs(ds_id, body.run_ids, user.id)
    return await _detail(s, ds_id, v.number)


@router.post("/datasets/{ds_id}/import", response_model=DatasetDetail)
async def import_examples(
    ds_id: str, body: ImportIn, s: DatasetService = Depends(svc), user: CurrentUser = Depends(current_user)
) -> DatasetDetail:
    v = await s.import_file(ds_id, body.format, body.content, body.mapping, body.split, user.id)
    return await _detail(s, ds_id, v.number)


@router.patch("/datasets/{ds_id}/examples/{ex_id}", response_model=DatasetDetail)
async def update_example(
    ds_id: str,
    ex_id: str,
    body: ExamplePatch,
    s: DatasetService = Depends(svc),
    user: CurrentUser = Depends(current_user),
) -> DatasetDetail:
    v = await s.update_example(ds_id, ex_id, body, user.id)
    return await _detail(s, ds_id, v.number)


@router.delete("/datasets/{ds_id}/examples/{ex_id}", response_model=DatasetDetail)
async def delete_example(
    ds_id: str, ex_id: str, s: DatasetService = Depends(svc), user: CurrentUser = Depends(current_user)
) -> DatasetDetail:
    v = await s.delete_example(ds_id, ex_id, user.id)
    return await _detail(s, ds_id, v.number)


# ----------------------------------------------------------------------------------- LangSmith sync (S13) ----
async def _langsmith(request: Request, s: DatasetService, ds_id: str) -> tuple[Dataset, Any, str]:
    from agentcanvas.db.models import WorkflowRow

    ds = await s.get(ds_id)
    row = await s.s.get(WorkflowRow, ds.workflow_id)
    project_id = row.project_id if row else ""
    secrets = request.app.state.projects.secrets.get(project_id, {})
    return ds, langsmith_sync.client_for(secrets), row.name if row else ds.workflow_id


@router.post("/datasets/{ds_id}/langsmith/push", response_model=DatasetOut)
async def push_to_langsmith(ds_id: str, body: PushIn, request: Request, s: DatasetService = Depends(svc)) -> DatasetOut:
    """Make the linked LangSmith dataset (created on the first push) hold this version's examples."""
    ds, client, workflow = await _langsmith(request, s, ds_id)
    v = await s.version(ds_id, body.version)
    examples = [Example.model_validate(e) for e in v.examples]
    link = await langsmith_sync.push(
        client, ds.langsmith, f"{workflow} / {ds.name}", ds.description, v.number, examples
    )
    ds.langsmith = link
    await s.s.commit()
    note(request, langsmith_dataset=link["dataset_id"], version=v.number)
    return _out(ds, await s.version(ds_id))


@router.post("/datasets/{ds_id}/langsmith/pull", response_model=DatasetDetail)
async def pull_from_langsmith(
    ds_id: str, request: Request, s: DatasetService = Depends(svc), user: CurrentUser = Depends(current_user)
) -> DatasetDetail:
    """A new version holding the linked LangSmith dataset's current examples."""
    ds, client, _ = await _langsmith(request, s, ds_id)
    if not ds.langsmith:
        raise InvalidInput("push the dataset to LangSmith first: that links it to a LangSmith dataset")
    link = dict(ds.langsmith)
    examples = await langsmith_sync.pull(client, link)
    v = await s.replace(ds_id, examples, f"pulled {len(examples)} example(s) from LangSmith", user.id)
    ds = await s.get(ds_id)
    ds.langsmith = {**link, "pulled_version": v.number, "synced_at": datetime.now(UTC).isoformat()}
    await s.s.commit()
    note(request, langsmith_dataset=link["dataset_id"], version=v.number)
    return await _detail(s, ds_id, v.number)
