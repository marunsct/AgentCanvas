"""Graceful shutdown starts at SIGTERM: uvicorn waits up to 30 s for open connections before the lifespan's shutdown,
and during that wait the schedulers must not start or claim runs, endless streams must end, and the runs drain."""

from __future__ import annotations

import asyncio
import signal

import httpx
from test_kill_switch import SLOW, _start_slow
from test_redeploy import run_row
from test_runs_v1 import make

from agentcanvas.api.main import create_app

# the hand-over job stops last, after the drain: its ticks keep the heartbeat of runs finishing in the grace period
JOBS = ("experiments", "cron", "retention", "crawl", "secrets", "handover")


async def rest(stream) -> list[str]:
    return [frame async for frame in stream]


async def test_sigterm_stops_the_schedulers_ends_streams_and_drains_before_uvicorn_waits(settings, monkeypatch):
    handed_on: list[int] = []

    def uvicorns(sig: int, frame: object) -> None:  # stands in for uvicorn's handler
        handed_on.append(sig)

    before = signal.signal(signal.SIGTERM, uvicorns)
    try:
        app = create_app(settings.model_copy(update={"drain_grace_s": 0.0}))
        async with app.router.lifespan_context(app):
            stopped: list[str] = []
            for job in JOBS:
                stop = getattr(app.state, job).stop

                async def recorded(job: str = job, stop=stop) -> None:
                    stopped.append(job)
                    await stop()

                monkeypatch.setattr(getattr(app.state, job), "stop", recorded)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                wf = await make(client, SLOW)
                run_id = await _start_slow(app, client, wf)
            presence = app.state.presence.stream(wf)
            assert (await anext(presence)).startswith("data: ")

            signal.raise_signal(signal.SIGTERM)  # the lifespan's shutdown comes only after uvicorn's wait

            assert handed_on == [signal.SIGTERM]  # uvicorn still hears it and starts its own shutdown
            # ends at once, instead of holding uvicorn's wait for open connections
            assert await asyncio.wait_for(rest(presence), 5) == []
            await asyncio.wait_for(app.state.runner.wait(run_id), 10)
            for _ in range(100):  # the hand-over job stops once the drain is over
                if len(stopped) == len(JOBS):
                    break
                await asyncio.sleep(0.05)
            assert app.state.runner.draining and stopped == list(JOBS)
            row = await run_row(app, run_id)
            assert (row.status, row.handover) == ("interrupted", True)  # handed over during the wait
        assert stopped == list(JOBS)  # the lifespan's shutdown did not stop them a second time
        assert signal.getsignal(signal.SIGTERM) is uvicorns  # given back
    finally:
        signal.signal(signal.SIGTERM, before)


async def test_without_a_signal_the_lifespan_winds_down_as_before(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        assert not app.state.runner.draining
    assert app.state.runner.draining and app.state.presence.closed
