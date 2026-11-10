"""Budget-aware model routing (G-8): "use the cheapest ready model in this list that the policy allows".

A model block (`model.chat`) may list `cheapest_of` models. When a run starts, each such block gets the cheapest
candidate that

* is **ready**: its provider is installed and has a key here (a project secret or a server variable), or the block's
  own key or address is for that provider; a local model is never chosen (whether its server is up is only known when
  it is called: list it among the backup models instead); a retired model is not ready;
* is **allowed**: swapping it into the block breaks no organisation policy that the block's own model does not
  already break (policies that apply to every environment, as the editor reports them).

and is the cheapest by catalog price (input plus output price per million tokens). A model without a price is chosen
only when no priced candidate qualifies, since a USD budget cannot count its calls. The winner becomes the block's
`model` for this run (a winner from another provider uses that provider's own key and address, as backup models
do, never the block's); the block's own `model` stays the one used when no candidate qualifies, and when the block
has `fallbacks` the winner is not tried a second time among them. Nothing is stored: the draft keeps both settings,
and each run notes its choice (the `ac.route` event) so a run explains the model that answered.
"""

from __future__ import annotations

import copy
import os
from datetime import UTC, datetime
from typing import Any

from agentcanvas import cloudmodels
from agentcanvas.db.repositories import PolicyRepo
from agentcanvas.ir.models import Workflow
from agentcanvas.registry import ModelInfo, Registry
from agentcanvas.validation.policies import Policy, check_policies

KEY = "cheapest_of"


def candidates(config: dict[str, Any]) -> list[str]:
    found = config.get(KEY)
    return [str(m) for m in found if m] if isinstance(found, list) else []


def routed(wf: Workflow) -> bool:
    """Whether any model block of the workflow, or of a subgraph inside it, asks for routing."""
    return _routed([r.model_dump() for r in wf.resources], [n.config for n in wf.nodes if n.type == "core.subgraph"])


def _routed(resources: list[dict[str, Any]], subgraphs: list[dict[str, Any]]) -> bool:
    if any(r.get("type") == "model.chat" and candidates(r.get("config") or {}) for r in resources):
        return True
    for config in subgraphs:
        inner = config.get("workflow") or {}
        if _routed(
            inner.get("resources") or [],
            [n.get("config") or {} for n in inner.get("nodes") or [] if n.get("type") == "core.subgraph"],
        ):
            return True
    return False


def price(info: ModelInfo | None) -> float | None:
    """What a million tokens in and a million out cost together; None when the catalog has no price."""
    if info is None or info.price_in_per_mtok is None or info.price_out_per_mtok is None:
        return None
    return info.price_in_per_mtok + info.price_out_per_mtok


def unready(
    model: str, config: dict[str, Any], registry: Registry, secrets: dict[str, str], demo: bool, hosted: bool = False
) -> str | None:
    """Why `model` cannot answer for this block right now; None when it can. A model in your own cloud account needs
    credentials (in the hosted edition, the project's own) and a region: the block's, when it is of the same cloud."""
    provider = registry.provider(model.partition(":")[0])
    if provider is None:
        return "its provider is not known here"
    if not provider.installed:
        return f"{provider.label} is not installed on this server"
    info = registry.model(model)
    if info is not None and info.retires is not None and info.retires <= datetime.now(UTC).date():
        return "it has been retired"
    if demo:
        return None
    if provider.local:
        return "local models are not chosen by price (their server may be down); list it among the backup models"
    own = cloudmodels.cloud_of(str(config.get("model", "")))
    if own and config.get("region") and cloudmodels.cloud_of(model) != own:  # where the block's prompts may go
        return f"the block keeps its prompts in its {cloudmodels.LABEL[own]} region, which this model is not in"
    if cloud := cloudmodels.cloud_of(model):
        if cloud == own == "aws" and cloudmodels.wider_reach(model, str(config["model"])):
            return "it may serve requests from more places than the block's own model"
        settings = {"region": config.get("region")} if own == cloud else {}
        return cloudmodels.region_problem(model, settings) or cloudmodels.credentials_problem(
            model, secrets, hosted=hosted
        )
    own_key = config.get("api_key") or config.get("base_url")  # the block's own key or address, for its own provider
    if (
        (own_key and str(config.get("model", "")).partition(":")[0] == provider.id)
        or provider.key_env is None
        or provider.key_env in secrets
        or os.environ.get(provider.key_env)
    ):
        return None
    return f"no {provider.key_env} here"


OWN_KEYS = ("api_key",)  # a block's key: for its own provider only
ADDRESS_HINTS = ("url", "endpoint", "api_base", "host", "client", "proxy")  # as the adapters': where to connect


def switch(config: dict[str, Any], model: str) -> None:
    """Make `model` the block's model. A model of another provider gets that provider's own key and address, as
    backup models do: the block's key and address (its own provider's, or a proxy's) are dropped."""
    own = str(config.get("model", ""))
    if model.partition(":")[0] != own.partition(":")[0]:
        for k in [k for k in config if k in OWN_KEYS or any(h in k.lower() for h in ADDRESS_HINTS)]:
            del config[k]
    if cloudmodels.cloud_of(model) != cloudmodels.cloud_of(own):  # a region and project are one cloud's
        for k in cloudmodels.BLOCK_ONLY:
            config.pop(k, None)
    config["model"] = model


def _workflow(data: dict[str, Any]) -> Workflow:
    return Workflow.model_validate({"id": "wf_routing", "name": "routing", **data})  # a subgraph's may lack both


def _swapped(data: dict[str, Any], rid: str, model: str) -> Workflow:
    probe = copy.deepcopy(data)
    for r in probe["resources"]:
        if r["id"] == rid:
            switch(r["config"], model)
    return _workflow(probe)


def _errors(wf: Workflow, policies: list[Policy], registry: Registry) -> set[tuple[str | None, str]]:
    return {(d.node_id, d.message) for d in check_policies(wf, policies, registry) if d.severity == "error"}


def route(
    wf: Workflow,
    registry: Registry,
    secrets: dict[str, str],
    policies: list[Policy],
    *,
    demo: bool = False,
    hosted: bool = False,
) -> tuple[Workflow, list[dict[str, Any]]]:
    """The workflow with each routed block's model chosen, and one note per block: its name, the model chosen, and
    every candidate with the reason it was not (ready, allowed, or dearer than the one chosen)."""
    if not routed(wf):
        return wf, []
    data = wf.model_dump(mode="json")
    notes: list[dict[str, Any]] = []
    _route(data, "", registry, secrets, policies, demo, notes, hosted)
    return Workflow.model_validate(data), notes


def _route(
    data: dict[str, Any],
    path: str,
    registry: Registry,
    secrets: dict[str, str],
    policies: list[Policy],
    demo: bool,
    notes: list[dict[str, Any]],
    hosted: bool = False,
) -> None:
    for r in data.get("resources") or []:
        config = r.get("config") or {}
        if r.get("type") != "model.chat" or not candidates(config):
            continue
        # policies see the whole workflow (a rule about a step reads the models wired to it); a block inside a
        # subgraph is judged on its own workflow, as the editor checks it
        here = _workflow(data)
        baseline = _errors(here, policies, registry)
        considered: list[dict[str, Any]] = []
        viable: list[tuple[bool, float, int, str]] = []
        for i, model in enumerate(dict.fromkeys(candidates(config))):
            why = unready(model, config, registry, secrets, demo, hosted)
            if why is None and policies and _errors(_swapped(data, r["id"], model), policies, registry) - baseline:
                why = "an organisation policy does not allow it"
            if why is not None:
                considered.append({"model": model, "usable": False, "reason": why})
                continue
            cost = price(registry.model(model))
            considered.append({"model": model, "usable": True, "price_per_mtok": cost})
            viable.append((cost is None, cost or 0.0, i, model))
        own = str(config.get("model", ""))
        chosen = min(viable)[3] if viable else own
        for c in considered:
            if c["usable"] and c["model"] != chosen:
                c["reason"] = "dearer than " + chosen if c["price_per_mtok"] is not None else "has no price"
        switch(config, chosen)
        if config.get("fallbacks"):
            config["fallbacks"] = [b for b in config["fallbacks"] if b != chosen]
        notes.append({
            "resource": path + str(r.get("name")), "chosen": chosen, "default": own,
            "reason": "cheapest of the usable candidates" if viable else "no candidate usable; the block's own model",
            "considered": considered,
        })  # fmt: skip
    for n in data.get("nodes") or []:
        inner = (n.get("config") or {}).get("workflow")
        if n.get("type") == "core.subgraph" and isinstance(inner, dict):
            _route(inner, f"{path}{n.get('name')}/", registry, secrets, policies, demo, notes, hosted)


async def route_run(app: Any, session: Any, wf: Workflow, project_id: str) -> tuple[Workflow, list[dict[str, Any]]]:
    """`route` for a run starting in `project_id` on the running app: the project's keys, registry and policies."""
    if not routed(wf):
        return wf, []
    rows = await PolicyRepo(session).of_project(project_id)
    return route(
        wf, app.state.projects.registry(project_id), app.state.projects.secrets.get(project_id, {}),
        [Policy.model_validate(row, from_attributes=True) for row in rows],
        demo=bool(getattr(app.state, "demo_models", False)), hosted=app.state.settings.edition == "hosted",
    )  # fmt: skip
