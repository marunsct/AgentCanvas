"""Which models take sampling settings (temperature, top_p, top_k), from the model catalog's `sampling` flag.

Current Claude models refuse a non-default temperature with a 400, and reasoning models such as GPT-5 take only the
default, so a model the catalog marks `"sampling": false` is not sent them (W020). Standalone, without the registry,
because exported projects vendor this module and the catalog file to build their model clients the same way.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

CATALOG = Path(__file__).parent / "registry" / "models.json"
SAMPLING = ("temperature", "top_p", "top_k")


@cache
def refusing() -> frozenset[str]:
    """The model ids the catalog says take no sampling settings."""
    if not CATALOG.is_file():
        return frozenset()
    return frozenset(m["id"] for m in json.loads(CATALOG.read_text()) if m.get("sampling") is False)


def without_sampling(model_id: str, params: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """`params` without the sampling settings `model_id` refuses, and the names left out."""
    if model_id not in refusing():
        return params, []
    dropped = [k for k in SAMPLING if k in params]
    return {k: v for k, v in params.items() if k not in SAMPLING}, dropped
