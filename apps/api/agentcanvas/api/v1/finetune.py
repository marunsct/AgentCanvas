"""/v1 fine-tuning export (G-10): accepted trajectories or a dataset as JSONL training examples."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note
from agentcanvas.api.deps import attachment, get_session
from agentcanvas.errors import InvalidInput, NotFound
from agentcanvas.services.datasets import DatasetService
from agentcanvas.services.finetune import MAX_EXAMPLES, Exported, FinetuneService, Skipped, to_jsonl
from agentcanvas.services.workflows import WorkflowService

from .workflows import service

router = APIRouter(tags=["evaluation"])


class FinetuneIn(BaseModel):
    source: Literal["runs", "dataset"] = Field("runs", description="accepted runs of the workflow, or a dataset")
    format: Literal["openai", "messages"] = Field("openai", description="OpenAI chat fine-tuning, or generic messages")
    run_ids: list[str] | None = Field(default=None, max_length=1000, description="runs only: these runs, not all")
    min_score: float = Field(default=0.8, ge=0, le=1, description="runs: the mean human score a run needs")
    include_online: bool = Field(default=False, description="runs: online evaluators' scores count as well")
    dataset_id: str | None = Field(default=None, description="dataset: which one")
    dataset_version: int | None = Field(default=None, description="dataset: default the latest")
    split: str | None = Field(default=None, description="dataset: only this split")
    system: str | None = Field(default=None, description="the system message; default the workflow's own prompt")
    limit: int = Field(default=1000, ge=1, le=MAX_EXAMPLES)


class FinetunePreview(BaseModel):
    examples: int
    skipped: list[Skipped]


async def build(session: AsyncSession, svc: WorkflowService, wf_id: str, body: FinetuneIn) -> Exported:
    view = await svc.get(wf_id)
    finetune = FinetuneService(session)
    if body.source == "dataset":
        if not body.dataset_id:
            raise InvalidInput("choose the dataset to export")
        datasets = DatasetService(session)
        ds = await datasets.get(body.dataset_id)
        if ds.workflow_id != wf_id:  # also keeps other organisations' datasets out
            raise NotFound(f"dataset '{body.dataset_id}' not found")
        version = await datasets.version(ds.id, body.dataset_version)
        examples = [e for e in version.examples if body.split is None or e.get("split", "") == body.split]
        return finetune.from_examples(examples, ds.id, body.format, body.system, view.workflow, body.limit)
    return await finetune.from_runs(
        view.workflow, wf_id, run_ids=body.run_ids, fmt=body.format, min_score=body.min_score,
        include_online=body.include_online, system=body.system, limit=body.limit,
    )  # fmt: skip


@router.post(
    "/workflows/{wf_id}/finetune-export",
    response_model=FinetunePreview,
    responses={200: {"content": {"application/x-ndjson": {}}, "description": "the JSONL file, unless preview"}},
)
async def finetune_export(
    wf_id: str,
    body: FinetuneIn,
    request: Request,
    preview: bool = Query(False, description="count the examples and say what was left out, without the file"),
    session: AsyncSession = Depends(get_session),
    svc: WorkflowService = Depends(service),
) -> Response:
    """Training examples as JSONL (G-10): accepted runs (scored by people, or by online evaluators if asked, with no
    thumbs-down) or a dataset's examples, in OpenAI's chat fine-tuning format or plain messages. It carries run
    content: builders only, and the export is audited."""
    out = await build(session, svc, wf_id, body)
    note(request, source=body.source, format=body.format, examples=len(out.examples), skipped=len(out.skipped))
    if preview:
        return Response(
            FinetunePreview(examples=len(out.examples), skipped=out.skipped[:100]).model_dump_json(),
            media_type="application/json",
        )
    name = f"finetune-{body.source}-{body.format}.jsonl"
    headers = {**attachment(name), "X-Examples": str(len(out.examples)), "X-Skipped": str(len(out.skipped))}
    return Response(to_jsonl(out.examples), media_type="application/x-ndjson", headers=headers)
