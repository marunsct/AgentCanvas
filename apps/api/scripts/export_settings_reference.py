"""docs/user-guide/server-settings.md: every server setting, from `agentcanvas/settings.py` and the code.

    uv run python scripts/export_settings_reference.py          # rewrite the page
    uv run python scripts/export_settings_reference.py --check  # exit 1 when the page is out of date

Each setting's text is the comment written next to it in settings.py, so the page cannot drift from the code:
tests/test_settings_reference.py fails when it does. Variables some modules read directly from the environment
(not through Settings) are listed after, with where they are read.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

API = Path(__file__).resolve().parents[1]
ROOT = API.parents[1]
SOURCE = API / "agentcanvas" / "settings.py"
PAGE = ROOT / "docs" / "user-guide" / "server-settings.md"
DIRECT = re.compile(r"""os\.(?:environ\.get|getenv|environ\[)\(?\s*["'](AGENTCANVAS_[A-Z0-9_]+)["']""")
SECRET = re.compile(r"(secret|password|token|_key$|keys$)")

HEAD = """# Server settings

Every setting of the AgentCanvas server, set as an environment variable (or in `apps/api/.env` when running from
source). Generated from the server's code by `apps/api/scripts/export_settings_reference.py`; do not edit by hand.
Settings \u203a **Server** shows the effective values of the most important ones on a running server.

Lists are JSON (`["a", "b"]`), booleans `true` or `false`. Settings whose names end in `_S` are seconds.
"""


def _comments(source: str) -> dict[int, str]:
    """Comment text by line number."""
    out = {}
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            text = tok.string.lstrip("#").strip()
            if not text.startswith(("fmt:", "type:", "noqa", "pyright:")) and not re.fullmatch(r"[A-Z0-9_]+", text):
                out[tok.start[0]] = text
    return out


def _settings_class(tree: ast.Module) -> ast.ClassDef:
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Settings")


def _env_name(field: str, value: ast.AST | None) -> str:
    if isinstance(value, ast.Call):
        for kw in value.keywords:
            if kw.arg == "validation_alias" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
    return "AGENTCANVAS_" + field.upper()


def rows() -> list[tuple[str, str, str]]:
    """(variable, default, what it does) for every Settings field, in the order settings.py declares them."""
    sys.path.insert(0, str(API))
    from agentcanvas.settings import Settings

    source = SOURCE.read_text()
    comments = _comments(source)
    lines = source.splitlines()
    out = []
    previous_end = -1  # the last line of the field before, when nothing came between (a group under one comment)
    for stmt in _settings_class(ast.parse(source)).body:
        if not isinstance(stmt, ast.AnnAssign) or not isinstance(stmt.target, ast.Name):
            continue
        name = stmt.target.id
        if name not in Settings.model_fields:
            continue
        text = []
        line = stmt.lineno - 1  # the comment lines just above it, then the one on its own line
        while line >= 1 and lines[line - 1].strip().startswith("#") and line in comments:
            text.insert(0, comments[line])
            line -= 1
        last = stmt.end_lineno or stmt.lineno
        for ln in range(stmt.lineno, last + 1):
            if ln in comments:
                text.append(comments[ln])
        if not text and previous_end == stmt.lineno - 1:
            text = ["As the row above (one comment in settings.py covers these)."]
        previous_end = stmt.end_lineno or stmt.lineno
        default = Settings.model_fields[name].default
        shown = "" if default is None else repr(default) if not isinstance(default, str) else f'"{default}"'
        if SECRET.search(name) and isinstance(default, str) and default:
            shown = f"{shown} (change it)"
        out.append((_env_name(name, stmt.value), shown.replace("|", "\\|"), " ".join(text).replace("|", "\\|")))
    return out


def direct() -> list[tuple[str, list[str]]]:
    """Variables read straight from the environment somewhere in the server's code, with the files."""
    known = {r[0] for r in rows()}
    found: dict[str, set[str]] = {}
    for path in sorted((API / "agentcanvas").rglob("*.py")):
        for name in DIRECT.findall(path.read_text(errors="replace")):
            if name not in known:
                found.setdefault(name, set()).add(str(path.relative_to(API)))
    return [(n, sorted(f)) for n, f in sorted(found.items())]


def page() -> str:
    parts = [HEAD, "| Variable | Default | What it does |", "|---|---|---|"]
    parts += [f"| `{v}` | {f'`{d}`' if d else ''} | {t} |" for v, d, t in rows()]
    others = direct()
    if others:
        parts += ["", "## Read directly from the environment", "",
                  "These are read where they are used rather than through the server's settings:", "",
                  "| Variable | Read in |", "|---|---|"]  # fmt: skip
        parts += [f"| `{n}` | {', '.join(f'`{f}`' for f in files)} |" for n, files in others]
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail when the page is out of date")
    args = parser.parse_args(argv)
    text = page()
    if args.check:
        current = PAGE.read_text() if PAGE.exists() else ""
        if current != text:
            print(f"{PAGE.relative_to(ROOT)} is out of date: run scripts/export_settings_reference.py", file=sys.stderr)
            return 1
        return 0
    PAGE.write_text(text)
    print(f"{PAGE.relative_to(ROOT)}: {len(rows())} settings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
