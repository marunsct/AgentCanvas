"""Knowledge bases (S24 Knowledge Studio): documents a workflow searches with the Search knowledge block.

A knowledge base belongs to a project and holds documents: uploaded files (PDF, Word, HTML, Markdown, text, CSV,
JSON), public web pages, or pasted text. Each document's text is kept, split into chunks (by paragraph, sentence,
fixed windows or markdown headings) and each chunk embedded, with the built-in offline embedder or a provider
model. Search ranks chunks by keyword (BM25), meaning (cosine) or both (hybrid, reciprocal-rank fusion); the math
is in `agentcanvas.knowledge_search`, shared with exported projects.

On Postgres with pgvector (G-6) the chunks are searched in the database: by meaning with an HNSW index per embedding
dimension (cosine), by keywords with full-text search (an IDF-weighted `ts_rank`, BM25-like), and hybrid fuses the
two rankings as in process. Scores and citations are the same as the in-process engine's, which SQLite (and Postgres
without pgvector) keeps using: there every change bumps the knowledge base's `version`, so each instance's in-memory
index knows when to reload. Changing the embedding model or chunking marks the knowledge base stale until it is
reindexed.

Web pages and sitemaps can be re-crawled (G-6): a knowledge base with `recrawl.every_hours` has its pages fetched
again by `CrawlJob`; a page whose text changed replaces its document, pages new to a sitemap are added and pages that
left it are removed. Every fetch keeps the private-address refusal of `fetch_url`.
"""

from __future__ import annotations

import asyncio
import hashlib
import html.parser
import io
import json
import socket
from array import array
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, null, or_, select, text
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from agentcanvas import knowledge_search as ks
from agentcanvas.db.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from agentcanvas.errors import Conflict, InvalidInput, NotFound
from agentcanvas.ids import new_id
from agentcanvas.log import get_logger
from agentcanvas.security.addresses import is_public
from agentcanvas.services import ocr

log = get_logger(__name__)

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000  # extracted text per document
MAX_DOCX_UNZIPPED = 100 * 1024 * 1024  # a Word file's parts, uncompressed: refuses zip bombs before parsing
MAX_CHUNKS = 50_000  # per knowledge base searched in process (SQLite, Postgres without pgvector): held in memory
# per knowledge base searched in Postgres: keyword search under row-level security cannot use the GIN index (Postgres
# lets only leakproof operators reach an index there, and `@@` is not one), so it filters the base's own rows, about
# a second per million chunks; the HNSW index of a million 1536-d vectors is about 6 GB
MAX_CHUNKS_PG = 1_000_000
# dimension -> the type its HNSW index casts to (migration e1b5c9d3f7a2); other dimensions are searched exactly
ANN_INDEXES: dict[int, str] = {
    384: "vector", 512: "vector", 768: "vector", 1024: "vector", 1536: "vector", 3072: "halfvec",
}  # fmt: skip
MAX_TERMS = 32  # query words a keyword search in Postgres weighs
MAX_SITEMAP_PAGES = 500  # pages one sitemap (with the sitemaps its index lists) adds
MAX_SITEMAPS_PER_INDEX = 10
CRAWL_CONCURRENCY = 4  # pages fetched at once while re-crawling
MAX_RECRAWL_HOURS = 24 * 90
MAX_PAGE_BYTES = 5 * 1024 * 1024
URL_TIMEOUT_S = 20.0
EMBED_BATCH = 64
INDEX_CACHE = 16  # knowledge bases held in memory per instance
INDEX_CACHE_BYTES = 256 * 2**20  # and about this much of their text and vectors at most; a bigger one is not kept
EXPORT_BATCH = 1000  # chunks read at a time by an export
SAMPLES = Path(__file__).resolve().parent.parent / "registry" / "knowledge"
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".csv", ".json", ".jsonl", ".rst", ".log", ".yaml", ".yml"}
SUFFIXES = TEXT_SUFFIXES | {".pdf", ".docx", ".html", ".htm"} | ocr.IMAGE_SUFFIXES

Embed = Callable[[Sequence[str]], Awaitable[list[list[float]]]]
EmbedderFactory = Callable[[dict[str, Any], dict[str, str]], Embed]


class Chunking(BaseModel):
    strategy: Literal["paragraphs", "sentences", "fixed", "markdown"] = "markdown"
    size: int = Field(default=800, ge=100, le=8000, description="characters per chunk, about")
    overlap: int = Field(default=100, ge=0, le=2000, description="fixed windows only: characters shared")


class Embedding(BaseModel):
    model: str = Field(default=ks.BUILTIN, description="'builtin' (offline) or '<provider>:<model>'")
    base_url: str | None = None
    api_key: dict[str, str] | None = Field(default=None, description='{"$secret": "NAME"}: a project secret')


async def builtin_embed(texts: Sequence[str]) -> list[list[float]]:
    return await asyncio.to_thread(ks.builtin_embed, texts)


def builtin_only(spec: dict[str, Any], secrets: dict[str, str]) -> Embed:
    if (spec.get("model") or ks.BUILTIN) != ks.BUILTIN:
        raise InvalidInput(f"embedding model '{spec.get('model')}' is not available on this server")
    return builtin_embed


def knowledge_embedder(adapter: Any) -> EmbedderFactory:
    """The host framework adapter's embedding models when it has them (LangChain's `init_embeddings`), else the
    built-in embedder only."""
    return getattr(adapter, "embedder", None) or builtin_only


# ---------------------------------------------------------------- extraction ----
class _Html(html.parser.HTMLParser):
    """Visible text of an HTML page, one block per line; the <title> is kept apart. Open elements are kept as a
    stack, so an element left unclosed (valid HTML5 drops </head>, and pages forget </nav>) ends where its parent
    does, and a skipped one never swallows the rest of the page (I-5)."""

    SKIP: ClassVar[set[str]] = {"script", "style", "noscript", "template", "svg", "head", "nav", "footer"}
    BLOCK: ClassVar[set[str]] = {"p", "div", "section", "article", "li", "tr", "br", "pre", "td"} | {
        f"h{i}" for i in range(1, 7)
    }
    VOID: ClassVar[set[str]] = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr",
    }  # fmt: skip
    # what <head> may hold: anything else starts the body, as browsers read it (the end tag is optional)
    HEAD_CONTENT: ClassVar[set[str]] = {"title", "meta", "link", "style", "script", "noscript", "base", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._open: list[str] = []
        self._skip = 0  # skipped elements among the open ones
        self._in_title = False

    def _close(self, tag: str) -> None:
        """Ends the latest open `tag` and every element left open inside it."""
        for i in range(len(self._open) - 1, -1, -1):
            if self._open[i] == tag:
                self._skip -= sum(t in self.SKIP for t in self._open[i:])
                del self._open[i:]
                return

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if "head" in self._open and (tag == "body" or tag not in self.HEAD_CONTENT):
            self._close("head")
        if tag == "title":
            self._in_title = True
            return
        if tag not in self.VOID:
            self._open.append(tag)
            self._skip += tag in self.SKIP
        if tag in self.BLOCK and not self._skip:
            self.parts.append("\n\n" + ("#" * int(tag[1]) + " " if tag[0] == "h" and tag[1:].isdigit() else ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
            return
        if tag in self.BLOCK and not self._skip:
            self.parts.append("\n\n")
        self._close(tag)

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self.parts).split("\n")]
        out: list[str] = []
        for line in lines:
            if line or (out and out[-1]):
                out.append(line)
        return "\n".join(out).strip()


def html_text(data: str) -> tuple[str, str]:
    """(title, text) of an HTML document."""
    p = _Html()
    p.feed(data)
    p.close()
    return " ".join(p.title.split()), p.text()


def _ocr_blank_pages(name: str, data: bytes, pages: list[str]) -> list[str]:
    """The pages of a PDF with a picture-only page (a scan) read by OCR, when Tesseract is there (N-1)."""
    blank = [i for i, t in enumerate(pages) if len(t) < ocr.MIN_PAGE_CHARS]
    if not blank:
        return pages
    eng = ocr.engine()
    if eng is None:
        return pages
    read = ocr.read_pdf_pages(eng, data, blank)
    out = [read.get(i, t) if len(t) < ocr.MIN_PAGE_CHARS and read.get(i) else t for i, t in enumerate(pages)]
    if len(blank) > eng.max_pages:
        out[-1] += (
            f"\n\n[OCR read {eng.max_pages} of the {len(blank)} pages without text in '{name}'; the rest are left out]"
        )
    return out


def extract(name: str, data: bytes) -> tuple[str, str]:
    """(title, text) of an uploaded file; PDF pages are separated by form feeds (\\f)."""
    suffix = Path(name).suffix.lower()
    stem = Path(name).stem
    if suffix not in SUFFIXES:
        raise InvalidInput(f"'{name}': unsupported file type; use one of {', '.join(sorted(SUFFIXES))}")
    if len(data) > MAX_FILE_BYTES:
        raise InvalidInput(f"'{name}' is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    try:
        if suffix in ocr.IMAGE_SUFFIXES:
            eng = ocr.engine()
            if eng is None:
                raise InvalidInput(
                    f"'{name}' is a picture; reading it needs OCR (Tesseract), which is not available here"
                )
            return stem, ocr.read_file(eng, data)
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            pages = [(page.extract_text() or "").strip() for page in reader.pages]
            text = "\f".join(_ocr_blank_pages(name, data, pages))
            meta_title = (reader.metadata.title if reader.metadata else None) or ""
            return (meta_title.strip() or stem), text
        if suffix == ".docx":
            import zipfile

            import docx

            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if sum(i.file_size for i in z.infolist()) > MAX_DOCX_UNZIPPED:
                    raise InvalidInput(
                        f"'{name}' unpacks to more than {MAX_DOCX_UNZIPPED // (1024 * 1024)} MB; it cannot be read"
                    )
            d = docx.Document(io.BytesIO(data))
            lines = []
            for para in d.paragraphs:
                style = (para.style.name if para.style is not None else "") or ""
                level = style.removeprefix("Heading ").strip()
                prefix = "#" * int(level) + " " if style.startswith("Heading ") and level.isdigit() else ""
                lines.append(prefix + para.text)
            for table in d.tables:
                for row in table.rows:
                    lines.append(" | ".join(cell.text for cell in row.cells))
            return (d.core_properties.title or stem), "\n\n".join(line for line in lines if line.strip())
    except InvalidInput:
        raise
    except Exception as exc:  # noqa: BLE001 - any parser failure is the file's problem
        raise InvalidInput(f"'{name}' could not be read: {exc}") from None
    if b"\x00" in data[:8192]:
        raise InvalidInput(f"'{name}' looks like a binary file, not text")
    text = data.decode("utf-8", errors="replace")
    if suffix in (".html", ".htm"):
        title, body = html_text(text)
        return (title or stem), body
    if suffix in (".md", ".markdown"):
        first = text.lstrip().split("\n", 1)[0]
        if first.startswith("# "):
            return first[2:].strip(), text
    return stem, text


# ------------------------------------------------------------------ web pages ----
async def public_address(host: str, port: int) -> str:
    """One public address `host` resolves to; private, loopback, link-local (cloud metadata) and reserved
    addresses are refused. The caller connects to this very address, so DNS cannot rebind it."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise InvalidInput(f"cannot resolve '{host}': {exc}") from None
    addresses = [str(info[4][0]) for info in infos]
    for a in addresses:
        if not is_public(a):  # NAT64 and other IPv6 forms of a private IPv4 address too (RT-15)
            raise InvalidInput(f"'{host}' resolves to a private or internal address; only public pages can be added")
    return addresses[0]


async def _get(url: str, transport: Any = None) -> tuple[str, str, str | None, bytes]:
    """(final url, content type, encoding, body) of a public address. At most 5 redirects, each re-checked; at most
    5 MB. Private, internal and cloud-metadata addresses are refused, also after a redirect."""
    import httpx

    current = url
    for _ in range(6):
        parts = urlsplit(current)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise InvalidInput(f"'{current}' is not an http(s) address")
        headers = {"User-Agent": "AgentCanvas-Knowledge/1.0", "Accept": "text/html,text/plain,application/pdf,*/*"}
        target, extensions = current, {}
        if transport is None:  # tests serve pages from a mock transport
            port = parts.port or (443 if parts.scheme == "https" else 80)
            ip = await public_address(parts.hostname, port)
            headers["Host"] = parts.netloc.rsplit("@", 1)[-1]
            extensions["sni_hostname"] = parts.hostname
            target = parts._replace(netloc=f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}").geturl()
        async with (
            httpx.AsyncClient(timeout=URL_TIMEOUT_S, transport=transport) as client,
            client.stream("GET", target, headers=headers, extensions=extensions or None) as r,
        ):
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                current = urljoin(current, r.headers["location"])
                continue
            if r.status_code >= 400:
                raise InvalidInput(f"{parts.hostname} answered HTTP {r.status_code}")
            body = bytearray()
            async for piece in r.aiter_bytes():
                body.extend(piece)
                if len(body) > MAX_PAGE_BYTES:
                    raise InvalidInput(f"'{current}' is larger than {MAX_PAGE_BYTES // (1024 * 1024)} MB")
            kind = r.headers.get("content-type", "").split(";")[0].strip().lower()
            return current, kind, r.encoding, bytes(body)
    raise InvalidInput(f"'{url}' redirects too many times")


async def fetch_url(url: str, transport: Any = None) -> tuple[str, str, str]:
    """(final url, title, text) of a public web page or file. At most 5 redirects, each re-checked; at most
    5 MB; HTML is reduced to its visible text, PDF and Word files are extracted."""
    current, kind, encoding, body = await _get(url, transport)
    parts = urlsplit(current)
    name = Path(parts.path).name or parts.hostname or "page"
    if kind == "application/pdf" or name.lower().endswith(".pdf"):
        title, text = await asyncio.to_thread(extract, name if name.lower().endswith(".pdf") else "page.pdf", body)
    elif kind.endswith("wordprocessingml.document"):
        title, text = await asyncio.to_thread(extract, "page.docx", body)
    elif kind in ("text/html", "application/xhtml+xml") or not kind:
        title, text = await asyncio.to_thread(html_text, body.decode(encoding or "utf-8", errors="replace"))
    elif kind.startswith("text/") or kind in ("application/json",):
        title, text = name, body.decode(encoding or "utf-8", errors="replace")
    else:
        raise InvalidInput(f"'{current}' is {kind}, not a page or document")
    return current, (title or parts.hostname or name), text


def sitemap_urls(xml: bytes) -> tuple[str, list[str]]:
    """("urlset" | "sitemapindex", the `<loc>` addresses) of a sitemap file. Documents that declare entities or a
    DTD are refused (entity expansion)."""
    import xml.etree.ElementTree as ET

    head = xml.lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise InvalidInput("the sitemap declares a DTD or entities; they are not read")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise InvalidInput(f"the sitemap is not valid XML: {exc}") from None
    kind = root.tag.rsplit("}", 1)[-1]
    if kind not in ("urlset", "sitemapindex"):
        raise InvalidInput("not a sitemap: its root element should be <urlset> or <sitemapindex>")
    locs = [
        (e.text or "").strip()
        for e in root.iter()
        if e.tag.rsplit("}", 1)[-1] == "loc" and (e.text or "").strip()
    ]  # fmt: skip
    return kind, locs


async def fetch_sitemap(url: str, transport: Any = None) -> list[str]:
    """The page addresses of a sitemap, or of the sitemaps a sitemap index lists (one level), in order and without
    repeats: only http(s) addresses on the sitemap's own host, at most `MAX_SITEMAP_PAGES`. The sitemap is fetched
    like a page, so private addresses are refused."""
    final, _, _, body = await _get(url, transport)
    host = urlsplit(final).hostname
    kind, locs = sitemap_urls(body)
    if kind == "sitemapindex":
        pages: list[str] = []
        for child in [u for u in locs if urlsplit(u).hostname == host][:MAX_SITEMAPS_PER_INDEX]:
            _, _, _, data = await _get(child, transport)
            sub_kind, sub = sitemap_urls(data)
            if sub_kind == "urlset":
                pages.extend(sub)
        locs = pages
    seen: dict[str, None] = {}
    for u in locs:
        parts = urlsplit(u)
        if parts.scheme in ("http", "https") and parts.hostname == host:
            seen.setdefault(u, None)
    return list(seen)[:MAX_SITEMAP_PAGES]


# ------------------------------------------------------------- vector bytes ----
def pack(vector: Sequence[float]) -> bytes:
    a = array("f", vector)
    if a.itemsize != 4:  # pragma: no cover - every supported platform has 32-bit floats
        raise RuntimeError("float32 arrays needed")
    return a.tobytes()


def compact(data: bytes | None) -> array[float] | None:
    """A stored vector as a float32 array: a quarter of a list of Python floats in memory (I-6)."""
    if not data:
        return None
    a = array("f")
    a.frombytes(data)
    return a


def unpack(data: bytes | None) -> list[float] | None:
    if not data:
        return None
    a = array("f")
    a.frombytes(data)
    return a.tolist()


def nearest_sql(dims: int) -> str:
    """The query for the chunks of a knowledge base nearest to `:q` (cosine): its expression is the one the HNSW
    index of `dims` dimensions is built on, so Postgres can use it."""
    kind = ANN_INDEXES.get(dims)
    column, q = (
        (f"CAST(embedding AS {kind}({dims}))", f"CAST(:q AS {kind}({dims}))")
        if kind
        else ("embedding", "CAST(:q AS vector)")
    )
    return (
        f"SELECT id, 1 - ({column} <=> {q}) AS score FROM knowledge_chunk WHERE kb_id = :kb AND dims = {int(dims)} "
        f"ORDER BY {column} <=> {q} LIMIT :k"
    )


def literal(vector: Sequence[float]) -> str:
    """A vector as pgvector's text input, `[0.1,0.2]`."""
    return json.dumps([float(v) for v in vector], separators=(",", ":"))


# ---------------------------------------------------------------- the index ----
_INDEX: OrderedDict[str, tuple[int, ks.Index, int]] = OrderedDict()  # kb id -> (version, index, its size in bytes)
_PGVECTOR: dict[str, tuple[int, ...] | None] = {}  # database -> pgvector's version, when the chunks are searched there
_COMPARABLE: OrderedDict[tuple[str, int, int], bool] = OrderedDict()  # (kb id, version, dims) -> one model's vectors
_INSERT = text(
    "INSERT INTO knowledge_chunk (id, kb_id, doc_id, ordinal, text, page, heading, dims, embedding, tsv) VALUES "
    "(:id, :kb_id, :doc_id, :ordinal, :text, :page, :heading, :dims, CAST(:embedding AS vector), "
    "to_tsvector('simple', concat_ws(' ', CAST(:title AS text), CAST(:heading AS text), CAST(:text AS text))))"
)


def forget(kb_id: str) -> None:
    _INDEX.pop(kb_id, None)


class KnowledgeService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        embedder: EmbedderFactory = builtin_only,
        secrets: Callable[[str], dict[str, str]] = lambda _: {},
    ):
        self.s = session
        self.embedder = embedder
        self.secrets = secrets

    # ------------------------------------------------------------ knowledge bases ----
    async def list(self, project_id: str) -> list[tuple[KnowledgeBase, int, int]]:
        """(knowledge base, documents, chunks) for each knowledge base of a project, counted in one query."""
        counts = (
            select(
                KnowledgeDocument.kb_id,
                func.count().label("documents"),
                func.coalesce(func.sum(KnowledgeDocument.chunks), 0).label("chunks"),
            )
            .where(KnowledgeDocument.kb_id.in_(select(KnowledgeBase.id).where(KnowledgeBase.project_id == project_id)))
            .group_by(KnowledgeDocument.kb_id)
            .subquery()
        )
        q = (
            select(KnowledgeBase, counts.c.documents, counts.c.chunks)
            .outerjoin(counts, counts.c.kb_id == KnowledgeBase.id)
            .where(KnowledgeBase.project_id == project_id)
            .order_by(KnowledgeBase.name)
        )
        return [(k, int(n or 0), int(c or 0)) for k, n, c in (await self.s.execute(q)).all()]

    async def counts(self, kb_id: str) -> tuple[int, int]:
        row = (
            await self.s.execute(
                select(func.count(), func.coalesce(func.sum(KnowledgeDocument.chunks), 0)).where(
                    KnowledgeDocument.kb_id == kb_id
                )
            )
        ).one()
        return int(row[0]), int(row[1])

    async def get(self, kb_id: str) -> KnowledgeBase:
        kb = await self.s.get(KnowledgeBase, kb_id)
        if kb is None:
            raise NotFound(f"knowledge base '{kb_id}' not found")
        return kb

    async def find(self, project_id: str, name: str) -> KnowledgeBase | None:
        return await self.s.scalar(
            select(KnowledgeBase).where(KnowledgeBase.project_id == project_id, KnowledgeBase.name == name)
        )

    async def by_name(self, project_id: str, name: str) -> KnowledgeBase:
        kb = await self.find(project_id, name)
        if kb is None:
            raise NotFound(f"knowledge base '{name}' not found in this project; add it on the Knowledge page")
        return kb

    async def create(
        self,
        project_id: str,
        name: str,
        *,
        description: str = "",
        embedding: Embedding | None = None,
        chunking: Chunking | None = None,
        user_id: str | None = None,
    ) -> KnowledgeBase:
        if not ks.NAME.match(name):
            raise InvalidInput("a knowledge base name is lower-case letters, digits, '-' and '_' (e.g. 'handbook')")
        if await self.s.scalar(
            select(KnowledgeBase.id).where(KnowledgeBase.project_id == project_id, KnowledgeBase.name == name)
        ):
            raise Conflict(f"this project already has a knowledge base named '{name}'")
        embedding = embedding or Embedding()
        self._embedder(embedding.model_dump(exclude_none=True), project_id)  # an unknown model fails now
        kb = KnowledgeBase(
            id=new_id("kb"), project_id=project_id, name=name, description=description,
            embedding=embedding.model_dump(exclude_none=True), chunking=(chunking or Chunking()).model_dump(),
            created_by=user_id,
        )  # fmt: skip
        self.s.add(kb)
        await self.s.commit()
        return kb

    async def update(
        self,
        kb_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        embedding: Embedding | None = None,
        chunking: Chunking | None = None,
    ) -> KnowledgeBase:
        """Rename or reconfigure; a new embedding model or chunking marks it stale until it is reindexed."""
        kb = await self.get(kb_id)
        if name is not None and name != kb.name:
            if not ks.NAME.match(name):
                raise InvalidInput("a knowledge base name is lower-case letters, digits, '-' and '_'")
            if await self.s.scalar(
                select(KnowledgeBase.id).where(KnowledgeBase.project_id == kb.project_id, KnowledgeBase.name == name)
            ):
                raise Conflict(f"this project already has a knowledge base named '{name}'")
            kb.name = name
        if description is not None:
            kb.description = description
        if embedding is not None and embedding.model_dump(exclude_none=True) != kb.embedding:
            self._embedder(embedding.model_dump(exclude_none=True), kb.project_id)
            kb.embedding, kb.stale = embedding.model_dump(exclude_none=True), True
        if chunking is not None and chunking.model_dump() != kb.chunking:
            kb.chunking, kb.stale = chunking.model_dump(), True
        await self._touch(kb)
        return kb

    async def delete(self, kb_id: str) -> None:
        kb = await self.get(kb_id)
        await self.s.execute(delete(KnowledgeChunk).where(KnowledgeChunk.kb_id == kb_id))
        await self.s.execute(delete(KnowledgeDocument).where(KnowledgeDocument.kb_id == kb_id))
        await self.s.delete(kb)
        await self.s.commit()
        forget(kb_id)

    async def _lock(self, kb_id: str) -> KnowledgeBase:
        """The knowledge base, its row locked until commit: adds and reindexes of one knowledge base take turns, so
        a reindex never deletes the chunks of a document added while it ran."""
        kb = await self.s.scalar(
            select(KnowledgeBase)
            .where(KnowledgeBase.id == kb_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if kb is None:
            raise NotFound(f"knowledge base '{kb_id}' not found")
        return kb

    async def _touch(self, kb: KnowledgeBase) -> None:
        """Commit, bumping `version` in SQL: an in-memory `+= 1` loses one of two concurrent changes, leaving
        search caches on the old index."""
        kb.updated_at = datetime.now(UTC)
        await self.s.flush()
        version = await self.s.scalar(
            sql_update(KnowledgeBase)
            .where(KnowledgeBase.id == kb.id)
            .values(version=KnowledgeBase.version + 1)
            .returning(KnowledgeBase.version)
            .execution_options(synchronize_session=False)
        )
        set_committed_value(kb, "version", version)
        await self.s.commit()

    # ----------------------------------------------------------------- documents ----
    async def documents(self, kb_id: str) -> list[KnowledgeDocument]:
        await self.get(kb_id)
        return list(
            (
                await self.s.scalars(
                    select(KnowledgeDocument)
                    .where(KnowledgeDocument.kb_id == kb_id)
                    .order_by(KnowledgeDocument.created_at, KnowledgeDocument.id)
                )
            ).all()
        )

    async def document(self, kb_id: str, doc_id: str) -> KnowledgeDocument:
        doc = await self.s.get(KnowledgeDocument, doc_id)
        if doc is None or doc.kb_id != kb_id:
            raise NotFound(f"document '{doc_id}' not found")
        return doc

    async def add_file(self, kb_id: str, name: str, data: bytes, user_id: str | None = None) -> KnowledgeDocument:
        return (await self.add_files(kb_id, [(name, data)], user_id))[0]

    async def add_files(
        self, kb_id: str, files: list[tuple[str, bytes]], user_id: str | None = None
    ) -> list[KnowledgeDocument]:
        """Add (name, bytes) files in one transaction: one that cannot be read or added adds none of them."""
        texts = [await asyncio.to_thread(extract, name, data) for name, data in files]  # parsers are CPU-bound
        try:
            added = [
                await self.add(kb_id, title=title, text=text, kind="upload", name=name, user_id=user_id, commit=False)
                for (name, _), (title, text) in zip(files, texts, strict=True)
            ]
            await self._touch(await self._lock(kb_id))
        except BaseException:
            await self.s.rollback()
            raise
        return added

    async def add_url(
        self, kb_id: str, url: str, user_id: str | None = None, transport: Any = None
    ) -> KnowledgeDocument:
        await self.get(kb_id)  # before going online
        final, title, text = await fetch_url(url, transport)
        return await self.add(kb_id, title=title, text=text, kind="url", name=title, url=final, user_id=user_id)

    # -------------------------------------------------------------------- re-crawl ----
    async def set_recrawl(self, kb_id: str, every_hours: int | None) -> KnowledgeBase:
        """Turn the scheduled re-crawl on (every `every_hours` hours) or off (None). The settings live in
        `recrawl` next to the sitemaps the base follows and the last result."""
        kb = await self.get(kb_id)
        if every_hours is not None and not 1 <= every_hours <= MAX_RECRAWL_HOURS:
            raise InvalidInput(f"re-crawl every 1 to {MAX_RECRAWL_HOURS} hours")
        cfg = dict(kb.recrawl or {})
        if every_hours is None:
            cfg.pop("every_hours", None)
        else:
            cfg["every_hours"] = every_hours
            if kb.crawled_at is None:  # just set up: the next crawl is one interval away
                kb.crawled_at = datetime.now(UTC)
        kb.recrawl = cfg or None
        await self.s.commit()
        return kb

    async def add_sitemap(
        self, kb_id: str, url: str, user_id: str | None = None, transport: Any = None
    ) -> dict[str, Any]:
        """Follow a sitemap: its pages are added now and kept in step by every re-crawl. Returns the crawl's summary."""
        kb = await self.get(kb_id)  # before going online
        pages = await fetch_sitemap(url, transport)
        if not pages:
            raise InvalidInput("the sitemap lists no pages")
        cfg = dict(kb.recrawl or {})
        cfg["sitemaps"] = {**(cfg.get("sitemaps") or {}), url: {}}
        kb.recrawl = cfg
        await self.s.commit()
        return await self.recrawl(kb_id, user_id, transport)

    async def recrawl(self, kb_id: str, user_id: str | None = None, transport: Any = None) -> dict[str, Any]:
        """Fetch the knowledge base's web pages again. A page whose text changed replaces its document; the pages
        of its sitemaps that are new are added and the ones that left the sitemap are removed; a page that cannot
        be fetched keeps its document and is reported. Returns and stores
        `{at, added, updated, removed, unchanged, failed: [{url, error}]}`."""
        kb = await self.get(kb_id)
        cfg = dict(kb.recrawl or {})
        sitemaps: dict[str, dict[str, str]] = {u: dict(m or {}) for u, m in (cfg.get("sitemaps") or {}).items()}
        result: dict[str, Any] = {"added": 0, "updated": 0, "removed": 0, "unchanged": 0, "failed": []}

        def failed(url: str, exc: Exception) -> None:
            result["failed"].append({"url": url, "error": str(exc)[:300]})

        # plain values, not the rows: a failed page update rolls back, which expires every loaded row (I-4)
        docs = {d.url: (d.id, d.sha256) for d in await self.documents(kb_id) if d.source == "url" and d.url}
        listed: dict[str, list[str]] = {}  # sitemap -> the addresses it lists now
        for sitemap in sitemaps:
            try:
                listed[sitemap] = await fetch_sitemap(sitemap, transport)
            except InvalidInput as exc:  # keep what it listed before
                failed(sitemap, exc)
        wanted = {  # pages still listed by a sitemap (or by one that could not be read)
            final
            for sitemap, mapping in sitemaps.items()
            for url, final in mapping.items()
            if sitemap not in listed or url in listed[sitemap]
        }
        for sitemap, pages in listed.items():  # pages that left a sitemap, before fetching anything
            for url, final in list(sitemaps[sitemap].items()):
                if url not in pages:
                    del sitemaps[sitemap][url]
                    if (gone := docs.get(final)) and final not in wanted:
                        await self.remove(kb_id, gone[0])
                        del docs[final]
                        result["removed"] += 1
        known = {u for mapping in sitemaps.values() for u in mapping}
        # the documents a sitemap added: only these follow a sitemap; a page added by hand is never adopted, so a
        # sitemap that stops listing it cannot remove it (I-17)
        adopted = {final for mapping in sitemaps.values() for final in mapping.values()}
        new_pages = [u for pages in listed.values() for u in pages if u not in known and u not in docs]
        new_pages = list(dict.fromkeys(new_pages))
        gate = asyncio.Semaphore(CRAWL_CONCURRENCY)

        async def fetch(url: str) -> tuple[str, tuple[str, str, str] | InvalidInput]:
            async with gate:
                try:
                    return url, await fetch_url(url, transport)
                except InvalidInput as exc:
                    return url, exc

        fetched = dict(await asyncio.gather(*(fetch(u) for u in [*docs, *new_pages])))

        for url, (doc_id, digest) in docs.items():  # pages already in the base: unchanged, or replaced
            got = fetched[url]
            if isinstance(got, InvalidInput):
                failed(url, got)
                continue
            _, title, text = got
            if hashlib.sha256(text.replace("\x00", "").strip().encode()).hexdigest() == digest:
                result["unchanged"] += 1
                continue
            try:
                await self.add(kb_id, title=title, text=text, kind="url", name=title, url=url, user_id=user_id,
                               commit=False)  # fmt: skip
                await self._drop(await self.document(kb_id, doc_id))
                await self._touch(await self.get(kb_id))
                result["updated"] += 1
            except (InvalidInput, Conflict) as exc:
                await self.s.rollback()
                failed(url, exc)

        for url in new_pages:  # pages new to a sitemap
            got = fetched[url]
            if isinstance(got, InvalidInput):
                failed(url, got)
                continue
            final, title, text = got
            if final in docs:  # redirects to a page the base already has: followed only if a sitemap added it
                if final in adopted:
                    for sitemap, pages in listed.items():
                        if url in pages:
                            sitemaps[sitemap][url] = final
                continue
            try:
                await self.add(kb_id, title=title, text=text, kind="url", name=title, url=final, user_id=user_id)
                result["added"] += 1
            except (InvalidInput, Conflict) as exc:
                await self.s.rollback()
                failed(url, exc)
                continue
            for sitemap, pages in listed.items():
                if url in pages:
                    sitemaps[sitemap][url] = final

        for sitemap, pages in listed.items():  # a page another sitemap added is followed by this one too
            for url in pages:
                if url not in sitemaps[sitemap] and url in docs and url in adopted:
                    sitemaps[sitemap][url] = url

        kb = await self.get(kb_id)
        cfg = dict(kb.recrawl or {})
        if sitemaps:
            cfg["sitemaps"] = sitemaps
        cfg["last"] = {"at": datetime.now(UTC).isoformat(timespec="seconds"), **result, "failed": result["failed"][:20]}
        kb.recrawl = cfg
        await self.s.commit()
        return cfg["last"]

    async def _drop(self, doc: KnowledgeDocument) -> None:
        await self.s.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc.id))
        await self.s.delete(doc)

    async def add(
        self,
        kb_id: str,
        *,
        title: str,
        text: str,
        kind: str = "text",
        name: str | None = None,
        url: str | None = None,
        user_id: str | None = None,
        commit: bool = True,
    ) -> KnowledgeDocument:
        """Add a document: keep its text, chunk and embed it. The same text twice is refused. `commit=False` leaves
        the commit (and the version bump) to the caller, which adds more in the same transaction."""
        seen = await self.get(kb_id)
        settings = (dict(seen.chunking or {}), dict(seen.embedding or {}))
        text = text.replace("\x00", "").strip()
        if not text:
            raise InvalidInput(
                f"'{name or title}' has no text to search (a scan needs OCR: install Tesseract or use the Docker image)"
            )
        if len(text) > MAX_TEXT_CHARS:
            raise InvalidInput(f"'{name or title}' has more than {MAX_TEXT_CHARS:,} characters of text; split it")
        digest = hashlib.sha256(text.encode()).hexdigest()
        doc = KnowledgeDocument(
            id=new_id("kdoc"), kb_id=kb_id, name=(name or title)[:300], source=kind, url=url, content=text,
            sha256=digest, created_by=user_id,
        )  # fmt: skip
        title = self._title(doc)[:300]  # the title search shows and cites, so the vectors match what is shown
        pieces = await asyncio.to_thread(self._chunks, seen, text, title)
        # embedding waits on a provider: done before the lock, so other adds of this base do not wait behind it (F-5)
        vectors = await self._embed(seen, [ks.embed_text({**p, "title": title}) for p in pieces])
        kb = await self._lock(kb_id)
        if (dict(kb.chunking or {}), dict(kb.embedding or {})) != settings:
            raise Conflict("the knowledge base's chunking or embedding model changed meanwhile; add the document again")
        if same := await self.s.scalar(
            select(KnowledgeDocument.name).where(KnowledgeDocument.kb_id == kb_id, KnowledgeDocument.sha256 == digest)
        ):
            raise Conflict(f"this knowledge base already has the same text ('{same}')")
        _, total = await self.counts(kb_id)
        if total + len(pieces) > (cap := await self.max_chunks()):
            raise InvalidInput(f"a knowledge base holds at most {cap:,} chunks; this adds {len(pieces)}")
        self.s.add(doc)
        await self.s.flush()
        await self._store(kb_id, doc.id, title, pieces, vectors)
        doc.chunks = len(pieces)
        if commit:
            await self._touch(kb)
        else:
            await self.s.flush()  # the next document's duplicate and size checks see this one
        return doc

    async def remove(self, kb_id: str, doc_id: str) -> None:
        kb = await self.get(kb_id)
        doc = await self.document(kb_id, doc_id)
        await self.s.execute(delete(KnowledgeChunk).where(KnowledgeChunk.doc_id == doc_id))
        await self.s.delete(doc)
        await self._touch(kb)

    async def chunks(self, kb_id: str, doc_id: str, limit: int = 200) -> list[KnowledgeChunk]:
        await self.document(kb_id, doc_id)
        return list(
            (
                await self.s.scalars(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.doc_id == doc_id)
                    .order_by(KnowledgeChunk.ordinal)
                    .limit(limit)
                )
            ).all()
        )

    async def reindex(self, kb_id: str) -> KnowledgeBase:
        """Chunk and embed every document again with the current settings."""
        kb = await self._lock(kb_id)
        docs = await self.documents(kb_id)
        await self.s.execute(delete(KnowledgeChunk).where(KnowledgeChunk.kb_id == kb_id))
        total, cap = 0, await self.max_chunks()
        for doc in docs:
            title = self._title(doc)
            pieces = await asyncio.to_thread(self._chunks, kb, doc.content, title)
            total += len(pieces)
            if total > cap:
                raise InvalidInput(f"with these settings the knowledge base needs more than {cap:,} chunks")
            vectors = await self._embed(kb, [ks.embed_text({**p, "title": title}) for p in pieces])
            await self._store(kb_id, doc.id, title, pieces, vectors)
            doc.chunks = len(pieces)
        kb.stale = False
        await self._touch(kb)
        return kb

    async def pgvector(self) -> tuple[int, ...] | None:
        """pgvector's version when this database searches the chunks itself (Postgres with the migration's
        columns), else None: the in-process engine."""
        bind = self.s.get_bind()
        if bind.dialect.name != "postgresql":
            return None
        key = str(getattr(bind, "url", None) or bind.engine.url)
        if key not in _PGVECTOR:
            row = (
                await self.s.execute(
                    text(
                        "SELECT (SELECT extversion FROM pg_extension WHERE extname = 'vector'), EXISTS (SELECT 1 FROM "
                        "information_schema.columns WHERE table_schema = current_schema() AND table_name = "
                        "'knowledge_chunk' AND column_name = 'embedding')"
                    )
                )
            ).one()
            version = tuple(int(n) for n in str(row[0]).split(".") if n.isdigit()) if row[0] and row[1] else None
            _PGVECTOR[key] = version
        return _PGVECTOR[key]

    async def max_chunks(self) -> int:
        return MAX_CHUNKS_PG if await self.pgvector() is not None else MAX_CHUNKS

    async def _store(
        self, kb_id: str, doc_id: str, title: str, pieces: list[dict[str, Any]], vectors: list[list[float]]
    ) -> None:
        """Save a document's chunks: in Postgres with the vector and the words to search, else the vector's bytes."""
        if await self.pgvector() is None:
            for i, (p, v) in enumerate(zip(pieces, vectors, strict=True)):
                self.s.add(
                    KnowledgeChunk(
                        id=f"{doc_id}_{i}", kb_id=kb_id, doc_id=doc_id, ordinal=i, text=p["text"], page=p["page"],
                        heading=p["heading"], vector=pack(v),
                    )
                )  # fmt: skip
            return
        rows = [
            {
                "id": f"{doc_id}_{i}", "kb_id": kb_id, "doc_id": doc_id, "ordinal": i, "text": p["text"],
                "page": p["page"], "heading": p["heading"], "dims": len(v), "embedding": literal(v),
                "title": title[:300],
            }
            for i, (p, v) in enumerate(zip(pieces, vectors, strict=True))
        ]  # fmt: skip
        if rows:
            await self.s.execute(_INSERT, rows)

    def _chunks(self, kb: KnowledgeBase, text: str, title: str) -> list[dict[str, Any]]:
        """The document's chunks; a heading that only repeats the title is dropped (the title is searched too)."""
        c = Chunking.model_validate(kb.chunking)
        pages = text.split("\f")
        out: list[dict[str, Any]] = []
        for n, page in enumerate(pages, start=1):
            for piece in ks.chunk_text(
                page, strategy=c.strategy, size=c.size, overlap=c.overlap, page=n if len(pages) > 1 else None
            ):
                out.append({**piece, "heading": piece["heading"] if piece["heading"] != title else None})
        return out

    @staticmethod
    def _title(doc: KnowledgeDocument) -> str:
        return title_of(doc.source, doc.name, doc.content)

    def _embedder(self, spec: dict[str, Any], project_id: str) -> Embed:
        try:
            return self.embedder(spec, self.secrets(project_id))
        except InvalidInput:
            raise
        except Exception as exc:  # noqa: BLE001 - unknown provider, missing package or key
            raise InvalidInput(f"embedding model '{spec.get('model')}' cannot be used: {exc}") from None

    async def _embed(self, kb: KnowledgeBase, texts: list[str]) -> list[list[float]]:
        embed = self._embedder(kb.embedding, kb.project_id)
        out: list[list[float]] = []
        for i in range(0, len(texts), EMBED_BATCH):
            try:
                out.extend(await embed(texts[i : i + EMBED_BATCH]))
            except InvalidInput:
                raise
            except Exception as exc:  # noqa: BLE001 - a provider error is reported, not a server error
                raise InvalidInput(f"the embedding model failed: {exc}") from None
        return out

    # -------------------------------------------------------------------- search ----
    async def _heads(self, kb_id: str) -> dict[str, tuple[str, str]]:
        """Per document: (title, source) for search results, read without the documents' whole text (I-6)."""
        rows = await self.s.execute(
            select(
                KnowledgeDocument.id, KnowledgeDocument.source, KnowledgeDocument.name, KnowledgeDocument.url,
                func.substr(KnowledgeDocument.content, 1, 2000).label("head"),
            ).where(KnowledgeDocument.kb_id == kb_id)
        )  # fmt: skip
        return {r.id: (title_of(r.source, r.name, r.head or ""), r.url or r.name) for r in rows.all()}

    async def index(self, kb: KnowledgeBase) -> ks.Index:
        cached = _INDEX.get(kb.id)
        if cached and cached[0] == kb.version:
            _INDEX.move_to_end(kb.id)
            return cached[1]
        docs = await self._heads(kb.id)
        if await self.pgvector() is None:
            rows: Sequence[Any] = (
                await self.s.scalars(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.kb_id == kb.id)
                    .order_by(KnowledgeChunk.doc_id, KnowledgeChunk.ordinal)
                )
            ).all()
            vectors: list[Any] = [compact(r.vector) for r in rows]
        else:  # the vectors are in the pgvector column
            rows = (
                await self.s.execute(
                    text(
                        "SELECT id, doc_id, text, page, heading, CAST(embedding AS text) AS embedding FROM "
                        "knowledge_chunk WHERE kb_id = :kb ORDER BY doc_id, ordinal"
                    ),
                    {"kb": kb.id},
                )
            ).all()
            vectors = [array("f", json.loads(r.embedding)) if r.embedding else None for r in rows]
        chunks = []
        for r, vector in zip(rows, vectors, strict=True):
            head = docs.get(r.doc_id)
            if head is None:
                continue
            chunks.append({
                "id": r.id, "doc_id": r.doc_id, "title": head[0], "source": head[1],
                "text": r.text, "page": r.page, "heading": r.heading, "vector": vector,
            })  # fmt: skip
        index = await asyncio.to_thread(ks.Index, chunks)
        size = sum(2 * len(c["text"]) + 4 * len(c["vector"] or ()) + 400 for c in chunks)  # roughly, with its terms
        _INDEX.pop(kb.id, None)
        if size <= INDEX_CACHE_BYTES:
            _INDEX[kb.id] = (kb.version, index, size)
            while len(_INDEX) > INDEX_CACHE or sum(x[2] for x in _INDEX.values()) > INDEX_CACHE_BYTES:
                _INDEX.popitem(last=False)
        return index

    async def search(
        self, kb: KnowledgeBase, query: str, *, mode: str = "hybrid", top_k: int = 5, min_score: float = 0.0
    ) -> list[dict[str, Any]]:
        if mode not in ks.MODES:
            raise InvalidInput(f"unknown search mode '{mode}'; use one of {', '.join(ks.MODES)}")
        if kb.stale:  # the vectors may be another model's: a query embedded with the new one would give noise
            mode = "keyword"
        if (version := await self.pgvector()) is not None:
            return await self._search_in_database(kb, query, mode, top_k, min_score, version)
        index = await self.index(kb)
        qv = None
        if mode != "keyword" and query.strip() and index.chunks:
            qv = (await self._embed(kb, [query]))[0]
        return ks.search(index, query, mode=mode, top_k=top_k, min_score=min_score, query_vector=qv)

    async def _search_in_database(
        self, kb: KnowledgeBase, query: str, mode: str, top_k: int, min_score: float, version: tuple[int, ...]
    ) -> list[dict[str, Any]]:
        """`ks.search` run by Postgres: the same modes, scores (0-1) and citations, without loading the chunks."""
        _, total = await self.counts(kb.id)
        if not total or not query.strip():
            return []
        qv = None if mode == "keyword" else (await self._embed(kb, [query]))[0]
        if qv is not None and not await self._comparable(kb, len(qv)):
            qv = None  # another model's vectors (or none): by keywords, as in process
        ranked: list[tuple[str, float]]
        if qv is None:
            ranked = await self._keyword(kb.id, query, total, top_k)
            top = ranked[0][1] if ranked else 1.0
            ranked = [(i, s / top) for i, s in ranked]
        elif mode == "semantic":
            ranked = [(i, min(s, 1.0)) for i, s in await self._nearest(kb.id, qv, top_k, version)]
        else:
            keyword = await self._keyword(kb.id, query, total, ks.FUSED)
            ranked = ks.fuse(keyword, await self._nearest(kb.id, qv, ks.FUSED, version))
        ranked = [(i, s) for i, s in ranked if s > 0 and s >= min_score][:top_k]
        found = await self._details([i for i, _ in ranked])
        return [{**found[i], "score": round(score, 4), "cite": n} for n, (i, score) in enumerate(ranked, start=1)]

    async def _comparable(self, kb: KnowledgeBase, dims: int) -> bool:
        """Whether every chunk has a vector of `dims` dimensions, i.e. from the query's model (cached per version)."""
        key = (kb.id, kb.version, dims)
        if key not in _COMPARABLE:
            other = await self.s.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM knowledge_chunk WHERE kb_id = :kb AND (dims IS NULL OR dims <> :d))"
                ),
                {"kb": kb.id, "d": dims},
            )
            _COMPARABLE[key] = not other
            while len(_COMPARABLE) > 256:
                _COMPARABLE.popitem(last=False)
        return _COMPARABLE[key]

    async def _keyword(self, kb_id: str, query: str, total: int, limit: int) -> list[tuple[str, float]]:
        """The best `limit` chunks by keywords: Postgres full-text search over the query's words (the in-process
        stop words left out), each word's `ts_rank` (length-normalised) weighted by its BM25 idf in this base."""
        terms = list(dict.fromkeys(ks.words(query)))[:MAX_TERMS]
        if not terms:
            return []
        q = [f"plainto_tsquery('simple', :t{i})" for i in range(len(terms))]
        any_term = " || ".join(q)
        df = ", ".join(f"count(*) FILTER (WHERE tsv @@ {t}) AS d{i}" for i, t in enumerate(q))
        score = " + ".join(
            f"ln(1 + (CAST(:n AS float8) - df.d{i} + 0.5) / (df.d{i} + 0.5)) * ts_rank(m.tsv, {t}, 1)"
            for i, t in enumerate(q)
        )
        sql = (
            f"WITH m AS (SELECT id, tsv FROM knowledge_chunk WHERE kb_id = :kb AND tsv @@ ({any_term})), "
            f"df AS (SELECT {df} FROM m) SELECT m.id, {score} AS score FROM m, df ORDER BY score DESC, m.id LIMIT :k"
        )
        params: dict[str, Any] = {"kb": kb_id, "n": total, "k": limit} | {f"t{i}": t for i, t in enumerate(terms)}
        return [(r.id, float(r.score)) for r in (await self.s.execute(text(sql), params)).all() if r.score > 0]

    async def _nearest(
        self, kb_id: str, vector: list[float], limit: int, version: tuple[int, ...]
    ) -> list[tuple[str, float]]:
        """The `limit` chunks nearest to `vector` by cosine, through the HNSW index of its dimension when there is
        one (scanning on past other bases' rows until enough of this one's are found), else exactly."""
        if ANN_INDEXES.get(len(vector)):
            await self.s.execute(text(f"SET LOCAL hnsw.ef_search = {min(1000, max(100, 2 * limit))}"))
            if version >= (0, 8):  # iterative index scans: the kb filter does not cut the results short
                await self.s.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
        rows = await self.s.execute(
            text(nearest_sql(len(vector))),
            {"kb": kb_id, "q": literal(vector), "k": limit},
        )
        found = [(r.id, float(r.score)) for r in rows.all() if r.score > 0]
        return sorted(found, key=lambda x: -x[1])  # a relaxed-order scan may be slightly out of order

    async def _details(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """What a search result shows of each chunk: its text, page and heading, its document's title and source."""
        if not ids:
            return {}
        rows = await self.s.execute(
            select(
                KnowledgeChunk.id, KnowledgeChunk.doc_id, KnowledgeChunk.text, KnowledgeChunk.page,
                KnowledgeChunk.heading, KnowledgeDocument.source, KnowledgeDocument.name, KnowledgeDocument.url,
                func.substr(KnowledgeDocument.content, 1, 2000).label("head"),
            )
            .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.doc_id)
            .where(KnowledgeChunk.id.in_(ids))
        )  # fmt: skip
        return {
            r.id: {
                "id": r.id, "doc_id": r.doc_id, "title": title_of(r.source, r.name, r.head or ""),
                "source": r.url or r.name, "text": r.text, "page": r.page, "heading": r.heading,
            }
            for r in rows.all()
        }  # fmt: skip

    async def export(self, kb: KnowledgeBase) -> str:
        """The knowledge base as `knowledge/<name>.jsonl` for exported projects. A stale one ships without its
        vectors (they may be another model's than the header names), so the export searches it by keywords too."""
        out = io.StringIO()
        out.write(ks.jsonl_header(kb.name, kb.embedding) + "\n")
        # read from the database a batch at a time, never cached: an export is a one-off (I-6). Built-in vectors
        # are recomputed on loading, a stale base's left out: then they are not even read
        vectors = (kb.embedding.get("model") or ks.BUILTIN) != ks.BUILTIN and not kb.stale
        heads = await self._heads(kb.id)
        pg = await self.pgvector() is not None
        after: tuple[str, int] = ("", -1)
        while True:
            if pg:
                vector_col = "CAST(embedding AS text)" if vectors else "NULL"
                rows = (
                    await self.s.execute(
                        text(
                            f"SELECT id, doc_id, ordinal, text, page, heading, {vector_col} AS vector FROM "
                            "knowledge_chunk WHERE kb_id = :kb AND (doc_id > :d OR (doc_id = :d AND ordinal > :o)) "
                            "ORDER BY doc_id, ordinal LIMIT :n"
                        ),
                        {"kb": kb.id, "d": after[0], "o": after[1], "n": EXPORT_BATCH},
                    )
                ).all()
            else:
                cols = [KnowledgeChunk.id, KnowledgeChunk.doc_id, KnowledgeChunk.ordinal, KnowledgeChunk.text,
                        KnowledgeChunk.page, KnowledgeChunk.heading]  # fmt: skip
                rows = (
                    await self.s.execute(
                        select(*cols, (KnowledgeChunk.vector if vectors else null()).label("vector"))
                        .where(
                            KnowledgeChunk.kb_id == kb.id,
                            or_(
                                KnowledgeChunk.doc_id > after[0],
                                and_(KnowledgeChunk.doc_id == after[0], KnowledgeChunk.ordinal > after[1]),
                            ),
                        )
                        .order_by(KnowledgeChunk.doc_id, KnowledgeChunk.ordinal)
                        .limit(EXPORT_BATCH)
                    )
                ).all()
            for r in rows:
                if (head := heads.get(r.doc_id)) is None:
                    continue
                if not vectors or r.vector is None:
                    vector = None
                else:
                    vector = json.loads(r.vector) if pg else compact(r.vector)
                chunk = {"id": r.id, "doc_id": r.doc_id, "title": head[0], "source": head[1], "text": r.text,
                         "page": r.page, "heading": r.heading, "vector": vector}  # fmt: skip
                out.write(ks.jsonl_row(chunk, keep_vector=vectors) + "\n")
            if len(rows) < EXPORT_BATCH:
                return out.getvalue()
            after = (rows[-1].doc_id, rows[-1].ordinal)

    # ------------------------------------------------------------------- samples ----
    async def sample(self, project_id: str, name: str = "handbook", user_id: str | None = None) -> KnowledgeBase:
        """The sample knowledge base: 22 policy pages of a made-up company, with a retrieval eval set."""
        folder = SAMPLES / name
        if name != "handbook" or not folder.is_dir():
            raise InvalidInput(f"no sample knowledge base named '{name}'; try 'handbook'")
        kb = await self.create(
            project_id, name, description="Sample: the employee handbook of Lumen Labs, a made-up company.",
            chunking=Chunking(strategy="markdown", size=400), user_id=user_id,
        )  # fmt: skip
        for path in sorted(folder.glob("*.md")):
            title, text = extract(path.name, path.read_bytes())
            await self.add(kb.id, title=title, text=text, kind="upload", name=path.name, user_id=user_id)
        return kb


def title_of(source: str, name: str, content: str) -> str:
    """The title a document is searched and cited by: an upload's first `# ` heading or its file name."""
    if source == "upload":
        first = content.lstrip().split("\n", 1)[0]
        return first[2:].strip() if first.startswith("# ") else Path(name).stem
    return name


def used_knowledge_bases(ir: dict[str, Any]) -> set[str]:
    """The knowledge bases a workflow's Search knowledge blocks name, subflows included."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if value.get("type") == "knowledge.search" and isinstance(value.get("config"), dict):
                name = value["config"].get("knowledge_base")
                if isinstance(name, str):
                    found.add(name)
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(ir)
    return found


def bundle(zip_bytes: bytes, knowledge: dict[str, str]) -> bytes:
    """An export zip with `src/knowledge/<name>.jsonl` added for each knowledge base, next to the vendored
    runtime that reads them."""
    import zipfile

    src = io.BytesIO(zip_bytes)
    out = io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        names = zin.namelist()
        root = names[0].split("/", 1)[0] if names else "export"
        for info in zin.infolist():
            zout.writestr(info, zin.read(info.filename))
        for name, text in sorted(knowledge.items()):
            info = zipfile.ZipInfo(f"{root}/src/knowledge/{name}.jsonl", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zout.writestr(info, text)
    return out.getvalue()


def sample_eval(name: str = "handbook") -> list[dict[str, Any]]:
    """The sample's retrieval eval set: dataset examples whose reference lists the relevant documents."""
    import json

    path = SAMPLES / f"{name}.eval.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class Searcher:
    """What the runtime calls for a Search knowledge block on this server: a project's knowledge base by name."""

    def __init__(self, db: Any, embedder: EmbedderFactory, secrets: Callable[[str], dict[str, str]]):
        self.db, self.embedder, self.secrets = db, embedder, secrets

    def for_project(self, project_id: str) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
        async def search(
            name: str, query: str, *, mode: str = "hybrid", top_k: int = 5, min_score: float = 0.0
        ) -> list[dict[str, Any]]:
            async with self.db.sessionmaker() as s:
                svc = KnowledgeService(s, embedder=self.embedder, secrets=self.secrets)
                kb = await svc.by_name(project_id, name)
                return await svc.search(kb, query, mode=mode, top_k=top_k, min_score=min_score)

        return search


class CrawlJob:
    """Re-crawls the knowledge bases whose `recrawl.every_hours` has passed since their last crawl. A replica claims a
    base by moving its `crawled_at` forward in one conditional UPDATE, so across replicas it is crawled once per
    interval."""

    def __init__(
        self,
        db: Any,
        embedder: EmbedderFactory,
        secrets: Callable[[str], dict[str, str]],
        transport: Callable[[], Any] = lambda: None,
        interval_s: float = 300.0,
    ):
        self.db, self.embedder, self.secrets, self.transport, self.interval_s = (
            db,
            embedder,
            secrets,
            transport,
            interval_s,
        )
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="knowledge-crawl")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick(datetime.now(UTC))
            except Exception:
                log.exception("knowledge.crawl_tick_failed")
            await asyncio.sleep(self.interval_s)

    async def tick(self, now: datetime) -> list[str]:
        """Crawl the bases due at `now`; returns their ids."""
        async with self.db.sessionmaker() as s:
            rows = (await s.execute(select(KnowledgeBase.id, KnowledgeBase.recrawl))).all()
        crawled = []
        for kb_id, cfg in rows:
            every = (cfg or {}).get("every_hours") if isinstance(cfg, dict) else None
            if not isinstance(every, int) or every < 1:
                continue
            # one tick of slack: a crawl at 09:00:10 is due again at the 09:00 tick a day later
            due = now - timedelta(hours=every) + timedelta(seconds=self.interval_s)
            async with self.db.sessionmaker() as s:
                claimed = await s.execute(
                    sql_update(KnowledgeBase)
                    .where(
                        KnowledgeBase.id == kb_id,
                        (KnowledgeBase.crawled_at.is_(None)) | (KnowledgeBase.crawled_at < due),
                    )
                    .values(crawled_at=now)
                )
                await s.commit()
                if claimed.rowcount != 1:  # type: ignore[attr-defined]
                    continue
                try:
                    svc = KnowledgeService(s, embedder=self.embedder, secrets=self.secrets)
                    summary = await svc.recrawl(kb_id, transport=self.transport())
                    log.info("knowledge.recrawled", kb=kb_id, **{k: v for k, v in summary.items() if k != "failed"})
                    crawled.append(kb_id)
                except Exception as exc:  # noqa: BLE001 - one base must not stop the others
                    await s.rollback()
                    log.warning("knowledge.recrawl_failed", kb=kb_id, error=str(exc))
        return crawled
