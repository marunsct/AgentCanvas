"""Models in your own cloud account: AWS Bedrock and Google Vertex AI. Clients are stubbed: no call leaves the test."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import httpx
import pytest

from agentcanvas import cloudmodels
from agentcanvas.adapters.langchain.nodes import Resources, chat_model_args, public_only_args
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import default_registry
from agentcanvas.runtime.runner import price
from agentcanvas.validation import validate

API = Path(__file__).resolve().parents[1]
AWS_KEYS = {"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE", "AWS_SECRET_ACCESS_KEY": "s3cret"}
CLOUD_VARS = (
    "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_BEARER_TOKEN_BEDROCK", "AWS_PROFILE", "AWS_WEB_IDENTITY_TOKEN_FILE", "GOOGLE_CLOUD_LOCATION",
    "CLOUD_ML_REGION", "GOOGLE_CLOUD_PROJECT", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_API_KEY",
)  # fmt: skip


@pytest.fixture(autouse=True)
def no_cloud_environment(monkeypatch, tmp_path):
    """Whatever the machine running the tests has (a developer's AWS profile, a CI runner's role) stays out."""
    for var in CLOUD_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "none"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("NO_GCE_CHECK", "True")


def service_account(**over) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    info = {"type": "service_account", "project_id": "acme-prod-123", "private_key_id": "k1",
            "private_key": key.decode(), "client_email": "runs@acme-prod-123.iam.gserviceaccount.com",
            "client_id": "1", "token_uri": "https://oauth2.googleapis.com/token"}  # fmt: skip
    return json.dumps({**info, **over})


# ------------------------------------------------------------------ AWS Bedrock ----
def test_bedrock_takes_the_projects_keys_and_the_blocks_region():
    model, args = chat_model_args("bedrock_converse:eu.anthropic.claude-sonnet-5-5", {"region": "eu-central-1"},
                                  {**AWS_KEYS, "AWS_SESSION_TOKEN": "tok"})  # fmt: skip
    assert model == "bedrock_converse:eu.anthropic.claude-sonnet-5-5"
    assert args == {"region_name": "eu-central-1", "aws_access_key_id": "AKIAEXAMPLE",
                    "aws_secret_access_key": "s3cret", "aws_session_token": "tok"}  # fmt: skip
    # a Bedrock API key (the project secret, or the block's own secret) instead of an access key
    assert chat_model_args("bedrock_converse:m", {"region": "us-east-1"}, {"AWS_BEARER_TOKEN_BEDROCK": "bk"})[1] == {
        "region_name": "us-east-1", "api_key": "bk"}  # fmt: skip
    assert chat_model_args("bedrock_converse:m", {"region": "us-east-1", "api_key": "own"}, AWS_KEYS)[1] == {
        "region_name": "us-east-1", "api_key": "own"}  # fmt: skip


def test_bedrock_without_project_keys_uses_the_servers_identity_and_default_region(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    assert chat_model_args("bedrock_converse:m", {}, {}) == ("bedrock_converse:m", {"region_name": "eu-west-1"})


def test_half_a_key_pair_is_refused_rather_than_mixed_with_the_servers_identity(monkeypatch):
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "the server's")
    with pytest.raises(ValueError, match="AWS_SECRET_ACCESS_KEY is missing"):
        chat_model_args("bedrock_converse:m", {"region": "eu-west-1"}, {"AWS_ACCESS_KEY_ID": "AKIA"})


@pytest.mark.parametrize("region", ["evil.example/#", "eu-central-1.evil.example", "EU-CENTRAL-1", "eu central 1"])
def test_a_region_that_is_not_one_is_refused(region):
    """The region becomes the host the signed request goes to: anything else could carry the token elsewhere."""
    with pytest.raises(ValueError, match="is not a region of AWS Bedrock"):
        chat_model_args("bedrock_converse:m", {"region": region}, AWS_KEYS)
    with pytest.raises(ValueError, match="is not a region of Google Vertex AI"):
        chat_model_args("google_vertexai:gemini-3.8-flash", {"region": region, "project": "acme-prod-123"}, {})


def test_no_region_anywhere_says_where_to_set_one():
    with pytest.raises(ValueError, match="need a region: set the block's Region, or AWS_REGION"):
        chat_model_args("bedrock_converse:m", {}, AWS_KEYS)


@pytest.mark.parametrize("setting", ["base_url", "endpoint_url", "credentials_profile_name", "client"])
def test_an_address_profile_or_client_of_its_own_is_refused(setting):
    """A block must not point the server's cloud identity at another address, or at another identity it has."""
    with pytest.raises(ValueError, match=f"do not take {setting}"):
        chat_model_args("bedrock_converse:m", {"region": "eu-west-1", setting: "x"}, {})


def test_bedrock_client_signs_for_the_chosen_region():
    res = Resources(secrets=AWS_KEYS)
    model = res.model("m", {"model": "bedrock_converse:eu.anthropic.claude-sonnet-5-5", "region": "eu-central-1"})
    assert type(model).__name__ == "ChatBedrockConverse"
    assert model.client.meta.endpoint_url == "https://bedrock-runtime.eu-central-1.amazonaws.com"


async def test_a_stubbed_bedrock_reply_is_priced_and_served_to_other_frameworks():
    """The bridge serves the canvas model to agents of the other frameworks: a Bedrock model answers there too."""
    from botocore.stub import Stubber

    from agentcanvas.adapters.langchain.bridge import ModelBridge

    model = Resources(secrets=AWS_KEYS).model("m", {"model": "bedrock_converse:eu.anthropic.claude-haiku-5-5",
                                                    "region": "eu-central-1"})  # fmt: skip
    reply = {"output": {"message": {"role": "assistant", "content": [{"text": "Hello from Frankfurt"}]}},
             "stopReason": "end_turn", "usage": {"inputTokens": 1000, "outputTokens": 100, "totalTokens": 1100},
             "metrics": {"latencyMs": 5}}  # fmt: skip
    with Stubber(model.client) as stub:
        stub.add_response("converse", reply)
        bridge = ModelBridge()
        await bridge.start()
        with bridge.serving(model) as key:
            async with httpx.AsyncClient(base_url=bridge.root) as c:
                body = {"model": "canvas", "messages": [{"role": "user", "content": "Hi"}]}
                r = await c.post("/v1/chat/completions", headers={"authorization": f"Bearer {key}"}, json=body)
        bridge.close_sockets()
        stub.assert_no_pending_responses()
    out = r.json()
    assert out["choices"][0]["message"]["content"] == "Hello from Frankfurt"
    assert out["usage"]["prompt_tokens"] == 1000
    assert model.metadata and model.metadata["ac_model"] == "bedrock_converse:eu.anthropic.claude-haiku-5-5"
    # the EU-only profile is priced 10% above the global one
    assert price("bedrock_converse:eu.anthropic.claude-haiku-5-5", 1000, 100) == pytest.approx(0.000165)
    assert price("bedrock_converse:global.anthropic.claude-haiku-5-5", 1000, 100) == pytest.approx(0.00015)


def test_a_backup_in_the_same_cloud_keeps_the_blocks_region(monkeypatch):
    seen = []
    real = chat_model_args

    def spy(model, params, secrets):
        seen.append((model, dict(params)))
        return real(model, params, secrets)

    monkeypatch.setattr("agentcanvas.adapters.langchain.nodes.chat_model_args", spy)
    monkeypatch.setattr("agentcanvas.adapters.langchain.nodes.init_chat_model", lambda *a, **k: object())
    Resources(secrets={**AWS_KEYS, "OPENAI_API_KEY": "k"}).model("m", {
        "model": "bedrock_converse:eu.anthropic.claude-sonnet-5-5", "region": "eu-central-1", "max_tokens": 50,
        "fallbacks": ["bedrock_converse:eu.amazon.nova-pro-v1:0", "openai:gpt-5.5"],
    })  # fmt: skip
    backups = dict(seen[1:])
    assert backups["bedrock_converse:eu.amazon.nova-pro-v1:0"] == {"max_tokens": 50, "region": "eu-central-1"}
    assert backups["openai:gpt-5.5"] == {"max_tokens": 50}  # another provider takes no region


def test_claude_on_bedrock_is_sent_no_temperature():
    _, args = chat_model_args("bedrock_converse:global.anthropic.claude-opus-5-5",
                              {"region": "us-east-1", "temperature": 0.3}, AWS_KEYS)  # fmt: skip
    assert "temperature" not in args
    assert chat_model_args("bedrock_converse:us.amazon.nova-pro-v1:0", {"region": "us-east-1", "temperature": 0.3},
                           AWS_KEYS)[1]["temperature"] == 0.3  # fmt: skip


# ------------------------------------------------------------------ Google Vertex AI ----
def test_gemini_on_vertex_uses_the_service_account_and_never_an_api_key(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "an AI Studio key")
    model, args = chat_model_args("google_vertexai:gemini-3.8-flash", {"region": "europe-west4"},
                                  {"GOOGLE_SERVICE_ACCOUNT_KEY": service_account(), "GOOGLE_API_KEY": "k"})  # fmt: skip
    assert model == "google_genai:gemini-3.8-flash"
    assert args["vertexai"] is True and args["api_key"] is None
    assert (args["project"], args["location"]) == ("acme-prod-123", "europe-west4")  # the key's project
    assert args["credentials"].service_account_email == "runs@acme-prod-123.iam.gserviceaccount.com"
    model = Resources(secrets={"GOOGLE_SERVICE_ACCOUNT_KEY": service_account()}).model(
        "m", {"model": "google_vertexai:gemini-3.8-flash", "region": "eu", "project": "acme-eu-456"}
    )
    assert type(model).__name__ == "ChatGoogleGenerativeAI"
    client = model.client._api_client
    assert (client.vertexai, client.project) == (True, "acme-eu-456")  # the block's project wins
    assert client._http_options.base_url == "https://aiplatform.eu.rep.googleapis.com/"


def test_claude_on_vertex_uses_the_servers_identity_and_project(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "acme-prod-123")
    assert chat_model_args("google_anthropic_vertex:claude-sonnet-5-5", {"temperature": 0.2}, {}) == (
        "google_anthropic_vertex:claude-sonnet-5-5", {"location": "us", "project": "acme-prod-123"})  # fmt: skip
    from google.auth.credentials import AnonymousCredentials

    monkeypatch.setattr("google.auth.default", lambda *a, **k: (AnonymousCredentials(), "acme-prod-123"))
    model = Resources().model("m", {"model": "google_anthropic_vertex:claude-sonnet-5-5", "region": "eu"})
    assert type(model).__name__ == "ChatAnthropicVertex"
    assert str(model.client.base_url) == "https://aiplatform.eu.rep.googleapis.com/v1/"


@pytest.mark.parametrize(
    ("key", "why"),
    [
        ("{not json", "must be a service account's JSON key file"),
        (json.dumps({"type": "external_account", "credential_source": {"file": "/etc/passwd"}}), "type service_acc"),
        (service_account(token_uri="https://tokens.evil.example/token"), "another token address"),
        (service_account(universe_domain="evil.example"), "another token address"),
    ],
)
def test_only_a_plain_service_account_key_is_taken(key, why):
    """Other credential files tell the server which file, address or command to get a token from."""
    with pytest.raises(ValueError, match=why):
        chat_model_args("google_vertexai:gemini-3.8-flash", {"region": "global"}, {"GOOGLE_SERVICE_ACCOUNT_KEY": key})


def test_vertex_needs_a_project_and_checks_it():
    with pytest.raises(ValueError, match="need a project: set the block's Project, or GOOGLE_CLOUD_PROJECT"):
        chat_model_args("google_vertexai:gemini-3.8-flash", {"region": "global"}, {})
    with pytest.raises(ValueError, match="is not a Google Cloud project id"):
        chat_model_args("google_vertexai:gemini-3.8-flash", {"region": "global", "project": "../../x"}, {})


def test_other_providers_leave_a_region_out():
    assert chat_model_args("openai:gpt-5.5", {"region": "eu-west-1", "project": "p", "api_key": "k"}, {})[1] == {
        "api_key": "k", "stream_usage": True}  # fmt: skip


# ------------------------------------------------------------------ the hosted edition ----
def test_the_hosted_edition_never_lends_the_servers_cloud_identity(monkeypatch):
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "operator-proj-1")
    for model in ("bedrock_converse:m", "google_vertexai:gemini-3.8-flash", "google_anthropic_vertex:claude-x"):
        init_id, args = chat_model_args(model, {"region": "global"} if "google" in model else {}, {})
        with pytest.raises(ValueError, match="use the project's own credentials: add the project secret"):
            public_only_args(init_id, args, "model")
    init_id, args = chat_model_args("bedrock_converse:m", {}, AWS_KEYS)
    assert public_only_args(init_id, args, "model") == args
    init_id, args = chat_model_args("google_vertexai:g", {"region": "global"},
                                    {"GOOGLE_SERVICE_ACCOUNT_KEY": service_account()})  # fmt: skip
    assert public_only_args(init_id, args, "model") is args


# ------------------------------------------------------------------ the server's identity ----
def test_the_servers_identity_is_seen_without_a_network_call(tmp_path):
    root = tmp_path / "root"
    (root / "sys/class/dmi/id").mkdir(parents=True)
    env = {"HOME": str(tmp_path)}
    assert not cloudmodels.server_identity("aws", env, root) and not cloudmodels.server_identity("gcp", env, root)
    assert cloudmodels.server_identity("aws", {**env, "AWS_WEB_IDENTITY_TOKEN_FILE": "/var/run/token"}, root)  # IRSA
    assert cloudmodels.server_identity("aws", {**env, "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://x"}, root)
    assert cloudmodels.server_identity("gcp", {**env, "K_SERVICE": "api"}, root)  # Cloud Run
    (root / "sys/class/dmi/id/product_name").write_text("Google Compute Engine\n")  # GCE, GKE (Workload Identity)
    assert cloudmodels.server_identity("gcp", env, root) and not cloudmodels.server_identity("aws", env, root)
    (root / "sys/class/dmi/id/sys_vendor").write_text("Amazon EC2\n")  # an instance role
    assert cloudmodels.server_identity("aws", env, root)


# ------------------------------------------------------------------ catalog and image ----
def test_the_catalog_prices_each_routing_choice():
    reg = default_registry()
    ids = {m.id for m in reg.models() if cloudmodels.cloud_of(m.id)}
    for family in ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"):
        world = reg.model(f"bedrock_converse:global.anthropic.{family}")
        assert world and world.price_in_per_mtok and not world.sampling
        for geo in ("us", "eu"):  # requests that stay in the US or the EU cost 10% more (2026-11-10)
            local = reg.model(f"bedrock_converse:{geo}.anthropic.{family}")
            assert local and local.price_in_per_mtok == pytest.approx(world.price_in_per_mtok * 1.1)
            assert local.price_out_per_mtok == pytest.approx(world.price_out_per_mtok * 1.1)
        assert f"google_anthropic_vertex:{family}" in ids
    for model in (
        "bedrock_converse:us.amazon.nova-pro-v1:0",
        "bedrock_converse:global.amazon.nova-2-lite-v1:0",
        "bedrock_converse:us.meta.llama4-maverick-17b-instruct-v1:0",
        "bedrock_converse:mistral.mistral-large-3-675b-instruct",
        "google_vertexai:gemini-3.8-flash",
    ):
        info = reg.model(model)
        assert info and info.price_in_per_mtok and info.price_out_per_mtok, model  # fmt: skip


def test_the_api_image_installs_the_cloud_model_providers_from_the_lock():
    project = tomllib.loads((API / "pyproject.toml").read_text())
    extras = project["project"]["optional-dependencies"]
    assert any(d.startswith("langchain-aws") for d in extras["models-aws"])
    assert any(d.startswith("langchain-google-vertexai") for d in extras["models-gcp"])
    lock = {p["name"]: p for p in tomllib.loads((API / "uv.lock").read_text())["package"]}
    assert {"langchain-aws", "langchain-google-vertexai"} <= set(lock)
    assert {"models-aws", "models-gcp"} <= set(lock["agentcanvas"]["optional-dependencies"])
    dockerfile = (API.parents[1] / "infra" / "Dockerfile.api").read_text()
    assert 'ARG MODELS="aws gcp"' in dockerfile
    assert dockerfile.count("$(for m in $MODELS; do printf -- '--extra models-%s ' \"$m\"; done)") == 2


# ------------------------------------------------------------------ exports ----
def test_an_export_builds_cloud_models_the_same_way_and_allows_their_regions_address(triage, monkeypatch):
    from agentcanvas.adapters.langchain import deploykit
    from agentcanvas.adapters.langchain.packager import RUNTIME_FILES, dependencies

    monkeypatch.setenv("AWS_REGION", "us-west-2")  # the exporting server's default is not the export's
    data = triage.model_dump(mode="json")
    data["resources"] += [
        {"id": "r_b", "type": "model.chat", "name": "bedrock",
         "config": {"model": "bedrock_converse:eu.anthropic.claude-sonnet-5-5", "region": "eu-central-1"}},
        {"id": "r_v", "type": "model.chat", "name": "vertex",
         "config": {"model": "google_vertexai:gemini-3.8-flash", "region": "eu", "project": "acme-prod-123"}},
        {"id": "r_c", "type": "model.chat", "name": "claude", "config": {"model": "google_anthropic_vertex:x"}},
    ]  # fmt: skip
    wf = type(triage).model_validate(data)
    assert "cloudmodels.py" in RUNTIME_FILES  # the vendored nodes.py builds these clients with it
    deps = dependencies(wf)
    assert any(d.startswith("langchain-aws") for d in deps)
    assert any(d.startswith("langchain-google-genai") for d in deps)
    assert any(d.startswith("langchain-google-vertexai") for d in deps)
    hosts = {h["host"] for h in deploykit.facts(wf, "triage", "1.0.0").hosts}
    assert {"bedrock-runtime.eu-central-1.amazonaws.com", "aiplatform.eu.rep.googleapis.com"} <= hosts
    assert not any("us-west-2" in h for h in hosts)  # a block without a region has no address to allow yet


# ------------------------------------------------------------------ the editor and price routing ----
def _workflow(*configs: dict) -> Workflow:
    return Workflow.model_validate({"id": "wf_c", "name": "cloud", "nodes": [], "resources": [
        {"id": f"r_{i}", "type": "model.chat", "name": f"m{i}", "config": c} for i, c in enumerate(configs)
    ]})  # fmt: skip


def test_bedrock_reach_orders_in_region_geography_and_global():
    eu, us, world, here = "b:eu.anthropic.x", "b:us.anthropic.x", "b:global.anthropic.x", "b:mistral.large-3"
    assert [cloudmodels.bedrock_reach(m) for m in (eu, world, here)] == ["eu", "global", "region"]
    assert cloudmodels.wider_reach(world, eu) and cloudmodels.wider_reach(eu, here) and cloudmodels.wider_reach(us, eu)
    assert not cloudmodels.wider_reach(eu, world) and not cloudmodels.wider_reach(here, eu)
    assert not cloudmodels.wider_reach(eu, "b:eu.amazon.nova-pro-v1:0")


def test_the_editor_checks_the_region_and_the_address(monkeypatch):
    wf = _workflow(
        {"model": "bedrock_converse:eu.anthropic.claude-sonnet-5-5"},
        {"model": "google_vertexai:gemini-3.8-flash", "region": "europe-west4.evil.example"},
        {"model": "google_anthropic_vertex:claude-sonnet-5-5", "region": "eu", "base_url": "https://proxy.example"},
        {"model": "bedrock_converse:eu.anthropic.claude-sonnet-5-5", "region": "eu-central-1"},
        {"model": "openai:gpt-5.5", "region": "eu-west-1"},
    )
    found = [(d.code, d.node_id, d.path) for d in validate(wf) if d.code in ("E024", "W029")]
    assert found == [
        ("E024", "r_0", "config.region"), ("E024", "r_1", "config.region"), ("E024", "r_2", "config.base_url"),
        ("W029", "r_4", "config.region"),
    ]  # fmt: skip
    monkeypatch.setenv("AWS_REGION", "eu-central-1")  # the server's default region serves a block without one
    assert [d.node_id for d in validate(wf) if d.code == "E024"] == ["r_1", "r_2"]


def test_price_routing_keeps_prompts_in_the_blocks_region(monkeypatch):
    from agentcanvas.services import routing

    eu, world = "bedrock_converse:eu.anthropic.claude-haiku-5-5", "bedrock_converse:global.anthropic.claude-haiku-5-5"
    wf = _workflow({"model": "bedrock_converse:eu.anthropic.claude-opus-5-5", "region": "eu-central-1",
                    "cheapest_of": ["openai:gpt-5-mini", "google_vertexai:gemini-3.8-flash", world, eu]})  # fmt: skip
    out, (note,) = routing.route(wf, default_registry(), {**AWS_KEYS, "OPENAI_API_KEY": "k"}, [])
    reasons = {c["model"]: c.get("reason") for c in note["considered"]}
    assert "keeps its prompts in its AWS Bedrock region" in reasons["openai:gpt-5-mini"]
    assert "keeps its prompts in its AWS Bedrock region" in reasons["google_vertexai:gemini-3.8-flash"]
    assert "more places than the block's own model" in reasons[world]  # global routing would leave the EU
    assert out.resources[0].config["model"] == eu  # same cloud and geography, the block's region: the cheapest
    assert out.resources[0].config["region"] == "eu-central-1"
    # the hosted edition: no project credentials, no Bedrock model is ready
    _, (note,) = routing.route(wf, default_registry(), {"OPENAI_API_KEY": "k"}, [], hosted=True)
    assert all(not c["usable"] for c in note["considered"] if c["model"].startswith("bedrock_converse:"))
    # a block of another provider with no region may move to a cloud model, which then needs a region of its own
    plain = _workflow({"model": "openai:gpt-5.5", "cheapest_of": [eu]})
    _, (note,) = routing.route(plain, default_registry(), AWS_KEYS, [])
    assert "need a region" in note["considered"][0]["reason"]
    monkeypatch.setenv("AWS_REGION", "eu-central-1")
    out, _ = routing.route(plain, default_registry(), AWS_KEYS, [])
    assert out.resources[0].config["model"] == eu
