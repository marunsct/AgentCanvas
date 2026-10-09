"""Importing this package registers every validation rule with the engine."""

from agentcanvas.validation.rules import agents, channels, config, graph, portability, ports, security, structure

__all__ = ["agents", "channels", "config", "graph", "portability", "ports", "security", "structure"]
