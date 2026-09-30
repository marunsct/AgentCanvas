"""Project workspaces (spec doc 05, M5): the code attached to the canvas — scripts, tools, custom nodes, events."""

from agentcanvas.workspace.loader import load_attr
from agentcanvas.workspace.store import Workspace, WorkspaceError

__all__ = ["Workspace", "WorkspaceError", "load_attr"]
