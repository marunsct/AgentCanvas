"""A migration that adds an index to a large table builds it concurrently on Postgres (`postgresql_concurrently`
inside an `autocommit_block`): a plain CREATE INDEX blocks every write to the table while it builds, during a rolling
upgrade whose old servers still serve. Migrations up to the run queue's (b8e2f6a0c4d7) are left as they ran."""

from __future__ import annotations

import ast
import pathlib

from alembic.script import ScriptDirectory

LARGE = {"run", "thread", "audit_log", "event_log", "interrupt_task", "knowledge_chunk", "run_search", "run_score"}
SINCE = "b8e2f6a0c4d7"


def _newer() -> list[pathlib.Path]:
    script = ScriptDirectory(str(pathlib.Path(__file__).parents[1] / "migrations"))
    return [pathlib.Path(r.path) for r in script.iterate_revisions("heads", SINCE) if r.revision != SINCE]


def blocking_indexes(source: str) -> list[str]:
    """The `create_index` calls on a large table that do not build concurrently (in `upgrade`, when there is one: a
    downgrade puts back what was there)."""
    tree = ast.parse(source)
    scope = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"), tree)
    found = []
    for node in ast.walk(scope):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "create_index":
            continue
        kw = {k.arg: k.value for k in node.keywords}
        table = node.args[1] if len(node.args) > 1 else kw.get("table_name")
        if not (isinstance(table, ast.Constant) and table.value in LARGE):
            continue
        concurrently = kw.get("postgresql_concurrently")
        sqlite_only = "sqlite_where" in kw and "postgresql_where" not in kw
        if not (isinstance(concurrently, ast.Constant) and concurrently.value is True) and not sqlite_only:
            found.append(f"{table.value}: line {node.lineno}")
    return found


def test_new_indexes_on_large_tables_are_built_concurrently():
    newer = _newer()
    assert newer, "the run queue's migration should have successors"
    offenders = {p.name: blocking_indexes(p.read_text()) for p in newer}
    assert {name: found for name, found in offenders.items() if found} == {}


def test_the_check_finds_a_blocking_index():
    assert blocking_indexes('op.create_index("run_x", "run", ["status"])') == ["run: line 1"]
    assert blocking_indexes('op.create_index("t_x", "tiny", ["a"])') == []
    concurrent = 'op.create_index("run_x", "run", ["a"], postgresql_where=w, postgresql_concurrently=True)'
    assert blocking_indexes(concurrent) == []
