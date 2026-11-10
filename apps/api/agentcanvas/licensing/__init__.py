"""AgentCanvas's own license (Business Source License 1.1, see LICENSE at the repository root).

The API image is built from apps/api, so the license travels in the package as `licensing/LICENSE`, a copy of the
root file (tests/test_licensing.py fails when they differ). Every export that carries AgentCanvas code (its runtime
modules, generated code) gets it as `LICENSE-AgentCanvas.txt`: the License must be displayed on each copy.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

SPDX = "BUSL-1.1"
EXPORT_NAME = "LICENSE-AgentCanvas.txt"


@cache
def text() -> str:
    return (Path(__file__).parent / "LICENSE").read_text(encoding="utf-8")


def with_license(files: dict, prefix: str = "") -> dict:
    """`files` (paths in an export) with AgentCanvas's license added at `prefix` (the project's folder)."""
    return {**files, f"{prefix}{EXPORT_NAME}": text()}
