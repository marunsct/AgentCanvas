"""Project secrets (M5-T6): encrypted at rest, never returned by the API, resolved only when a run needs them."""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import json
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, quote_plus

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import Project, Secret
from agentcanvas.errors import InvalidInput, NotFound
from agentcanvas.ids import new_id
from agentcanvas.log import get_logger
from agentcanvas.runtime.masking import mask_strings
from agentcanvas.security.kms import Keyring

log = get_logger(__name__)

NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _fernet(secret_key: str) -> Fernet:
    """The pre-envelope key (S10 and earlier): only reads secrets written before KMS envelope encryption."""
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret_key.encode()).digest()))


async def _org_of(session: AsyncSession, project_id: str) -> str:
    org_id = await session.scalar(select(Project.org_id).where(Project.id == project_id))
    if org_id is None:
        raise NotFound(f"project '{project_id}' not found")
    return org_id


class SecretService:
    """Values are encrypted with the project's organisation data key (`Keyring`, envelope encryption)."""

    def __init__(self, session: AsyncSession, keyring: Keyring):
        self.s = session
        self.keys = keyring
        # after `values`: organisations whose data key could not be unwrapped (org id -> why), and their projects
        self.key_errors: dict[str, str] = {}
        self.unreadable: set[str] = set()

    async def names(self, project_id: str) -> list[Secret]:
        q = select(Secret).where(Secret.project_id == project_id).order_by(Secret.name)
        return list((await self.s.scalars(q)).all())

    async def put(self, project_id: str, name: str, value: str, user_id: str | None) -> None:
        if not NAME.match(name):
            raise InvalidInput("secret names are UPPER_SNAKE_CASE, e.g. OPENAI_API_KEY")
        if len(value) < MIN_SECRET:  # it could not be masked where a run's output shows it
            raise InvalidInput(f"a secret needs at least {MIN_SECRET} characters, so outputs can hide it")
        f = await self.keys.fernet(self.s, await _org_of(self.s, project_id))
        token = f.encrypt(value.encode())
        row = await self.s.scalar(select(Secret).where(Secret.project_id == project_id, Secret.name == name))
        if row is None:
            self.s.add(
                Secret(
                    id=new_id("sec"), project_id=project_id, name=name, ciphertext=token, org_key=True,
                    created_by=user_id,
                )
            )  # fmt: skip
        else:
            row.ciphertext, row.org_key = token, True
        await self._changed(project_id)
        await self.s.commit()

    async def delete(self, project_id: str, name: str) -> None:
        res: Any = await self.s.execute(delete(Secret).where(Secret.project_id == project_id, Secret.name == name))
        if not res.rowcount:
            raise NotFound(f"secret '{name}' not found")
        await self._changed(project_id)
        await self.s.commit()

    async def _changed(self, project_id: str) -> None:
        """Every instance's copy of this project's secrets is now stale (`SecretCache`)."""
        await self.s.execute(
            update(Project).where(Project.id == project_id).values(secrets_version=Project.secrets_version + 1)
        )

    async def values(self, project_id: str | None = None) -> dict[str, dict[str, str]]:
        """Decrypted values per project (for the runtime resolver only)."""
        q = select(Secret, Project.org_id).join(Project, Project.id == Secret.project_id)
        if project_id is not None:
            q = q.where(Secret.project_id == project_id)
        out: dict[str, dict[str, str]] = {}
        for row, org_id in (await self.s.execute(q)).all():
            if org_id in self.key_errors:
                self.unreadable.add(row.project_id)
                continue
            try:  # one organisation's unreadable data key (a rotated secret key, a KMS outage) is its alone
                f = await self.keys.fernet(self.s, org_id) if row.org_key else self.keys.legacy
            except Exception as exc:  # noqa: BLE001 - RuntimeError, InvalidToken or the KMS client's own errors
                self.key_errors[org_id] = f"{type(exc).__name__}: {exc}"
                self.unreadable.add(row.project_id)
                continue
            try:
                out.setdefault(row.project_id, {})[row.name] = f.decrypt(row.ciphertext).decode()
            except InvalidToken:  # encrypted with another key: unusable, never crash startup
                log.warning("secret.undecryptable", project_id=row.project_id, name=row.name)
        return out


class SecretCache:
    """This instance's decrypted copy of the project secrets: `cache` (project id -> {name: value}) holds the
    dicts the project resolvers read, so they are updated in place; `keyring` returns the instance's current one.
    Another instance may change a secret: a project's `secrets_version` says when the copy is behind. It is
    checked for the run's project when a run starts, and for every project every `interval_s` (what the API lists
    and masks)."""

    def __init__(
        self, db: Any, keyring: Callable[[], Keyring], cache: dict[str, dict[str, str]], interval_s: float = 10.0
    ):
        self.db, self.keyring, self.cache, self.interval_s = db, keyring, cache, interval_s
        self.versions: dict[str, int] = {}
        self._task: asyncio.Task | None = None
        self._reported: set[str] = set()  # organisations whose unreadable data key was logged

    async def sync(self) -> None:
        """Reload the projects whose secrets changed since they were read (every project at startup). A project
        whose organisation's data key cannot be unwrapped is left as it was and tried again on the next sync."""
        async with self.db.sessionmaker() as s:
            rows = (await s.execute(select(Project.id, Project.secrets_version))).all()
            versions: dict[str, int] = {project_id: version for project_id, version in rows}
            stale = [p for p, v in versions.items() if self.versions.get(p) != v]
            if not stale:
                return
            svc = SecretService(s, self.keyring())
            values = await svc.values(stale[0] if len(stale) == 1 else None)
        self._report(svc)
        for project_id in stale:
            if project_id not in svc.unreadable:
                self._set(project_id, versions[project_id], values.get(project_id, {}))

    def _report(self, svc: SecretService) -> None:
        for org_id, why in svc.key_errors.items():
            if org_id not in self._reported:
                self._reported.add(org_id)
                log.error(
                    "secret.org_key_unreadable", org_id=org_id, error=why,
                    hint="the master key that wrapped this organisation's data key is not AGENTCANVAS_KMS_KEY or in "
                    "AGENTCANVAS_KMS_PREVIOUS_KEYS (with AGENTCANVAS_KMS_KEY empty it is derived from "
                    "AGENTCANVAS_SECRET_KEY): its secrets stay unavailable until that key is back",
                )  # fmt: skip

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="secrets")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_s)
            try:
                await self.sync()
            except Exception:
                log.exception("secrets.sync_failed")

    async def refresh(self, project_id: str) -> None:
        """Reload the project's secrets when another change was saved since they were read (one indexed read when
        nothing changed)."""
        async with self.db.sessionmaker() as s:
            version = await s.scalar(select(Project.secrets_version).where(Project.id == project_id))
            if version is None or self.versions.get(project_id) == version:
                return
            svc = SecretService(s, self.keyring())
            values = (await svc.values(project_id)).get(project_id, {})
        self._report(svc)
        if project_id not in svc.unreadable:
            self._set(project_id, version, values)

    def _set(self, project_id: str, version: int, values: dict[str, str]) -> None:
        current = self.cache.setdefault(project_id, {})
        if current != values:
            current.clear()
            current.update(values)
        self.versions[project_id] = version


async def migrate_legacy(session: AsyncSession, keyring: Keyring) -> int:
    """Re-encrypt secrets written before envelope encryption with their organisation's data key."""
    moved = 0
    q = select(Secret, Project.org_id).join(Project, Project.id == Secret.project_id).where(Secret.org_key.is_(False))
    for row, org_id in (await session.execute(q)).all():
        try:
            value = keyring.legacy.decrypt(row.ciphertext)
        except InvalidToken:
            log.warning("secret.undecryptable", project_id=row.project_id, name=row.name)
            continue
        row.ciphertext = (await keyring.fernet(session, org_id)).encrypt(value)
        row.org_key = True
        moved += 1
    await session.commit()
    return moved


def hide_secrets(payload: Any, secrets: dict[str, str]) -> Any:
    """`payload` with every secret value (6+ characters) masked: state a run wrote can hold a value a tool or
    script read, and thread state, history and explanations return it (S12)."""
    return hide_values(payload, [v for v in secrets.values() if len(v) >= MIN_SECRET])


MIN_SECRET = 6  # shorter values cannot be masked in outputs without masking ordinary text too


@functools.lru_cache(maxsize=4096)
def encoded_forms(value: str) -> tuple[str, ...]:
    """A secret as it may appear in an output: as is, base64 (standard and URL-safe, with or without padding),
    URL-encoded, and JSON-escaped. A provider's error can echo a key in any of these (S-9)."""
    raw = value.encode()
    forms = {
        value, base64.b64encode(raw).decode(), base64.b64encode(raw).decode().rstrip("="),
        base64.urlsafe_b64encode(raw).decode().rstrip("="), quote(value, safe=""), quote_plus(value),
        json.dumps(value)[1:-1],
    }  # fmt: skip
    return tuple(sorted((f for f in forms if len(f) >= MIN_SECRET), key=len, reverse=True))


def _all_forms(values: list[str]) -> list[str]:
    return sorted({f for v in values for f in encoded_forms(v)}, key=len, reverse=True)


def hide_values(payload: Any, values: list[str]) -> Any:
    """`payload` as JSON values with each of `values` (longest first, and their encoded forms) masked inside its
    strings. Masking the strings, not the JSON text, also finds values with characters JSON escapes."""
    return _hide(payload, _all_forms(values)) if values else payload


def masker(values: list[str]) -> Callable[[Any], Any]:
    """`hide_values` with `values` fixed, their encoded forms worked out once: for a run's every event."""
    forms = _all_forms([v for v in values if len(v) >= MIN_SECRET])
    return (lambda payload: _hide(payload, forms)) if forms else (lambda payload: payload)


def _hide(payload: Any, values: list[str]) -> Any:
    text = json.dumps(payload, default=str, ensure_ascii=False)
    if not any(v in text for v in values):  # the usual case (a token, a step event): one pass, no copy (F-1)
        return payload
    plain = json.loads(text)
    masked = mask_strings(plain, values)
    return payload if masked == plain else masked


def redact(text: str, values: list[str]) -> str:
    for v in _all_forms(values):
        text = text.replace(v, "***")
    return text
