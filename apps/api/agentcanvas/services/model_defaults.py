"""Which model a project uses for what (Settings > Models): one rule, written down, used everywhere.

A block or evaluator that names its model uses it. Otherwise the project's default for that purpose, then the
project's model (first-run setup), else nothing, with a sentence saying what to choose. Purposes:

- `chat`: model blocks and the Playground (the project's model);
- `judge`: LLM-as-judge and pairwise evaluators;
- `simulated_user`: the user a multi-turn experiment simulates;
- `embedding`: knowledge bases (the server's built-in offline embedding when nothing is chosen).

A default is applied when something is saved without a model (an evaluator, an experiment, a knowledge base), so
results say which model produced them and changing the default later does not change them. The Copilot has its own
setting (`copilot/settings.py`), which follows the same order with the workflow's own models in between.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Purpose = Literal["chat", "judge", "simulated_user", "embedding"]
PURPOSES: dict[str, str] = {
    "chat": "model blocks and the Playground",
    "judge": "judge evaluators",
    "simulated_user": "simulated users in experiments",
    "embedding": "knowledge bases",
}
KEY = "model_defaults"  # in the project's settings: {purpose: "provider:model"}
BUILTIN_EMBEDDING = "builtin"


class Resolved(BaseModel):
    purpose: str
    model: str | None = Field(description="provider:model, or null when nothing is chosen")
    origin: str = Field(description="where the choice comes from, in the person's words")
    problem: str | None = Field(None, description="what to choose when there is no model")


def stored(project_settings: dict[str, Any] | None) -> dict[str, str]:
    raw = (project_settings or {}).get(KEY) or {}
    return {k: v for k, v in raw.items() if k in PURPOSES and isinstance(v, str) and v}


def resolve(project_settings: dict[str, Any] | None, purpose: str) -> Resolved:
    own = stored(project_settings).get(purpose)
    if own and purpose != "chat":
        return Resolved(
            purpose=purpose, model=own, origin=f"the project's default for {PURPOSES[purpose]}", problem=None
        )
    if purpose == "embedding":
        return Resolved(
            purpose=purpose, model=BUILTIN_EMBEDDING, origin="the server's built-in embedding (offline)", problem=None
        )
    project_model = ((project_settings or {}).get("default_model") or {}).get("model")
    if project_model:
        return Resolved(
            purpose=purpose, model=project_model, origin="the project's model (Settings > Models)", problem=None
        )
    what = "a model for the project" if purpose == "chat" else f"a model for {PURPOSES[purpose]}"
    return Resolved(purpose=purpose, model=None, origin="none", problem=f"choose {what} in Settings > Models")


def require(project_settings: dict[str, Any] | None, purpose: str) -> str:
    """The model for `purpose`, or InvalidInput saying what to choose."""
    from agentcanvas.errors import InvalidInput

    r = resolve(project_settings, purpose)
    if r.model is None:
        raise InvalidInput(f"no model was given and the project has none for {PURPOSES[purpose]}: {r.problem}")
    return r.model
