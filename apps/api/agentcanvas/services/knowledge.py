"""Knowledge bases (S24 Knowledge Studio): documents a workflow searches with the Search knowledge block.

A knowledge base belongs to a project and holds documents: uploaded files (PDF, Word, HTML, Markdown, text, CSV,
JSON), public web pages, or pasted text. Each document's text is kept, split into chunks (by paragraph, sentence,
fixed windows or markdown headings) and each chunk embedded, with the built-in offline embedder or a provider
model. Search ranks chunks by keyword (BM25), meaning (cosine) or both (hybrid, reciprocal-rank fusion); the math
is in `agentcanvas.knowledge_search`, shared with exported projects.

Every change bumps the knowledge base's `version`, so each instance's in-memory index knows when to reload.
Changing the embedding model or chunking marks the knowledge base stale until it is reindexed.
"""

from __future__ import annotations

import asyncio
import hashlib
import html.parser
import io
import ipaddress
import socket
from array import array
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar, Literal
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from agentcanvas import knowledge_search as ks
from agentcanvas.db.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from agentcanvas.errors import Conflict, InvalidInput, NotFound
from agentcanvas.ids import new_id

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000  # extracted text per document
MAX_DOCX_UNZIPPED = 100 * 1024 * 1024  # a Word file's parts, uncompressed: refuses zip bombs before parsing
MAX_CHUNKS = 50_000  # per knowledge base: search runs in memory
MAX_PAGE_BYTES = 5 * 1024 * 1024
URL_TIMEOUT_S = 20.0
EMBED_BATCH = 64
INDEX_CACHE = 16  # knowledge bases held in memory per instance
SAMPLES = Path(__file__).resolve().parent.parent / "registry" / "knowledge"
TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".csv", ".json", ".jsonl", ".rst", ".log", ".yaml", ".yml"}
SUFFIXES = TEXT_SUFFIXES | {".pdf", ".docx", ".html", ".htm"}

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
    """Visible text of an HTML page, one block per line; the <title> is kept apart."""

    SKIP: ClassVar[set[str]] = {"script", "style", "noscript", "template", "svg", "head", "nav", "footer"}
    BLOCK: ClassVar[set[str]] = {"p", "div", "section", "article", "li", "tr", "br", "pre", "td"} | {
        f"h{i}" for i in range(1, 7)
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n\n" + ("#" * int(tag[1]) + " " if tag[0] == "h" and tag[1:].isdigit() else ""))

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n\n")

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


def extract(name: str, data: bytes) -> tuple[str, str]:
    """(title, text) of an uploaded file; PDF pages are separated by form feeds (\\f)."""
    suffix = Path(name).suffix.lower()
    stem = Path(name).stem
    if suffix not in SUFFIXES:
        raise InvalidInput(f"'{name}': unsupported file type; use one of {', '.join(sorted(SUFFIXES))}")
    if len(data) > MAX_FILE_BYTES:
        raise InvalidInput(f"'{name}' is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB")
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            text = "\f".join((page.extract_text() or "").strip() for page in reader.pages)
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
        if not ipaddress.ip_address(a.split("%")[0]).is_global:
            raise InvalidInput(f"'{host}' resolves to a private or internal address; only public pages can be added")
    return addresses[0]


async def fetch_url(url: str, transport: Any = None) -> tuple[str, str, str]:
    """(final url, title, text) of a public web page or file. At most 5 redirects, each re-checked; at most
    5 MB; HTML is reduced to its visible text, PDF and Word files are extracted."""
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
        name = Path(parts.path).name or parts.hostname
        if kind == "application/pdf" or name.lower().endswith(".pdf"):
            title, text = await asyncio.to_thread(
                extract, name if name.lower().endswith(".pdf") else "page.pdf", bytes(body)
            )
        elif kind.endswith("wordprocessingml.document"):
            title, text = await asyncio.to_thread(extract, "page.docx", bytes(body))
        elif kind in ("text/html", "application/xhtml+xml") or not kind:
            title, text = await asyncio.to_thread(html_text, body.decode(r.encoding or "utf-8", errors="replace"))
        elif kind.startswith("text/") or kind in ("application/json",):
            title, text = name, body.decode(r.encoding or "utf-8", errors="replace")
        else:
            raise InvalidInput(f"'{current}' is {kind}, not a page or document")
        return current, (title or parts.hostname), text
    raise InvalidInput(f"'{url}' redirects too many times")


# ------------------------------------------------------------- vector bytes ----
def pack(vector: Sequence[float]) -> bytes:
    a = array("f", vector)
    if a.itemsize != 4:  # pragma: no cover - every supported platform has 32-bit floats
        raise RuntimeError("float32 arrays needed")
    return a.tobytes()


def unpack(data: bytes | None) -> list[float] | None:
    if not data:
        return None
    a = array("f")
    a.frombytes(data)
    return a.tolist()


# ---------------------------------------------------------------- the index ----
_INDEX: OrderedDict[str, tuple[int, ks.Index]] = OrderedDict()  # kb id -> (version, index)


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
        """(knowledge base, documents, chunks) for each knowledge base of a project."""
        kbs = list(
            (
                await self.s.scalars(
                    select(KnowledgeBase).where(KnowledgeBase.project_id == project_id).order_by(KnowledgeBase.name)
                )
            ).all()
        )
        return [(k, *(await self.counts(k.id))) for k in kbs]

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
        kb = await self._lock(kb_id)
        text = text.replace("\x00", "").strip()
        if not text:
            raise InvalidInput(f"'{name or title}' has no text to search (a scanned PDF needs OCR first)")
        if len(text) > MAX_TEXT_CHARS:
            raise InvalidInput(f"'{name or title}' has more than {MAX_TEXT_CHARS:,} characters of text; split it")
        digest = hashlib.sha256(text.encode()).hexdigest()
        if same := await self.s.scalar(
            select(KnowledgeDocument.name).where(KnowledgeDocument.kb_id == kb_id, KnowledgeDocument.sha256 == digest)
        ):
            raise Conflict(f"this knowledge base already has the same text ('{same}')")
        doc = KnowledgeDocument(
            id=new_id("kdoc"), kb_id=kb_id, name=(name or title)[:300], source=kind, url=url, content=text,
            sha256=digest, created_by=user_id,
        )  # fmt: skip
        title = self._title(doc)[:300]  # the title search shows and cites, so the vectors match what is shown
        pieces = await asyncio.to_thread(self._chunks, kb, text, title)
        _, total = await self.counts(kb_id)
        if total + len(pieces) > MAX_CHUNKS:
            raise InvalidInput(f"a knowledge base holds at most {MAX_CHUNKS:,} chunks; this adds {len(pieces)}")
        vectors = await self._embed(kb, [ks.embed_text({**p, "title": title}) for p in pieces])
        self.s.add(doc)
        await self.s.flush()
        for i, (p, v) in enumerate(zip(pieces, vectors, strict=True)):
            self.s.add(
                KnowledgeChunk(
                    id=f"{doc.id}_{i}", kb_id=kb_id, doc_id=doc.id, ordinal=i, text=p["text"], page=p["page"],
                    heading=p["heading"], vector=pack(v),
                )
            )  # fmt: skip
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
        total = 0
        for doc in docs:
            title = self._title(doc)
            pieces = await asyncio.to_thread(self._chunks, kb, doc.content, title)
            total += len(pieces)
            if total > MAX_CHUNKS:
                raise InvalidInput(f"with these settings the knowledge base needs more than {MAX_CHUNKS:,} chunks")
            vectors = await self._embed(kb, [ks.embed_text({**p, "title": title}) for p in pieces])
            for i, (p, v) in enumerate(zip(pieces, vectors, strict=True)):
                self.s.add(
                    KnowledgeChunk(
                        id=f"{doc.id}_{i}", kb_id=kb_id, doc_id=doc.id, ordinal=i, text=p["text"], page=p["page"],
                        heading=p["heading"], vector=pack(v),
                    )
                )  # fmt: skip
            doc.chunks = len(pieces)
        kb.stale = False
        await self._touch(kb)
        return kb

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
        if doc.source == "upload":
            first = doc.content.lstrip().split("\n", 1)[0]
            return first[2:].strip() if first.startswith("# ") else Path(doc.name).stem
        return doc.name

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
    async def index(self, kb: KnowledgeBase) -> ks.Index:
        cached = _INDEX.get(kb.id)
        if cached and cached[0] == kb.version:
            _INDEX.move_to_end(kb.id)
            return cached[1]
        docs = {d.id: d for d in await self.documents(kb.id)}
        rows = (
            await self.s.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.kb_id == kb.id)
                .order_by(KnowledgeChunk.doc_id, KnowledgeChunk.ordinal)
            )
        ).all()
        chunks = []
        for r in rows:
            doc = docs.get(r.doc_id)
            if doc is None:
                continue
            chunks.append({
                "id": r.id, "doc_id": r.doc_id, "title": self._title(doc), "source": doc.url or doc.name,
                "text": r.text, "page": r.page, "heading": r.heading, "vector": unpack(r.vector),
            })  # fmt: skip
        index = await asyncio.to_thread(ks.Index, chunks)
        _INDEX[kb.id] = (kb.version, index)
        while len(_INDEX) > INDEX_CACHE:
            _INDEX.popitem(last=False)
        return index

    async def search(
        self, kb: KnowledgeBase, query: str, *, mode: str = "hybrid", top_k: int = 5, min_score: float = 0.0
    ) -> list[dict[str, Any]]:
        if mode not in ks.MODES:
            raise InvalidInput(f"unknown search mode '{mode}'; use one of {', '.join(ks.MODES)}")
        index = await self.index(kb)
        if kb.stale:  # the vectors may be another model's: a query embedded with the new one would give noise
            mode = "keyword"
        qv = None
        if mode != "keyword" and query.strip() and index.chunks:
            qv = (await self._embed(kb, [query]))[0]
        return ks.search(index, query, mode=mode, top_k=top_k, min_score=min_score, query_vector=qv)

    async def export(self, kb: KnowledgeBase) -> str:
        """The knowledge base as `knowledge/<name>.jsonl` for exported projects. A stale one ships without its
        vectors (they may be another model's than the header names), so the export searches it by keywords too."""
        index = await self.index(kb)
        chunks = [{**c, "vector": None} for c in index.chunks] if kb.stale else index.chunks
        return ks.dump_jsonl(kb.name, kb.embedding, chunks)

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
