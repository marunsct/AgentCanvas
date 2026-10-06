"""Secrets never leave in traces (S12, S11 carry-over): LangSmith runs and OpenTelemetry spans are masked.

Every secret value the server knows (6+ characters, all projects) is replaced with `***` in LangSmith run inputs,
outputs and metadata, and in the attributes and event attributes of exported OTel spans. Fields named in a
workflow's `settings.trace_redact` are redacted per step by the adapter (`node_options(redact=...)`).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from opentelemetry.sdk.trace.export import SpanExporter

MIN_LENGTH = 6
MASK = "***"

# returns the secret values to hide; the API sets it once projects and their secrets are loaded
provider: Callable[[], Iterable[str]] = lambda: ()  # noqa: E731


def _values() -> list[str]:
    return sorted({v for v in provider() if isinstance(v, str) and len(v) >= MIN_LENGTH}, key=len, reverse=True)


def mask_text(text: str, values: Sequence[str] | None = None) -> str:
    for v in _values() if values is None else values:
        text = text.replace(v, MASK)
    return text


def mask(payload: Any) -> Any:
    """`payload` (anything JSON-like) with every known secret value masked; unchanged when none occurs."""
    values = _values()
    if not values:
        return payload
    if isinstance(payload, str):
        return mask_text(payload, values)
    text = json.dumps(payload, default=str)
    if not any(v in text for v in values):
        return payload
    return json.loads(mask_text(text, values))


def install_langsmith() -> bool:
    """Make LangChain's tracer use a LangSmith client that masks secrets; True when LangSmith tracing is on."""
    from agentcanvas.runtime.tracing import tracing_enabled

    if not tracing_enabled():
        return False
    import langsmith

    langsmith.configure(client=langsmith.Client(hide_inputs=mask, hide_outputs=mask, hide_metadata=mask))
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

        events = [Event(e.name, self._attrs(e.attributes, values), e.timestamp) for e in span.events]
        return ReadableSpan(
            name=mask_text(span.name, values), context=span.context, parent=span.parent, resource=span.resource,
            attributes=self._attrs(span.attributes, values), events=events, links=span.links, kind=span.kind,
            status=span.status, start_time=span.start_time, end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )  # fmt: skip

    def shutdown(self) -> None:
        self.inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self.inner.force_flush(timeout_millis)
