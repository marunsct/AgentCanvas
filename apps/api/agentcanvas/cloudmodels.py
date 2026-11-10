"""Models in your own cloud account: AWS Bedrock and Google Vertex AI.

Their calls are signed with a cloud identity instead of an API key: the project's own credentials (project secrets
with fixed names) or else the server's (an IRSA or instance role on AWS, Application Default Credentials such as
Workload Identity on Google Cloud). The region a model block names is where its prompts go (data residency), so it is
checked strictly: it becomes part of the host name the signed request is sent to.

- `bedrock_converse:<model or inference profile id>`: LangChain's ChatBedrockConverse (langchain-aws).
- `google_vertexai:<gemini model>`: Gemini through langchain-google-genai's Vertex AI backend (ChatVertexAI, of
  langchain-google-vertexai, is deprecated for Gemini).
- `google_anthropic_vertex:<claude model>`: Claude on Vertex AI, ChatAnthropicVertex (langchain-google-vertexai).

Standalone (the standard library; boto3 and google-auth only when a client is made), because exported projects vendor
it beside the adapter's `nodes.py` to build their model clients the same way.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

BEDROCK = "bedrock_converse"
VERTEX = "google_vertexai"
CLAUDE_VERTEX = "google_anthropic_vertex"
CLOUD = {BEDROCK: "aws", VERTEX: "gcp", CLAUDE_VERTEX: "gcp"}  # provider id -> the cloud whose identity it uses
LABEL = {"aws": "AWS Bedrock", "gcp": "Google Vertex AI"}

# project secrets (or server variables, for the server's own identity) with the credentials
AWS_KEY_ID, AWS_SECRET_KEY, AWS_SESSION_TOKEN = "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"
BEDROCK_API_KEY = "AWS_BEARER_TOKEN_BEDROCK"  # a Bedrock API key, instead of an access key
GCP_KEY = "GOOGLE_SERVICE_ACCOUNT_KEY"  # the contents of a service account's JSON key file
SECRETS = {"aws": (AWS_KEY_ID, AWS_SECRET_KEY, AWS_SESSION_TOKEN, BEDROCK_API_KEY), "gcp": (GCP_KEY,)}

# the server's defaults when a block names no region (or project)
REGION_VARS = {"aws": ("AWS_REGION", "AWS_DEFAULT_REGION"), "gcp": ("GOOGLE_CLOUD_LOCATION", "CLOUD_ML_REGION")}
PROJECT_VARS = ("GOOGLE_CLOUD_PROJECT",)

# A region becomes part of the host name (bedrock-runtime.<region>.amazonaws.com, <region>-aiplatform.googleapis.com):
# anything but a region name could send the signed request, and the token on it, to another host
REGION = {
    "aws": re.compile(r"[a-z]{2}(-gov|-iso[a-z]?)?-[a-z]+-\d{1,2}"),
    "gcp": re.compile(r"global|us|eu|[a-z]+-[a-z]+\d{1,2}"),
}
GCP_PROJECT = re.compile(r"([a-z][a-z0-9.-]{0,62}:)?[a-z][a-z0-9-]{4,28}[a-z0-9]")  # Google's project id rule
TOKEN_URI, UNIVERSE = "https://oauth2.googleapis.com/token", "googleapis.com"
SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)

# block settings these models take; anything else (an address of its own, a profile, a client) is refused: a block
# must not point the server's cloud identity at another address, or at another identity the server has
SETTINGS = {
    BEDROCK: {"temperature", "max_tokens", "timeout", "api_key", "region", "stream_usage"},
    VERTEX: {"temperature", "max_tokens", "timeout", "region", "project", "stream_usage"},
    CLAUDE_VERTEX: {"temperature", "max_tokens", "timeout", "region", "project", "stream_usage"},
}
BLOCK_ONLY = ("region", "project")  # block settings of these providers only; other providers leave them out


def cloud_of(model_id: str) -> str | None:
    """'aws' or 'gcp' for a model in your own cloud account, else None."""
    return CLOUD.get(str(model_id).partition(":")[0])


BEDROCK_GEOS = ("us", "eu", "apac", "au", "jp", "ca", "us-gov")  # cross-region inference within one geography


def bedrock_reach(model_id: str) -> str:
    """Where Bedrock may serve a model id from, within the region called: "global" (anywhere), a geography ("eu"),
    or "region" (only the region called)."""
    name = str(model_id).partition(":")[2]
    prefix = name.split(".", 1)[0] if "." in name else ""
    return "global" if prefix == "global" else prefix if prefix in BEDROCK_GEOS else "region"


def wider_reach(model_id: str, than: str) -> bool:
    """Whether Bedrock model `model_id` may serve requests from more places than model `than` (a cheaper global
    profile in place of an EU one would take prompts out of the EU)."""
    mine, theirs = bedrock_reach(model_id), bedrock_reach(than)
    return mine != theirs and theirs != "global" and (mine == "global" or theirs == "region" or mine != "region")


def default_region(cloud: str, env: Mapping[str, str] = os.environ) -> str | None:
    return next((env[v] for v in REGION_VARS[cloud] if env.get(v)), None)


def region_of(model_id: str, config: Mapping[str, Any], env: Mapping[str, str] = os.environ) -> str | None:
    """The region a model's prompts go to: the block's, else the server's default."""
    cloud = cloud_of(model_id)
    if cloud is None:
        return None
    return str(config.get("region") or "").strip() or default_region(cloud, env)


def region_problem(model_id: str, config: Mapping[str, Any], env: Mapping[str, str] = os.environ) -> str | None:
    """Why the model's region cannot be used, in plain words; None when it can."""
    cloud = cloud_of(model_id)
    if cloud is None:
        return None
    region = region_of(model_id, config, env)
    if not region:
        server = " or ".join(REGION_VARS[cloud])
        return f"{LABEL[cloud]} models need a region: set the block's Region, or {server} on the server"
    if not REGION[cloud].fullmatch(region):
        example = "eu-central-1" if cloud == "aws" else "europe-west4, us or global"
        return f"'{region}' is not a region of {LABEL[cloud]} (for example {example})"
    return None


def project_secrets(cloud: str, secrets: Mapping[str, str]) -> bool:
    """Whether the project has its own credentials for `cloud` (complete or not)."""
    return any(secrets.get(name) for name in SECRETS[cloud])


def server_identity(cloud: str, env: Mapping[str, str] = os.environ, root: Path = Path("/")) -> bool:
    """Whether the server has a cloud identity of its own, from what the environment shows without a network call:
    credentials or a role in variables (IRSA's web-identity token, a container or Pod Identity credentials address),
    a credentials file, or a virtual machine of that cloud (its instance role or service account). A guess for the
    editor and the model menus: a call uses the SDK's whole credential chain."""

    def vendor(*needles: str) -> bool:
        for name in ("sys_vendor", "product_name", "board_vendor"):
            try:
                text = (root / "sys/class/dmi/id" / name).read_text(errors="replace")
            except OSError:
                continue
            if any(n in text for n in needles):
                return True
        return False

    home = Path(env.get("HOME") or "~").expanduser()
    if cloud == "aws":
        markers = (AWS_KEY_ID, BEDROCK_API_KEY, "AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_CONTAINER_CREDENTIALS_FULL_URI",
                   "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "AWS_PROFILE")  # fmt: skip
        files = [env.get("AWS_SHARED_CREDENTIALS_FILE") or home / ".aws/credentials",
                 env.get("AWS_CONFIG_FILE") or home / ".aws/config"]  # fmt: skip
        return any(env.get(m) for m in markers) or any(Path(f).is_file() for f in files) or vendor("Amazon EC2")
    adc = env.get("GOOGLE_APPLICATION_CREDENTIALS")
    gcloud = Path(env.get("CLOUDSDK_CONFIG") or home / ".config/gcloud") / "application_default_credentials.json"
    # Cloud Run and Cloud Functions have the metadata server but no DMI vendor
    return bool(adc and Path(adc).is_file()) or gcloud.is_file() or bool(env.get("K_SERVICE")) or vendor("Google")


def credentials_problem(
    model_id: str, secrets: Mapping[str, str], *, hosted: bool = False, env: Mapping[str, str] = os.environ
) -> str | None:
    """Why the model would have no credentials here, in plain words; None when it has some (or may have: the
    server's identity is a guess)."""
    cloud = cloud_of(model_id)
    if cloud is None or project_secrets(cloud, secrets):
        return None
    if hosted:
        return hosted_problem(cloud)
    if server_identity(cloud, env):
        return None
    who = "an IAM role (IRSA, Pod Identity or an instance role)" if cloud == "aws" else "Workload Identity"
    return f"found no {LABEL[cloud]} credentials: add the project secret(s) {_names(cloud)}, or give the server {who}"


def hosted_problem(cloud: str) -> str:
    """The hosted edition lends no tenant the server's cloud identity."""
    return f"{LABEL[cloud]} models here use the project's own credentials: add the project secret(s) {_names(cloud)}"


def _names(cloud: str) -> str:
    return f"{AWS_KEY_ID} and {AWS_SECRET_KEY} (or {BEDROCK_API_KEY})" if cloud == "aws" else GCP_KEY


def client_args(
    model_id: str, params: dict[str, Any], secrets: Mapping[str, str], env: Mapping[str, str] = os.environ
) -> tuple[str, dict[str, Any]]:
    """The `init_chat_model` id and keyword arguments of a model in your own cloud account."""
    provider, _, name = model_id.partition(":")
    cloud = CLOUD[provider]
    extra = sorted(set(params) - SETTINGS[provider])
    if extra:
        why = (
            ": it reaches the provider at its own address (for private networking, use a VPC or Private Service "
            "Connect endpoint with private DNS)"
            if any(k in ("base_url", "endpoint_url", "api_endpoint") for k in extra)
            else ""
        )
        raise ValueError(f"model '{model_id}': {LABEL[cloud]} models do not take {', '.join(extra)}{why}")
    args = dict(params)
    if problem := region_problem(model_id, args, env):
        raise ValueError(f"model '{model_id}': {problem}")
    region = str(region_of(model_id, args, env))
    args.pop("region", None)
    if cloud == "aws":
        args["region_name"] = region
        if "api_key" not in args:
            args.update(_aws_credentials(secrets))
        return model_id, args
    args["location"] = region
    project = str(args.pop("project", None) or "").strip() or None
    creds, key_project = _gcp_credentials(secrets)
    if creds is not None:
        args["credentials"] = creds
    project = project or key_project or next((env[v] for v in PROJECT_VARS if env.get(v)), None)
    if not project:
        raise ValueError(
            f"model '{model_id}': Google Vertex AI models need a project: set the block's Project, or "
            f"{PROJECT_VARS[0]} on the server"
        )
    if not GCP_PROJECT.fullmatch(project):
        raise ValueError(f"model '{model_id}': '{project}' is not a Google Cloud project id")
    args["project"] = project
    if provider == VERTEX:  # Gemini: langchain-google-genai's Vertex AI backend, never its API key
        args.update(vertexai=True, api_key=None)
        return f"google_genai:{name}", args
    return model_id, args


def _aws_credentials(secrets: Mapping[str, str]) -> dict[str, Any]:
    """The project's AWS credentials as ChatBedrockConverse takes them; {} = the server's identity."""
    if secrets.get(BEDROCK_API_KEY):
        return {"api_key": secrets[BEDROCK_API_KEY]}
    key_id, secret = secrets.get(AWS_KEY_ID), secrets.get(AWS_SECRET_KEY)
    if not key_id and not secret:
        return {}
    if not (key_id and secret):  # half a key pair: the server's own identity must not stand in for the rest
        missing = AWS_SECRET_KEY if key_id else AWS_KEY_ID
        raise ValueError(f"the project secret {missing} is missing: AWS credentials need both {AWS_KEY_ID} and "
                         f"{AWS_SECRET_KEY}")  # fmt: skip
    out: dict[str, Any] = {"aws_access_key_id": key_id, "aws_secret_access_key": secret}
    if secrets.get(AWS_SESSION_TOKEN):
        out["aws_session_token"] = secrets[AWS_SESSION_TOKEN]
    return out


def _gcp_credentials(secrets: Mapping[str, str]) -> tuple[Any, str | None]:
    """The project's service account (credentials, its project id); (None, None) = the server's identity.

    Only a service account key is taken: other credential files (external accounts, workload identity federation
    configs) name files, addresses or commands of the server to get a token from."""
    raw = secrets.get(GCP_KEY)
    if not raw:
        return None, None
    try:
        info = json.loads(raw)
    except ValueError:
        info = None
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise ValueError(f"the project secret {GCP_KEY} must be a service account's JSON key file (type "
                         "service_account)")  # fmt: skip
    if info.get("token_uri", TOKEN_URI) != TOKEN_URI or info.get("universe_domain", UNIVERSE) != UNIVERSE:
        raise ValueError(f"the project secret {GCP_KEY} names another token address: only Google's is used")
    from google.oauth2 import service_account

    try:
        creds = service_account.Credentials.from_service_account_info({**info, "token_uri": TOKEN_URI}, scopes=SCOPES)
    except (ValueError, KeyError) as exc:
        raise ValueError(f"the project secret {GCP_KEY} is not a usable service account key: {exc}") from None
    return creds, info.get("project_id")


def has_own_credentials(init_id: str, args: Mapping[str, Any]) -> bool:
    """Whether `client_args` gave the client the project's credentials (not the server's identity)."""
    provider = init_id.partition(":")[0]
    if provider == BEDROCK:
        return bool(args.get("api_key") or args.get("aws_access_key_id"))
    return args.get("credentials") is not None


def host(model_id: str, config: Mapping[str, Any], env: Mapping[str, str] = os.environ) -> str | None:
    """The address the model's calls go to (for an export's network allow-list), when its region is known."""
    cloud = cloud_of(model_id)
    if cloud is None or region_problem(model_id, config, env):
        return None
    region = str(region_of(model_id, config, env))
    if cloud == "aws":
        return f"bedrock-runtime.{region}.amazonaws.com"
    if region == "global":
        return "aiplatform.googleapis.com"
    if region in ("us", "eu"):  # multi-region
        return f"aiplatform.{region}.rep.googleapis.com"
    return f"{region}-aiplatform.googleapis.com"
