"""LangSmith dataset sync (S13, FR-EVAL-02): push a dataset version to LangSmith, pull one back. Explicit only.

The key is the project's `LANGSMITH_API_KEY` secret, else the server's (`LANGSMITH_API_KEY`); `LANGSMITH_ENDPOINT`
picks a self-hosted LangSmith. A dataset is linked to one LangSmith dataset the first time it is pushed (created,
named after the workflow and dataset). A push makes the LangSmith dataset hold exactly that version's examples
(LangSmith keeps its own history) and tags it `agentcanvas-v<N>`; a pull turns the LangSmith dataset's current
examples into a new version here. Inputs, reference outputs, splits and metadata travel; an example's run context
and expected tool calls ride in its metadata (`agentcanvas_context`, `agentcanvas_trajectory`).
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from typing import Any

from agentcanvas.errors import InvalidInput
from agentcanvas.services.datasets import Example

KEY = "LANGSMITH_API_KEY"
CONTEXT, TRAJECTORY, ORIGIN = "agentcanvas_context", "agentcanvas_trajectory", "agentcanvas_example_id"
INTERNAL = {CONTEXT, TRAJECTORY, ORIGIN, "dataset_split"}


def api_key(project_secrets: dict[str, str]) -> str | None:
    return project_secrets.get(KEY) or os.getenv(KEY) or None


def make_client(key: str) -> Any:
    """A LangSmith client for `key` (patched in tests)."""
    import langsmith

    return langsmith.Client(api_key=key, api_url=os.getenv("LANGSMITH_ENDPOINT") or None)


def client_for(project_secrets: dict[str, str]) -> Any:
    key = api_key(project_secrets)
    if key is None:
        raise InvalidInput(f"LangSmith is not configured: set the project secret {KEY} (or the server's)")
    return make_client(key)


def to_langsmith(ex: Example) -> dict[str, Any]:
    metadata = {**ex.metadata, ORIGIN: ex.id}
    if ex.context:
        metadata[CONTEXT] = ex.context
    if ex.trajectory is not None:
        metadata[TRAJECTORY] = ex.trajectory
    out: dict[str, Any] = {"inputs": ex.input, "outputs": ex.reference, "metadata": metadata}
    if ex.split:
        out["split"] = ex.split
    return out


def from_langsmith(e: Any) -> Example:
    metadata = dict(getattr(e, "metadata", None) or {})
    split = metadata.get("dataset_split") or []
    split = split[0] if isinstance(split, list) and split else split if isinstance(split, str) else ""
    split = "" if split == "base" else split  # LangSmith's name for "no split"
    return Example(
        id=str(metadata.get(ORIGIN) or ""),
        input=dict(e.inputs or {}),
        reference=dict(e.outputs or {}),
        context=metadata.get(CONTEXT),
        trajectory=metadata.get(TRAJECTORY),
        split=split,
        metadata={k: v for k, v in metadata.items() if k not in INTERNAL},
    )


def _push(client: Any, link: dict[str, Any] | None, name: str, description: str, number: int,
          examples: list[Example]) -> dict[str, Any]:  # fmt: skip
    ls_id = (link or {}).get("dataset_id")
    if not ls_id or not client.has_dataset(dataset_id=ls_id):
        ds = client.create_dataset(name, description=description or None, metadata={"source": "agentcanvas"})
        ls_id = str(ds.id)
    else:
        ds = client.read_dataset(dataset_id=ls_id)
    old = [e.id for e in client.list_examples(dataset_id=ls_id)]
    if old:
        client.delete_examples(example_ids=old)
    if examples:
        client.create_examples(dataset_id=ls_id, examples=[to_langsmith(x) for x in examples])
    now = datetime.now(UTC)
    try:
        client.update_dataset_tag(dataset_id=ls_id, as_of=now, tag=f"agentcanvas-v{number}")
    except Exception:  # noqa: BLE001 - the tag is a convenience; the push itself succeeded
        pass
    return {
        "dataset_id": ls_id, "name": getattr(ds, "name", name), "url": getattr(ds, "url", None),
        "pushed_version": number, "pulled_version": (link or {}).get("pulled_version"), "synced_at": now.isoformat(),
    }  # fmt: skip


async def push(client: Any, link: dict[str, Any] | None, name: str, description: str, number: int,
               examples: list[Example]) -> dict[str, Any]:  # fmt: skip
    """Make the linked LangSmith dataset (created if needed) hold these examples; the new link."""
    return await asyncio.to_thread(_push, client, link, name, description, number, examples)


async def pull(client: Any, link: dict[str, Any]) -> list[Example]:
    """The linked LangSmith dataset's current examples."""

    def run() -> list[Example]:
        return [from_langsmith(e) for e in client.list_examples(dataset_id=link["dataset_id"])]

    return await asyncio.to_thread(run)
