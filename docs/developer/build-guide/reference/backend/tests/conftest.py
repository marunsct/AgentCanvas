import json
import pathlib

import pytest

from agentcanvas.ir.models import Workflow

ROOT = pathlib.Path(__file__).parents[1]


@pytest.fixture
def triage() -> Workflow:
    return Workflow.model_validate(json.loads((ROOT / "examples/support_triage.ir.json").read_text()))
