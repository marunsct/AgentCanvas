"""Run the API with stateless demo models (no API keys needed):

    uvicorn agentcanvas.dev.fake_server:app --reload --port 8000

Every chat model resolves to `DemoChatModel`; tools and scripts run for real. Workflows are stored
in the local SQLite database like in normal dev mode.
"""

from agentcanvas.adapters.langchain.testing import DemoResources
from agentcanvas.api.main import app

_demo = DemoResources()
app.state.resources_factory = lambda thread_id, wf: _demo

__all__ = ["app"]
