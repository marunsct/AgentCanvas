"""Run the API with scripted fake models (no API keys needed):

uvicorn agentcanvas.dev.fake_server:app --reload --port 8000

The example workflows in `examples/*.ir.json` are preloaded.
"""

import json
import pathlib

from agentcanvas.api import legacy as api
from agentcanvas.api.main import app as _app
from agentcanvas.ir.models import Workflow
from agentcanvas.runtime.nodes import Resources
from agentcanvas.testing import scripted, tool_call

EXAMPLES = pathlib.Path(__file__).parents[2] / "examples"

_per_thread: dict[str, Resources] = {}


def fake_resources(thread_id: str) -> Resources:
    """Scripted fakes are stateful, so each thread gets its own script."""
    if thread_id not in _per_thread:
        _per_thread[thread_id] = Resources(
            overrides={
                "fast_model": scripted("low"),
                "smart_model": scripted(tool_call("send_reply", {"text": "Hi"}), "Reply sent."),
            }
        )
    return _per_thread[thread_id]


api.S.resources_factory = fake_resources
for path in sorted(EXAMPLES.glob("*.ir.json")):
    wf = Workflow.model_validate(json.loads(path.read_text()))
    api.S.workflows[wf.id] = wf
app = _app
