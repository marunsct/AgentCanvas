"""/v1 datasets (FR-EVAL-02): examples a workflow is evaluated on, versioned on every change."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.models import Dataset, DatasetVersion
from agentcanvas.services.datasets import DatasetService, Example, ExamplePatch, Mapping, to_jsonl

router = APIRouter(tags=["datasets"])


def svc(session: AsyncSession = Depends(get_session)) -> DatasetService:
    return DatasetService(session)


class DatasetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class DatasetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class DatasetOut(BaseModel):
    id: str
    workflow_id: str
    name: str
    description: str
    version: int
    examples: int
    splits: dict[str, int] = Field(description="examples per split ('' = none)")
    created_at: datetime
    updated_at: datetime


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
