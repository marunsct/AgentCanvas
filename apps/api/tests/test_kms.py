"""Envelope encryption of secrets (S11): per-organisation data keys wrapped by a rotatable master key."""

from __future__ import annotations

import sys
import types

import pytest
from sqlalchemy import select

from agentcanvas.db.models import OrgDataKey, Secret
from agentcanvas.security.kms import AwsKmsKey, GcpKmsKey, Keyring, LocalKey, master_key
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
    async with app.state.db.sessionmaker() as s:
        svc = SecretService(s, Keyring(OLD))
        assert await svc.values("prj_default") == {}
    assert "neither" in svc.key_errors["org_default"] and svc.unreadable == {"prj_default"}


async def test_an_organisation_whose_data_key_is_unreadable_does_not_stop_the_others(app, api_client):
    """OP-3: e.g. AGENTCANVAS_SECRET_KEY rotated while AGENTCANVAS_KMS_KEY is empty. Startup's sync must not fail: the
    other organisations' secrets load, and that organisation's projects are tried again on the next sync."""
    from agentcanvas.db.models import Organization, Project
    from agentcanvas.services.secrets import SecretCache

    app.state.keyring = Keyring(OLD, legacy_secret_key=app.state.settings.secret_key)
    async with app.state.db.sessionmaker() as s:
        s.add(Organization(id="org_b", name="B"))
        await s.flush()
        s.add(Project(id="prj_b", org_id="org_b", name="B"))
        await s.commit()
        await SecretService(s, app.state.keyring).put("prj_b", "B_TOKEN", "token-b", None)
    app.state.keyring = Keyring(NEW, legacy_secret_key=app.state.settings.secret_key)  # OLD is gone
    assert (await api_client.put(f"{P}/secrets/API_TOKEN", json={"value": "token-a"})).status_code == 204

    cache: dict[str, dict[str, str]] = {}
    keys = {"now": app.state.keyring}
    secrets = SecretCache(app.state.db, lambda: keys["now"], cache)
    await secrets.sync()  # does not raise
    assert cache["prj_default"] == {"API_TOKEN": "token-a"}
    assert "prj_b" not in secrets.versions  # not marked as read
    keys["now"] = Keyring(NEW, [OLD], app.state.settings.secret_key)  # the old key is back as a previous key
    await secrets.sync()
    assert cache["prj_b"] == {"B_TOKEN": "token-b"}


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


def _stub_gcp(monkeypatch, kms: types.ModuleType | None) -> None:
    """`from google.cloud import kms` sees only this module (None: the package is not installed)."""
    cloud = types.ModuleType("google.cloud")
    cloud.__path__ = []  # a namespace package with nothing in it
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    if kms is None:
        monkeypatch.delitem(sys.modules, "google.cloud.kms", raising=False)
    else:
        cloud.kms = kms  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "google.cloud.kms", kms)


def test_cloud_master_keys_build_their_clients_from_the_installed_packages(monkeypatch):
    made: list[str] = []
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda service: made.append(service) or "aws-client"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "boto3", boto3)
    kms = types.ModuleType("google.cloud.kms")
    kms.KeyManagementServiceClient = lambda: "gcp-client"  # type: ignore[attr-defined]
    _stub_gcp(monkeypatch, kms)

    aws = master_key("aws:arn:aws:kms:eu-west-1:1:key/k", "dev")
    gcp = master_key("gcp:projects/p/locations/l/keyRings/r/cryptoKeys/k", "dev")
    assert isinstance(aws, AwsKmsKey) and aws._kms == "aws-client" and made == ["kms"]
    assert isinstance(gcp, GcpKmsKey) and gcp._kms == "gcp-client"


def test_a_cloud_master_key_without_its_client_stops_startup_naming_the_setting_and_the_extra(monkeypatch):
    """The image had neither client, so a server set up as the deploy guide says crashed on a bare import error."""
    from agentcanvas.settings import Settings

    monkeypatch.setitem(sys.modules, "boto3", None)  # not installed
    _stub_gcp(monkeypatch, None)
    with pytest.raises(RuntimeError, match=r"AGENTCANVAS_KMS_KEY names a aws: .*boto3.*uv sync --extra kms-aws"):
        master_key("aws:arn:aws:kms:eu-west-1:1:key/k", "dev")
    with pytest.raises(RuntimeError, match=r"AGENTCANVAS_KMS_KEY .*google-cloud-kms.*--extra kms-gcp"):
        Keyring.from_settings(Settings(kms_key="gcp:projects/p/locations/l/keyRings/r/cryptoKeys/k"))
    with pytest.raises(RuntimeError, match=r"AGENTCANVAS_KMS_PREVIOUS_KEYS .*--extra kms-aws"):
        Keyring.from_settings(Settings(kms_previous_keys=["aws:arn:aws:kms:eu-west-1:1:key/old"]))


def test_the_api_image_installs_both_cloud_kms_clients_from_the_lock():
    import tomllib
    from pathlib import Path

    api = Path(__file__).resolve().parents[1]
    extras = tomllib.loads((api / "pyproject.toml").read_text())["project"]["optional-dependencies"]
    assert any(d.startswith("boto3") for d in extras["kms-aws"])
    assert any(d.startswith("google-cloud-kms") for d in extras["kms-gcp"])
    lock = {p["name"]: p for p in tomllib.loads((api / "uv.lock").read_text())["package"]}
    assert {"boto3", "google-cloud-kms"} <= set(lock)
    assert {"kms-aws", "kms-gcp"} <= set(lock["agentcanvas"]["optional-dependencies"])
    dockerfile = (api.parents[1] / "infra" / "Dockerfile.api").read_text()
    assert 'ARG KMS="aws gcp"' in dockerfile
    assert dockerfile.count("$(for k in $KMS; do printf -- '--extra kms-%s ' \"$k\"; done)") == 2  # both uv syncs
    assert "--all-extras" in (api.parents[1] / "scripts" / "audit-deps.sh").read_text()  # what ships is audited


def test_staging_and_production_warn_when_the_master_key_is_derived_from_the_secret_key():
    from agentcanvas.api.main import kms_hint
    from agentcanvas.settings import Settings

    assert "AGENTCANVAS_KMS_PREVIOUS_KEYS" in (kms_hint(Settings(env="prod")) or "")
    assert kms_hint(Settings(env="staging"))
    assert kms_hint(Settings(env="prod", kms_key="file:/run/secrets/master.key")) is None
    assert kms_hint(Settings(env="dev")) is None


async def test_the_old_secret_key_in_a_file_keeps_secrets_readable_after_the_secret_key_changes(app, tmp_path):
    """The procedure in docs/user-guide/deploy.md (Rotating AGENTCANVAS_SECRET_KEY)."""
    from agentcanvas.settings import Settings

    old_sk, new_sk = "o" * 64, "n" * 64
    before = Keyring.from_settings(Settings(secret_key=old_sk))
    async with app.state.db.sessionmaker() as s:
        await SecretService(s, before).put("prj_default", "API_TOKEN", "token-x", None)
    (tmp_path / "old-secret-key").write_text(old_sk + "\n")
    previous = [f"file:{tmp_path / 'old-secret-key'}"]
    after = Keyring.from_settings(Settings(secret_key=new_sk, kms_previous_keys=previous))
    assert (await read(app, after))["API_TOKEN"] == "token-x"
    async with app.state.db.sessionmaker() as s:
        assert await after.rewrap(s) == 1
    assert (await read(app, Keyring.from_settings(Settings(secret_key=new_sk))))["API_TOKEN"] == "token-x"
