"""Envelope encryption for secrets (S11, carried over from S10).

Each organisation has a random **data key**; project secrets and webhook signing secrets are encrypted with it
(Fernet). The data key is stored only **wrapped** by the master key, which lives outside the database:

| `AGENTCANVAS_KMS_KEY` | master key |
|---|---|
| empty (dev) | derived from `AGENTCANVAS_SECRET_KEY` |
| `file:/run/secrets/master.key` | a local key file (any 32+ random characters) |
| `aws:arn:aws:kms:…:key/…` | AWS KMS (`pip install boto3`; credentials from the environment) |
| `gcp:projects/…/cryptoKeys/…` | Google Cloud KMS (`pip install google-cloud-kms`) |

**Rotation without downtime:** deploy with the new key in `AGENTCANVAS_KMS_KEY` and the old one in
`AGENTCANVAS_KMS_PREVIOUS_KEYS` (instances unwrap with either), run `python -m agentcanvas.security.kms rewrap`,
then drop the old key. Re-wrapping changes only the wrapped data keys; secrets are not re-encrypted. The same
command moves secrets written before envelope encryption onto their organisation's data key.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentcanvas.db.models import OrgDataKey
from agentcanvas.log import get_logger

log = get_logger(__name__)


class MasterKey(Protocol):
    id: str

    def wrap(self, data_key: bytes) -> bytes: ...
    def unwrap(self, blob: bytes) -> bytes: ...


def _derived(material: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest()))


class LocalKey:
    """A master key from key material (a key file, or AGENTCANVAS_SECRET_KEY in development)."""

    def __init__(self, material: str):
        if len(material) < 32:
            raise ValueError("a local master key needs at least 32 characters")
        self.id = "local:" + hashlib.sha256(material.encode()).hexdigest()[:16]
        self._f = _derived("agentcanvas-master:" + material)

    def wrap(self, data_key: bytes) -> bytes:
        return self._f.encrypt(data_key)

    def unwrap(self, blob: bytes) -> bytes:
        return self._f.decrypt(blob)


class AwsKmsKey:
    def __init__(self, arn: str, client: Any = None):
        if client is None:
            import boto3  # type: ignore[import-not-found]  # optional: only deployments on AWS install it

            client = boto3.client("kms")
        self.id, self.arn, self._kms = "aws:" + arn, arn, client

    def wrap(self, data_key: bytes) -> bytes:
        return self._kms.encrypt(KeyId=self.arn, Plaintext=data_key)["CiphertextBlob"]

    def unwrap(self, blob: bytes) -> bytes:
        return self._kms.decrypt(KeyId=self.arn, CiphertextBlob=blob)["Plaintext"]


class GcpKmsKey:
    def __init__(self, name: str, client: Any = None):
        if client is None:
            from google.cloud import kms  # type: ignore[import-not-found]  # optional: only on Google Cloud

            client = kms.KeyManagementServiceClient()
        self.id, self.name, self._kms = "gcp:" + name, name, client

    def wrap(self, data_key: bytes) -> bytes:
        return self._kms.encrypt(request={"name": self.name, "plaintext": data_key}).ciphertext

    def unwrap(self, blob: bytes) -> bytes:
        return self._kms.decrypt(request={"name": self.name, "ciphertext": blob}).plaintext


def master_key(spec: str, secret_key: str) -> MasterKey:
    """`AGENTCANVAS_KMS_KEY` (or an entry of `AGENTCANVAS_KMS_PREVIOUS_KEYS`) -> the master key."""
    if not spec:
        return LocalKey(secret_key.ljust(32, "\0"))  # development default; short dev keys are padded
    kind, _, ref = spec.partition(":")
    if kind == "file":
        return LocalKey(Path(ref).read_text().strip())
    if kind == "aws":
        return AwsKmsKey(ref)
    if kind == "gcp":
        return GcpKmsKey(ref)
    raise ValueError(f"AGENTCANVAS_KMS_KEY must start with file:, aws: or gcp: (got '{kind}:…')")


class Keyring:
    """Organisation data keys: created on first use, unwrapped once per process and cached."""

    def __init__(self, current: MasterKey, previous: list[MasterKey] | None = None, legacy_secret_key: str = ""):
        self.current = current
        self.masters = {k.id: k for k in [*(previous or []), current]}
        # secrets written before envelope encryption (S10 and earlier): Fernet from AGENTCANVAS_SECRET_KEY
        self.legacy = _derived(legacy_secret_key)
        self._cache: dict[str, Fernet] = {}
        self._lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Any) -> Keyring:
        previous = [master_key(spec, settings.secret_key) for spec in settings.kms_previous_keys]
        return cls(master_key(settings.kms_key, settings.secret_key), previous, settings.secret_key)

    async def _unwrap(self, row: OrgDataKey) -> bytes:
        master = self.masters.get(row.master_key_id)
        if master is None:
            raise RuntimeError(
                f"the data key of organisation '{row.org_id}' is wrapped by '{row.master_key_id}', which is neither "
                "AGENTCANVAS_KMS_KEY nor in AGENTCANVAS_KMS_PREVIOUS_KEYS"
            )
        return await asyncio.to_thread(master.unwrap, row.wrapped_key)

    async def fernet(self, session: AsyncSession, org_id: str) -> Fernet:
        """The organisation's data key as a Fernet (created and wrapped on first use)."""
        if (cached := self._cache.get(org_id)) is not None:
            return cached
        async with self._lock:
            if (cached := self._cache.get(org_id)) is not None:
                return cached
            row = await session.get(OrgDataKey, org_id)
            if row is None:
                data_key = Fernet.generate_key()
                wrapped = await asyncio.to_thread(self.current.wrap, data_key)
                try:
                    async with session.begin_nested():
                        session.add(OrgDataKey(org_id=org_id, master_key_id=self.current.id, wrapped_key=wrapped))
                    await session.commit()
                except IntegrityError:  # another instance created it first: use theirs
                    row = await session.get(OrgDataKey, org_id, populate_existing=True)
                    assert row is not None
                    data_key = await self._unwrap(row)
            else:
                data_key = await self._unwrap(row)
            self._cache[org_id] = Fernet(data_key)
            return self._cache[org_id]

    async def rewrap(self, session: AsyncSession) -> int:
        """Wrap every data key with the current master key; returns how many changed."""
        changed = 0
        for row in (await session.scalars(select(OrgDataKey))).all():
            if row.master_key_id == self.current.id:
                continue
            data_key = await self._unwrap(row)
            row.wrapped_key = await asyncio.to_thread(self.current.wrap, data_key)
            row.master_key_id, row.rotated_at = self.current.id, datetime.now(UTC)
            changed += 1
        await session.commit()
        return changed


async def _main(argv: list[str]) -> int:
    from agentcanvas.db.session import Database
    from agentcanvas.services.secrets import migrate_legacy
    from agentcanvas.settings import get_settings

    if argv != ["rewrap"]:
        print("usage: python -m agentcanvas.security.kms rewrap")
        return 2
    settings = get_settings()
    keyring, db = Keyring.from_settings(settings), Database(settings.database_url)
    try:
        async with db.sessionmaker() as s:
            moved = await migrate_legacy(s, keyring)
            rewrapped = await keyring.rewrap(s)
    finally:
        await db.dispose()
    print(f"re-wrapped {rewrapped} organisation key(s) with {keyring.current.id}; moved {moved} legacy secret(s)")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
