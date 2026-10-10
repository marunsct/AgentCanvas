"""Safety block rules (S23): own patterns compile, a repair needs a model, a flag-only check saves its verdict."""

from __future__ import annotations

import re
from collections.abc import Iterator

from agentcanvas.validation.engine import Diagnostic, ValidationContext, error, rule, warning

GUARDS = ("guard.pii", "guard.injection", "guard.moderation", "guard.schema")


@rule
def guardrail_settings(ctx: ValidationContext) -> Iterator[Diagnostic]:
    wired_models = {e.target.node for e in ctx.wf.edges if e.kind == "wiring" and e.target.port == "model"}
    for n in ctx.wf.nodes:
        if n.type not in GUARDS:
            continue
        cfg = n.config
        patterns = cfg.get("custom_patterns") if n.type == "guard.pii" else None
        for i, p in enumerate(patterns if isinstance(patterns, list) else []):
            pattern = p.get("pattern") if isinstance(p, dict) else None
            if not isinstance(pattern, str):
                continue
            try:
                re.compile(pattern)
            except re.error as exc:
                yield error(
                    "E004",
                    f"'{n.name}': pattern {i + 1} is not a valid regular expression: {exc}",
                    node_id=n.id,
                    path=f"config.custom_patterns[{i}].pattern",
                )
        if n.type == "guard.schema" and cfg.get("repair") and n.id not in wired_models:
            yield error(
                "E004",
                f"'{n.name}': “Let the AI fix it once” needs an AI model plugged into its model input",
                node_id=n.id,
                path="config.repair",
            )
        default_fail = "redact" if n.type == "guard.pii" else "stop"
        if cfg.get("on_fail", default_fail) == "flag" and not (cfg.get("flag_output") or cfg.get("report_output")):
            yield warning(
                "W019",
                f"'{n.name}' only flags problems but saves its verdict nowhere, so nothing can act on it",
                node_id=n.id,
                path="config.flag_output",
                hint="pick a field in “Save yes/no in” and branch on it with Choose a path, or set “When it fails”"
                " to stop",
            )
