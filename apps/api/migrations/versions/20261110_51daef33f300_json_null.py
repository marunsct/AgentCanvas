"""JSON null in nullable JSON columns becomes SQL NULL (2026-11-10)

Python None was stored as the JSON value null, so `recording IS NOT NULL` matched resume runs, which keep no
recording: run search indexed a resume without its question, and online evaluators judged a later resume with no
input or labels. The column type now writes SQL NULL; this turns the rows written before into SQL NULL too. One
statement per table, so each table is read once.

Revision ID: 51daef33f300
Revises: b8e2f6a0c4d7
Create Date: 2026-11-10 12:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "51daef33f300"
down_revision: str | None = "b8e2f6a0c4d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NULLABLE_JSON = {  # every nullable JSON column, as of this revision
    "app_user": ["preferences"],
    "organization": ["environments", "settings", "sso_groups"],
    "oauth_client": ["resources"],
    "knowledge_base": ["recrawl"],
    "copilot_conversation": ["brief"],
    "dataset": ["langsmith"],
    "test_case": ["context", "last_result"],
    "experiment": ["simulation"],
    "release_gate": ["evaluators"],
    "experiment_result": ["output", "trajectory", "scores"],
    "run": ["context", "recording", "events", "labels"],
    "interrupt_task": ["decision"],
}


def upgrade() -> None:
    postgres = op.get_bind().dialect.name == "postgresql"
    for table, columns in NULLABLE_JSON.items():
        # Postgres keeps JSONB (jsonb_typeof is 'null' for the JSON value null); SQLite keeps the text 'null'
        is_null = (lambda c: f"jsonb_typeof({c}) = 'null'") if postgres else (lambda c: f"{c} = 'null'")
        sets = ", ".join(f"{c} = CASE WHEN {is_null(c)} THEN NULL ELSE {c} END" for c in columns)
        op.execute(f"UPDATE {table} SET {sets} WHERE {' OR '.join(is_null(c) for c in columns)}")


def downgrade() -> None:
    pass  # SQL NULL reads back as None, as the JSON null did
