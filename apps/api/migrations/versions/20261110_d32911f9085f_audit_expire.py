"""retention deletes audit entries through a function, so the API's role may not (2026-11-10)

The API's database role loses UPDATE and DELETE on `audit_log` (and may only read `audit_anchor`) in
`agentcanvas.db.app_role`, so a SQL injection, a compromised API server or a bug cannot rewrite or remove history.
Retention still deletes entries past the audit retention: through `audit_expire(org, cutoff)`, which runs as its
owner (this migration's role, SECURITY DEFINER) and can do only that: delete the start of the organisation's hash
chain, up to the first entry younger than the cutoff, record where the chain now starts in `audit_anchor`, and refuse
a cutoff less than a day old (23 hours, for clocks a little apart). It mirrors `RetentionJob._expire_audit`, which
SQLite (no roles) keeps using. Postgres only.

Revision ID: d32911f9085f
Revises: 24a6fd6e48f9
Create Date: 2026-11-10 22:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "d32911f9085f"
down_revision: str | None = "24a6fd6e48f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTION = """
CREATE OR REPLACE FUNCTION audit_expire(p_org text, p_cutoff timestamptz) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    v_kept bigint;
    v_last bigint;
    v_hash text;
    v_rows integer;
BEGIN
    IF p_cutoff IS NULL OR p_cutoff > now() - interval '23 hours' THEN
        RAISE EXCEPTION 'audit_expire: audit entries younger than a day are kept (cutoff %)', p_cutoff;
    END IF;
    IF p_org IS NULL THEN  -- entries of no organisation have no chain
        DELETE FROM public.audit_log WHERE org_id IS NULL AND created_at < p_cutoff;
        GET DIAGNOSTICS v_rows = ROW_COUNT;
        RETURN v_rows;
    END IF;
    SELECT id INTO v_kept FROM public.audit_log
        WHERE org_id = p_org AND created_at >= p_cutoff ORDER BY id LIMIT 1;
    SELECT id, row_hash INTO v_last, v_hash FROM public.audit_log
        WHERE org_id = p_org AND created_at < p_cutoff AND (v_kept IS NULL OR id < v_kept)
        ORDER BY id DESC LIMIT 1;
    IF v_last IS NULL THEN
        RETURN 0;
    END IF;
    DELETE FROM public.audit_log WHERE org_id = p_org AND id <= v_last;
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    IF v_rows > 0 THEN  -- another server may have deleted them a moment ago
        INSERT INTO public.audit_anchor (org_id, through_id, last_hash, rows) VALUES (p_org, v_last, v_hash, v_rows);
    END IF;
    RETURN v_rows;
END
$$
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(FUNCTION)
    op.execute("REVOKE ALL ON FUNCTION audit_expire(text, timestamptz) FROM PUBLIC")  # granted to the API's role only


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS audit_expire(text, timestamptz)")
