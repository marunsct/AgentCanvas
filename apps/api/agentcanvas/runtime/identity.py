"""Who a run acts for (G-7): the organisation and, when a person started it, that person.

The runner sets it for the task that executes a run, and the API sets it for a request that browses a connector's tools.
An app connector that signs in with OAuth reads it when it makes a call, so one compiled graph and one list of
tools serve every person, each call carrying the caller's own connection rather than a token baked in when the
graph was built.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class RunIdentity:
    org_id: str | None
    user_id: str | None  # None: no person started this run (a schedule, a webhook, a deployment, a share link)
    # the organisation's service connection may be used although a person started it: a run of a deployment (resumed
    # from the inbox, say) or an admin's own. A builder's draft run may not borrow the admin's connection.
    service_ok: bool = False


CURRENT: ContextVar[RunIdentity | None] = ContextVar("agentcanvas_run_identity", default=None)
