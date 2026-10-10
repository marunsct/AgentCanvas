"""knowledge bases (S24 Knowledge Studio)

`knowledge_base` per project with its embedding model and chunking options, `knowledge_document` with the extracted
text, `knowledge_chunk` with each chunk's text and embedding (float32 bytes).

Revision ID: d5f9b3a7c1e4
Revises: c4e8a2f6b0d3
Create Date: 2026-10-10 18:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5f9b3a7c1e4"
down_revision: str | None = "c4e8a2f6b0d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    ts = {"server_default": sa.text("CURRENT_TIMESTAMP"), "nullable": False}
    op.create_table(
        "knowledge_base",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("project_id", sa.String(), sa.ForeignKey("project.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("embedding", JSON, nullable=False),
        sa.Column("chunking", JSON, nullable=False),
        sa.Column("stale", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **ts),
        sa.Column("updated_at", sa.DateTime(timezone=True), **ts),
        sa.UniqueConstraint("project_id", "name", name="knowledge_base_name"),
    )
    op.create_index("ix_knowledge_base_project_id", "knowledge_base", ["project_id"])
    op.create_table(
        "knowledge_document",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("kb_id", sa.String(), sa.ForeignKey("knowledge_base.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("sha256", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'ready'"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chunks", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("app_user.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), **ts),
    )
    op.create_index("ix_knowledge_document_kb_id", "knowledge_document", ["kb_id"])
    op.create_table(
        "knowledge_chunk",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("kb_id", sa.String(), sa.ForeignKey("knowledge_base.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "doc_id", sa.String(), sa.ForeignKey("knowledge_document.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("heading", sa.Text(), nullable=True),
        sa.Column("vector", sa.LargeBinary(), nullable=True),
    )
    op.create_index("knowledge_chunk_kb", "knowledge_chunk", ["kb_id", "doc_id", "ordinal"])
    op.create_index("ix_knowledge_chunk_doc_id", "knowledge_chunk", ["doc_id"])


def downgrade() -> None:
    op.drop_table("knowledge_chunk")
    op.drop_table("knowledge_document")
    op.drop_table("knowledge_base")
