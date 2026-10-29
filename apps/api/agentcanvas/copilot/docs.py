"""Keyword search over the user guide (S15), for questions the knowledge pack does not answer.

The guide's Markdown is split into sections by heading and scored with BM25. It is read from
`AGENTCANVAS_DOCS_DIR`, else from the copy in this package (`make schema` refreshes it from docs/user-guide).
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PACKAGED = Path(__file__).parent / "knowledge" / "user-guide"
WORD = re.compile(r"[a-z0-9_]+")
STOP = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "it", "how", "do", "i", "what", "can",
        "my", "with", "be", "this", "that", "are", "you", "your", "does", "why", "when"}  # fmt: skip
K1, B = 1.4, 0.75


@dataclass(frozen=True)
class Section:
    path: str
    heading: str
    text: str
    words: tuple[str, ...]


def _words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in STOP]


def docs_dir() -> Path | None:
    configured = os.environ.get("AGENTCANVAS_DOCS_DIR")
    for d in [*([Path(configured)] if configured else []), PACKAGED]:
        if d.is_dir():
            return d
    return None


@cache
def sections(root: str | None = None) -> tuple[Section, ...]:
    base = Path(root) if root else docs_dir()
    if base is None:
        return ()
    out: list[Section] = []
    for path in sorted(base.glob("*.md")):
        parts: list[tuple[str, list[str]]] = [(path.stem, [])]
        fenced = False
        for line in path.read_text().splitlines():
            fenced ^= line.lstrip().startswith("```")
            if line.startswith("#") and not fenced:
                parts.append((line.lstrip("#").strip(), []))
            else:
                parts[-1][1].append(line)
        for heading, lines in parts:
            text = "\n".join(lines).strip()
            if text:
                out.append(Section(path.name, heading, text, tuple(_words(heading + " " + text))))
    return tuple(out)


@cache
def _index(root: str | None = None) -> tuple[float, Counter[str], tuple[Counter[str], ...]]:
    """The guide's BM25 statistics, once: average length, document frequencies, each section's term counts (F-5)."""
    docs = sections(root)
    avg = sum(len(d.words) for d in docs) / len(docs) if docs else 0.0
    return avg, Counter(t for d in docs for t in set(d.words)), tuple(Counter(d.words) for d in docs)


def search(query: str, limit: int = 4, root: str | None = None) -> list[dict[str, str]]:
    docs = sections(root)
    terms = _words(query)
    if not docs or not terms:
        return []
    avg, df, tfs = _index(root)
    scored = []
    for d, tf in zip(docs, tfs, strict=True):
        score = 0.0
        for t in terms:
            if tf[t]:
                idf = math.log(1 + (len(docs) - df[t] + 0.5) / (df[t] + 0.5))
                score += idf * tf[t] * (K1 + 1) / (tf[t] + K1 * (1 - B + B * len(d.words) / avg))
        if score > 0:
            scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    return [{"page": d.path, "section": d.heading, "text": d.text[:900]} for _, d in scored[:limit]]
