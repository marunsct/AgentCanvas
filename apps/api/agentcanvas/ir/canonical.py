"""Canonical JSON and hashing of IR documents (spec doc 03 §2.6).

`ir_hash` identifies a workflow's behaviour: it is used to cache builds, tag runs and detect
drift. Layout is stored separately, so moving nodes never changes the hash.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from agentcanvas.ir.models import Workflow


def canonical_dict(wf: Workflow) -> dict[str, Any]:
    return wf.model_dump(mode="json")


def canonical_json(value: Workflow | dict[str, Any]) -> str:
    data = canonical_dict(value) if isinstance(value, Workflow) else value
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def ir_hash(value: Workflow | dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
