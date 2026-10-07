"""Secrets never leave in traces (S12, S11 carry-over): LangSmith runs and OpenTelemetry spans are masked.

Every secret value the server knows (6+ characters, all projects) is replaced with `***` in LangSmith run inputs,
outputs, metadata and errors, and in the name, attributes, event attributes and status of exported OTel spans.

Fields named in a workflow's `settings.trace_redact` are redacted in every LangSmith run of that workflow's runs: each
step's trace policy hides them (`node_options(redact=...)`), and the client's anonymizer replaces them at any depth
in the graph's root run and in model runs. Their values are remembered while the run lasts and masked wherever they
reappear as text, such as inside a model's prompt (S13). OTel spans carry no run payloads.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from opentelemetry.sdk.trace.export import SpanExporter

MIN_LENGTH = 6
MASK = "***"
REDACTED = "[redacted]"
MIN_REDACTED_TEXT = 4  # shorter redacted values are hidden under their key only, not wherever the text reappears


@dataclass
class Redaction:
    """The `trace_redact` fields of the run being traced, and the values seen under them so far."""

    keys: frozenset[str]
    values: set[str] = field(default_factory=set)


_redaction: ContextVar[Redaction | None] = ContextVar("trace_redaction", default=None)


@contextmanager
def redacting(keys: Iterable[str]) -> Iterator[None]:
    """Redact `keys` in everything traced in this context (a run's execution and the tasks it starts)."""
    token = _redaction.set(Redaction(frozenset(keys)) if keys else None)
    try:
        yield
    finally:
        try:
            _redaction.reset(token)
        except ValueError:  # reset from another context (a cancelled run's generator): nothing to restore
            pass


# returns the secret values to hide; the API sets it once projects and their secrets are loaded
provider: Callable[[], Iterable[str]] = lambda: ()  # noqa: E731


def _values() -> list[str]:
    return sorted({v for v in provider() if isinstance(v, str) and len(v) >= MIN_LENGTH}, key=len, reverse=True)


def mask_text(text: str, values: Sequence[str] | None = None, mark: str = MASK) -> str:
    for v in _values() if values is None else values:
        text = text.replace(v, mark)
    return text


def _walk(value: Any, values: Sequence[str], mark: str = MASK) -> Any:
    if isinstance(value, str):
        return mask_text(value, values, mark)
    if isinstance(value, dict):
        return {_walk(k, values, mark) if isinstance(k, str) else k: _walk(v, values, mark) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(_walk(v, values, mark) for v in value)
    return value


def mask_strings(value: Any, values: Sequence[str], mark: str = MASK) -> Any:
    """`value` with each of `values` masked inside every string (dict keys included), at any depth."""
    return _walk(value, values, mark)


def _texts(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _texts(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _texts(v)
    elif isinstance(value, int | float) and not isinstance(value, bool):
        yield str(value)


def _redact(value: Any, r: Redaction) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in r.keys:
                r.values.update(t for t in _texts(v) if len(t) >= MIN_REDACTED_TEXT)
                out[k] = REDACTED
            else:
                out[k] = _redact(v, r)
        return out
    if isinstance(value, list | tuple):
        return type(value)(_redact(v, r) for v in value)
    return value


def mask(payload: Any) -> Any:
    """`payload` (anything JSON-like) with every known secret value masked in its strings and keys, and, while a run
    with `trace_redact` fields is traced, those fields and their values redacted."""
    values = _values()
    r = _redaction.get()
    if r is not None:
        payload = _redact(payload, r)
        if r.values:  # where they reappear as text, e.g. inside a model's prompt
            payload = _walk(payload, sorted(r.values, key=len, reverse=True), REDACTED)
    return _walk(payload, values) if values else payload


def install_langsmith() -> bool:
    """Make LangChain's tracer use a LangSmith client that masks secrets; True when LangSmith tracing is on."""
    from agentcanvas.runtime.tracing import tracing_enabled

    if not tracing_enabled():
        return False
    import langsmith

    # the anonymizer covers inputs, outputs, metadata and the run's error text (hide_* would miss the error)
    langsmith.configure(client=langsmith.Client(anonymizer=mask))
    return True


class MaskingExporter(SpanExporter):
    """Wraps an OTel span exporter: spans leave with secret values masked in their attributes and events."""

    def __init__(self, inner: SpanExporter):
        self.inner = inner

    def export(self, spans: Sequence[Any]) -> Any:
        values = _values()
        return self.inner.export([self._mask(s, values) for s in spans] if values else spans)

    @staticmethod
    def _attrs(attrs: Any, values: list[str]) -> dict[str, Any] | None:
        if attrs is None:
            return None

        def one(v: Any) -> Any:
            if isinstance(v, str):
                return mask_text(v, values)
            if isinstance(v, tuple | list):
                return tuple(mask_text(x, values) if isinstance(x, str) else x for x in v)
            return v

        return {k: one(v) for k, v in attrs.items()}

    def _mask(self, span: Any, values: list[str]) -> Any:
        from opentelemetry.sdk.trace import Event, ReadableSpan
        from opentelemetry.trace import Status

        events = [Event(e.name, self._attrs(e.attributes, values), e.timestamp) for e in span.events]
        return ReadableSpan(
            name=mask_text(span.name, values), context=span.context, parent=span.parent, resource=span.resource,
            attributes=self._attrs(span.attributes, values), events=events, links=span.links, kind=span.kind,
            status=Status(span.status.status_code, mask_text(span.status.description, values))
            if span.status.description else span.status, start_time=span.start_time, end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )  # fmt: skip

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)
