"""A run that calls another deployment of its organisation (a Remote agent step over A2A, an MCP tool pointing at a
deployment) waits for that run while it holds a slot of the run queue. With every slot held by such callers, their
own calls would wait behind them until they timed out. So each A2A and MCP call a run makes carries the run's id,
signed with the server's secret (`X-AgentCanvas-Run`); a deployment run started with a valid one, while that run is
in flight in the same organisation, takes no place in the queue: it works within its caller's slot.

The header carries only the run id and a signature no one else can check or make.
"""

from __future__ import annotations

import hashlib
import hmac

from agentcanvas.runtime.identity import CURRENT

HEADER = "X-AgentCanvas-Run"
_KEY: dict[str, bytes] = {}  # "key": the server's secret, once installed


def install(secret: str | None) -> None:
    """Set by the server at startup (the server's secret key): its runs' A2A and MCP calls carry the header."""
    from agentcanvas.adapters.langchain import nodes

    _KEY.clear()
    if secret:
        _KEY["key"] = secret.encode()
    nodes.RUN_HEADERS = header if secret else None


def _sign(key: bytes, run_id: str) -> str:
    return hmac.new(key, f"nested-run:{run_id}".encode(), hashlib.sha256).hexdigest()[:32]


def header() -> dict[str, str]:
    """The header that names the run making this call; empty outside a run (or a server)."""
    who = CURRENT.get()
    key = _KEY.get("key")
    if key is None or who is None or who.run_id is None:
        return {}
    return {HEADER: f"{who.run_id}.{_sign(key, who.run_id)}"}


def caller(value: str | None) -> str | None:
    """The run that made this request, when its header is genuine."""
    key = _KEY.get("key")
    if key is None or not value or "." not in value:
        return None
    run_id, _, sig = value.strip().rpartition(".")
    return run_id if run_id and hmac.compare_digest(sig, _sign(key, run_id)) else None
