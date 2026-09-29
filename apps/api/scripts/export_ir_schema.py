"""Export the IR JSON Schema and node manifests for the web app (ticket M1-T7).

uv run python scripts/export_ir_schema.py      # writes packages/ir-schema/*.json
"""

from __future__ import annotations

import json
from pathlib import Path

from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry

OUT = Path(__file__).parents[3] / "packages" / "ir-schema"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ir.schema.json").write_text(json.dumps(Workflow.model_json_schema(), indent=2) + "\n")
    manifests = [m.model_dump(mode="json") for m in default_registry().all()]
    (OUT / "node-types.json").write_text(json.dumps(manifests, indent=2) + "\n")
    from agentcanvas.api.main import create_app

    openapi = create_app().openapi()
    (OUT / "openapi.json").write_text(json.dumps(openapi, indent=2, sort_keys=True) + "\n")
    print(f"wrote {OUT}: ir.schema.json, node-types.json ({len(manifests)} node types), openapi.json")


if __name__ == "__main__":
    main()
