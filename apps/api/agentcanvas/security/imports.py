"""Which Python modules user code may be imported from (validator rule E080, runtime safe_import).

Kept free of framework imports so the framework-neutral IR package can use it.
"""

from __future__ import annotations

import os

# shared code at the workspace root, and each workflow's own code under flows/<workflow>/ (S17)
WORKSPACE_IMPORT_PREFIXES = ("scripts.", "tools.", "nodes.", "events.", "flows.")
DEV_ONLY_IMPORT_PREFIXES = ("examples.",)  # demo code: importable only in dev/test (review v0.6 V-10)


def allowed_import_prefixes(env: str | None = None) -> tuple[str, ...]:
    env = env or os.getenv("AGENTCANVAS_ENV", "dev")
    return WORKSPACE_IMPORT_PREFIXES + (DEV_ONLY_IMPORT_PREFIXES if env in ("dev", "test") else ())


def is_allowed_import_path(import_path: str, env: str | None = None) -> bool:
    module, sep, attr = import_path.partition(":")
    return bool(sep and attr and module.startswith(allowed_import_prefixes(env)))
