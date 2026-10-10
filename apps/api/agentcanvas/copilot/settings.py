"""Which model the Copilot talks with (S14): the workflow's own model, or a separate one; org policies apply.

Stored in the project's settings under `copilot`. `source = "workflow"` (the default) uses the project's default
model from first-run setup, else the first AI model in the workflow; `"separate"` uses `model` (with its own base
URL and key secret). Either way the model must support tool calling and pass the organisation's model policies.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from agentcanvas.errors import InvalidInput
from agentcanvas.ir.models import Resource, Workflow
from agentcanvas.registry import Registry
from agentcanvas.validation.policies import Policy, check_policies

DEFAULT_TOKEN_CAP = 400_000


class CopilotSettings(BaseModel):
    enabled: bool = True
    source: Literal["workflow", "separate"] = "workflow"
    model: str | None = Field(None, pattern=r"^[a-z0-9_\-]+:.+$", description="provider:model when separate")
    base_url: str | None = Field(None, max_length=500)
    api_key: dict[str, str] | None = Field(None, description='{"$secret": NAME}')
    token_cap: int = Field(DEFAULT_TOKEN_CAP, ge=1_000, le=10_000_000, description="per conversation, in + out")
    ask_model: str | None = Field(
        None, pattern=r"^[a-z0-9_\-]+:.+$", description="split mode: a cheaper model for Ask-mode questions"
    )
    privacy: Literal["standard", "local_only"] = Field(
        "standard", description="local_only: the Copilot may only use models running on your own machines"
    )
    notes: str = Field("", max_length=4000, description="project conventions the Copilot always reads")


class ResolvedModel(BaseModel):
    model: str
    config: dict[str, Any] = Field(default_factory=dict)  # base_url, api_key reference
    origin: str  # "project default", "workflow resource 'fast_model'", "Copilot settings"


def load(project_settings: dict[str, Any] | None) -> CopilotSettings:
    return CopilotSettings.model_validate((project_settings or {}).get("copilot") or {})


def _endpoint_for(model: str, settings: CopilotSettings, project_settings: dict[str, Any] | None) -> dict[str, Any]:
    """The base URL and key of the separate model or the project default when `model` is from the same provider
    (a self-hosted server answers both), else none: the provider's own endpoint and key."""
    provider = model.partition(":")[0]
    if settings.model and settings.model.partition(":")[0] == provider:
        return {k: v for k, v in (("base_url", settings.base_url), ("api_key", settings.api_key)) if v}
    default = (project_settings or {}).get("default_model") or {}
    if str(default.get("model", "")).partition(":")[0] == provider:
        return {k: v for k, v in default.items() if k in ("base_url", "api_key")}
    return {}


def resolve(
    settings: CopilotSettings,
    project_settings: dict[str, Any] | None,
    wf: Workflow,
    registry: Registry,
    policies: list[Policy],
    mode: str = "build",
) -> ResolvedModel:
    """The Copilot's model for this workflow (and mode), or InvalidInput saying what to change."""
    if mode == "ask" and settings.ask_model:
        chosen = ResolvedModel(
            model=settings.ask_model,
            config=_endpoint_for(settings.ask_model, settings, project_settings),
            origin="Copilot settings (questions)",
        )
    elif settings.source == "separate":
        if not settings.model:
            raise InvalidInput("the Copilot is set to use a separate model, but none is chosen in Copilot settings")
        config = {k: v for k, v in (("base_url", settings.base_url), ("api_key", settings.api_key)) if v}
        chosen = ResolvedModel(model=settings.model, config=config, origin="Copilot settings")
    elif default := (project_settings or {}).get("default_model"):
        config = {k: v for k, v in default.items() if k in ("base_url", "api_key")}
        chosen = ResolvedModel(model=default["model"], config=config, origin="project default")
    else:
        res = next((r for r in wf.resources if r.type == "model.chat" and r.config.get("model")), None)
        if res is None:
            raise InvalidInput(
                "the Copilot needs a model: finish first-run setup, add an AI model to this workflow, "
                "or choose a separate model in Copilot settings"
            )
        config = {k: v for k, v in res.config.items() if k in ("base_url", "api_key")}
        chosen = ResolvedModel(model=str(res.config["model"]), config=config, origin=f"workflow model '{res.name}'")
    base_url = chosen.config.get("base_url")
    check_model(chosen.model, settings, policies, registry, base_url if isinstance(base_url, str) else None)
    return chosen


def _local_address(base_url: str) -> bool:
    """An address on this machine or the local network: an IP literal that is not public, or a name that is not
    on the internet (`localhost`, `ollama` in compose, `*.local`, `*.internal`)."""
    import ipaddress
    from urllib.parse import urlsplit

    host = (urlsplit(base_url if "://" in base_url else f"http://{base_url}").hostname or "").rstrip(".").lower()
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return bool(host) and ("." not in host or host.endswith((".local", ".internal", ".localhost", ".lan")))


def check_model(
    model: str, settings: CopilotSettings, policies: list[Policy], registry: Registry, base_url: str | None = None
) -> None:
    """A model the Copilot may use: local when the settings say so, able to call tools, allowed by policies."""
    if settings.privacy == "local_only":
        provider = registry.provider(model.partition(":")[0])
        if base_url and not _local_address(base_url):
            raise InvalidInput(
                f"the Copilot is set to local models only, but {model} is served from {base_url}, which is not on "
                "this machine or network; use a local address or turn off local-only"
            )
        if provider is None or not provider.local:
            raise InvalidInput(
                f"the Copilot is set to local models only, but {model} is not a local model; "
                "choose a local model (Ollama, LM Studio, vLLM, llama.cpp) in Copilot settings"
            )
    info = registry.model(model)
    if info is not None and not info.tools:
        raise InvalidInput(f"{model} cannot call tools, which the Copilot needs; choose another model")
    check_model_policies(model, policies, registry, base_url)


def check_model_policies(model: str, policies: list[Policy], registry: Registry, base_url: str | None = None) -> None:
    """The organisation's resource policies hold for the Copilot's model as for any AI model in a workflow."""
    relevant = [p for p in policies if p.scope == "resource"]
    if not relevant:
        return
    config: dict[str, Any] = {"model": model, **({"base_url": base_url} if base_url else {})}
    probe = Workflow(
        id="wf_copilot_policy",
        name="copilot",
        resources=[
            Resource(
                id="r_copilot",
                type="model.chat",
                name="copilot_model",
                config=config,
            )
        ],
    )
    broken = [d for d in check_policies(probe, relevant, registry) if d.severity == "error"]
    if broken:
        raise InvalidInput(f"your organisation's policies do not allow {model} for the Copilot: {broken[0].message}")
