"""Files a model is shown (G-5): content blocks for images and PDFs, text for the rest.

Standalone, without the registry or the database, because exported projects vendor this module (like `sampling.py`)
and the catalog file beside it. A file field holds a *reference* (`ir/files.py`); this reads the bytes only when a
model call needs them, and builds LangChain's standard multimodal content blocks for the message, so the bytes are
never written to state or checkpoints:

* an image goes as an `image` block to a model the catalog says can see (`vision`), or whose capability is unknown;
* a PDF goes as a `file` block to such a model, and as its extracted text to a model that cannot see (the fallback:
  layout and pictures are lost);
* Word documents and text files always go as text.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import zipfile
from collections.abc import Awaitable, Callable, Sequence
from functools import cache
from pathlib import Path
from typing import Any

CATALOG = Path(__file__).parent / "registry" / "models.json"
MAX_TEXT_CHARS = 100_000  # a file's extracted text is cut here, with a note
MAX_INLINE_BYTES = 2 * 1024 * 1024  # a file sent inline in a reference (exports) is read up to this size
MAX_DOCX_UNZIPPED = 50 * 1024 * 1024
FileReader = Callable[[dict[str, Any]], Awaitable[tuple[bytes, str, str]]]  # reference -> (bytes, media type, name)
IMAGES = ("image/png", "image/jpeg", "image/gif", "image/webp")
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@cache
def _vision() -> dict[str, bool]:
    if not CATALOG.is_file():
        return {}
    return {m["id"]: bool(m.get("vision")) for m in json.loads(CATALOG.read_text())}


def sees(model_id: str | None) -> bool | None:
    """True/False: the catalog says the model takes images (and PDFs as pages) or does not; None: not in the
    catalog (a local or new model), so it is tried with them."""
    return _vision().get(model_id) if model_id else None


def model_id_of(model: Any) -> str | None:
    """The catalog id a model client was tagged with (`tag_model` in the node library)."""
    meta = getattr(model, "metadata", None) or {}
    value = meta.get("ac_model") if isinstance(meta, dict) else None
    return value if isinstance(value, str) else None


async def read_inline(ref: dict[str, Any]) -> tuple[bytes, str, str]:
    """The default reader, for projects running without an AgentCanvas server: a reference that carries its bytes
    (`data`, base64, up to 2 MB) or an address (`url`, http or https). The server's own runs read the file store."""
    name = str(ref.get("name") or "file")
    media = str(ref.get("media_type") or "application/octet-stream")
    if isinstance(ref.get("data"), str):
        if len(ref["data"]) > MAX_INLINE_BYTES * 4 // 3 + 4:
            raise ValueError(f"the file '{name}' is too large to send inline; give a url instead")
        try:
            return base64.b64decode(ref["data"], validate=True), media, name
        except (binascii.Error, ValueError):
            raise ValueError(f"the file '{name}': `data` is not base64") from None
    if isinstance(ref.get("url"), str) and ref["url"].startswith(("http://", "https://")):
        import httpx

        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            res = await client.get(ref["url"])
            res.raise_for_status()
            if len(res.content) > 20 * 1024 * 1024:
                raise ValueError(f"the file '{name}' is larger than 20 MB")
            return res.content, str(res.headers.get("content-type") or media).split(";")[0], name
    raise ValueError(f"the file '{name}' has no id on this server, no data and no url")


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()


def docx_text(data: bytes) -> str:
    import docx

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        if sum(i.file_size for i in z.infolist()) > MAX_DOCX_UNZIPPED:
            raise ValueError("it unpacks to more than 50 MB")
    d = docx.Document(io.BytesIO(data))
    lines = [p.text for p in d.paragraphs]
    for table in d.tables:
        lines += [" | ".join(cell.text for cell in row.cells) for row in table.rows]
    return "\n\n".join(line for line in lines if line.strip())


def _text(name: str, label: str, body: str) -> dict[str, Any]:
    body = body.strip()
    cut = len(body) > MAX_TEXT_CHARS
    if cut:
        body = body[:MAX_TEXT_CHARS]
    note = f"\n[The file is longer; only the first {MAX_TEXT_CHARS:,} characters are shown.]" if cut else ""
    return {"type": "text", "text": f"[Attached file “{name}” ({label})]\n{body or '(no text found)'}{note}"}


async def blocks_for(ref: dict[str, Any], read: FileReader, model_id: str | None) -> list[dict[str, Any]]:
    """The content blocks that show one file to a model."""
    data, media, name = await read(ref)
    can_see = sees(model_id) is not False
    if media in IMAGES:
        if not can_see:
            return [{"type": "text", "text": f"[Attached image “{name}” is not shown: this model cannot see images.]"}]
        return [{"type": "image", "base64": base64.b64encode(data).decode(), "mime_type": media}]
    if media == "application/pdf":
        if can_see:
            return [{
                "type": "file", "base64": base64.b64encode(data).decode(), "mime_type": media,
                "extras": {"filename": name},
            }]  # fmt: skip
        try:
            return [_text(name, "PDF, text extracted", await _in_thread(pdf_text, data))]
        except Exception as exc:  # noqa: BLE001 - an unreadable file is the file's problem, not the run's
            return [{"type": "text", "text": f"[Attached PDF “{name}” could not be read: {exc}]"}]
    if media == DOCX:
        try:
            return [_text(name, "Word document, text extracted", await _in_thread(docx_text, data))]
        except Exception as exc:  # noqa: BLE001
            return [{"type": "text", "text": f"[Attached document “{name}” could not be read: {exc}]"}]
    if b"\x00" not in data[:8192]:
        return [_text(name, media, data.decode("utf-8", errors="replace"))]
    return [{"type": "text", "text": f"[Attached file “{name}” ({media}) cannot be shown to a model.]"}]


async def _in_thread(fn: Callable[[bytes], str], data: bytes) -> str:
    import asyncio

    return await asyncio.to_thread(fn, data)


def file_fields(channels: Sequence[dict[str, Any]], only: Sequence[str] | None = None) -> list[str]:
    """The state fields that hold files (G-5): channels whose JSON Schema says `format: file`, or a list of files;
    `only` (a block's `files` setting) narrows them. These are the fields a model is shown."""

    def is_file(schema: Any) -> bool:
        return isinstance(schema, dict) and (
            schema.get("format") == "file" or (schema.get("type") == "array" and is_file(schema.get("items")))
        )

    names = [c["name"] for c in channels if is_file(c.get("type")) and c.get("io", "inout") != "out"]
    return [n for n in names if n in only] if only else names


def refs_in(state: Any, names: Sequence[str]) -> list[dict[str, Any]]:
    """The file references held by the state fields `names` (a file, or a list of files), in that order."""
    out: list[dict[str, Any]] = []
    for name in names:
        value = state.get(name) if hasattr(state, "get") else None
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict) and item.get("kind") == "file":
                out.append(item)
    return out


async def attachment_blocks(
    state: Any, names: Sequence[str], read: FileReader | None, model_id: str | None
) -> list[dict[str, Any]]:
    """Blocks for every file in the fields `names` of `state`; empty when there are none."""
    blocks: list[dict[str, Any]] = []
    for ref in refs_in(state, names):
        blocks += await blocks_for(ref, read or read_inline, model_id)
    return blocks
