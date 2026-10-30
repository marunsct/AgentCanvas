"""N-8: infra/schema.sql lists every table the models define (regenerate it with scripts/export_schema_sql.sh)."""

from __future__ import annotations

import re
from pathlib import Path

from agentcanvas.db.models import Base

SCHEMA = Path(__file__).resolve().parents[3] / "infra" / "schema.sql"


def test_the_reference_schema_has_every_table_and_every_column():
    text = SCHEMA.read_text()
    tables = {
        m.group(1): m.group(2) for m in re.finditer(r"^CREATE TABLE public\.(\w+) \((.*?)^\);", text, re.M | re.S)
    }
    missing = sorted(set(Base.metadata.tables) - set(tables))
    assert not missing, f"infra/schema.sql lacks {missing}; run scripts/export_schema_sql.sh"
    for name, table in Base.metadata.tables.items():
        absent = [
            c.name for c in table.columns if not re.search(rf"^\s+\"?{re.escape(c.name)}\"?\s", tables[name], re.M)
        ]
        assert not absent, f"infra/schema.sql: table {name} lacks columns {absent}; run scripts/export_schema_sql.sh"
