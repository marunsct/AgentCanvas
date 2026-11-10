"""Rules for the test suite itself, checked so a new test file cannot break them by accident."""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).parent


def test_sign_in_tokens_made_at_import_outlast_the_suite():
    """A token built when its module is imported (a module constant) gets test_auth.token's default five minutes,
    and the whole suite runs for half an hour: tests late in the run then fail with 401. Such tokens set `exp`."""
    made_at_import = re.compile(r"^[A-Z][A-Z0-9_]* = .*\btoken\((?!.*\bexp=)", re.M)
    short = [
        f"{p.name}: {m.group(0)}"
        for p in sorted(TESTS.glob("test_*.py"))
        for m in made_at_import.finditer(p.read_text())
    ]
    assert not short, f"give these an `exp` (e.g. int(time.time()) + 86400): {short}"
