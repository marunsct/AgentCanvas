"""Completion callbacks of deployment runs (E-2).

A caller of the deployment API may name a `callback_url` when it starts (or resumes) a run: once the run ends, the
URL is sent the run's result, the same JSON `GET /v1/deployments/{id}/runs/{run_id}` answers, as a `POST`.

- **Only to an https address**, and in the hosted edition only to a public one: checked when the run is asked for,
  and again as each delivery connects (a name that resolves elsewhere later is refused).
- **Signed** like AgentCanvas's incoming webhooks: `X-AgentCanvas-Signature: sha256=<HMAC-SHA256 of
  "<timestamp>.<body>">` with the deployment's callback secret, and `X-AgentCanvas-Timestamp`. The receiver checks
  both (and refuses old timestamps); `X-AgentCanvas-Delivery` (the run id) tells repeats of one callback apart.
- **Delivered at least once:** each callback is a row (`run_callback`). Every server delivers the due ones; a
  conditional update gives each attempt to one server. A failure (no answer, not 2xx) is tried again after
  growing pauses, `MAX_ATTEMPTS` times in all, and every attempt's outcome is kept on the row.
- A deployment has no callback secret until a builder creates one; until then a run asking for a callback is refused.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import random
import secrets
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas import observability
from agentcanvas.db.models import Deployment, Run, RunCallback
from agentcanvas.db.repositories import ScopeRepo
from agentcanvas.errors import InvalidInput
from agentcanvas.log import get_logger
from agentcanvas.security.addresses import is_metadata
from agentcanvas.security.kms import Keyring
from agentcanvas.services.run_results import ResultRepo
from agentcanvas.services.triggers import SIGNATURE_HEADER, TIMESTAMP_HEADER, sign
from agentcanvas.version import VERSION

log = get_logger(__name__)

DELIVERY_HEADER = "X-AgentCanvas-Delivery"
MAX_URL = 2000
MAX_ATTEMPTS = 8
TIMEOUT_S = 10.0  # one delivery's whole time: connect, send, answer
LEASE_S = 60  # an attempt in flight is not taken by another server for this long
INTERVAL_S = 5.0  # how often each server looks for due callbacks (a run that ends here wakes it at once)
BATCH = 50
_ORG_KEY = "v2:"  # encrypted with the organisation's data key, as trigger secrets are


def backoff_s(attempts: int) -> float:
    """The pause after the `attempts`-th failed attempt: 30 s, 90 s, 4.5 min, … at most an hour (about 3 hours in
    all), with some jitter so many callbacks to one receiver spread out."""
    return min(30 * 3 ** (attempts - 1), 3600) * random.uniform(0.8, 1.2)


def _default_client(public_only: bool) -> httpx.AsyncClient:
    if public_only:  # the hosted edition: the address is checked as the connection is made (DNS rebinding)
        from agentcanvas.adapters.langchain.pinned import public_async_client

        return public_async_client(timeout=TIMEOUT_S, follow_redirects=False)
    return httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False)


# the HTTP client deliveries use; tests substitute one that reaches a fake receiver
client_factory: Callable[[bool], httpx.AsyncClient] = _default_client


async def check_url(url: str, public_only: bool) -> str:
    """`url` if a callback may be sent there, else 422 saying why."""
    parts = urlsplit(url)
    if len(url) > MAX_URL:
        raise InvalidInput(f"callback_url is longer than {MAX_URL} characters")
    if parts.scheme != "https" or not parts.hostname:
        raise InvalidInput("callback_url must be an https:// address")
    if parts.username or parts.password or parts.fragment:
        raise InvalidInput("callback_url must not carry a user name, password or #fragment")
    try:
        if is_metadata(parts.hostname):  # the cloud metadata service hands out the server's credentials
            raise InvalidInput("callback_url points at a link-local or metadata address")
    except ValueError:
        pass  # a host name, not an address
    if public_only:
        from agentcanvas.services.trace_export import internal_address

        if (why := await internal_address(url)) is not None:
            raise InvalidInput(f"callback_url: {why} (callbacks go to public addresses only)")
    return url


def new_secret() -> str:
    return "whsec_" + secrets.token_urlsafe(24)


async def set_secret(session: AsyncSession, keyring: Keyring, dep: Deployment) -> str:
    """A new callback secret for the deployment (the old one stops signing at once); shown once."""
    org_id = await ScopeRepo(session).org_of("deployment", dep.id)
    if org_id is None:
        raise InvalidInput(f"deployment '{dep.id}' has no organisation")
    secret = new_secret()
    dep.callback_secret = _ORG_KEY + (await keyring.fernet(session, org_id)).encrypt(secret.encode()).decode()
    await session.commit()
    return secret


async def _secret(session: AsyncSession, keyring: Keyring, dep_id: str) -> str | None:
    stored = await session.scalar(select(Deployment.callback_secret).where(Deployment.id == dep_id))
    org_id = await ScopeRepo(session).org_of("deployment", dep_id)
    if not stored or org_id is None:
        return None
    return (await keyring.fernet(session, org_id)).decrypt(stored.removeprefix(_ORG_KEY).encode()).decode()


async def require_secret(session: AsyncSession, dep: Deployment) -> None:
    if not dep.callback_secret:
        raise InvalidInput(
            "this deployment has no callback secret yet, so its callbacks could not be signed: a builder creates one "
            "on the workflow's Versions tab (Callback secret), or with POST /v1/deployments/{id}/callback-secret"
        )


def body_of(run_id: str, dep_id: str, result: dict[str, Any]) -> bytes:
    """What a callback sends: the run's result as GET answers it, and which deployment and run it is about (a run a
    restart stopped is reported with the run that continued it: `run_id` stays the one the caller holds)."""
    payload = {"type": "run.finished", "deployment_id": dep_id, **result, "run_id": run_id}
    if result["run_id"] != run_id:
        payload["continued_as"] = result["run_id"]
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str).encode()


class CallbackJob:
    """Delivers due callbacks, on every server."""

    def __init__(self, db: Any, runner: Any, keyring: Keyring, public_only: bool = False):
        self.db, self.runner, self.keyring, self.public_only = db, runner, keyring, public_only
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()

    def start(self) -> None:
        observability.job_started("callbacks")
        self._task = asyncio.create_task(self._loop(), name="callbacks")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def on_run_finished(self, h: Any, *_: Any) -> None:
        """A deployment run ended here: its callback, if it has one, need not wait for the next round."""
        if h.deployment_id is not None:
            self._wake.set()

    async def _loop(self) -> None:
        while True:
            try:
                with observability.job_round("callbacks"):
                    await self.tick()
            except Exception:
                log.exception("callbacks.tick_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), INTERVAL_S)
            self._wake.clear()

    async def tick(self) -> list[str]:
        """Attempt every due callback whose run has ended; returns the run ids attempted."""
        now = datetime.now(UTC)
        async with self.db.sessionmaker() as s:
            q = (
                select(RunCallback.run_id, RunCallback.deployment_id, RunCallback.url, RunCallback.attempts)
                .join(Run, Run.id == RunCallback.run_id)
                .where(RunCallback.status == "pending", RunCallback.next_at <= now, Run.status != "running")
                .order_by(RunCallback.next_at)
                .limit(BATCH)
            )
            due = (await s.execute(q)).all()
        attempted: list[str] = []
        for run_id, dep_id, url, attempts in due:
            if await self._attempt(run_id, dep_id, url, attempts):
                attempted.append(run_id)
        return attempted

    async def _attempt(self, run_id: str, dep_id: str, url: str, attempts: int) -> bool:
        async with self.db.sessionmaker() as s:
            result = await ResultRepo(s, self.runner).of(run_id, dep_id)
            if result is None:  # deleted meanwhile (its conversation): the row went with it
                return False
            if result.status == "running":  # a restart stopped it and another server continues it
                await s.execute(
                    update(RunCallback)
                    .where(RunCallback.run_id == run_id, RunCallback.status == "pending")
                    .values(next_at=datetime.now(UTC) + timedelta(seconds=INTERVAL_S))
                )
                await s.commit()
                return False
            claimed = await s.execute(  # one server per attempt: whoever moves `attempts` on first
                update(RunCallback)
                .where(RunCallback.run_id == run_id, RunCallback.status == "pending", RunCallback.attempts == attempts)
                .values(attempts=attempts + 1, next_at=datetime.now(UTC) + timedelta(seconds=LEASE_S))
            )
            await s.commit()
            if claimed.rowcount != 1:  # type: ignore[attr-defined]
                return False
            secret = await _secret(s, self.keyring, dep_id)
        status, error = None, None
        if secret is None:
            error = "the deployment has no callback secret any more"
        else:
            body = body_of(run_id, dep_id, asdict(result))
            status, error = await self._send(url, body, secret, run_id)
        await self._record(run_id, attempts + 1, status, error, final=secret is None)
        return True

    async def _send(self, url: str, body: bytes, secret: str, run_id: str) -> tuple[int | None, str | None]:
        stamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json", "User-Agent": f"AgentCanvas/{VERSION} (run callback)",
            SIGNATURE_HEADER: sign(secret, body, stamp), TIMESTAMP_HEADER: stamp, DELIVERY_HEADER: run_id,
        }  # fmt: skip
        try:
            async with client_factory(self.public_only) as client:
                res = await client.post(url, content=body, headers=headers)
        except Exception as exc:  # noqa: BLE001 - any failure to reach the receiver is a failed attempt
            return None, f"{type(exc).__name__}: {str(exc)[:300]}"
        if 200 <= res.status_code < 300:
            return res.status_code, None
        return res.status_code, f"answered {res.status_code}"

    async def _record(self, run_id: str, attempt: int, status: int | None, error: str | None, final: bool) -> None:
        now = datetime.now(UTC)
        if error is None:
            values: dict[str, Any] = {"status": "delivered", "delivered_at": now, "last_error": None}
        elif final or attempt >= MAX_ATTEMPTS:
            values = {"status": "failed", "last_error": error}
            log.warning("callback.failed", run_id=run_id, attempts=attempt, error=error)
        else:
            values = {"next_at": now + timedelta(seconds=backoff_s(attempt)), "last_error": error}
        async with self.db.sessionmaker() as s:
            await s.execute(
                update(RunCallback).where(RunCallback.run_id == run_id).values(last_status=status, **values)
            )
            await s.commit()
