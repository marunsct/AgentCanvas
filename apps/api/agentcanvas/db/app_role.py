"""Create or update the API's own database role: a login that is neither a superuser nor BYPASSRLS.

Row-level security (`db/rls.py`) is skipped by superusers, and the official Postgres image makes its
`POSTGRES_USER` one. So the owner runs the migrations and the API connects as this ordinary role (H4 of the
2026-10-18 review). Run it after `alembic upgrade head`, with `DATABASE_URL` pointing at the owner:

    APP_DB_USER=agentcanvas_app APP_DB_PASSWORD=... python -m agentcanvas.db.app_role

It is idempotent: an existing role gets its password and attributes reset and its grants renewed.

The role may read and add audit entries but never change or delete them (ASVS V16.4.2): retention deletes old ones
through `audit_expire`, a function that runs as the owner and can do only that (migration d32911f9085f).
"""

from __future__ import annotations

import os
import re
import sys
from typing import LiteralString, cast

from agentcanvas.db.rls import ALL_TENANTS

NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
NO_DEFAULT = (
    f"app_role: only a superuser may give '{{role}}' the default {ALL_TENANTS}=on. Its connections still get it as a "
    "startup option, which a connection pooler such as PgBouncer refuses or drops: have a superuser run "
    f"ALTER ROLE {{role}} SET {ALL_TENANTS} = 'on', or see the operations guide's PgBouncer advice"
)


def statements(role: str) -> list[str]:
    """The SQL templates: `{role}` takes the role as an identifier, `{pw}` the password as a literal (DDL takes no
    bind parameters)."""
    if not NAME.match(role):
        raise ValueError(f"'{role}' is not a valid role name")
    return [
        "CREATE ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD {pw}",
        "GRANT USAGE, CREATE ON SCHEMA public TO {role}",  # CREATE: the run store makes its own tables
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}",
        # the audit log is append-only for the API, and where retention left its hash chain is the owner's to write
        "REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM {role}",
        "REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON audit_anchor FROM {role}",
        "GRANT EXECUTE ON FUNCTION audit_expire(text, timestamptz) TO {role}",
        # every connection starts in all-tenants mode (db/rls.py) even when a pooler such as PgBouncer refuses or
        # drops the startup option that also asks for it; a Matura request narrows its own transactions
        f"ALTER ROLE {{role}} SET {ALL_TENANTS} = 'on'",
    ]


# the audit log's statements need its tables and function: a restore creates the role on an empty database first
AUDIT_READY = (
    "SELECT to_regclass('public.audit_log') IS NOT NULL AND to_regclass('public.audit_anchor') IS NOT NULL"
    " AND to_regprocedure('public.audit_expire(text, timestamptz)') IS NOT NULL"
)
NOT_MIGRATED = (
    "app_role: the audit log's tables are not there yet (an empty database): its privileges for '{role}' are set when "
    "this runs again after the migrations"
)


def ensure(url: str, role: str, password: str) -> None:
    import psycopg
    from psycopg import sql

    if not password:
        raise ValueError("APP_DB_PASSWORD is empty")
    url = url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        audit_ready = bool((conn.execute(AUDIT_READY).fetchone() or (False,))[0])
        if not audit_ready:
            print(NOT_MIGRATED.format(role=role), file=sys.stderr)
        for template in statements(role):
            if "audit_" in template and not audit_ready:
                continue
            query = template
            if exists and query.startswith("CREATE ROLE"):
                query = query.replace("CREATE ROLE {role} LOGIN", "ALTER ROLE {role} WITH LOGIN")
            # the statements are this module's own constants; psycopg fills in only the identifier and the literal
            statement = sql.SQL(cast(LiteralString, query)).format(role=sql.Identifier(role), pw=sql.Literal(password))
            if query.startswith(f"ALTER ROLE {{role}} SET {ALL_TENANTS}"):
                try:
                    conn.execute(statement)
                except psycopg.errors.InsufficientPrivilege:  # only a superuser may (a managed Postgres's owner is not)
                    print(NO_DEFAULT.format(role=role), file=sys.stderr)
                continue
            conn.execute(statement)


def main() -> int:
    url, role, password = (
        os.environ.get("DATABASE_URL", ""),
        os.environ.get("APP_DB_USER", ""),
        os.environ.get("APP_DB_PASSWORD", ""),
    )
    if not url.startswith(("postgresql", "postgres")) or not role:
        print("app_role: nothing to do (no Postgres DATABASE_URL or APP_DB_USER)", file=sys.stderr)
        return 0
    ensure(url, role, password)
    print(f"app_role: '{role}' is ready (no superuser, no BYPASSRLS)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
