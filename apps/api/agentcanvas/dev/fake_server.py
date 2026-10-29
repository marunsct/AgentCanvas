"""Run the API with stateless demo models (no API keys needed):

    uvicorn agentcanvas.dev.fake_server:app --reload --port 8000

Every chat model resolves to `DemoChatModel`; tools and workspace scripts run for real. Workflows are stored
in the local SQLite database like in normal dev mode.
"""

from agentcanvas.adapters.langchain.testing import DemoResources
from agentcanvas.api.main import app

app.state.resources_class = DemoResources
app.state.demo_models = True  # every model answers here, so the Copilot's model menu offers the whole catalogue

__all__ = ["app"]
