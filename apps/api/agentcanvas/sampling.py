"""Which models take sampling settings (temperature, top_p, top_k), from the model catalog's `sampling` flag.

Reasoning models such as GPT-5 take only the default, so a model the catalog marks `"sampling": false` is not sent
them (W020). Every Claude model refuses them: the Anthropic SDK 2.x sends API version 2026-10-21, on which a request
with `temperature`, `top_p` or `top_k` gets a 400 whatever the model, so the whole `anthropic` provider refuses them,
listed in the catalog or not. Standalone, without the registry, because exported projects vendor this module and the
catalog file to build their model clients the same way.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

CATALOG = Path(__file__).parent / "registry" / "models.json"
SAMPLING = ("temperature", "top_p", "top_k")
# providers whose every model refuses sampling settings (the Anthropic SDK's API version 2026-10-21)
REFUSING_PROVIDERS = frozenset({"anthropic"})


@cache
def refusing() -> frozenset[str]:
    """The model ids the catalog says take no sampling settings."""
    if not CATALOG.is_file():
        return frozenset()
    return frozenset(m["id"] for m in json.loads(CATALOG.read_text()) if m.get("sampling") is False)


def refuses(model_id: str) -> bool:
    """`model_id` takes no sampling settings: its provider refuses them, or the catalog says so."""
    model_id = str(model_id or "")
    if model_id.partition(":")[0] in REFUSING_PROVIDERS:
        return True
    return model_id in refusing()


def without_sampling(model_id: str, params: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """`params` without the sampling settings `model_id` refuses, and the names left out."""
    if not refuses(model_id):
        return params, []
    dropped = [k for k in SAMPLING if k in params]
    return {k: v for k, v in params.items() if k not in SAMPLING}, dropped
