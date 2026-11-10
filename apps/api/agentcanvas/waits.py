"""When a Wait step (`core.wait`) is due, from its settings: plain functions the validator, the LangGraph step and
an exported service share (exports carry this file).

A duration is a number of seconds, an ISO 8601 duration without years or months (`PT2H30M`, `P3D`, `P1W`), or
amounts with units (`3d`, `2h 30m`, `90 minutes`, `1 week`). A time is an ISO 8601 date and time
(`2026-12-01T09:00`, with `Z` or an offset), or a date (its midnight); one without an offset is in the step's time
zone, UTC by default. A wait may last up to `MAX_WAIT`.

A step resumes with `woken(...)`: {"timed_out", "event", "woke_at"} (the event as {"id", "type", "data"} when one
woke it, `timed_out` when an event wait's timeout ran out first; `skipped` when a test or dry run did not wait).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_WAIT = timedelta(days=366)
MODES = ("for", "until", "event")
# events the platform raises about runs themselves: a Wait cannot wait for them (they carry no key, and a run's own
# lifecycle waking it would loop)
LIFECYCLE = ("run.", "interrupt.")
EVENT_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")  # as the events API takes them

_UNITS = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
}  # fmt: skip
_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*([a-z]+)")
_ISO = re.compile(
    r"P(?:(\d+(?:\.\d+)?)W)?(?:(\d+(?:\.\d+)?)D)?(?:T(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?)?"
)
EXAMPLES = "e.g. 3d, 2h 30m, 90 minutes or PT45M"


class WaitError(ValueError):
    """A Wait step's time or duration cannot be used; the message says why, in plain words."""


def is_template(value: Any) -> bool:
    """Set by a template ({{ … }}): known only when the run gets to the step."""
    return isinstance(value, str) and ("{{" in value or "{%" in value)


def parse_duration(value: Any) -> timedelta:
    """A duration (see the module docstring); WaitError when it is not one, or is negative."""
    if isinstance(value, bool):
        raise WaitError(f"'{value}' is not a duration ({EXAMPLES})")
    if isinstance(value, int | float):
        seconds = float(value)
    else:
        text = str(value or "").strip().lower()
        if not text:
            raise WaitError(f"no duration given ({EXAMPLES})")
        seconds = _seconds(text)
    if seconds < 0:
        raise WaitError(f"a duration cannot be negative ('{value}')")
    try:
        return timedelta(seconds=seconds)
    except OverflowError as exc:
        raise WaitError(f"'{value}' is longer than a Wait step may wait") from exc


def _seconds(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        pass
    if text.startswith("p"):
        m = _ISO.fullmatch(text.upper())
        if m is None or not any(m.groups()) or text.upper().endswith("T"):
            raise WaitError(f"'{text}' is not an ISO 8601 duration without years or months, e.g. PT2H or P3D")
        weeks, days, hours, minutes, secs = (float(g or 0) for g in m.groups())
        return weeks * 604800 + days * 86400 + hours * 3600 + minutes * 60 + secs
    compact = re.sub(r"[\s,]+|\band\b", " ", text).strip()
    parts = _AMOUNT.findall(compact)
    if not parts or _AMOUNT.sub("", compact).strip():
        raise WaitError(f"'{text}' is not a duration ({EXAMPLES})")
    total = 0.0
    for amount, unit in parts:
        if unit not in _UNITS:
            raise WaitError(f"'{unit}' in '{text}' is not a unit of time (use s, m, h, d or w)")
        total += float(amount) * _UNITS[unit]
    return total


def zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise WaitError(f"'{name}' is not a time zone (use a name like Europe/Berlin)") from exc


def parse_time(value: Any, timezone: str | None = None) -> datetime:
    """A point in time (see the module docstring), in UTC; WaitError when it is not one."""
    if isinstance(value, datetime):
        at = value
    else:
        text = str(value or "").strip()
        if not text:
            raise WaitError("no time given (e.g. 2026-12-01T09:00 or 2026-12-01T09:00:00Z)")
        try:
            at = datetime.fromisoformat(text.replace(" ", "T", 1) if len(text) > 10 else text)
        except ValueError:
            try:
                d = date.fromisoformat(text)
            except ValueError:
                raise WaitError(f"'{text}' is not a date and time (e.g. 2026-12-01T09:00 or 2026-12-01)") from None
            at = datetime(d.year, d.month, d.day)
    if at.tzinfo is None:
        at = at.replace(tzinfo=zone(timezone))
    return at.astimezone(UTC)


def due_at(
    mode: str,
    *,
    now: datetime,
    duration: Any = None,
    until: Any = None,
    timeout: Any = None,
    timezone: str | None = None,
) -> datetime:
    """When a step with these (rendered) settings is due: an event wait's timeout runs out then."""
    if mode == "until":
        at = parse_time(until, timezone)
    elif mode == "event":
        at = now + parse_duration(timeout)
    elif mode == "for":
        at = now + parse_duration(duration)
    else:
        raise WaitError(f"'{mode}' is not a way to wait (use for, until or event)")
    if at - now > MAX_WAIT:
        raise WaitError(f"a Wait step may wait up to {MAX_WAIT.days} days, not until {at.isoformat()}")
    return at


def check_event(name: Any) -> str:
    """The event type an event wait waits for; WaitError when it cannot be one."""
    text = str(name or "").strip()
    if not EVENT_NAME.fullmatch(text):
        raise WaitError(f"'{text}' is not an event type: lowercase words with dots, e.g. invoice.paid")
    if text.startswith(LIFECYCLE):
        raise WaitError(f"'{text}' is an event about runs themselves: a Wait step cannot wait for it")
    return text


def woken(
    *, timed_out: bool = False, event: dict[str, Any] | None = None, at: datetime | None = None, skipped: bool = False
) -> dict[str, Any]:
    """The value a Wait step continues with (and saves in its output)."""
    out: dict[str, Any] = {
        "timed_out": timed_out,
        "event": event,
        "woke_at": (at or datetime.now(UTC)).astimezone(UTC).isoformat(),
    }
    if skipped:
        out["skipped"] = True
    return out
