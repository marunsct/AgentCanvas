"""Trace export (S15): each finished run, as OpenTelemetry spans, to the tracing tool a team already uses.

A project's admin picks a destination (Langfuse, Arize Phoenix, Datadog through its Agent, or any OTLP/HTTP
endpoint); after every run of that project's workflows the run is sent as one trace:

    workflow run (AGENT)  ─┬─ step (CHAIN) ─┬─ model call (LLM): model, tokens, reply
                           │                └─ tool call (TOOL): arguments, result
                           └─ step ...

Spans carry the OpenTelemetry GenAI conventions (`gen_ai.*`) and the OpenInference ones (`openinference.*`,
`input.value`, `llm.token_count.*`), so Langfuse, Phoenix and Datadog LLM Observability all show models, tokens
and inputs. Secrets are already masked in run events; `include_content: false` sends timings, models and tokens
only. Export runs in the background (batched) and never slows or fails a run.

Run events have one timestamp each (when they happened), so a step span is exact and model and tool calls are
points inside their step.
"""

from __future__ import annotations

import base64
import json
import threading
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, field_validator

from agentcanvas.log import get_logger

if TYPE_CHECKING:
    from opentelemetry.sdk.trace import TracerProvider

    from agentcanvas.runtime.runner import RunHandle

log = get_logger(__name__)

Kind = Literal["langfuse", "phoenix", "datadog", "otlp"]
SecretRef = dict[str, str]  # {"$secret": NAME}
MAX_VALUE = 8_000  # characters per input/output attribute

DEFAULTS: dict[str, str] = {
    "langfuse": "https://cloud.langfuse.com",
    "phoenix": "http://localhost:6006",
    "datadog": "http://localhost:4318",
}
GUIDE: dict[str, str] = {
    "langfuse": "Langfuse project → Settings → API keys: a public key (pk-lf-…) and a secret key (sk-lf-…). "
    "EU cloud: https://cloud.langfuse.com, US: https://us.cloud.langfuse.com, or your own Langfuse.",
    "phoenix": "Your Phoenix address (port 6006 by default). Phoenix Cloud or a Phoenix with auth also needs an "
    "API key.",
    "datadog": "The Datadog Agent's OTLP/HTTP receiver (port 4318; enable otlp_config in datadog.yaml or "
    "DD_OTLP_CONFIG_RECEIVER_PROTOCOLS_HTTP_ENDPOINT). The Agent holds your Datadog API key.",
    "otlp": "Any OpenTelemetry collector or backend that accepts OTLP over HTTP (protobuf): the full traces URL.",
}


class TraceExport(BaseModel):
    """A project's trace destination (stored in the project's settings under `trace_export`)."""

    enabled: bool = True
    kind: Kind
    endpoint: str = Field("", max_length=500, description="base URL (the full traces URL for otlp); empty = default")
    public_key: SecretRef | None = Field(None, description="Langfuse public key, as a secret")
    secret_key: SecretRef | None = Field(None, description="Langfuse secret key, as a secret")
    api_key: SecretRef | None = Field(None, description="Phoenix API key, as a secret (optional)")
    headers: dict[str, str | SecretRef] = Field(default_factory=dict, description="otlp: extra headers")
    include_content: bool = Field(True, description="send inputs, outputs and model replies (masked)")

    @field_validator("endpoint")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if v and not v.startswith(("https://", "http://")):
            raise ValueError("the endpoint must start with https:// or http://")
        return v

    def secrets(self) -> set[str]:
        refs = [
            self.public_key,
            self.secret_key,
            self.api_key,
            *(h for h in self.headers.values() if isinstance(h, dict)),
        ]
        return {r["$secret"] for r in refs if isinstance(r, dict) and r.get("$secret")}

    def target(self, secrets: dict[str, str]) -> tuple[str, dict[str, str]]:
        """The OTLP traces URL and headers, with secret values filled in (KeyError names a missing secret)."""

        def value(ref: SecretRef | str | None) -> str:
            if isinstance(ref, dict):
                return secrets[ref["$secret"]]
            return ref or ""

        base = self.endpoint or DEFAULTS.get(self.kind, "")
        if self.kind == "langfuse":
            if not (self.public_key and self.secret_key):
                raise KeyError("the Langfuse public and secret keys")
            token = base64.b64encode(f"{value(self.public_key)}:{value(self.secret_key)}".encode()).decode()
            return f"{base}/api/public/otel/v1/traces", {"Authorization": f"Basic {token}"}
        if self.kind == "phoenix":
            return f"{base}/v1/traces", ({"Authorization": f"Bearer {value(self.api_key)}"} if self.api_key else {})
        if self.kind == "datadog":
            return f"{base}/v1/traces", {}
        if not base:
            raise KeyError("the OTLP endpoint")
        return base, {k: value(v) for k, v in self.headers.items()}


def load(project_settings: dict[str, Any] | None) -> TraceExport | None:
    raw = (project_settings or {}).get("trace_export")
    try:
        return TraceExport.model_validate(raw) if raw else None
    except ValueError:
        return None


def _clip(value: Any, redaction: Any = None) -> str:
    if redaction is not None:  # the workflow's trace_redact fields, and their values wherever they reappear
        from agentcanvas.runtime import masking

        value = masking._redact(value, redaction)
        if redaction.values:
            value = masking.mask_strings(value, sorted(redaction.values, key=len, reverse=True), masking.REDACTED)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= MAX_VALUE else text[:MAX_VALUE] + "…"


def spans(h: RunHandle, status: str, error: str | None, content: bool) -> list[dict[str, Any]]:
    """The run as span records (name, parent index, start/end ns, attributes, error), root first."""
    from agentcanvas.runtime.masking import Redaction

    r = Redaction(frozenset(h.trace_redact)) if h.trace_redact else None
    times = h.event_times + [h.event_times[-1]] * (len(h.events) - len(h.event_times)) if h.event_times else []
    if not times:
        return []
    root: dict[str, Any] = {
        "name": f"invoke_agent {h.workflow_name or h.workflow_id}",
        "parent": None,
        "start": times[0],
        "end": times[-1],
        "attrs": {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": h.workflow_name or h.workflow_id,
            "gen_ai.agent.id": h.workflow_id,
            "gen_ai.conversation.id": h.thread_id,
            "session.id": h.thread_id,
            "openinference.span.kind": "AGENT",
            "agentcanvas.run_id": h.run_id,
            "agentcanvas.thread_id": h.thread_id,
            "agentcanvas.workflow_id": h.workflow_id,
            "agentcanvas.status": status,
            "agentcanvas.origin": h.origin,
            "gen_ai.usage.input_tokens": int(h.usage.get("input_tokens", 0)),
            "gen_ai.usage.output_tokens": int(h.usage.get("output_tokens", 0)),
            "llm.token_count.prompt": int(h.usage.get("input_tokens", 0)),
            "llm.token_count.completion": int(h.usage.get("output_tokens", 0)),
        },
        "error": error if status == "error" else None,
    }
    if h.deployment_id:
        root["attrs"]["agentcanvas.deployment_id"] = h.deployment_id
    if h.usage.get("cost_usd"):
        root["attrs"]["agentcanvas.cost_usd"] = float(h.usage["cost_usd"])
    for k, v in h.labels.items():
        root["attrs"][f"agentcanvas.label.{k}"] = v
    if content and h.input is not None:
        root["attrs"]["input.value"] = _clip(h.input, r)
    out: list[dict[str, Any]] = [root]
    open_steps: dict[str, int] = {}  # task id -> span index
    step_of_node: dict[str, int] = {}  # canvas node id -> its open step span index
    for ev, t in zip(h.events, times, strict=False):
        kind = ev.get("type")
        if kind == "STEP_STARTED":
            attrs = {"openinference.span.kind": "CHAIN", "agentcanvas.node_id": ev.get("canvas_node_id") or ""}
            step = {"name": str(ev.get("stepName")), "parent": 0, "start": t, "end": t, "error": None, "attrs": attrs}
            out.append(step)
            open_steps[str(ev.get("task_id"))] = len(out) - 1
            if ev.get("canvas_node_id"):
                step_of_node[ev["canvas_node_id"]] = len(out) - 1
        elif kind == "STEP_FINISHED" and (i := open_steps.pop(str(ev.get("task_id")), None)) is not None:
            out[i]["end"] = t
            out[i]["attrs"]["agentcanvas.step_status"] = ev.get("status") or "ok"
            if ev.get("status") == "error":
                out[i]["error"] = ev.get("error") or "step failed"
        elif kind == "CUSTOM" and ev.get("name") in ("ac.usage", "ac.model_reply", "ac.tool_io"):
            v = ev.get("value") or {}
            parent = step_of_node.get(v.get("canvas_node_id") or "", 0)
            if ev["name"] == "ac.usage":
                model = str(v.get("model") or "model")
                provider, _, name = model.partition(":")
                attrs = {
                    "gen_ai.operation.name": "chat", "gen_ai.request.model": name or model,
                    "gen_ai.provider.name": provider if name else "", "gen_ai.system": provider if name else "",
                    "gen_ai.usage.input_tokens": int(v.get("input_tokens") or 0),
                    "gen_ai.usage.output_tokens": int(v.get("output_tokens") or 0),
                    "openinference.span.kind": "LLM", "llm.model_name": name or model,
                    "llm.token_count.prompt": int(v.get("input_tokens") or 0),
                    "llm.token_count.completion": int(v.get("output_tokens") or 0),
                }  # fmt: skip
                if v.get("cost_usd") is not None:
                    attrs["agentcanvas.cost_usd"] = float(v["cost_usd"])
                out.append({"name": f"chat {name or model}", "parent": parent, "start": t, "end": t,
                            "attrs": attrs, "error": None})  # fmt: skip
            elif ev["name"] == "ac.model_reply":
                llm = next(
                    (
                        s
                        for s in reversed(out)
                        if s["parent"] == parent and s["attrs"].get("llm.model_name") and not s.get("replied")
                    ),
                    None,
                )
                if llm is None:  # a model that reports no token usage: the call still gets its span
                    name = str(v.get("resource") or "model")
                    llm = {"name": f"chat {name}", "parent": parent, "start": t, "end": t, "error": None,
                           "attrs": {"gen_ai.operation.name": "chat", "openinference.span.kind": "LLM",
                                     "llm.model_name": name, "agentcanvas.model_resource": name}}  # fmt: skip
                    out.append(llm)
                llm["replied"] = True
                if content:
                    llm["attrs"]["output.value"] = _clip(v.get("reply"), r)
            elif ev["name"] == "ac.tool_io":
                tool = str(v.get("name") or "tool")
                attrs = {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": tool,
                         "openinference.span.kind": "TOOL", "tool.name": tool}  # fmt: skip
                if content:
                    attrs["input.value"] = _clip(v.get("args"), r)
                    attrs["output.value"] = _clip(v.get("output"), r)
                out.append({"name": f"execute_tool {tool}", "parent": parent, "start": t, "end": t, "attrs": attrs,
                            "error": None})  # fmt: skip
        elif kind == "RUN_FINISHED" and content and ev.get("output") is not None:
            root["attrs"]["output.value"] = _clip(ev.get("output"), r)
    for i in open_steps.values():  # interrupted or failed mid-step
        out[i]["end"] = times[-1]
    return out


class Exporter:
    """One OpenTelemetry pipeline per destination (batched, background thread), reused across runs."""

    def __init__(self) -> None:
        self._providers: dict[str, TracerProvider] = {}
        self._lock = threading.Lock()

    def _provider(self, url: str, headers: dict[str, str]) -> TracerProvider:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        from agentcanvas.runtime.masking import MaskingExporter

        key = json.dumps([url, sorted(headers.items())])
        with self._lock:
            if key not in self._providers:
                provider = TracerProvider(resource=Resource.create({"service.name": "agentcanvas"}))
                exporter = MaskingExporter(OTLPSpanExporter(endpoint=url, headers=headers))
                provider.add_span_processor(BatchSpanProcessor(exporter))
                self._providers[key] = provider
            return self._providers[key]

    def send(self, url: str, headers: dict[str, str], records: list[dict[str, Any]]) -> None:
        emit(self._provider(url, headers), records)

    def shutdown(self) -> None:
        with self._lock:
            for p in self._providers.values():
                p.shutdown()
            self._providers.clear()


def emit(provider: Any, records: list[dict[str, Any]]) -> None:
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode

    tracer = provider.get_tracer("agentcanvas")
    made: list[Any] = []
    for r in records:
        parent = made[r["parent"]] if r["parent"] is not None else None
        ctx = trace.set_span_in_context(parent) if parent is not None else None
        span = tracer.start_span(r["name"], context=ctx, start_time=r["start"], attributes=r["attrs"])
        if r.get("error"):
            span.set_status(Status(StatusCode.ERROR, str(r["error"])[:500]))
        made.append(span)
    for span, r in reversed(list(zip(made, records, strict=True))):  # children first
        span.end(end_time=max(r["end"], r["start"]))


def test_send(url: str, headers: dict[str, str]) -> str | None:
    """Send one test span right now; None when the destination accepted it, else what went wrong."""
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

    result: list[SpanExportResult] = []

    class Recording(SpanExporter):
        def __init__(self) -> None:
            self.inner = OTLPSpanExporter(endpoint=url, headers=headers, timeout=10)

        def export(self, spans: Any) -> SpanExportResult:
            result.append(self.inner.export(spans))
            return result[-1]

        def shutdown(self) -> None:
            self.inner.shutdown()

    provider = TracerProvider(resource=Resource.create({"service.name": "agentcanvas"}))
    provider.add_span_processor(SimpleSpanProcessor(Recording()))
    with provider.get_tracer("agentcanvas").start_as_current_span("agentcanvas connection test") as span:
        span.set_attribute("openinference.span.kind", "CHAIN")
    provider.shutdown()
    if result and result[0] == SpanExportResult.SUCCESS:
        return None
    return f"{url} did not accept the trace (check the address, the keys and that it accepts OTLP over HTTP)"


class TraceExportHook:
    """Runner finish hook: sends each run of a project with a trace destination."""

    def __init__(self, db: Any, projects: Any, exporter: Exporter | None = None, ttl_s: float = 30.0):
        self.db, self.projects = db, projects
        self.exporter = exporter or Exporter()
        self.ttl_s = ttl_s
        self._config: dict[str, tuple[float, TraceExport | None]] = {}  # project id -> (loaded at, config)

    def forget(self, project_id: str) -> None:
        """Settings changed: read them again on the next run."""
        self._config.pop(project_id, None)

    async def config(self, project_id: str) -> TraceExport | None:
        import time

        from agentcanvas.db.models import Project

        cached = self._config.get(project_id)
        if cached is not None and time.monotonic() - cached[0] < self.ttl_s:
            return cached[1]
        async with self.db.sessionmaker() as s:
            project = await s.get(Project, project_id)
        cfg = load(project.settings if project else None)
        self._config[project_id] = (time.monotonic(), cfg)
        return cfg

    async def on_run_finished(self, h: RunHandle, status: str, error: str | None, _interrupts: Any) -> None:
        from agentcanvas.services.projects import PROJECT_OF

        project_id = PROJECT_OF.get(h.workflow_id)
        if project_id is None:
            return
        cfg = await self.config(project_id)
        if cfg is None or not cfg.enabled:
            return
        try:
            url, headers = cfg.target(self.projects.secrets.get(project_id, {}))
        except KeyError as exc:
            log.warning("trace_export.missing_secret", project_id=project_id, missing=str(exc))
            return
        records = spans(h, status, error, cfg.include_content)
        if records:
            self.exporter.send(url, headers, records)
