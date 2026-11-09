"""File fields (G-5, FR-X-16): a state field that holds a file a person attached (an image, a PDF, a document).

A file field is an ordinary channel whose JSON Schema says `"format": "file"`:

    {"type": "object", "format": "file"}                                   any file
    {"type": "object", "format": "file", "contentMediaType": "image/*"}    images only
    {"type": "array", "items": {"type": "object", "format": "file"}}       several files

so documents from before file fields stay valid and every exporter that carries a JSON Schema carries the field. Its
value is a *file reference* (`FileRef`), never the file itself: the bytes live in the server's file store (uploaded
with `POST /v1/projects/{id}/files` or through a share link) and the reference carries the id, name, media type and
size. Outside the server (an exported project) a reference may carry the bytes as `data` (base64) or a `url`
instead; the server stores inline `data` as a file when a run starts, so checkpoints never hold the bytes.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

FILE_FORMAT = "file"
IMAGE_TYPES = ("image/png", "image/jpeg", "image/gif", "image/webp")
PDF_TYPE = "application/pdf"


class FileRef(BaseModel):
    """The value of a file field."""

    model_config = ConfigDict(extra="ignore")

    kind: Literal["file"] = "file"
    id: str | None = Field(None, description="the file in the server's file store (fil_…)")
    name: str = Field("file", max_length=255)
    media_type: str = Field("application/octet-stream", description="as the server read it from the content")
    size: int | None = Field(None, ge=0, description="bytes")
    data: str | None = Field(None, description="the bytes, base64 (exported projects; the server stores them)")
    url: str | None = Field(None, description="where the file is (exported projects only)")


def file_schema(accept: str | None = None) -> dict[str, Any]:
    """The JSON Schema of a file field; `accept` narrows it, e.g. `image/*`."""
    schema: dict[str, Any] = {"type": "object", "format": FILE_FORMAT}
    if accept:
        schema["contentMediaType"] = accept
    return schema


def is_file_schema(schema: Any) -> bool:
    """True for a file field's schema, or a list of files."""
    if not isinstance(schema, dict):
        return False
    if schema.get("format") == FILE_FORMAT:
        return True
    return schema.get("type") == "array" and is_file_schema(schema.get("items"))


def accepts(schema: Any) -> str | None:
    """The media types a file field takes (`image/*`), None for any file."""
    if not isinstance(schema, dict):
        return None
    if schema.get("type") == "array":
        return accepts(schema.get("items"))
    value = schema.get("contentMediaType")
    return value if isinstance(value, str) and value else None


def is_file_ref(value: Any) -> bool:
    return isinstance(value, dict) and value.get("kind") == "file"


def file_refs(value: Any) -> list[dict[str, Any]]:
    """The file references in a field's value: the value itself, or the references of a list."""
    if is_file_ref(value):
        return [value]
    if isinstance(value, list):
        return [v for v in value if is_file_ref(v)]
    return []


def is_image(media_type: str | None) -> bool:
    return bool(media_type) and str(media_type).split(";")[0].strip().lower() in IMAGE_TYPES


def type_matches(media_type: str, accept: str | None) -> bool:
    """`image/png` against `image/*`, `application/pdf` or a comma-separated list of either."""
    if not accept:
        return True
    media_type = media_type.lower()
    for want in (a.strip().lower() for a in accept.split(",")):
        if want == media_type or (want.endswith("/*") and media_type.startswith(want[:-1])) or want == "*/*":
            return True
    return False
