"""Run the API with scripted fake models (no API keys needed):

uvicorn agentcanvas.dev.fake_server:app --reload --port 8000
"""

from agentcanvas.api import app as api
from agentcanvas.runtime.nodes import Resources
from agentcanvas.testing import scripted, tool_call

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
app = api.app
