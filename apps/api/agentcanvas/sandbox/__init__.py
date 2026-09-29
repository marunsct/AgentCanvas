"""Sandboxed execution of workspace scripts (M5-T4). Filled in by the sandbox runners."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentcanvas.settings import Settings


def script_runner(settings: Settings, workspace: Path, secrets: dict[str, str]) -> Any:
    """A factory `import_path -> async node` for sandboxed modes, or None to run scripts in-process."""
    if settings.script_execution == "inprocess":
        return None
    from agentcanvas.sandbox.runners import make_runner

    return make_runner(settings, workspace, secrets)
