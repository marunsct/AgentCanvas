"""Wait step rules (F-10): what a `core.wait` block needs for the way it waits.

* E097 error — the duration, time, time zone, event or time limit is missing or cannot be used (a template is
  checked when the run gets there); a wait longer than `waits.MAX_WAIT`.
* W096 warning — an event wait with no key: one event ends every run waiting for that event type.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from jinja2 import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment

from agentcanvas import waits
from agentcanvas.validation.engine import Diagnostic, ValidationContext, error, rule, warning

_jinja = SandboxedEnvironment()
NEEDS = {"for": ("duration", "how long to wait"), "until": ("until", "the time to wait until"),
         "event": ("event", "the event to wait for")}  # fmt: skip


def _problem(key: str, value: object, mode: str, timezone: str | None) -> str | None:
    """Why a setting cannot be used, or None (a template: known only when the run gets there)."""
    if waits.is_template(value):
        try:
            _jinja.parse(str(value))
        except TemplateSyntaxError as exc:
            return f"template error: {exc.message}"
        return None
    now = datetime.now(UTC)
    try:
        if key == "until":
            waits.due_at("until", now=now, until=value, timezone=timezone)
        elif key == "event":
            waits.check_event(value)
        else:
            waits.due_at("event" if key == "timeout" else mode, now=now, duration=value, timeout=value)
    except waits.WaitError as exc:
        return str(exc)
    return None


@rule
def wait_settings(ctx: ValidationContext) -> Iterator[Diagnostic]:
    for n in ctx.wf.nodes:
        if n.type != "core.wait" or n.disabled:
            continue
        cfg = n.config
        mode = cfg.get("mode") or "for"
        if mode not in waits.MODES:
            continue  # the schema check (E004) says so
        timezone = cfg.get("timezone") or None
        if timezone:
            try:
                waits.zone(timezone)
            except waits.WaitError as exc:
                yield error("E097", f"'{n.name}': {exc}", node_id=n.id, path="config.timezone")
                timezone = None
        key, what = NEEDS[mode]
        needed = [key, "timeout"] if mode == "event" else [key]
        for k in needed:
            value = cfg.get(k)
            if value in (None, ""):
                missing = what if k == key else "a time limit (how long to wait for the event at most)"
                yield error("E097", f"'{n.name}': set {missing}", node_id=n.id, path=f"config.{k}")
            elif (why := _problem(k, value, mode, timezone)) is not None:
                yield error("E097", f"'{n.name}': {why}", node_id=n.id, path=f"config.{k}")
        if mode == "event" and not cfg.get("event_key"):
            yield warning(
                "W096",
                f"'{n.name}': no key, so one '{cfg.get('event') or 'such'}' event ends every run waiting for it",
                node_id=n.id,
                path="config.event_key",
                hint="set 'Only the event with this key', e.g. {{ state.order_id }}, and send it as \"key\" in the "
                "event's data",
            )
