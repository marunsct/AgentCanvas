"""Export every starter template to every deploy target (S16 CI): `python scripts/export_templates.py <dir>`.

Writes <dir>/<target>/<template>/<project>/ so CI can lint the deploy files, run each export's own tests and
deploy some of them for real.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

from agentcanvas.adapters import get_adapter
from agentcanvas.adapters.langchain.deploykit import TARGETS
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry

TEMPLATES = Path(__file__).resolve().parents[1] / "agentcanvas/registry/templates"


def main(out: Path) -> None:
    files = sorted(TEMPLATES.glob("*.ir.json"))
    for f in files:
        wf = Workflow.model_validate(json.loads(f.read_text()))
        for target in TARGETS:
            _, data = get_adapter(wf.framework).package(wf, default_registry(), target=target, version="1.0.0")
            zipfile.ZipFile(io.BytesIO(data)).extractall(out / target / f.name.removesuffix(".ir.json"))
    print(f"exported {len(files)} templates to {len(TARGETS)} targets in {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
