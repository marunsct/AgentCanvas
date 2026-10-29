"""Envelope encryption of secrets (S11): per-organisation data keys wrapped by a rotatable master key."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from agentcanvas.db.models import OrgDataKey, Secret
from agentcanvas.security.kms import AwsKmsKey, Keyring, LocalKey, master_key
from agentcanvas.services.secrets import SecretService, _fernet, migrate_legacy

P = "/v1/projects/prj_default"
OLD, NEW = LocalKey("old-master-key-" + "a" * 32), LocalKey("new-master-key-" + "b" * 32)


async def read(app, keyring: Keyring) -> dict[str, str]:
    async with app.state.db.sessionmaker() as s:
        return (await SecretService(s, keyring).values("prj_default")).get("prj_default", {})


async def test_secrets_use_the_organisation_data_key(app, api_client):
    assert (await api_client.put(f"{P}/secrets/API_TOKEN", json={"value": "token-1"})).status_code == 204
    async with app.state.db.sessionmaker() as s:
        row = await s.scalar(select(Secret).where(Secret.name == "API_TOKEN"))
        key = await s.get(OrgDataKey, "org_default")
    assert row is not None and row.org_key and key is not None
    assert key.master_key_id == app.state.keyring.current.id and b"token-1" not in row.ciphertext
    with pytest.raises(Exception):  # noqa: B017 - the old single key cannot read it
        _fernet(app.state.settings.secret_key).decrypt(row.ciphertext)
    assert (await read(app, Keyring.from_settings(app.state.settings)))["API_TOKEN"] == "token-1"  # a fresh process


async def test_legacy_secrets_stay_readable_and_move_to_the_data_key(app):
    legacy = _fernet(app.state.settings.secret_key).encrypt(b"tok-old")
    async with app.state.db.sessionmaker() as s:
        s.add(Secret(id="sec_legacy", project_id="prj_default", name="OLD_TOKEN", ciphertext=legacy))
        await s.commit()
    assert (await read(app, app.state.keyring))["OLD_TOKEN"] == "tok-old"
    async with app.state.db.sessionmaker() as s:
        assert await migrate_legacy(s, app.state.keyring) == 1
        row = await s.get(Secret, "sec_legacy")
        assert row is not None and row.org_key and row.ciphertext != legacy
        assert await migrate_legacy(s, app.state.keyring) == 0
    assert (await read(app, app.state.keyring))["OLD_TOKEN"] == "tok-old"


async def test_rotating_the_master_key_rewraps_without_downtime(app, api_client):
    app.state.keyring = Keyring(OLD, legacy_secret_key=app.state.settings.secret_key)
    await api_client.put(f"{P}/secrets/API_TOKEN", json={"value": "token-2"})

    rolling = Keyring(NEW, [OLD], app.state.settings.secret_key)  # deployed with both keys: still reads
    assert (await read(app, rolling))["API_TOKEN"] == "token-2"
    async with app.state.db.sessionmaker() as s:
        assert await rolling.rewrap(s) == 1
        assert await rolling.rewrap(s) == 0
        key = await s.get(OrgDataKey, "org_default")
        assert key is not None and key.master_key_id == NEW.id and key.rotated_at is not None

    assert (await read(app, Keyring(NEW)))["API_TOKEN"] == "token-2"  # old key dropped
    with pytest.raises(RuntimeError, match="neither"):
        await read(app, Keyring(OLD))


def test_cloud_master_keys():
    class FakeKms:
        def encrypt(self, KeyId, Plaintext):
            return {"CiphertextBlob": b"wrapped:" + KeyId.encode() + b":" + Plaintext[::-1]}

        def decrypt(self, KeyId, CiphertextBlob):
            prefix = b"wrapped:" + KeyId.encode() + b":"
            assert CiphertextBlob.startswith(prefix)
            return {"Plaintext": CiphertextBlob.removeprefix(prefix)[::-1]}

    aws = AwsKmsKey("arn:aws:kms:eu-west-1:1:key/k", client=FakeKms())
    assert aws.id == "aws:arn:aws:kms:eu-west-1:1:key/k" and aws.unwrap(aws.wrap(b"dek")) == b"dek"
    assert master_key("", "dev").id.startswith("local:")
    with pytest.raises(ValueError, match="file:, aws: or gcp:"):
        master_key("vault:x", "dev")
    with pytest.raises(ValueError, match="32 characters"):
        LocalKey("short")
