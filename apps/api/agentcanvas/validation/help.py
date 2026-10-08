"""Plain-English help for validation codes and common run errors (S15), shared by the editor and the Copilot.

`help.json` is the source; `make schema` copies it to packages/ir-schema/diagnostics.json and the web app's
`pnpm gen:help` into its help module.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

CATALOG = Path(__file__).parent / "help.json"


@cache
def catalog() -> dict[str, Any]:
    return json.loads(CATALOG.read_text())


@cache
def _run_errors() -> list[tuple[re.Pattern[str], dict[str, str]]]:
    return [(re.compile(r["pattern"], re.I), {"what": r["what"], "fix": r["fix"]}) for r in catalog()["run_errors"]]


def diagnostic_help(code: str) -> dict[str, str] | None:
    return catalog()["diagnostics"].get(code)


def run_error_help(message: str | None) -> dict[str, str] | None:
    if not message:
        return None
    return next((h for pattern, h in _run_errors() if pattern.search(message)), None)
