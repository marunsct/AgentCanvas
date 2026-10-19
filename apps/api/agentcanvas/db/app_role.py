"""Create or update the API's own database role: a login that is neither a superuser nor BYPASSRLS.

Row-level security (`db/rls.py`) is skipped by superusers, and the official Postgres image makes its
`POSTGRES_USER` one. So the owner runs the migrations and the API connects as this ordinary role (H4 of the
2026-10-18 review). Run it after `alembic upgrade head`, with `DATABASE_URL` pointing at the owner:

    APP_DB_USER=agentcanvas_app APP_DB_PASSWORD=... python -m agentcanvas.db.app_role

It is idempotent: an existing role gets its password and attributes reset and its grants renewed.
"""

from __future__ import annotations

import os
import re
import sys
from typing import LiteralString, cast

NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


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
    ]


def ensure(url: str, role: str, password: str) -> None:
    import psycopg
    from psycopg import sql

    if not password:
        raise ValueError("APP_DB_PASSWORD is empty")
    url = url.replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
        for template in statements(role):
            query = template
            if exists and query.startswith("CREATE ROLE"):
                query = query.replace("CREATE ROLE {role} LOGIN", "ALTER ROLE {role} WITH LOGIN")
            # the statements are this module's own constants; psycopg fills in only the identifier and the literal
            conn.execute(
                sql.SQL(cast(LiteralString, query)).format(role=sql.Identifier(role), pw=sql.Literal(password))
            )


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
