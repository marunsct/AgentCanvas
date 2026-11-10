"""Deploy kit (S16): what an export needs to run as a service in the person's own environment.

The person picks a target (their laptop, a Linux server, Kubernetes, Cloud Run, AWS, Azure). The export then holds
the service (`app.py`, the same API as an AgentCanvas deployment), deploy files for that target only, four guides
written for this workflow (prerequisites, setup, deploy, verify and operate) with its names, keys and hosts filled
in, `make doctor` to check the environment first and `make smoke` to check the deployed service.

`kit.json` carries the workflow's facts to the generated scripts, so they stay plain files that need no
generation-time templating beyond the package name. AgentCanvas never holds the person's cloud credentials: every
cloud step runs on their machine with their own CLI.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agentcanvas import cloudmodels, designs
from agentcanvas.adapters.langchain.codegen import one_line
from agentcanvas.errors import InvalidInput
from agentcanvas.ir.contract import output_names
from agentcanvas.ir.models import SECRET_NAME, Workflow

KIT_DIR = Path(__file__).with_name("kit")
# Base images by version and digest, so a build next month gets the same bytes (bump them together, deliberately)
PYTHON_IMAGE = "python:3.12.16-slim@sha256:8523d6abeb48ceae0b7240d30c5808fc26cb79c1a635bacdf19cf3baecfbec07"
UV_IMAGE = "ghcr.io/astral-sh/uv:0.11.33@sha256:77280f2f771df71f90786c314fe1bbc1e023feac652969bbf139c280babf2eb7"
POSTGRES_IMAGE = "postgres:16.15-alpine@sha256:0cefaabd882e42bcb2a2f3dc56d3ce4770560ba62fd7f197f1a2e008fd5731ab"
CADDY_IMAGE = "caddy:2.11.7-alpine@sha256:eb4d35b7452d1ab6171fbdbe8d9275b2301dad6dad92115d91e19fb9b24d510f"
INTERRUPTING = {"core.approval", "core.human_input", "core.wait"}
AGENTS = {"core.agent", "agent.deep"}
MEMORY = {"core.memory_recall", "core.memory_remember", "core.memory_forget", "tool.memory", "backend.store"}

PROVIDER_HOSTS = {
    "openai": "api.openai.com",
    "anthropic": "api.anthropic.com",
    "google_genai": "generativelanguage.googleapis.com",
    "mistralai": "api.mistral.ai",
    "groq": "api.groq.com",
    "deepseek": "api.deepseek.com",
    "xai": "api.x.ai",
}
PROVIDER_KEYS = {  # provider -> (key variable, label)
    "openai": ("OPENAI_API_KEY", "OpenAI"),
    "azure_openai": ("AZURE_OPENAI_API_KEY", "Azure OpenAI"),
    "anthropic": ("ANTHROPIC_API_KEY", "Anthropic"),
    "google_genai": ("GOOGLE_API_KEY", "Google AI Studio"),
    "mistralai": ("MISTRAL_API_KEY", "Mistral AI"),
    "groq": ("GROQ_API_KEY", "Groq"),
    "deepseek": ("DEEPSEEK_API_KEY", "DeepSeek"),
    "xai": ("XAI_API_KEY", "xAI"),
}
LOCAL_PROVIDERS = {
    "ollama": "http://localhost:11434",
    "lmstudio": "http://localhost:1234/v1",
    "vllm": "http://localhost:8000/v1",
    "llamacpp": "http://localhost:8080/v1",
}


@dataclass(frozen=True)
class Tool:
    name: str
    command: list[str]
    why: str
    install: str
    optional: bool = False


@dataclass(frozen=True)
class Target:
    id: str
    label: str
    summary: str
    tools: list[Tool]
    deploy_env: list[tuple[str, str]]  # (variable, what it is) the deploy script needs
    database: str
    shared_database: bool  # several copies of the service run at once: conversations must live in Postgres
    sizing: str


DOCKER = Tool("docker", ["docker", "--version"], "builds the service's image", "https://docs.docker.com/get-docker/")
COMPOSE = Tool(
    "docker compose", ["docker", "compose", "version"], "starts the service and its database",
    "Docker Desktop includes it; on Linux install the docker-compose-plugin package",
)  # fmt: skip
MAKE = Tool("make", ["make", "--version"], "runs the commands in the guides", "macOS: xcode-select --install; "
            "Debian/Ubuntu: sudo apt install make; Windows: use WSL")  # fmt: skip
PYTHON = Tool(
    "python3", ["python3", "--version"], "runs make doctor and make smoke", "https://www.python.org/downloads/"
)
UV = Tool(
    "uv", ["uv", "--version"], "runs the tests and the service without Docker",
    "https://docs.astral.sh/uv/getting-started/installation/", optional=True,
)  # fmt: skip

TARGETS: dict[str, Target] = {
    t.id: t
    for t in [
        Target(
            "local", "Your computer (Docker)", "The service and a Postgres database on your own computer.",
            [DOCKER, COMPOSE, MAKE, PYTHON, UV], [],
            "Postgres in Docker Compose (included)", False,
            "1 CPU and 1 GB of memory for the service is plenty to start.",
        ),
        Target(
            "server", "Linux server or VM with Docker",
            "The service, Postgres and Caddy (automatic HTTPS) on one Linux machine you control.",
            [DOCKER, COMPOSE, MAKE, PYTHON, UV],
            [("DOMAIN", "the public name of the server, e.g. agent.example.com, with a DNS A record pointing at it"),
             ("POSTGRES_PASSWORD", "a password for the bundled database (make setup fills one in)")],
            "Postgres in Docker Compose (included), or a managed Postgres via DATABASE_URL", False,
            "A 2 CPU / 4 GB machine runs the service, Postgres and Caddy comfortably.",
        ),
        Target(
            "kubernetes", "Kubernetes (Helm)", "A Helm chart: Deployment, Service, optional Ingress and autoscaling.",
            [DOCKER, MAKE, PYTHON, UV,
             Tool("kubectl", ["kubectl", "version", "--client"], "talks to your cluster",
                  "https://kubernetes.io/docs/tasks/tools/"),
             Tool("helm", ["helm", "version", "--short"], "installs the chart", "https://helm.sh/docs/intro/install/")],
            [("IMAGE_REPOSITORY", "your registry path for the image, e.g. ghcr.io/acme/support-bot"),
             ("K8S_NAMESPACE", "the namespace to deploy into (created if missing)"),
             ("INGRESS_HOST", "optional: a public host name; leave empty to reach the service inside the cluster")],
            "A managed Postgres your cluster can reach (DATABASE_URL)", True,
            "Requests 250m CPU / 512 Mi, limits 1 CPU / 1 Gi per pod; 2 replicas; autoscaling up to 5 on CPU.",
        ),
        Target(
            "cloudrun", "Google Cloud Run", "A Cloud Run service built with Cloud Build; keys in Secret Manager.",
            [MAKE, PYTHON, UV,
             Tool("gcloud", ["gcloud", "--version"], "builds and deploys on Google Cloud",
                  "https://cloud.google.com/sdk/docs/install")],
            [("GCP_PROJECT", "your Google Cloud project id"), ("GCP_REGION", "the region, e.g. europe-west1"),
             ("GCP_SERVICE_ACCOUNT", "optional: the account the service runs as (default: the project's compute "
              "account); the deploy lets it read the secrets")],
            "Cloud SQL for PostgreSQL (DATABASE_URL)", True,
            "1 CPU / 1 GiB per instance, 0 to 5 instances, 80 concurrent requests each.",
        ),
        Target(
            "aws", "AWS (ECS Fargate)", "An ECS Fargate service; the image in ECR, keys in Secrets Manager.",
            [DOCKER, MAKE, PYTHON, UV,
             Tool("aws", ["aws", "--version"], "pushes the image and deploys on AWS",
                  "https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html")],
            [("AWS_REGION", "the region, e.g. eu-west-1"), ("ECS_CLUSTER", "an existing ECS cluster name"),
             ("ECS_SUBNETS", "comma-separated subnet ids the service runs in"),
             ("ECS_SECURITY_GROUP", "a security group that allows inbound 8000 from your load balancer or callers"),
             ("ECS_EXECUTION_ROLE_ARN", "a task execution role that can pull from ECR and read the secret")],
            "Amazon RDS for PostgreSQL (DATABASE_URL)", True,
            "0.5 vCPU / 1 GB per task, 2 tasks; put an Application Load Balancer in front for HTTPS.",
        ),
        Target(
            "azure", "Azure Container Apps", "A Container App built in Azure Container Registry; keys as app secrets.",
            [MAKE, PYTHON, UV,
             Tool("az", ["az", "version"], "builds and deploys on Azure",
                  "https://learn.microsoft.com/cli/azure/install-azure-cli")],
            [("AZ_RESOURCE_GROUP", "an existing resource group"), ("AZ_ACR_NAME", "an Azure Container Registry name"),
             ("AZ_CONTAINERAPP_ENV", "an existing Container Apps environment")],
            "Azure Database for PostgreSQL (DATABASE_URL)", True,
            "0.5 CPU / 1 Gi per replica, 1 to 5 replicas on HTTP load; HTTPS is built in.",
        ),
    ]
}  # fmt: skip


@dataclass
class Facts:
    """What the workflow needs from an environment, read from its IR."""

    name: str
    pkg: str
    slug: str
    version: str
    models: list[dict[str, Any]] = field(default_factory=list)
    env: list[dict[str, Any]] = field(default_factory=list)  # model keys and secrets the service reads
    hosts: list[dict[str, Any]] = field(default_factory=list)
    parts: list[dict[str, str]] = field(default_factory=list)  # agents in an environment of their own (S28)
    pauses: bool = False
    memory: bool = False
    chat: bool = False
    example_input: dict[str, Any] = field(default_factory=dict)
    example_context: dict[str, Any] = field(default_factory=dict)

    @property
    def needs_database(self) -> bool:
        return self.pauses or self.memory or self.chat


def _walk(wf: Workflow) -> tuple[list[Any], list[Any]]:
    nodes, resources = list(wf.nodes), list(wf.resources)
    for n in wf.nodes:
        if n.type == "core.subgraph":
            try:
                inner = _walk(wf.subworkflow(n))
            except Exception:  # noqa: BLE001 - an invalid subgraph cannot be exported anyway
                continue
            nodes += inner[0]
            resources += inner[1]
    return nodes, resources


def _secret_names(value: Any) -> set[str]:
    if isinstance(value, dict):
        if isinstance(value.get("$secret"), str):
            name = value["$secret"]
            if not SECRET_NAME.fullmatch(name):  # it becomes a shell and Makefile variable in the export
                raise InvalidInput(f"secret name {name!r} is not a variable name (letters, digits and _)")
            return {name}
        return set().union(*(_secret_names(v) for v in value.values())) if value else set()
    if isinstance(value, list):
        return set().union(*(_secret_names(v) for v in value)) if value else set()
    return set()


def _host(url: str) -> tuple[str, int] | None:
    u = urlparse(url if "://" in url else f"https://{url}")
    if not u.hostname or "{" in u.hostname:
        return None
    return u.hostname, u.port or (80 if u.scheme == "http" else 443)


def _request_body(f: Facts) -> str:
    body: dict[str, Any] = {"input": f.example_input}
    if f.example_context:
        body["context"] = f.example_context
    return json.dumps(body, ensure_ascii=False)


def _example_context(wf: Workflow, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Context for `make smoke` and the service tests: the first test case's, else a value per required input field
    (a required one missing makes the service answer 400)."""
    fields = [c for c in wf.context.fields if c.source == "input"]
    for c in cases:
        if isinstance(c.get("context"), dict) and c["context"]:
            return {k: v for k, v in c["context"].items() if any(f.name == k for f in fields)}
    out: dict[str, Any] = {}
    for f in fields:
        if f.required:
            kind = (f.type or {}).get("type", "string")
            out[f.name] = 1 if kind in ("number", "integer") else True if kind == "boolean" else f"example-{f.name}"
    return out


def _hinted(schema: dict[str, Any]) -> Any:
    """A value the channel's JSON Schema suggests: `examples`, `default`, or the first of `enum`; None if it has none."""
    if isinstance(schema.get("examples"), list) and schema["examples"]:
        return schema["examples"][0]
    if schema.get("default") not in (None, ""):
        return schema["default"]
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return schema["enum"][0]
    return None


def _example(wf: Workflow, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """An input for `make smoke`: the first saved test case's, or one value per input channel."""
    for c in cases:
        if isinstance(c.get("input"), dict) and c["input"]:
            return c["input"]
    wanted = set(wf.settings.input_channels or [])
    out: dict[str, Any] = {}
    for ch in wf.state.channels:
        if (wanted and ch.name not in wanted) or (not wanted and ch.io not in ("in", "inout")):
            continue
        kind = (ch.type or {}).get("type", "string")
        hinted = _hinted(ch.type or {})
        if (
            hinted is not None
        ):  # the channel's own example, default or first allowed value: a made-up string may not parse
            out[ch.name] = hinted
        elif ch.reducer == "add_messages":
            out[ch.name] = [{"role": "user", "content": "Hello! What can you do?"}]
        elif kind == "boolean":
            out[ch.name] = True
        elif kind in ("number", "integer"):
            out[ch.name] = 1
        elif kind == "array":
            out[ch.name] = ["example"]
        elif kind == "object":
            out[ch.name] = {}
        else:
            out[ch.name] = f"An example {ch.description or ch.name}".strip()
    return out


def facts(wf: Workflow, pkg: str, version: str, cases: list[dict[str, Any]] | None = None) -> Facts:
    nodes, resources = _walk(wf)
    # the name heads comments in shell, YAML and Makefiles: one line, no trailing \ joining the next line
    f = Facts(name=one_line(wf.name).rstrip("\\ ") or pkg, pkg=pkg, slug=pkg.replace("_", "-"), version=version)
    env: dict[str, dict[str, Any]] = {}
    hosts: dict[str, dict[str, Any]] = {}
    for r in resources:
        cfg = r.config or {}
        if r.type == "model.chat":
            model = str(cfg.get("model", ""))
            provider = model.partition(":")[0]
            f.models.append({"resource": r.name, "model": model, "config": cfg})
            secret_key = _secret_names(cfg.get("api_key"))
            if provider in PROVIDER_KEYS and not secret_key:
                var, label = PROVIDER_KEYS[provider]
                env.setdefault(var, {"name": var, "kind": "model_key", "required": True,
                                     "where": f"an API key from your {label} account"})  # fmt: skip
            base = cfg.get("base_url") or LOCAL_PROVIDERS.get(provider)
            cloud_host = cloudmodels.host(model, cfg, {})  # AWS Bedrock, Vertex AI: the block's region's address
            host = (
                _host(str(base)) if base
                else _host(f"https://{cloud_host}") if cloud_host
                else (_host(PROVIDER_HOSTS[provider]) if provider in PROVIDER_HOSTS else None)
            )  # fmt: skip
            if host:
                hosts.setdefault(host[0], {"host": host[0], "port": host[1], "kind": "model",
                                           "why": f"the AI model {model}"})  # fmt: skip
        elif r.type == "mcp.server" and cfg.get("transport", "http") == "http" and cfg.get("url"):
            if host := _host(str(cfg["url"])):
                hosts.setdefault(host[0], {"host": host[0], "port": host[1], "kind": "tool",
                                           "why": f"the app connector (MCP) '{r.name}'"})  # fmt: skip
        elif r.type == "tool.http":
            allowed = cfg.get("allowed_domains")
            for d in [cfg.get("url"), *(allowed if isinstance(allowed, list) else [])]:
                if isinstance(d, str) and (host := _host(d.removeprefix("*."))):
                    hosts.setdefault(host[0], {"host": host[0], "port": host[1], "kind": "tool",
                                               "why": f"the web API tool '{r.name}'"})  # fmt: skip
        if r.type in MEMORY:
            f.memory = True
    for name in sorted(_secret_names(wf.model_dump(mode="json"))):
        env.setdefault(name, {"name": name, "kind": "secret", "required": True,
                              "where": f"the value of the project secret {name} in AgentCanvas (Workspace → "
                              "Secrets); AgentCanvas never exports secret values"})  # fmt: skip
    for n in nodes:
        if n.type in INTERRUPTING or (n.type in AGENTS and n.config.get("approve_tools")):
            f.pauses = True
        fw = designs.AGENT_FRAMEWORKS.get(str(n.engine)) if n.type in designs.DESIGN_TYPES else None
        if fw is not None and fw.own_env:
            f.parts.append({"name": n.name, "framework": fw.id, "label": fw.label})
        if n.type == "agent.a2a" and n.config.get("url") and (host := _host(str(n.config["url"]))):
            hosts.setdefault(host[0], {"host": host[0], "port": host[1], "kind": "agent",
                                       "why": f"the remote agent (A2A) '{n.name}'"})  # fmt: skip
        if n.type in MEMORY:
            f.memory = True
    f.chat = any(c.reducer == "add_messages" and c.io in ("in", "inout") for c in wf.state.channels)
    f.env, f.hosts = list(env.values()), list(hosts.values())
    f.example_input = _example(wf, cases or [])
    f.example_context = _example_context(wf, cases or [])
    return f


def kit_json(wf: Workflow, f: Facts, target: Target) -> str:
    s = wf.settings
    outputs = output_names(wf)
    tools = [{"name": t.name, "command": t.command, "why": t.why, "optional": t.optional} for t in target.tools]
    env = [*f.env, *({"name": n, "kind": "deploy", "required": not w.startswith("optional"), "where": w}
                     for n, w in target.deploy_env)]  # fmt: skip
    return json.dumps(
        {
            "name": f.name, "slug": f.slug, "version": f.version, "target": target.id,
            "env": env, "hosts": f.hosts, "tools": tools,
            "models": [{"resource": m["resource"], "model": m["model"], "config": m["config"]} for m in f.models],
            "needs_database": f.needs_database, "example_input": f.example_input, "parts": f.parts,
            "example_context": f.example_context,
            "node_ids": {n.name: n.id for n in wf.nodes}, "output_channels": outputs,
            "context_fields": [{"name": c.name, "source": c.source, "required": c.required}
                               for c in wf.context.fields],
            "recursion_limit": s.recursion_limit,
            # the per-run limits of the workflow budget (the service enforces them; agentcanvas/.../spend.py)
            "budget": {k: v for k, v in (s.budget.model_dump() if s.budget else {}).items()
                       if k in ("max_tokens", "max_cost_usd", "max_seconds") and v is not None},
            "error_routes": any(n.policies and n.policies.error_route for n in wf.nodes),
        },
        indent=2,
        ensure_ascii=False,
    ) + "\n"  # fmt: skip


def service_vars(f: Facts) -> list[str]:
    """The variables the running service reads (what the deploy scripts hand to the platform)."""
    return ["API_KEYS", "DATABASE_URL", *(v["name"] for v in f.env)]


# ------------------------------------------------------------------------------------------------ files ----
def _template(name: str, pkg: str) -> str:
    return (KIT_DIR / name).read_text(encoding="utf-8").replace("__PKG__", pkg)


def env_example(f: Facts, target: Target) -> str:
    lines = [
        "# Settings for the service. `make setup` copies this file to .env with a fresh API key; then fill in",
        "# the empty values (docs/2-SETUP.md). Never commit .env.",
        "",
        "# The keys callers send as `Authorization: Bearer <key>` (comma-separated; rotate by adding a new one first)",
        "API_KEYS=",
    ]
    keys = [v for v in f.env if v["kind"] == "model_key"]
    if keys:
        lines += ["", "# AI model keys"] + [f"# {v['where']}\n{v['name']}=" for v in keys]
    secrets = [v for v in f.env if v["kind"] == "secret"]
    if secrets:
        lines += ["", "# Secrets the workflow uses (copy the values from AgentCanvas → Workspace → Secrets)"]
        lines += [f"{v['name']}=" for v in secrets]
    lines += ["", "# Database: Postgres keeps conversations and memories (see docs/1-PREREQUISITES.md#data)"]
    if target.id in ("local", "server"):
        lines += ["# Docker Compose sets DATABASE_URL to its own Postgres; set it here only to use another one."]
        lines += ["POSTGRES_PASSWORD="] if target.id == "server" else []
    lines += [
        "DATABASE_URL=",
        "# Without DATABASE_URL: a SQLite file (one machine only)",
        "SQLITE_PATH=data/checkpoints.sqlite",
    ]
    if target.deploy_env:
        lines += ["", f"# Deploying to {target.label}"]
        lines += [f"# {w}\n{n}=" for n, w in target.deploy_env if n != "POSTGRES_PASSWORD"]
    lines += [
        "",
        "# Optional",
        "PORT=8000",
        "LOG_LEVEL=INFO",
        "RUN_TIMEOUT_SECONDS=300",
        "# DEMO_MODE=true answers with canned replies instead of calling AI models (for a first try without keys)",
        "DEMO_MODE=false",
        "# FEEDBACK_FILE=data/feedback.jsonl",
        "LANGSMITH_TRACING=false",
        "LANGSMITH_API_KEY=",
    ]
    return "\n".join(lines) + "\n"


def dockerfile(pkg: str, parts: list[dict[str, str]] | None = None) -> str:
    part_lines = ""
    if parts:  # S28: each framework that clashes with LangGraph gets its own environment, inside the same image
        names = " ".join(p["name"] for p in parts)
        part_lines = f"""
# Parts of this mixed flow, each in its own Python environment ({", ".join(f"{p['name']}: {p['label']}" for p in parts)})
COPY parts ./parts
RUN for p in {names}; do uv venv parts/$p/.venv && uv pip install --python parts/$p/.venv/bin/python \\
      -r parts/$p/requirements.txt || exit 1; done"""
    return f"""# The service: port 8000, a non-root user, a health check. Build: docker build -t {pkg.replace("_", "-")} .
FROM {PYTHON_IMAGE}
COPY --from={UV_IMAGE} /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \\
    PYTHONUNBUFFERED=1 PYTHONPATH=/app/src PATH=/app/.venv/bin:$PATH
WORKDIR /app
# uv.lock (written by `make setup`, `make install` or `uv lock`) pins every package; without it uv resolves afresh
COPY pyproject.toml uv.lock* ./
RUN uv sync --no-dev --no-install-project $([ -f uv.lock ] && echo --locked){part_lines}
COPY src ./src
COPY workflow.ir.json ./
RUN useradd --uid 10001 --create-home app && mkdir -p /app/data && chown app /app/data
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \\
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]
CMD ["uvicorn", "{pkg}.app:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "src", "--proxy-headers"]
"""


DOCKERIGNORE = ".env\n.venv/\nparts/*/.venv/\n**/__pycache__/\ndata/\n.git/\n"


def makefile(f: Facts, target: Target) -> str:
    logs = {
        "local": "docker compose --env-file .env -f deploy/local/docker-compose.yml logs -f app",
        "server": "docker compose --env-file .env -f deploy/server/docker-compose.yml logs -f app",
        "kubernetes": f"kubectl -n $${{K8S_NAMESPACE:-default}} logs -f deploy/{f.slug}",
        "cloudrun": f"gcloud run services logs tail {f.slug} --region $$GCP_REGION --project $$GCP_PROJECT",
        "aws": f"aws logs tail /ecs/{f.slug} --follow --region $$AWS_REGION",
        "azure": f"az containerapp logs show -n {f.slug} -g $$AZ_RESOURCE_GROUP --follow",
    }[target.id]
    parts_install = parts_cmd = ""
    if f.parts:  # S28: and each part's, in an environment of its own
        parts_install = ", and each framework part's in its own environment"
        parts_cmd = (
            "\n\t@for p in $(PARTS); do uv venv -q --allow-existing parts/$$p/.venv && "
            "uv pip install -q --python parts/$$p/.venv/bin/python -r parts/$$p/requirements.txt || exit 1; done"
        )
    return f"""# {f.name}: the commands the guides in docs/ use. `make` lists them.
PKG := {f.pkg}{f"{chr(10)}PARTS := {' '.join(p['name'] for p in f.parts)}" if f.parts else ""}
URL ?= http://localhost:8000
SHELL := /bin/bash

.PHONY: help doctor setup install test gate run deploy smoke logs
help:  ## list the commands
\t@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\\t/'

doctor:  ## check this environment has everything (ARGS=--live also tries each model key)
\t@python3 src/$(PKG)/checks.py $(ARGS)

setup:  ## create .env with a fresh API key (then fill in the empty values), and lock the packages (uv.lock)
\t@python3 src/$(PKG)/checks.py --init
\tuv lock -q

install:  ## install the service's Python packages (needs uv){parts_install}
\tuv sync{parts_cmd}

test: install  ## the graph builds and the saved test cases pass, offline
\tuv run pytest -q

gate: install  ## the release gate: every saved test case passes, and the service answers the API
\tuv run pytest -q tests/test_smoke.py tests/test_service.py

run: install  ## run the service here without Docker, on PORT (default 8000)
\tset -a; source .env; set +a; uv run uvicorn $(PKG).app:app --app-dir src --port $${{PORT:-8000}}

deploy:  ## deploy to {target.label}
\tbash deploy/{target.id}/deploy.sh

smoke:  ## call the deployed service once: make smoke URL=https://...
\t@python3 src/$(PKG)/smoke.py $(URL)

logs:  ## follow the service's logs
\tset -a; source .env; set +a; {logs}
"""


# --------------------------------------------------------------------------------------------- targets ----
def _load_env_sh() -> str:
    return 'cd "$(dirname "$0")/../.."\n[ -f .env ] || { echo "No .env: run make setup first"; exit 1; }\nset -a; . ./.env; set +a\n'


def target_files(f: Facts, target: Target) -> dict[str, str]:
    slug, svc_vars = f.slug, service_vars(f)
    secret_vars = [v for v in svc_vars if v != "DATABASE_URL"]
    stored = [*secret_vars, "DATABASE_URL"]  # what the platform's secret store holds
    if target.id in ("local", "server"):
        return _compose_files(f, target)
    if target.id == "kubernetes":
        return _helm_files(f)
    if target.id == "cloudrun":
        sets = ",".join(f"{v}={slug}-{v.lower().replace('_', '-')}:latest" for v in stored)
        return {
            "deploy/cloudrun/deploy.sh": f"""#!/usr/bin/env bash
# Build with Cloud Build, keep the settings in Secret Manager and deploy to Cloud Run (docs/3-DEPLOY.md).
set -euo pipefail
{_load_env_sh()}: "${{GCP_PROJECT:?set GCP_PROJECT in .env}}" "${{GCP_REGION:?set GCP_REGION in .env}}"
REPO=${{ARTIFACT_REPO:-agentcanvas}}
IMAGE="$GCP_REGION-docker.pkg.dev/$GCP_PROJECT/$REPO/{slug}:${{TAG:-$(date +%Y%m%d-%H%M%S)}}"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \\
  secretmanager.googleapis.com --project "$GCP_PROJECT"
gcloud artifacts repositories describe "$REPO" --location "$GCP_REGION" --project "$GCP_PROJECT" >/dev/null 2>&1 ||
  gcloud artifacts repositories create "$REPO" --repository-format docker --location "$GCP_REGION" --project "$GCP_PROJECT"
gcloud builds submit --tag "$IMAGE" --project "$GCP_PROJECT" .
# the service runs as this account; it must be able to read the secrets below
SA=${{GCP_SERVICE_ACCOUNT:-$(gcloud projects describe "$GCP_PROJECT" --format 'value(projectNumber)')-compute@developer.gserviceaccount.com}}
for var in {" ".join(stored)}; do
  name="{slug}-$(echo "$var" | tr 'A-Z_' 'a-z-')"
  value="${{!var:-}}"
  [ -n "$value" ] || {{ [ "$var" = DATABASE_URL ] && value="unset"; }} || {{ echo "$var is empty in .env"; exit 1; }}
  gcloud secrets describe "$name" --project "$GCP_PROJECT" >/dev/null 2>&1 ||
    gcloud secrets create "$name" --replication-policy automatic --project "$GCP_PROJECT"
  printf %s "$value" | gcloud secrets versions add "$name" --data-file=- --project "$GCP_PROJECT" >/dev/null
  gcloud secrets add-iam-policy-binding "$name" --member "serviceAccount:$SA" \\
    --role roles/secretmanager.secretAccessor --project "$GCP_PROJECT" >/dev/null
done
gcloud run deploy {slug} --image "$IMAGE" --region "$GCP_REGION" --project "$GCP_PROJECT" \\
  --port 8000 --cpu 1 --memory 1Gi --min-instances 0 --max-instances 5 --concurrency 80 --timeout 600 \\
  --allow-unauthenticated --service-account "$SA" --set-secrets "{sets}"
echo "Deployed. Next: make smoke URL=$(gcloud run services describe {slug} --region "$GCP_REGION" \\
  --project "$GCP_PROJECT" --format 'value(status.url)')"
""",
        }
    if target.id == "aws":
        secrets = ",\n".join(f'        {{"name": "{v}", "valueFrom": "$SECRET_ARN:{v}::"}}' for v in stored)
        task = f"""{{
  "family": "{slug}",
  "requiresCompatibilities": ["FARGATE"],
  "networkMode": "awsvpc",
  "cpu": "512",
  "memory": "1024",
  "executionRoleArn": "$ECS_EXECUTION_ROLE_ARN",
  "containerDefinitions": [
    {{
      "name": "{slug}",
      "image": "$IMAGE",
      "essential": true,
      "portMappings": [{{"containerPort": 8000, "protocol": "tcp"}}],
      "secrets": [
{secrets}
      ],
      "healthCheck": {{
        "command": ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"],
        "interval": 30, "timeout": 5, "retries": 3, "startPeriod": 20
      }},
      "logConfiguration": {{
        "logDriver": "awslogs",
        "options": {{"awslogs-group": "/ecs/{slug}", "awslogs-region": "$AWS_REGION", "awslogs-stream-prefix": "app",
                    "awslogs-create-group": "true"}}
      }}
    }}
  ]
}}
"""
        return {
            "deploy/aws/task-definition.json": task,
            "deploy/aws/deploy.sh": f"""#!/usr/bin/env bash
# Push the image to ECR, keep the settings in Secrets Manager and run it on ECS Fargate (docs/3-DEPLOY.md).
set -euo pipefail
{_load_env_sh()}for v in AWS_REGION ECS_CLUSTER ECS_SUBNETS ECS_SECURITY_GROUP ECS_EXECUTION_ROLE_ARN; do
  [ -n "${{!v:-}}" ] || {{ echo "set $v in .env"; exit 1; }}
done
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
IMAGE="$REGISTRY/{slug}:${{TAG:-$(date +%Y%m%d-%H%M%S)}}"
aws ecr describe-repositories --repository-names {slug} --region "$AWS_REGION" >/dev/null 2>&1 ||
  aws ecr create-repository --repository-name {slug} --region "$AWS_REGION" >/dev/null
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
docker build --platform linux/amd64 -t "$IMAGE" . && docker push "$IMAGE"
SECRET_JSON=$(python3 - <<'PY'
import json, os
print(json.dumps({{v: os.environ.get(v) or ("unset" if v == "DATABASE_URL" else "") for v in {json.dumps(stored)}}}))
PY
)
SECRET_ARN=$(aws secretsmanager describe-secret --secret-id {slug} --region "$AWS_REGION" --query ARN --output text \\
  2>/dev/null || aws secretsmanager create-secret --name {slug} --secret-string "$SECRET_JSON" --region "$AWS_REGION" \\
  --query ARN --output text)
aws secretsmanager put-secret-value --secret-id {slug} --secret-string "$SECRET_JSON" --region "$AWS_REGION" >/dev/null
export IMAGE SECRET_ARN
TASK=$(python3 -c 'import os,sys; print(os.path.expandvars(open(sys.argv[1]).read()))' deploy/aws/task-definition.json)
REVISION=$(aws ecs register-task-definition --cli-input-json "$TASK" --region "$AWS_REGION" \\
  --query taskDefinition.taskDefinitionArn --output text)
NET="awsvpcConfiguration={{subnets=[$ECS_SUBNETS],securityGroups=[$ECS_SECURITY_GROUP],assignPublicIp=ENABLED}}"
if aws ecs describe-services --cluster "$ECS_CLUSTER" --services {slug} --region "$AWS_REGION" \\
    --query 'services[?status==`ACTIVE`]' --output text | grep -q .; then
  aws ecs update-service --cluster "$ECS_CLUSTER" --service {slug} --task-definition "$REVISION" \\
    --region "$AWS_REGION" >/dev/null
else
  aws ecs create-service --cluster "$ECS_CLUSTER" --service-name {slug} --task-definition "$REVISION" \\
    --desired-count 2 --launch-type FARGATE --network-configuration "$NET" --region "$AWS_REGION" >/dev/null
fi
aws ecs wait services-stable --cluster "$ECS_CLUSTER" --services {slug} --region "$AWS_REGION"
echo "Deployed {slug} ($REVISION). Point your load balancer at port 8000, then: make smoke URL=https://<its address>"
""",
        }
    # azure
    secret_args = " ".join(
        f'{v.lower().replace("_", "-")}="${{{v}:-unset}}"' for v in stored
    )  # unset: DATABASE_URL only
    env_args = " ".join(f"{v}=secretref:{v.lower().replace('_', '-')}" for v in stored)
    return {
        "deploy/azure/deploy.sh": f"""#!/usr/bin/env bash
# Build in Azure Container Registry and run on Azure Container Apps; settings become app secrets (docs/3-DEPLOY.md).
set -euo pipefail
{_load_env_sh()}for v in AZ_RESOURCE_GROUP AZ_ACR_NAME AZ_CONTAINERAPP_ENV {" ".join(secret_vars)}; do
  [ -n "${{!v:-}}" ] || {{ echo "set $v in .env"; exit 1; }}
done
IMAGE="$AZ_ACR_NAME.azurecr.io/{slug}:${{TAG:-$(date +%Y%m%d-%H%M%S)}}"
az acr build --registry "$AZ_ACR_NAME" --image "${{IMAGE#*/}}" .
if az containerapp show -n {slug} -g "$AZ_RESOURCE_GROUP" >/dev/null 2>&1; then
  az containerapp secret set -n {slug} -g "$AZ_RESOURCE_GROUP" --secrets {secret_args} >/dev/null
  az containerapp update -n {slug} -g "$AZ_RESOURCE_GROUP" --image "$IMAGE" --set-env-vars {env_args} >/dev/null
else
  az containerapp create -n {slug} -g "$AZ_RESOURCE_GROUP" --environment "$AZ_CONTAINERAPP_ENV" \\
    --image "$IMAGE" --registry-server "$AZ_ACR_NAME.azurecr.io" --registry-identity system \\
    --target-port 8000 --ingress external --cpu 0.5 --memory 1Gi --min-replicas 1 --max-replicas 5 \\
    --secrets {secret_args} --env-vars {env_args} >/dev/null
fi
echo "Deployed. Next: make smoke URL=https://$(az containerapp show -n {slug} -g "$AZ_RESOURCE_GROUP" \\
  --query properties.configuration.ingress.fqdn -o tsv)"
""",
    }


def _compose_files(f: Facts, target: Target) -> dict[str, str]:
    t = target.id
    password = "${POSTGRES_PASSWORD:?run make setup}" if t == "server" else "app"
    app_ports = '    ports: ["${PORT:-8000}:8000"]\n' if t == "local" else '    expose: ["8000"]\n'
    compose = f"""# {f.name} on {target.label}: `make deploy` (docs/3-DEPLOY.md)
name: {f.slug}
services:
  app:
    build: ../..
    image: {f.slug}:latest
    env_file: ../../.env
    environment:
      DATABASE_URL: ${{DATABASE_URL:-postgresql://app:{password}@db:5432/app}}
{app_ports}    depends_on:
      db: {{condition: service_healthy}}
    restart: unless-stopped
  db:
    image: {POSTGRES_IMAGE}
    environment: {{POSTGRES_USER: app, POSTGRES_PASSWORD: "{password}", POSTGRES_DB: app}}
    volumes: [db:/var/lib/postgresql/data]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app"]
      interval: 5s
      retries: 20
    restart: unless-stopped
"""
    if t == "server":
        compose += f"""  proxy:
    image: {CADDY_IMAGE}
    ports: ["80:80", "443:443"]
    environment: {{DOMAIN: "${{DOMAIN:?set DOMAIN in .env}}"}}
    volumes: ["./Caddyfile:/etc/caddy/Caddyfile:ro", "caddy:/data"]
    depends_on: [app]
    restart: unless-stopped
"""
    compose += "volumes:\n  db: {}\n" + ("  caddy: {}\n" if t == "server" else "")
    files = {
        f"deploy/{t}/docker-compose.yml": compose,
        f"deploy/{t}/deploy.sh": f"""#!/usr/bin/env bash
# Build and start the service{" behind Caddy (automatic HTTPS for $DOMAIN)" if t == "server" else ""} (docs/3-DEPLOY.md).
set -euo pipefail
{_load_env_sh()}docker compose --env-file .env -f deploy/{t}/docker-compose.yml up -d --build --wait
echo "Running. Next: make smoke URL={"https://$DOMAIN" if t == "server" else "http://localhost:${PORT:-8000}"}"
""",
    }
    if t == "server":
        files["deploy/server/Caddyfile"] = "{$DOMAIN} {\n\tencode gzip\n\treverse_proxy app:8000\n}\n"
    return files


def _helm_files(f: Facts) -> dict[str, str]:
    s = f.slug
    return {
        "deploy/kubernetes/deploy.sh": f"""#!/usr/bin/env bash
# Push the image, store the settings as a Kubernetes secret and install the Helm chart (docs/3-DEPLOY.md).
set -euo pipefail
{_load_env_sh()}: "${{IMAGE_REPOSITORY:?set IMAGE_REPOSITORY in .env}}"
NS=${{K8S_NAMESPACE:-default}}
TAG=${{TAG:-$(date +%Y%m%d-%H%M%S)}}
docker build --platform linux/amd64 -t "$IMAGE_REPOSITORY:$TAG" . && docker push "$IMAGE_REPOSITORY:$TAG"
kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"
python3 - <<'PY' > /tmp/{s}-env
import os
for v in {json.dumps(service_vars(f))}:
    print(f"{{v}}={{os.environ.get(v, '')}}")
PY
kubectl -n "$NS" create secret generic {s}-env --from-env-file=/tmp/{s}-env --dry-run=client -o yaml | kubectl apply -f -
rm -f /tmp/{s}-env
helm upgrade --install {s} deploy/kubernetes/chart -n "$NS" --wait \\
  --set image.repository="$IMAGE_REPOSITORY" --set image.tag="$TAG" \\
  ${{INGRESS_HOST:+--set ingress.enabled=true --set ingress.host=$INGRESS_HOST}}
echo "Deployed. Try it: kubectl -n $NS port-forward svc/{s} 8000:80 & make smoke"
""",
        "deploy/kubernetes/chart/Chart.yaml": f"""apiVersion: v2
name: {s}
description: {json.dumps(f"{f.name}, exported from AgentCanvas")}
type: application
version: 0.1.0
appVersion: "{f.version}"
""",
        "deploy/kubernetes/chart/values.yaml": f"""image:
  repository: ""   # set by deploy.sh from IMAGE_REPOSITORY
  tag: latest
  pullPolicy: IfNotPresent
replicas: 2
secretName: {s}-env   # created by deploy.sh from .env
resources:
  requests: {{cpu: 250m, memory: 512Mi}}
  limits: {{cpu: "1", memory: 1Gi}}
autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 5
  targetCPUUtilizationPercentage: 70
service:
  port: 80
ingress:
  enabled: false
  className: ""      # e.g. nginx
  host: ""
  tls: true          # needs cert-manager or an existing secret named <host>-tls
  annotations: {{}}
""",
        "deploy/kubernetes/chart/templates/deployment.yaml": f"""apiVersion: apps/v1
kind: Deployment
metadata:
  name: {s}
  labels: {{app: {s}}}
spec:
  {{{{- if not .Values.autoscaling.enabled }}}}
  replicas: {{{{ .Values.replicas }}}}
  {{{{- end }}}}
  selector:
    matchLabels: {{app: {s}}}
  template:
    metadata:
      labels: {{app: {s}}}
    spec:
      securityContext: {{runAsNonRoot: true, runAsUser: 10001, fsGroup: 10001}}
      containers:
        - name: app
          image: "{{{{ .Values.image.repository }}}}:{{{{ .Values.image.tag }}}}"
          imagePullPolicy: {{{{ .Values.image.pullPolicy }}}}
          ports: [{{name: http, containerPort: 8000}}]
          envFrom: [{{secretRef: {{name: {{{{ .Values.secretName }}}}}}}}]
          readinessProbe: {{httpGet: {{path: /healthz, port: http}}, periodSeconds: 10}}
          livenessProbe: {{httpGet: {{path: /healthz, port: http}}, initialDelaySeconds: 20, periodSeconds: 20}}
          resources: {{{{- toYaml .Values.resources | nindent 12 }}}}
          securityContext: {{allowPrivilegeEscalation: false, readOnlyRootFilesystem: true}}
          volumeMounts: [{{name: data, mountPath: /app/data}}, {{name: tmp, mountPath: /tmp}}]
      volumes: [{{name: data, emptyDir: {{}}}}, {{name: tmp, emptyDir: {{}}}}]
""",
        "deploy/kubernetes/chart/templates/service.yaml": f"""apiVersion: v1
kind: Service
metadata:
  name: {s}
  labels: {{app: {s}}}
spec:
  selector: {{app: {s}}}
  ports: [{{name: http, port: {{{{ .Values.service.port }}}}, targetPort: http}}]
""",
        "deploy/kubernetes/chart/templates/hpa.yaml": f"""{{{{- if .Values.autoscaling.enabled }}}}
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {s}
spec:
  scaleTargetRef: {{apiVersion: apps/v1, kind: Deployment, name: {s}}}
  minReplicas: {{{{ .Values.autoscaling.minReplicas }}}}
  maxReplicas: {{{{ .Values.autoscaling.maxReplicas }}}}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: {{type: Utilization, averageUtilization: {{{{ .Values.autoscaling.targetCPUUtilizationPercentage }}}}}}
{{{{- end }}}}
""",
        "deploy/kubernetes/chart/templates/ingress.yaml": f"""{{{{- if .Values.ingress.enabled }}}}
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: {s}
  annotations: {{{{- toYaml .Values.ingress.annotations | nindent 4 }}}}
spec:
  {{{{- with .Values.ingress.className }}}}
  ingressClassName: {{{{ . }}}}
  {{{{- end }}}}
  {{{{- if .Values.ingress.tls }}}}
  tls: [{{hosts: ["{{{{ .Values.ingress.host }}}}"], secretName: "{{{{ .Values.ingress.host }}}}-tls"}}]
  {{{{- end }}}}
  rules:
    - host: {{{{ .Values.ingress.host | quote }}}}
      http:
        paths:
          - path: /
            pathType: Prefix
            backend: {{service: {{name: {s}, port: {{name: http}}}}}}
{{{{- end }}}}
""",
    }


# ---------------------------------------------------------------------------------------------- guides ----
def prerequisites(f: Facts, target: Target) -> dict[str, Any]:
    """What the person needs before deploying (the export dialog's preview and 1-PREREQUISITES.md)."""
    return {
        "target": target.id,
        "label": target.label,
        "summary": target.summary,
        "tools": [{"name": t.name, "why": t.why, "install": t.install, "optional": t.optional} for t in target.tools],
        "keys": [{"name": v["name"], "kind": v["kind"], "where": v["where"]} for v in f.env],
        "deploy_settings": [{"name": n, "what": w} for n, w in target.deploy_env],
        "hosts": [{"host": h["host"], "port": h["port"], "why": h["why"]} for h in f.hosts],
        "database": {
            "needed": f.needs_database,
            "why": _database_why(f),
            "where": target.database,
            "shared": target.shared_database,
        },
        "sizing": target.sizing,
    }


def _database_why(f: Facts) -> str:
    reasons = [
        *(["it pauses for people (approvals or questions) and must remember where it stopped"] if f.pauses else []),
        *(["it keeps memories between conversations"] if f.memory else []),
        *(["it continues chats across messages"] if f.chat else []),
    ]
    return "; ".join(reasons) if reasons else "optional: each run stands alone"


def _md_list(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (none)"


def guides(f: Facts, target: Target) -> dict[str, str]:
    p = prerequisites(f, target)
    tools = [
        f"**{t['name']}**{' (optional)' if t['optional'] else ''}: {t['why']}. Install: {t['install']}."
        for t in p["tools"]
    ]
    keys = [f"`{k['name']}`: {k['where']}." for k in p["keys"]]
    hosts = [f"`{h['host']}:{h['port']}`: {h['why']}." for h in p["hosts"]]
    deploy_settings = [f"`{d['name']}`: {d['what']}." for d in p["deploy_settings"]]
    db = p["database"]
    local_model = any(m["model"].partition(":")[0] in LOCAL_PROVIDERS for m in f.models)
    pre = f"""# 1. Prerequisites: {f.name} on {target.label}

{target.summary} Check all of this with `make doctor`: it lists anything missing and the section here that fixes it.

## Tools

{_md_list(tools)}

## Accounts and keys

{_md_list([*keys, "`API_KEYS`: the key(s) your callers will send. `make setup` creates one."])}

{"You also need: " + chr(10) + _md_list(deploy_settings) if deploy_settings else ""}

## Network

The service makes outbound HTTPS calls to:

{_md_list(hosts)}
{chr(10) + "A model on `localhost` is not reachable from inside a container: use `host.docker.internal` (Docker Desktop) or the machine's address in the model's base URL." + chr(10) if local_model else ""}
Callers reach the service on port 8000 (behind {"Caddy on 80/443" if target.id == "server" else "your load balancer or ingress" if target.shared_database else "localhost"}).

## Data

Database needed: **{"yes" if db["needed"] else "no"}**: {db["why"]}. Use: {db["where"]}.
{"Every copy of the service must share it, so set `DATABASE_URL`." if db["needed"] and db["shared"] else ""}

## Sizing

{target.sizing}
"""
    deploy_cmds = {
        "local": "make deploy        # builds the image and starts the service and Postgres\nmake smoke",
        "server": "make deploy        # on the server: builds, starts Postgres, the service and Caddy\n"
        "make smoke URL=https://$DOMAIN",
        "kubernetes": "make deploy        # pushes the image, creates the secret, installs the chart\n"
        f"kubectl -n $K8S_NAMESPACE port-forward svc/{f.slug} 8000:80 &\nmake smoke",
        "cloudrun": "gcloud auth login && gcloud config set project $GCP_PROJECT\n"
        "make deploy        # Cloud Build, Secret Manager, Cloud Run\nmake smoke URL=<the URL it prints>",
        "aws": "aws configure      # or aws sso login\nmake deploy        # ECR, Secrets Manager, ECS Fargate\n"
        "make smoke URL=https://<your load balancer>",
        "azure": "az login\nmake deploy        # Azure Container Registry build, Container Apps\n"
        "make smoke URL=<the URL it prints>",
    }[target.id]
    setup = f"""# 2. Setup

## 1. Fill in the settings

```bash
make setup          # creates .env with a fresh API key
```

Open `.env` and fill in every empty value:

{_md_list(keys + deploy_settings)}

To try it before you have keys, set `DEMO_MODE=true`: the AI models answer with canned replies.

## 2. Database

{"The bundled Postgres in Docker Compose is used automatically; leave `DATABASE_URL` empty." if target.id in ("local", "server") else "Create a Postgres database (" + target.database + ") and set `DATABASE_URL=postgresql://user:password@host:5432/dbname` in `.env`. The service creates its tables on start."}
{"Without a database the service keeps conversations in a SQLite file on one machine (`SQLITE_PATH`)." if not db["needed"] else ""}

## 3. Check and test

```bash
make doctor         # everything ready?
make doctor ARGS=--live   # also one tiny call to each AI model
make test           # the graph builds and the saved test cases pass (needs uv)
```
"""
    deploy = f"""# 3. Deploy to {target.label}

```bash
{deploy_cmds}
```

`make deploy` runs `deploy/{target.id}/deploy.sh`; read it first, every step is a plain command you can also run by
hand. Re-running it deploys a new version (a new image tag) with no downtime{"" if target.shared_database else " beyond a restart"}.

The service answers the same API as an AgentCanvas deployment (runs, resume, feedback; not its MCP and A2A
addresses), so an existing client only changes its base URL and key:

```bash
curl -s $URL/v1/deployments/{f.slug}/runs/wait -H "Authorization: Bearer $KEY" \\
  -H 'Content-Type: application/json' -d '{_request_body(f)}'
```

`/runs/stream` streams the same run as Server-Sent Events, `/threads/{{id}}/resume` answers an approval or a
question, `/runs/{{id}}/feedback` records a thumbs up or down, and `/docs` shows them all.
"""
    operate = f"""# 4. Verify and operate

## Check it works

```bash
make smoke URL=<the service's URL>
```

It calls `/healthz`, runs the workflow once with an example input (answering "yes" to approvals) and prints the
answer.

## Logs

`make logs` follows them. Each failed run is logged with its run and thread ids; set `LANGSMITH_TRACING=true` and
`LANGSMITH_API_KEY` to trace every run in LangSmith.

## Scale

{target.sizing} The service keeps no state in memory between requests except recent run ids for feedback, so you can
run more copies once conversations live in Postgres.

## Upgrade

Change the workflow in AgentCanvas, export it again for the same target, copy your `.env` into the new folder and run
`make test && make deploy`. `workflow.ir.json` records which version is deployed (import it into AgentCanvas to see
it).

## Roll back

Deploy the previous image tag: `TAG=<previous tag> make deploy` after checking out the previous export, or point the
platform at the previous image ({"docker compose: keep the old folder and run make deploy there" if target.id in ("local", "server") else "the platform keeps every revision"}).

## Back up

{"Back up the `db` volume: `docker compose -f deploy/" + target.id + "/docker-compose.yml exec db pg_dump -U app app > backup.sql`." if target.id in ("local", "server") else "Turn on automated backups of your managed Postgres (" + target.database + ")."}

## Rotate keys

API keys: add the new key to `API_KEYS` (comma-separated), deploy, move callers over, then remove the old one and
deploy again. Model keys and secrets: change them in `.env` and run `make deploy`.

## If something is wrong

- `make doctor` (on the machine you deploy from) lists missing settings and unreachable hosts.
- The service refuses to start without its settings and logs exactly which ones are missing.
- `401`: the caller's key is not in `API_KEYS`. `404 thread not found`: the conversation is in another database.
- A run fails with a model error: try `make doctor ARGS=--live` with the same `.env`.
"""
    readme_targets = (
        f"Deploy target: **{target.label}**. Guides: [prerequisites](docs/1-PREREQUISITES.md), "
        "[setup](docs/2-SETUP.md), [deploy](docs/3-DEPLOY.md), [verify and operate](docs/4-VERIFY-AND-OPERATE.md)."
    )
    return {
        "docs/1-PREREQUISITES.md": re.sub(r"\n{3,}", "\n\n", pre),
        "docs/2-SETUP.md": re.sub(r"\n{3,}", "\n\n", setup),
        "docs/3-DEPLOY.md": deploy,
        "docs/4-VERIFY-AND-OPERATE.md": operate,
        "_readme_targets": readme_targets,
    }


def files(wf: Workflow, f: Facts, target: Target) -> dict[str, str]:
    """Every deploy-kit file for `target`, by path in the export."""
    out = {
        "Dockerfile": dockerfile(f.pkg, f.parts),
        ".dockerignore": DOCKERIGNORE,
        ".env.example": env_example(f, target),
        "Makefile": makefile(f, target),
        f"src/{f.pkg}/kit.json": kit_json(wf, f, target),
        f"src/{f.pkg}/app.py": _template("app.py.tmpl", f.pkg),
        f"src/{f.pkg}/checks.py": _template("checks.py.tmpl", f.pkg),
        f"src/{f.pkg}/smoke.py": _template("smoke.py.tmpl", f.pkg),
        "tests/test_service.py": _template("test_service.py.tmpl", f.pkg),
        "workflow.ir.json": json.dumps(wf.model_dump(mode="json", exclude_none=True), indent=2, ensure_ascii=False)
        + "\n",
        **target_files(f, target),
        **guides(f, target),
    }
    return out
