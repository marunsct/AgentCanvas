"""Graceful shutdown starts at SIGTERM (FR-X-10).

uvicorn runs the lifespan's shutdown only after it has waited up to `--timeout-graceful-shutdown` (30 s) for open
connections. Waiting that long before draining had two costs: the schedulers (hand-over, cron, experiments, crawls)
went on claiming and starting runs on a server about to stop, and the pod's grace period ran out before the steps
after the drain (online scores, the search index, trace exports). So the wind-down begins when the signal arrives:
the schedulers stop, endless streams (presence) end so uvicorn's wait does not last, and the runs get their drain
grace while uvicorn waits for the rest. The lifespan's shutdown then awaits that same wind-down and does what is
left.

The budget a pod needs, kept below `terminationGracePeriodSeconds` and compose's `stop_grace_period`:
`PRESTOP_S + max(STREAM_WAIT_S, drain_grace_s) + AFTER_DRAIN_S`.
"""

from __future__ import annotations

import asyncio
import signal
import threading
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from typing import Any

PRESTOP_S = 5  # the chart's preStop sleep: the Service drops the pod first
STREAM_WAIT_S = 30  # uvicorn's --timeout-graceful-shutdown (infra/Dockerfile.api)
AFTER_DRAIN_S = 25  # online scores and the search index (10 s at most, side by side), trace exports, closing up
SIGNALS = (signal.SIGTERM, signal.SIGINT)  # the ones uvicorn shuts down on


class WindDown:
    """Runs `steps` once, from the first stop signal or from the lifespan's shutdown, whichever comes first."""

    def __init__(self, steps: Callable[[], Coroutine[Any, Any, None]]):
        self._steps = steps
        self._task: asyncio.Task[None] | None = None

    def begin(self) -> asyncio.Task[None]:
        if self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._steps(), name="wind-down")
        return self._task

    @contextmanager
    def on_signals(self) -> Iterator[None]:
        """Begin at SIGTERM / SIGINT as well, then hand the signal on to the handler that was there (uvicorn's,
        which starts its own shutdown). Signal handlers can only be set from the main thread: elsewhere this does
        nothing, and the wind-down begins at the lifespan's shutdown as before."""
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        loop = asyncio.get_running_loop()
        previous = {sig: signal.getsignal(sig) for sig in SIGNALS}

        def handler(sig: int, frame: Any) -> None:
            loop.call_soon_threadsafe(self.begin)
            prev = previous[signal.Signals(sig)]
            if callable(prev):
                prev(sig, frame)
            elif prev == signal.SIG_DFL:  # nobody shuts down gracefully on it: it ends the process, as before
                signal.signal(sig, signal.SIG_DFL)
                signal.raise_signal(sig)

        for sig in SIGNALS:
            signal.signal(sig, handler)
        try:
            yield
        finally:
            for sig, prev in previous.items():
                signal.signal(sig, prev)
