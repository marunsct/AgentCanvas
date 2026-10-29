"""Datasets for evaluating workflows (FR-EVAL-02).

A dataset belongs to a workflow and holds **examples**: an input, an optional run context, the reference output
(channel -> expected value), an optional reference tool trajectory, a split ("train", "test", …) and free-form
metadata. Examples come from saved test cases, from past runs, or from CSV/JSONL files, and are checked against the
workflow's input contract.

Every change writes a new immutable **version** holding the whole example set, so an experiment that names a
version always sees the same examples. Two concurrent edits cannot both win: the second gets 409 and retries.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Callable
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import Dataset, DatasetVersion, Run, TestCase, Thread, WorkflowRow
from agentcanvas.errors import Conflict, InvalidInput, NotFound
from agentcanvas.ids import new_id
from agentcanvas.ir.contract import contract
from agentcanvas.ir.models import Workflow
from agentcanvas.runtime.types import REPLAYABLE

MAX_EXAMPLES = 10_000


class Example(BaseModel):
    id: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] | None = None
    reference: dict[str, Any] = Field(default_factory=dict, description="output channel -> expected value")
    trajectory: list[str] | None = Field(default=None, description="expected tool calls, in order")
    split: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExamplePatch(BaseModel):
    input: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    reference: dict[str, Any] | None = None
    trajectory: list[str] | None = None
    split: str | None = None
    metadata: dict[str, Any] | None = None


class Mapping(BaseModel):
    """Which CSV column fills which field. Without one: columns named like an input field fill it,
    `reference.<channel>` columns the reference, a `split` column the split, and the rest go to metadata."""

    input: dict[str, str] = Field(default_factory=dict, description="input field -> column")
    reference: dict[str, str] = Field(default_factory=dict, description="output channel -> column")
    split: str | None = None


def _coerce(cell: str, schema: dict[str, Any]) -> Any:
    """A CSV cell as the value its JSON Schema expects: strings stay strings, anything else is parsed as JSON."""
    if schema.get("type") in (None, "string"):
        return cell
    try:
        return json.loads(cell)
    except ValueError:
        return cell  # left as text: validation reports it


def parse_csv(content: str, wf: Workflow, mapping: Mapping | None) -> list[Example]:
    # no cell is longer than the file (the module's default is 128 KB); only ever raised, as the limit is global
    csv.field_size_limit(max(csv.field_size_limit(), len(content) + 1))
    try:
        return _parse_csv(content, wf, mapping)
    except csv.Error as exc:
        raise InvalidInput(f"not a CSV file: {exc}") from None


def _parse_csv(content: str, wf: Workflow, mapping: Mapping | None) -> list[Example]:
    reader = csv.DictReader(io.StringIO(content))
    columns = reader.fieldnames or []
    terms = contract(wf)
    fields = terms.input
    # a reference is compared with the run's output: typed as the output contract (or its channel) says, else text
    outputs = {**{ch.name: ch.type for ch in wf.state.channels}, **terms.output}
    if mapping is None:
        mapping = Mapping(
            input={c: c for c in columns if c in fields},
            reference={c.removeprefix("reference."): c for c in columns if c.startswith("reference.")},
            split="split" if "split" in columns else None,
        )
    missing = [
        c for c in [*mapping.input.values(), *mapping.reference.values(), mapping.split] if c and c not in columns
    ]
    if missing:
        raise InvalidInput(f"the file has no column {', '.join(repr(c) for c in missing)}")
    used = {*mapping.input.values(), *mapping.reference.values(), mapping.split}
    out = []
    for row in reader:
        reference = {ch: _coerce(row[c], outputs.get(ch, {})) for ch, c in mapping.reference.items() if row.get(c)}
        out.append(
            Example(
                input={f: _coerce(row[c], fields.get(f, {})) for f, c in mapping.input.items() if row.get(c)},
                reference={ch: v for ch, v in reference.items() if v is not None},  # a "null" cell: no reference
                split=(row.get(mapping.split) or "") if mapping.split else "",
                metadata={c: v for c, v in row.items() if c not in used and c is not None and v},
            )
        )
    return out


def parse_jsonl(content: str) -> list[Example]:
    """One object per line: {input, reference, context, trajectory, split, metadata}. LangSmith-style
    {inputs, outputs} lines are accepted too."""
    out = []
    for n, line in enumerate(content.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                raise ValueError("not an object")
            if "inputs" in obj and "input" not in obj:
                obj = {**obj, "input": obj.pop("inputs"), "reference": obj.pop("outputs", {}) or {}}
            out.append(Example.model_validate({k: v for k, v in obj.items() if k != "id"}))
        except (ValueError, ValidationError) as exc:
            raise InvalidInput(f"line {n}: {str(exc).splitlines()[0]}") from None
    return out


def check(wf: Workflow, examples: list[Example], first: int = 1) -> None:
    """Every example's input must fit the workflow's input contract (422 listing the first problems)."""
    fields = contract(wf).input
    problems = []
    for n, ex in enumerate(examples, start=first):
        for name, value in ex.input.items():
            if name not in fields:
                problems.append(f"example {n}: '{name}' is not an input of this workflow")
            elif errors := list(Draft202012Validator(fields[name]).iter_errors(value)):
                problems.append(f"example {n}: '{name}': {errors[0].message}")
        if len(problems) >= 20:
            break
    if problems:
        raise InvalidInput("; ".join(problems[:20]), problems=problems[:20])


class DatasetService:
    def __init__(self, session: AsyncSession):
        self.s = session

    async def _workflow(self, wf_id: str) -> Workflow:
        row = await self.s.get(WorkflowRow, wf_id)
        if row is None:
            raise NotFound(f"workflow '{wf_id}' not found")
        return Workflow.model_validate(row.draft_ir)

    async def get(self, ds_id: str) -> Dataset:
        ds = await self.s.get(Dataset, ds_id)
        if ds is None:
            raise NotFound(f"dataset '{ds_id}' not found")
        return ds

    async def version(self, ds_id: str, number: int | None = None) -> DatasetVersion:
        q = select(DatasetVersion).where(DatasetVersion.dataset_id == ds_id)
        q = q.where(DatasetVersion.number == number) if number else q.order_by(DatasetVersion.number.desc())
        v = await self.s.scalar(q.limit(1))
        if v is None:
            raise NotFound(f"dataset '{ds_id}' has no version {number}")
        return v

    async def versions(self, ds_id: str) -> list[DatasetVersion]:
        q = select(DatasetVersion).where(DatasetVersion.dataset_id == ds_id).order_by(DatasetVersion.number.desc())
        return list((await self.s.scalars(q)).all())

    async def list(self, wf_id: str) -> list[tuple[Dataset, DatasetVersion]]:
        latest = (
            select(DatasetVersion.dataset_id, func.max(DatasetVersion.number).label("n"))
            .group_by(DatasetVersion.dataset_id)
            .subquery()
        )
        q = (
            select(Dataset, DatasetVersion)
            .join(latest, latest.c.dataset_id == Dataset.id)
            .join(DatasetVersion, (DatasetVersion.dataset_id == Dataset.id) & (DatasetVersion.number == latest.c.n))
            .where(Dataset.workflow_id == wf_id)
            .order_by(Dataset.created_at.desc(), Dataset.id.desc())
        )
        return [(d, v) for d, v in (await self.s.execute(q)).all()]

    async def create(self, wf_id: str, name: str, description: str, user_id: str | None) -> DatasetVersion:
        await self._workflow(wf_id)
        ds = Dataset(id=new_id("ds"), workflow_id=wf_id, name=name, description=description, created_by=user_id)
        self.s.add(ds)
        await self.s.flush()
        v = DatasetVersion(
            id=new_id("dsv"), dataset_id=ds.id, number=1, message="created", examples=[], created_by=user_id
        )
        self.s.add(v)
        await self.s.commit()
        return v

    async def rename(self, ds_id: str, name: str | None, description: str | None) -> Dataset:
        ds = await self.get(ds_id)
        if name is not None:
            ds.name = name
        if description is not None:
            ds.description = description
        await self.s.commit()
        return ds

    async def delete(self, ds_id: str) -> None:
        await self.s.delete(await self.get(ds_id))
        await self.s.commit()

    async def change(
        self,
        ds_id: str,
        edit: Callable[[list[Example]], list[Example]],
        message: str,
        user_id: str | None,
        added: list[Example] | None = None,
    ) -> DatasetVersion:
        """Apply `edit` to the latest examples and save the result as the next version. New examples (`added`)
        are checked against the workflow's input contract first."""
        ds = await self.get(ds_id)
        latest = await self.version(ds_id)
        current = [Example.model_validate(e) for e in latest.examples]
        if added:
            check(await self._workflow(ds.workflow_id), added, first=len(current) + 1)
            for ex in added:
                ex.id = new_id("ex")
        examples = edit(current)
        if len(examples) > MAX_EXAMPLES:
            raise InvalidInput(f"a dataset holds at most {MAX_EXAMPLES} examples")
        v = DatasetVersion(
            id=new_id("dsv"),
            dataset_id=ds_id,
            number=latest.number + 1,
            message=message,
            examples=[e.model_dump() for e in examples],
            created_by=user_id,
        )
        self.s.add(v)
        try:
            await self.s.commit()
        except IntegrityError:
            await self.s.rollback()
            raise Conflict("the dataset changed meanwhile; reload it and try again") from None
        return v

    async def replace(self, ds_id: str, examples: list[Example], message: str, user_id: str | None) -> DatasetVersion:
        """A new version holding exactly `examples` (checked); those without an id get one."""
        ds = await self.get(ds_id)
        check(await self._workflow(ds.workflow_id), examples)
        seen: set[str] = set()
        for ex in examples:
            if not ex.id or ex.id in seen:
                ex.id = new_id("ex")
            seen.add(ex.id)
        return await self.change(ds_id, lambda _: examples, message, user_id)

    async def add(self, ds_id: str, examples: list[Example], message: str, user_id: str | None) -> DatasetVersion:
        if not examples:
            raise InvalidInput("no examples to add")
        return await self.change(ds_id, lambda cur: [*cur, *examples], message, user_id, added=examples)

    async def update_example(self, ds_id: str, ex_id: str, patch: ExamplePatch, user_id: str | None) -> DatasetVersion:
        ds = await self.get(ds_id)
        latest = await self.version(ds_id)
        current = [Example.model_validate(e) for e in latest.examples]
        target = next((e for e in current if e.id == ex_id), None)
        if target is None:
            raise NotFound(f"example '{ex_id}' not found in dataset '{ds_id}'")
        updated = target.model_copy(update=patch.model_dump(exclude_unset=True))
        check(await self._workflow(ds.workflow_id), [updated], first=current.index(target) + 1)
        return await self.change(
            ds_id, lambda cur: [updated if e.id == ex_id else e for e in cur], f"edited {ex_id}", user_id
        )

    async def delete_example(self, ds_id: str, ex_id: str, user_id: str | None) -> DatasetVersion:
        latest = await self.version(ds_id)
        if not any(e.get("id") == ex_id for e in latest.examples):
            raise NotFound(f"example '{ex_id}' not found in dataset '{ds_id}'")
        return await self.change(ds_id, lambda cur: [e for e in cur if e.id != ex_id], f"removed {ex_id}", user_id)

    async def from_test_cases(self, ds_id: str, ids: list[str] | None, user_id: str | None) -> DatasetVersion:
        ds = await self.get(ds_id)
        q = select(TestCase).where(TestCase.workflow_id == ds.workflow_id).order_by(TestCase.created_at)
        cases = [c for c in (await self.s.scalars(q)).all() if ids is None or c.id in ids]
        if ids is not None and (unknown := set(ids) - {c.id for c in cases}):
            raise NotFound(f"test case '{sorted(unknown)[0]}' not found in this workflow")
        examples = [
            Example(
                input=c.input,
                context=c.context,
                reference=(c.expect or {}).get("channels") or {},
                metadata={"test_case": c.name, "test_case_id": c.id},
            )
            for c in cases
        ]
        return await self.add(ds_id, examples, f"added {len(examples)} test case(s)", user_id)

    async def from_runs(self, ds_id: str, run_ids: list[str], user_id: str | None) -> DatasetVersion:
        """Past runs of the dataset's workflow as examples: the run's input, and its final output as the
        reference. Only runs with a recording (fresh runs that succeeded or paused) qualify."""
        ds = await self.get(ds_id)
        q = (
            select(Run)
            .join(Thread, Thread.id == Run.thread_id)
            .where(Run.id.in_(run_ids), Thread.workflow_id == ds.workflow_id)
        )
        runs = {r.id: r for r in (await self.s.scalars(q)).all()}
        examples = []
        for rid in run_ids:
            run = runs.get(rid)
            if run is None:
                raise NotFound(f"run '{rid}' not found in this workflow")
            if not run.recording or run.recording.get("status") not in REPLAYABLE:
                raise InvalidInput(f"run '{rid}' has no recorded input (only new runs that finished or paused do)")
            examples.append(
                Example(
                    input=run.recording["input"], reference=run.recording.get("output") or {}, metadata={"run_id": rid}
                )
            )
        return await self.add(ds_id, examples, f"added {len(examples)} run(s)", user_id)

    async def import_file(
        self,
        ds_id: str,
        fmt: Literal["csv", "jsonl"],
        content: str,
        mapping: Mapping | None,
        split: str | None,
        user_id: str | None,
    ) -> DatasetVersion:
        ds = await self.get(ds_id)
        examples = (
            parse_jsonl(content)
            if fmt == "jsonl"
            else parse_csv(content, await self._workflow(ds.workflow_id), mapping)
        )
        if split:
            for ex in examples:
                ex.split = ex.split or split
        return await self.add(ds_id, examples, f"imported {len(examples)} example(s) from {fmt.upper()}", user_id)


def to_jsonl(examples: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(e, default=str) + "\n" for e in examples)
