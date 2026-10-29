"""/v1 knowledge bases (S24 Knowledge Studio): documents a workflow searches with the Search knowledge block."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.api.audit import note
from agentcanvas.api.deps import CurrentUser, current_user, get_session
from agentcanvas.db.models import KnowledgeBase, KnowledgeDocument
from agentcanvas.db.repositories import ProjectRepo
from agentcanvas.errors import InvalidInput
from agentcanvas.services import model_defaults
from agentcanvas.services.knowledge import (
    MAX_FILE_BYTES,
    Chunking,
    Embedding,
    KnowledgeService,
    builtin_only,
    knowledge_embedder,
)

router = APIRouter(tags=["knowledge"])
MAX_FILES = 20


def svc(request: Request, session: AsyncSession = Depends(get_session)) -> KnowledgeService:
    projects = getattr(request.app.state, "projects", None)
    embedder = knowledge_embedder(projects.adapter) if projects is not None else builtin_only
    secrets = projects.secrets if projects is not None else {}
    return KnowledgeService(session, embedder=embedder, secrets=lambda p: secrets.get(p, {}))


class KnowledgeBaseIn(BaseModel):
    name: str = Field(default="", max_length=64, description="lower-case letters, digits, '-', '_'")
    description: str = Field(default="", max_length=2000)
    embedding: Embedding | None = None
    chunking: Chunking | None = None
    sample: Literal["handbook"] | None = Field(default=None, description="load a sample knowledge base instead")


class KnowledgeBasePatch(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    description: str | None = Field(default=None, max_length=2000)
    embedding: Embedding | None = None
    chunking: Chunking | None = None


class RecrawlIn(BaseModel):
    every_hours: int | None = Field(
        default=None, ge=1, le=24 * 90, description="fetch the web pages again this often; null turns it off"
    )


class RecrawlResult(BaseModel):
    at: datetime
    added: int
    updated: int
    removed: int
    unchanged: int
    failed: list[dict[str, str]] = Field(description="[{url, error}]: pages that could not be fetched or added")


class RecrawlOut(BaseModel):
    every_hours: int | None
    sitemaps: list[str]
    last: RecrawlResult | None


class KnowledgeBaseOut(BaseModel):
    id: str
    project_id: str
    name: str
    description: str
    embedding: dict[str, Any]
    chunking: Chunking
    stale: bool = Field(description="settings changed since the documents were indexed: reindex")
    version: int
    recrawl: RecrawlOut
    documents: int
    chunks: int
    created_at: datetime
    updated_at: datetime


class DocumentOut(BaseModel):
    id: str
    name: str
    source: str = Field(description="upload | url | text")
    url: str | None
    chunks: int
    characters: int
    created_at: datetime


class ChunkOut(BaseModel):
    id: str
    ordinal: int
    text: str
    page: int | None
    heading: str | None


class UrlIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class SitemapIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000, description="a sitemap.xml (or a sitemap index) on a public host")


class TextIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1)


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    mode: Literal["hybrid", "semantic", "keyword"] = "hybrid"
    top_k: int = Field(default=5, ge=1, le=50)
    min_score: float = Field(default=0.0, ge=0, le=1, description="0-1 in every mode (see the guide)")


class SearchResult(BaseModel):
    chunk_id: str
    doc_id: str | None
    title: str | None
    source: str | None
    text: str
    page: int | None
    heading: str | None
    score: float
    cite: int


class SearchOut(BaseModel):
    results: list[SearchResult]


def _recrawl(kb: KnowledgeBase) -> RecrawlOut:
    cfg = kb.recrawl if isinstance(kb.recrawl, dict) else {}
    last = cfg.get("last")
    return RecrawlOut(
        every_hours=cfg.get("every_hours"), sitemaps=list(cfg.get("sitemaps") or {}),
        last=RecrawlResult.model_validate(last) if last else None,
    )  # fmt: skip


def _out(kb: KnowledgeBase, documents: int, chunks: int) -> KnowledgeBaseOut:
    return KnowledgeBaseOut(
        id=kb.id, project_id=kb.project_id, name=kb.name, description=kb.description, embedding=kb.embedding,
        chunking=Chunking.model_validate(kb.chunking), stale=kb.stale, version=kb.version, recrawl=_recrawl(kb),
        documents=documents,
        chunks=chunks, created_at=kb.created_at, updated_at=kb.updated_at,
    )  # fmt: skip


async def _full(s: KnowledgeService, kb: KnowledgeBase) -> KnowledgeBaseOut:
    return _out(kb, *(await s.counts(kb.id)))


def _doc(d: KnowledgeDocument) -> DocumentOut:
    return DocumentOut(
        id=d.id, name=d.name, source=d.source, url=d.url, chunks=d.chunks, characters=len(d.content),
        created_at=d.created_at,
    )  # fmt: skip


@router.get("/projects/{project_id}/knowledge", response_model=list[KnowledgeBaseOut])
async def list_knowledge_bases(project_id: str, s: KnowledgeService = Depends(svc)) -> list[KnowledgeBaseOut]:
    return [_out(kb, n, c) for kb, n, c in await s.list(project_id)]


async def _project_settings(s: KnowledgeService, project_id: str) -> dict:
    return (await ProjectRepo(s.s).require(project_id)).settings or {}


def _on_project_server(embedding: Embedding | None, project_settings: dict) -> Embedding | None:
    """An embedding model of the provider the project's model is on (an LM Studio embedding model beside an LM Studio
    chat model) is reached at the same address with the same key, unless it names its own."""
    default = project_settings.get("default_model") or {}
    if embedding is None or embedding.base_url or embedding.api_key:
        return embedding
    if embedding.model.partition(":")[0] != str(default.get("model", "")).partition(":")[0]:
        return embedding
    return embedding.model_copy(update={"base_url": default.get("base_url"), "api_key": default.get("api_key")})


@router.post("/projects/{project_id}/knowledge", response_model=KnowledgeBaseOut, status_code=201)
async def create_knowledge_base(
    project_id: str,
    body: KnowledgeBaseIn,
    request: Request,
    s: KnowledgeService = Depends(svc),
    user: CurrentUser = Depends(current_user),
) -> KnowledgeBaseOut:
    """A new, empty knowledge base, or with `sample` the sample one (its documents already added)."""
    if body.sample:
        kb = await s.sample(project_id, body.sample, user.id)
    else:
        embedding = body.embedding
        project_settings = await _project_settings(s, project_id)
        if embedding is None and (own := model_defaults.stored(project_settings).get("embedding")):
            embedding = Embedding(model=own)  # the project's embedding model (Settings > Models)
        embedding = _on_project_server(embedding, project_settings)
        kb = await s.create(
            project_id, body.name, description=body.description, embedding=embedding, chunking=body.chunking,
            user_id=user.id,
        )  # fmt: skip
    note(request, knowledge_base=kb.name)
    return await _full(s, kb)


@router.get("/knowledge/{kb_id}", response_model=KnowledgeBaseOut)
async def get_knowledge_base(kb_id: str, s: KnowledgeService = Depends(svc)) -> KnowledgeBaseOut:
    return await _full(s, await s.get(kb_id))


@router.patch("/knowledge/{kb_id}", response_model=KnowledgeBaseOut)
async def update_knowledge_base(
    kb_id: str, body: KnowledgeBasePatch, s: KnowledgeService = Depends(svc)
) -> KnowledgeBaseOut:
    """A new embedding model or chunking marks the knowledge base stale until it is reindexed."""
    embedding = body.embedding
    if embedding is not None:
        embedding = _on_project_server(embedding, await _project_settings(s, (await s.get(kb_id)).project_id))
    kb = await s.update(
        kb_id, name=body.name, description=body.description, embedding=embedding, chunking=body.chunking
    )
    return await _full(s, kb)


@router.delete("/knowledge/{kb_id}", status_code=204)
async def delete_knowledge_base(kb_id: str, s: KnowledgeService = Depends(svc)) -> Response:
    await s.delete(kb_id)
    return Response(status_code=204)


@router.post("/knowledge/{kb_id}/reindex", response_model=KnowledgeBaseOut)
async def reindex_knowledge_base(kb_id: str, s: KnowledgeService = Depends(svc)) -> KnowledgeBaseOut:
    """Chunk and embed every document again with the knowledge base's current settings."""
    return await _full(s, await s.reindex(kb_id))


@router.get("/knowledge/{kb_id}/documents", response_model=list[DocumentOut])
async def list_documents(kb_id: str, s: KnowledgeService = Depends(svc)) -> list[DocumentOut]:
    return [_doc(d) for d in await s.documents(kb_id)]


@router.post("/knowledge/{kb_id}/documents", response_model=list[DocumentOut], status_code=201)
async def upload_documents(
    kb_id: str,
    request: Request,
    files: Annotated[list[UploadFile], File(description="PDF, Word, HTML, Markdown, text, CSV or JSON files")],
    s: KnowledgeService = Depends(svc),
    user: CurrentUser = Depends(current_user),
) -> list[DocumentOut]:
    """Upload files (multipart field `files`, up to 20 at a time, 20 MB each), in one transaction: a file that
    cannot be read or added (unreadable, the same text twice, too many chunks) adds none of them."""
    if len(files) > MAX_FILES:
        raise InvalidInput(f"upload at most {MAX_FILES} files at a time")
    await s.get(kb_id)
    read: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise InvalidInput(f"'{f.filename}' is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
        read.append((f.filename or "document.txt", data))
    added = await s.add_files(kb_id, read, user.id)
    note(request, documents=[d.name for d in added])
    return [_doc(d) for d in added]


@router.post("/knowledge/{kb_id}/documents/url", response_model=DocumentOut, status_code=201)
async def add_url(
    kb_id: str,
    body: UrlIn,
    request: Request,
    s: KnowledgeService = Depends(svc),
    user: CurrentUser = Depends(current_user),
) -> DocumentOut:
    """Add a public web page or document by its address (private and internal addresses are refused)."""
    doc = await s.add_url(kb_id, body.url, user.id, transport=getattr(request.app.state, "knowledge_transport", None))
    note(request, url=doc.url)
    return _doc(doc)


@router.post("/knowledge/{kb_id}/documents/sitemap", response_model=RecrawlResult, status_code=201)
async def add_sitemap(
    kb_id: str,
    body: SitemapIn,
    request: Request,
    s: KnowledgeService = Depends(svc),
    user: CurrentUser = Depends(current_user),
) -> RecrawlResult:
    """Add every page of a sitemap (up to 500, on the sitemap's own host) and keep following it: a re-crawl adds
    its new pages and removes the ones that left it. Private and internal addresses are refused."""
    done = await s.add_sitemap(
        kb_id, body.url, user.id, transport=getattr(request.app.state, "knowledge_transport", None)
    )
    note(request, sitemap=body.url, added=done["added"])
    return RecrawlResult.model_validate(done)


@router.put("/knowledge/{kb_id}/recrawl", response_model=KnowledgeBaseOut)
async def set_recrawl(kb_id: str, body: RecrawlIn, s: KnowledgeService = Depends(svc)) -> KnowledgeBaseOut:
    """Fetch the knowledge base's web pages and sitemaps again every `every_hours` hours (null: never)."""
    return await _full(s, await s.set_recrawl(kb_id, body.every_hours))


@router.post("/knowledge/{kb_id}/recrawl", response_model=RecrawlResult)
async def recrawl_now(
    kb_id: str,
    request: Request,
    s: KnowledgeService = Depends(svc),
    user: CurrentUser = Depends(current_user),
) -> RecrawlResult:
    """Fetch the web pages and sitemaps now: changed pages are replaced, new sitemap pages added, gone ones removed."""
    done = await s.recrawl(kb_id, user.id, transport=getattr(request.app.state, "knowledge_transport", None))
    return RecrawlResult.model_validate(done)


@router.post("/knowledge/{kb_id}/documents/text", response_model=DocumentOut, status_code=201)
async def add_text(
    kb_id: str, body: TextIn, s: KnowledgeService = Depends(svc), user: CurrentUser = Depends(current_user)
) -> DocumentOut:
    return _doc(await s.add(kb_id, title=body.title, text=body.text, kind="text", user_id=user.id))


@router.delete("/knowledge/{kb_id}/documents/{doc_id}", status_code=204)
async def delete_document(kb_id: str, doc_id: str, s: KnowledgeService = Depends(svc)) -> Response:
    await s.remove(kb_id, doc_id)
    return Response(status_code=204)


@router.get("/knowledge/{kb_id}/documents/{doc_id}/chunks", response_model=list[ChunkOut])
async def list_chunks(kb_id: str, doc_id: str, s: KnowledgeService = Depends(svc)) -> list[ChunkOut]:
    """How the document was split (the first 200 chunks)."""
    return [
        ChunkOut(id=c.id, ordinal=c.ordinal, text=c.text, page=c.page, heading=c.heading)
        for c in await s.chunks(kb_id, doc_id)
    ]


@router.post("/knowledge/{kb_id}/search", response_model=SearchOut)
async def search_knowledge(kb_id: str, body: SearchIn, s: KnowledgeService = Depends(svc)) -> SearchOut:
    """What a Search knowledge block with these settings would find."""
    kb = await s.get(kb_id)
    found = await s.search(kb, body.query, mode=body.mode, top_k=body.top_k, min_score=body.min_score)
    return SearchOut(results=[SearchResult(chunk_id=r.pop("id"), **r) for r in found])
