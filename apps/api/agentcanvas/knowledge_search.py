"""Knowledge search (S24): chunking, a built-in offline embedder, keyword (BM25), meaning (cosine) and hybrid search.

Pure Python with no dependencies, so the server and exported projects search the same way. A knowledge base is a list
of chunks: `{"id", "doc_id", "title", "source", "text", "page", "heading", "vector"}`; `vector` is the chunk's
embedding (from the built-in embedder or a provider model), `None` when it has none.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

BUILTIN = "builtin"
DIM = 512
MODES = ("hybrid", "semantic", "keyword")
STRATEGIES = ("paragraphs", "sentences", "fixed", "markdown")
RRF_K = 60  # reciprocal-rank fusion constant (Cormack et al. 2009)
NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")  # a knowledge base's name: also its file name in exports

_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
# A markdown ATX heading line (matched on the right-stripped line). Deliberately simple so matching stays linear:
# the optional closing `#`s are trimmed in Python, not by a lazy group followed by `\s*#*\s*$` (cubic backtracking).
_HEADING = re.compile(r" {0,3}#{1,6}[ \t]+(.*)")
_STOP = frozenset(
    "a an and are as at be by can do does for from has have how i if in is it its me my of on or our so that the "
    "their them there these this to was we what when where which who why will with you your".split()
)


# ------------------------------------------------------------------ chunking ----
def chunk_text(
    text: str, *, strategy: str = "paragraphs", size: int = 800, overlap: int = 100, page: int | None = None
) -> list[dict[str, Any]]:
    """Split one text into chunks of about `size` characters: by paragraph (default), sentence, fixed windows with
    `overlap` characters, or markdown headings (each chunk remembers its heading)."""
    size = max(100, int(size))
    overlap = max(0, min(int(overlap), size // 2))
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    if strategy == "fixed":
        step = size - overlap
        parts = [text[i : i + size] for i in range(0, len(text), step)]
        return [{"text": p.strip(), "page": page, "heading": None} for p in parts if p.strip()]
    if strategy == "markdown":
        out: list[dict[str, Any]] = []
        heading: str | None = None
        section: list[str] = []

        def flush() -> None:
            body = "\n".join(section).strip()
            if body:
                out.extend({"text": t, "page": page, "heading": heading} for t in _pack(_paragraphs(body), size))

        for line in text.split("\n"):
            m = _HEADING.match(line.rstrip())
            if m:
                flush()
                heading, section = _heading_text(m.group(1)), []
            else:
                section.append(line)
        flush()
        return out
    units = _sentences(text) if strategy == "sentences" else _paragraphs(text)
    return [{"text": t, "page": page, "heading": None} for t in _pack(units, size)]


def _heading_text(rest: str) -> str:
    """A heading's text without its optional closing sequence of `#`s."""
    title = rest.strip()
    bare = title.rstrip("#").rstrip()
    return bare or title


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", " ".join(text.split())) if s.strip()]


def _pack(units: Iterable[str], size: int) -> list[str]:
    """Join small units up to `size` characters; split units longer than `size` at word boundaries."""
    out: list[str] = []
    buf = ""
    for whole in units:
        unit = whole
        while len(unit) > size:
            cut = unit.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            if buf:
                out.append(buf)
                buf = ""
            out.append(unit[:cut].strip())
            unit = unit[cut:].strip()
        if not unit:
            continue
        if buf and len(buf) + 1 + len(unit) > size:
            out.append(buf)
            buf = unit
        else:
            buf = f"{buf}\n{unit}" if buf else unit
    if buf:
        out.append(buf)
    return out


# ----------------------------------------------------------------- embedding ----
def words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOP]


def _bucket(feature: str) -> tuple[int, float]:
    h = hashlib.blake2b(feature.encode(), digest_size=8).digest()
    n = int.from_bytes(h, "little")
    return n % DIM, (1.0 if (n >> 63) & 1 else -1.0)


def builtin_embed(texts: Sequence[str]) -> list[list[float]]:
    """The built-in embedder: hashed words and character trigrams, L2-normalised. Offline and deterministic. It
    matches word forms and misspellings ("reimbursed" ~ "reimbursement"), not synonyms; use a provider model for
    meaning across different words."""
    out = []
    for text in texts:
        vec = [0.0] * DIM
        for w in words(text):
            i, s = _bucket("w:" + w)
            vec[i] += s
            padded = f"<{w}>"
            for j in range(len(padded) - 2):
                i, s = _bucket("c:" + padded[j : j + 3])
                vec[i] += 0.5 * s
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        out.append([round(v / norm, 6) for v in vec])
    return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two vectors of the same length (vectors of different models raise ValueError)."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# -------------------------------------------------------------------- search ----
@dataclass
class Index:
    """Chunks prepared for search: BM25 statistics and vectors."""

    chunks: list[dict[str, Any]]
    terms: list[Counter[str]] = field(default_factory=list)
    df: Counter[str] = field(default_factory=Counter)
    avgdl: float = 0.0

    def __post_init__(self) -> None:
        self.terms = [Counter(words(embed_text(c))) for c in self.chunks]
        for t in self.terms:
            self.df.update(t.keys())
        self.avgdl = (sum(sum(t.values()) for t in self.terms) / len(self.terms)) if self.terms else 0.0

    def bm25(self, query: str, k1: float = 1.2, b: float = 0.75) -> list[float]:
        n = len(self.chunks)
        q = set(words(query))
        scores = []
        for t in self.terms:
            dl = sum(t.values())
            s = 0.0
            for w in q:
                f = t.get(w, 0)
                if not f:
                    continue
                idf = math.log(1 + (n - self.df[w] + 0.5) / (self.df[w] + 0.5))
                s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * dl / (self.avgdl or 1)))
            scores.append(s)
        return scores

    def comparable(self, query_vector: Sequence[float]) -> bool:
        """Whether every chunk has a vector of the query's length, i.e. from the same embedding model."""
        return all(c.get("vector") and len(c["vector"]) == len(query_vector) for c in self.chunks)

    def semantic(self, query_vector: Sequence[float]) -> list[float]:
        return [cosine(query_vector, c["vector"]) if c.get("vector") else 0.0 for c in self.chunks]


def search(
    index: Index,
    query: str,
    *,
    mode: str = "hybrid",
    top_k: int = 5,
    min_score: float = 0.0,
    query_vector: Sequence[float] | None = None,
) -> list[dict[str, Any]]:
    """The best `top_k` chunks for `query`, each with `score` and a `cite` number (1, 2, …) for answers to quote.
    keyword = BM25 over words; semantic = cosine of embeddings (`query_vector` is the query's embedding from the
    knowledge base's model, the built-in embedder's by default); hybrid = both rankings fused by reciprocal rank.
    When the chunks' vectors cannot be compared with the query's (another model's, or missing), the search is by
    keywords: comparing vectors of different models gives noise.

    `score` (and `min_score`) is 0-1 in every mode: keyword = BM25 relative to the best match (which scores 1);
    semantic = the cosine; hybrid = the fused score relative to the most possible (1: first in both rankings)."""
    if mode not in MODES:
        raise ValueError(f"unknown search mode {mode!r}; use one of {', '.join(MODES)}")
    if not index.chunks or not query.strip():
        return []
    ranked: list[tuple[int, float]]
    qv = None if mode == "keyword" else query_vector if query_vector is not None else builtin_embed([query])[0]
    if qv is None or not index.comparable(qv):
        ranked = _ranked(index.bm25(query))
        top = ranked[0][1] if ranked else 1.0
        ranked = [(i, s / top) for i, s in ranked]
    else:
        semantic = _ranked(index.semantic(qv))
        if mode == "semantic":
            ranked = [(i, min(s, 1.0)) for i, s in semantic]
        else:
            fused: dict[int, float] = {}
            best = 2.0 / (RRF_K + 1)  # first in both rankings
            for ranking in (_ranked(index.bm25(query)), semantic):
                for rank, (i, _) in enumerate(ranking[:50]):
                    fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1) / best
            ranked = sorted(fused.items(), key=lambda x: -x[1])
    out = []
    for i, score in ranked:
        if len(out) >= top_k:
            break
        if score <= 0 or score < min_score:
            continue
        c = index.chunks[i]
        out.append({
            "id": c["id"], "doc_id": c.get("doc_id"), "title": c.get("title"), "source": c.get("source"),
            "text": c["text"], "page": c.get("page"), "heading": c.get("heading"), "score": round(score, 4),
            "cite": len(out) + 1,
        })  # fmt: skip
    return out


def _ranked(scores: list[float]) -> list[tuple[int, float]]:
    return sorted(((i, s) for i, s in enumerate(scores) if s > 0), key=lambda x: -x[1])


def sources(results: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The citation list for results: one entry per cited chunk, `[n] title (page)`."""
    return [
        {"cite": r["cite"], "title": r.get("title"), "source": r.get("source"), "page": r.get("page")} for r in results
    ]


# --------------------------------------------------------- retrieval metrics ----
def retrieval_metrics(retrieved: Sequence[str], relevant: Sequence[str], k: int) -> dict[str, float]:
    """recall@k, precision@k, hit@k and reciprocal rank of the first relevant item (MRR over a set)."""
    top = list(retrieved)[:k]
    rel = set(relevant)
    hits = [x for x in top if x in rel]
    first = next((i for i, x in enumerate(top) if x in rel), None)
    found = len(set(hits))
    return {
        "recall": found / len(rel) if rel else 0.0,
        "precision": len(hits) / len(top) if top else 0.0,
        "hit": 1.0 if hits else 0.0,
        "mrr": 1.0 / (first + 1) if first is not None else 0.0,
    }


# ------------------------------------------------------------------ files ----
def dump_jsonl(name: str, embedding: dict[str, Any], chunks: Sequence[dict[str, Any]]) -> str:
    """A knowledge base as JSON lines: a header, then one chunk per line. Built-in vectors are left out (loading
    recomputes them); a provider model's are kept, since recomputing them needs the provider."""
    builtin = (embedding.get("model") or BUILTIN) == BUILTIN
    lines = [json.dumps({"knowledge_base": name, "embedding": {"model": embedding.get("model") or BUILTIN}})]
    for c in chunks:
        row = {k: c.get(k) for k in ("id", "doc_id", "title", "source", "text", "page", "heading")}
        if not builtin and c.get("vector"):
            row["vector"] = [round(float(v), 6) for v in c["vector"]]
        lines.append(json.dumps(row, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def load_jsonl(text: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """(header, chunks) from `dump_jsonl` output, with built-in vectors recomputed."""
    rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not rows or "knowledge_base" not in rows[0]:
        raise ValueError("not a knowledge base file: the first line names the knowledge base")
    header, chunks = rows[0], rows[1:]
    if (header.get("embedding", {}).get("model") or BUILTIN) == BUILTIN:
        for c, v in zip(chunks, builtin_embed([embed_text(c) for c in chunks]), strict=True):
            c["vector"] = v
    return header, chunks


def embed_text(chunk: dict[str, Any]) -> str:
    """What a chunk's embedding is computed from: its document title, heading and text."""
    return " ".join(str(x) for x in (chunk.get("title"), chunk.get("heading"), chunk.get("text")) if x)
