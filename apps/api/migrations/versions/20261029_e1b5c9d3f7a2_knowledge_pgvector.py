"""knowledge chunks searched in Postgres: pgvector and full-text search (G-6)

On Postgres with the pgvector extension, `knowledge_chunk` gets three columns the API reads and writes with SQL of
its own (they are not in the ORM model, which SQLite shares):

- `embedding vector`: the chunk's embedding, of any dimension (knowledge bases use different models), and `dims`, its
  length. An approximate nearest-neighbour (HNSW, cosine) index exists per common dimension, each a partial index
  over the expression `embedding::vector(n)` for the rows `WHERE dims = n` (pgvector's way to index a column of mixed
  dimensions); `dims` is a plain integer so the predicate stays usable under row-level security, whose policy only
  lets leakproof comparisons reach an index. Above 2,000 dimensions the index is on `halfvec` (pgvector indexes
  `vector` up to 2,000 dimensions, `halfvec` up to 4,000).
- `tsv tsvector`: the words of the document title, heading and text (`simple` configuration: no stemming and no
  language, like the in-process engine), with a GIN index, for keyword search.

Existing chunks are backfilled from their float32 bytes, which are then cleared (the vector is kept once). Without
pgvector nothing changes and the API keeps searching in process, as on SQLite. The dimension list is copied here so
later changes to `services/knowledge.py` do not rewrite this migration.

Revision ID: e1b5c9d3f7a2
Revises: d8f2b6c0e4a9
Create Date: 2026-10-29 18:00:00
"""

from __future__ import annotations

import json
import logging
from array import array
from collections.abc import Sequence
from pathlib import PurePosixPath

import sqlalchemy as sa
from alembic import op

revision: str = "e1b5c9d3f7a2"
down_revision: str | None = "d8f2b6c0e4a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# dimension -> the type its index casts to: the built-in embedder (512) and the common provider models
ANN_INDEXES: dict[int, str] = {
    384: "vector", 512: "vector", 768: "vector", 1024: "vector", 1536: "vector", 3072: "halfvec",
}  # fmt: skip
BATCH = 500
log = logging.getLogger("alembic.runtime.migration")


def _has_vector(conn: sa.Connection) -> bool:
    """Whether pgvector is installed, installing it when the server offers it and this role may."""
    if conn.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar():
        return True
    if not conn.execute(sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")).scalar():
        log.warning("pgvector is not available: knowledge bases keep being searched in process")
        return False
    try:
        with conn.begin_nested():
            conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    except sa.exc.DBAPIError as exc:
        log.warning("pgvector cannot be installed (%s): knowledge bases keep being searched in process", exc.orig)
        return False
    return True


def _title(source: str, name: str, head: str) -> str:
    """The title a chunk is searched and cited by (`KnowledgeService._title`), from the document's first line."""
    if source == "upload":
        first = head.split("\n", 1)[0]
        return first[2:].strip() if first.startswith("# ") else PurePosixPath(name).stem
    return name


def _index(dims: int, kind: str) -> str:
    return (
        f"CREATE INDEX IF NOT EXISTS knowledge_chunk_ann_{dims} ON knowledge_chunk "
        f"USING hnsw ((embedding::{kind}({dims})) {kind}_cosine_ops) WHERE dims = {dims}"
    )


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql" or not _has_vector(conn):
        return
    op.execute("ALTER TABLE knowledge_chunk ADD COLUMN IF NOT EXISTS dims integer")
    op.execute("ALTER TABLE knowledge_chunk ADD COLUMN IF NOT EXISTS embedding vector")
    op.execute("ALTER TABLE knowledge_chunk ADD COLUMN IF NOT EXISTS tsv tsvector")
    read = sa.text(
        "SELECT c.id, c.vector, d.source, d.name, substr(ltrim(d.content, E' \\t\\n\\r\\f'), 1, 1000) AS head "
        "FROM knowledge_chunk c JOIN knowledge_document d ON d.id = c.doc_id WHERE c.id > :last ORDER BY c.id "
        "LIMIT :batch"
    )
    write = sa.text(
        "UPDATE knowledge_chunk SET dims = :dims, embedding = CAST(:embedding AS vector), "
        "tsv = to_tsvector('simple', concat_ws(' ', CAST(:title AS text), heading, text)), vector = NULL "
        "WHERE id = :id"
    )
    last = ""
    while rows := conn.execute(read, {"last": last, "batch": BATCH}).all():
        params = []
        for r in rows:
            vector = array("f")
            vector.frombytes(r.vector or b"")
            params.append({
                "id": r.id, "dims": len(vector) or None,
                "embedding": json.dumps([round(v, 7) for v in vector]) if vector else None,
                "title": _title(r.source, r.name, r.head or "")[:300],
            })  # fmt: skip
        conn.execute(write, params)
        last = rows[-1].id
    op.execute("CREATE INDEX IF NOT EXISTS knowledge_chunk_tsv ON knowledge_chunk USING gin (tsv)")
    for dims, kind in ANN_INDEXES.items():  # built after the backfill: one bulk build each
        op.execute(_index(dims, kind))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    column = "SELECT 1 FROM information_schema.columns WHERE table_name = 'knowledge_chunk' AND column_name = 'dims'"
    if not conn.execute(sa.text(column)).scalar():
        return
    read = sa.text(
        "SELECT id, CAST(embedding AS text) AS embedding FROM knowledge_chunk WHERE id > :last AND embedding IS NOT "
        "NULL ORDER BY id LIMIT :batch"
    )
    write = sa.text("UPDATE knowledge_chunk SET vector = :vector WHERE id = :id")
    last = ""
    while rows := conn.execute(read, {"last": last, "batch": BATCH}).all():
        conn.execute(write, [{"id": r.id, "vector": array("f", json.loads(r.embedding)).tobytes()} for r in rows])
        last = rows[-1].id
    for dims in ANN_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS knowledge_chunk_ann_{dims}")
    op.execute("DROP INDEX IF EXISTS knowledge_chunk_tsv")
    for column in ("tsv", "embedding", "dims"):
        op.execute(f"ALTER TABLE knowledge_chunk DROP COLUMN IF EXISTS {column}")
