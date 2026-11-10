"""AG-UI 1.0 events from a run's canonical events (F-8): what `POST /v1/deployments/{id}/ag-ui` streams.

The canonical events are only AG-UI-like (ADR 0008): text has no message ids, tool calls and pauses are CUSTOM
`ac.*` events. This translator turns them into AG-UI 1.0 events built with the official `ag-ui-protocol` models, so
the wire follows the specification:

* RUN_STARTED (with the protocol version) and RUN_FINISHED, or RUN_ERROR; STEP_STARTED / STEP_FINISHED per step.
* TEXT_MESSAGE_START / CONTENT / END: one message per model reply of the steps whose text is the answer (`shown`),
  from the streamed tokens, or from the reply itself when the model did not stream. A run that wrote no message
  (a workflow with a safety block streams nothing) ends with its final answer as one message.
* TOOL_CALL_START / ARGS / END / RESULT from the tool calls of those steps (`ac.tool_io`).
* REASONING_START / REASONING_MESSAGE_* / REASONING_END from streamed thinking (`ac.reasoning`).
* STATE_SNAPSHOT / STATE_DELTA (RFC 6902 patches) of the workflow's input and output fields, never its working ones.
* A pause ends the run with the `interrupt` outcome and its interrupts. A call of a tool the application runs itself
  (`client_tool`) is sent as a tool call, and the run ends as a success naming it in `pendingToolCallIds`.
* CUSTOM for warnings, queueing and the workflow's own custom events; usage becomes RUN_FINISHED's `usage`.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from ag_ui.core import (
    PROTOCOL_VERSION,
    BaseEvent,
    CustomEvent,
    Interrupt,
    ReasoningEndEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    ReasoningStartEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
    RunFinishedSuccessOutcome,
    RunStartedEvent,
    StateDeltaEvent,
    StateSnapshotEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    TokenUsage,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder

from agentcanvas.runtime.types import CLIENT_TOOL

# the `ac.*` events a caller gets as CUSTOM: the others are translated (replies, tool calls, thinking, pauses, usage)
# or stay inside (what hidden steps' agents answered, rubric checks, cost alerts and routing are the builders')
FORWARDED = {"ac.warning", "ac.queued", "ac.custom", "ac.truncated"}
META = "agentcanvas"  # the key of AgentCanvas's own details in an event's metadata (`ag-ui` is the protocol's)


def encode(event: BaseEvent) -> str:
    """One event as an SSE frame, with the package's own encoder."""
    return EventEncoder().encode(event)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _pointer(key: str) -> str:
    return "/" + key.replace("~", "~0").replace("/", "~1")


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)


class Translator:
    """Turns one run's canonical events into AG-UI events, in order (`feed`), keeping what pairs them up.

    `thread_id` and `run_id` are the ones the caller sent (AG-UI ids); `agentcanvas_run_id` names the run here.
    `shown`: canvas node ids whose text and tool calls reach the caller. `fields`: the state fields shown (input and
    output, not the chat's message list), `reducers` those whose updates are partial (sent in the final snapshot).
    `answer(output)`: the run's final answer text; `ask(interrupt)`: a pause as an AG-UI interrupt."""

    def __init__(
        self,
        *,
        thread_id: str,
        run_id: str,
        agentcanvas_run_id: str,
        shown: set[str],
        fields: set[str],
        reducers: set[str],
        snapshot: dict[str, Any] | None,
        answer: Callable[[dict[str, Any] | None], str],
        ask: Callable[[dict[str, Any]], Interrupt],
        warnings: list[str] | None = None,
    ):
        self.thread_id, self.run_id, self.ac_run_id = thread_id, run_id, agentcanvas_run_id
        self.shown, self.fields, self.reducers = shown, fields, reducers
        self.snapshot = snapshot  # None: a resumed run starts from the state the caller already has
        self.state: dict[str, Any] = dict(snapshot or {})
        self.answer, self.ask = answer, ask
        self.warnings = list(warnings or [])
        self.started = self.ended = False
        self.text: dict[str | None, str] = {}  # node -> its open text message
        self.streamed: set[str | None] = set()  # nodes whose current model reply streamed text
        self.reply: dict[str | None, str] = {}  # node -> the message of its latest model reply (a tool call's parent)
        self.reasoning: dict[str | None, tuple[str, str]] = {}  # node -> (span id, message id), open
        self.steps: Counter[str] = Counter()
        self.wrote = False  # any text message was sent
        self.usage: dict[str, dict[str, int]] = {}

    # ------------------------------------------------------------------------------------------ feed ----
    def feed(self, ev: dict[str, Any]) -> list[BaseEvent]:
        if self.ended:
            return []
        kind = ev.get("type")
        out = self._start()  # a run stopped before it began still opens with RUN_STARTED
        node = ev.get("canvas_node_id")
        if kind == "STEP_STARTED":
            name = str(ev.get("stepName") or "")
            self.steps[name] += 1
            if self.steps[name] == 1:  # the same step running twice at once (a fan-out) opens once
                out.append(StepStartedEvent(step_name=name))
        elif kind == "STEP_FINISHED":
            name = str(ev.get("stepName") or "")
            out += self._close(node)
            if self.steps[name] > 0:
                self.steps[name] -= 1
                if self.steps[name] == 0:
                    del self.steps[name]
                    out.append(StepFinishedEvent(step_name=name))
        elif kind == "TEXT_MESSAGE_CONTENT":
            if node in self.shown and isinstance(ev.get("delta"), str) and ev["delta"]:
                out += self._end_reasoning(node)
                out += self._write(node, ev["delta"])
                self.streamed.add(node)
        elif kind == "STATE_DELTA":
            out += self._delta(ev.get("delta"))
        elif kind == "CUSTOM":
            out += self._custom(ev, node)
        elif kind == "RUN_FINISHED":
            out += self._finished(ev)
        elif kind == "RUN_ERROR":
            self.ended = True
            message = str(ev.get("message") or "the run failed")
            out.append(RunErrorEvent(message=message, code=str(ev.get("code") or "error"), usage=self._usage()))
        return out

    def _start(self) -> list[BaseEvent]:
        if self.started:
            return []
        self.started = True
        out: list[BaseEvent] = [
            RunStartedEvent(
                thread_id=self.thread_id, run_id=self.run_id, protocol_version=PROTOCOL_VERSION,
                metadata={META: {"run_id": self.ac_run_id}},
            )
        ]  # fmt: skip
        if self.snapshot is not None:
            out.append(StateSnapshotEvent(snapshot=dict(self.state)))
        for warning in self.warnings:
            out.append(CustomEvent(name="ac.warning", value={"message": warning}))
        return out

    # ------------------------------------------------------------------------------------------ text ----
    def _write(self, node: str | None, delta: str) -> list[BaseEvent]:
        out: list[BaseEvent] = []
        if node not in self.text:
            self.text[node] = self.reply[node] = _id("msg")
            self.wrote = True
            out.append(TextMessageStartEvent(message_id=self.text[node], role="assistant"))
        out.append(TextMessageContentEvent(message_id=self.text[node], delta=delta))
        return out

    def _end_text(self, node: str | None) -> list[BaseEvent]:
        message = self.text.pop(node, None)
        return [TextMessageEndEvent(message_id=message)] if message else []

    def _end_reasoning(self, node: str | None) -> list[BaseEvent]:
        opened = self.reasoning.pop(node, None)
        if opened is None:
            return []
        span, message = opened
        return [ReasoningMessageEndEvent(message_id=message), ReasoningEndEvent(message_id=span)]

    def _close(self, node: str | None) -> list[BaseEvent]:
        return [*self._end_reasoning(node), *self._end_text(node)]

    def _reply(self, node: str | None, value: dict[str, Any]) -> list[BaseEvent]:
        """A model reply ended: its message closes; one that did not stream is sent whole."""
        out = self._end_reasoning(node)
        if node in self.streamed:
            self.streamed.discard(node)
            return [*out, *self._end_text(node)]
        out += self._end_text(node)
        self.reply.pop(node, None)
        reply = value.get("reply")
        content = reply.get("content") if isinstance(reply, dict) else reply
        if isinstance(content, str) and content:
            out += [*self._write(node, content), *self._end_text(node)]
        return out

    def _think(self, node: str | None, delta: str) -> list[BaseEvent]:
        out: list[BaseEvent] = self._end_text(node) if node in self.text else []
        if node not in self.reasoning:
            span, message = _id("rsn"), _id("msg")
            self.reasoning[node] = (span, message)
            out += [ReasoningStartEvent(message_id=span), ReasoningMessageStartEvent(message_id=message)]
        out.append(ReasoningMessageContentEvent(message_id=self.reasoning[node][1], delta=delta))
        return out

    # ------------------------------------------------------------------------------------------ tools ----
    def _tool_call(self, name: str, args: Any, node: str | None, call_id: str | None = None) -> tuple[str, list]:
        call_id = call_id or _id("call")
        parent = self.reply.get(node)
        return call_id, [
            ToolCallStartEvent(tool_call_id=call_id, tool_call_name=name, parent_message_id=parent),
            ToolCallArgsEvent(tool_call_id=call_id, delta=_text(args if args is not None else {})),
            ToolCallEndEvent(tool_call_id=call_id),
        ]

    def _tool_io(self, node: str | None, io: dict[str, Any]) -> list[BaseEvent]:
        out = self._close(node)
        call_id, events = self._tool_call(str(io.get("tool") or io.get("resource") or "tool"), io.get("args"), node)
        out += events
        result = io["error"] if io.get("error") is not None else io.get("output")
        out.append(ToolCallResultEvent(message_id=_id("msg"), tool_call_id=call_id, content=_text(result), role="tool"))
        return out

    # ------------------------------------------------------------------------------------------ state ----
    def _delta(self, delta: Any) -> list[BaseEvent]:
        if not isinstance(delta, dict):
            return []
        ops = []
        for key, value in delta.items():
            if key in self.fields and key not in self.reducers:  # partial updates come whole in the final snapshot
                self.state[key] = value
                ops.append({"op": "add", "path": _pointer(key), "value": value})
        return [StateDeltaEvent(delta=ops)] if ops else []

    # ------------------------------------------------------------------------------------------ custom ----
    def _custom(self, ev: dict[str, Any], node: str | None) -> list[BaseEvent]:
        name, value = str(ev.get("name") or ""), ev.get("value")
        if name == "ac.usage" and isinstance(value, dict):
            self._count(value)
        elif name == "ac.reasoning" and isinstance(value, dict):
            if node in self.shown and isinstance(value.get("delta"), str) and value["delta"]:
                return self._think(node, value["delta"])
        elif name == "ac.model_reply" and isinstance(value, dict):
            node = value.get("canvas_node_id", node)
            if node in self.shown:
                return self._reply(node, value)
        elif name == "ac.tool_io" and isinstance(value, dict):
            node = value.get("canvas_node_id", node)
            if node in self.shown:
                return self._tool_io(node, value)
        elif name in FORWARDED:
            return [CustomEvent(name=name, value=value)]
        return []

    def _count(self, record: dict[str, Any]) -> None:
        model = str(record.get("model") or "")
        n = self.usage.setdefault(model, {"in": 0, "out": 0, "read": 0, "write": 0})
        n["in"] += int(record.get("input_tokens") or 0)
        n["out"] += int(record.get("output_tokens") or 0)
        n["read"] += int(record.get("cache_read_tokens") or 0)
        n["write"] += int(record.get("cache_write_tokens") or 0)

    def _usage(self) -> list[TokenUsage] | None:
        out = []
        for model, n in self.usage.items():
            if not (n["in"] or n["out"]):
                continue
            provider, _, name = model.partition(":")
            out.append(
                TokenUsage(
                    provider=provider if name else None, model=name or model or None, input_tokens=n["in"],
                    output_tokens=n["out"], total_tokens=n["in"] + n["out"],
                    cached_input_tokens=min(n["read"], n["in"]) or None,
                    cache_write_input_tokens=min(n["write"], max(n["in"] - n["read"], 0)) or None,
                )
            )  # fmt: skip
        return out or None

    # ------------------------------------------------------------------------------------------ end ----
    def _finished(self, ev: dict[str, Any]) -> list[BaseEvent]:
        self.ended = True
        out: list[BaseEvent] = []
        for node in list({*self.text, *self.reasoning}):
            out += self._close(node)
        for name in list(self.steps):
            out.append(StepFinishedEvent(step_name=name))
        self.steps.clear()
        output = ev.get("output") if isinstance(ev.get("output"), dict) else None
        interrupts = [i for i in ev.get("interrupts") or [] if isinstance(i, dict)]
        client = sorted(  # in the order the model made the calls
            (i for i in interrupts if i.get("kind") == CLIENT_TOOL),
            key=lambda i: _obj(i.get("payload")).get("index") or 0,
        )
        asked = [i for i in interrupts if i.get("kind") != CLIENT_TOOL]
        if ev.get("outcome") == "interrupt" and not interrupts:  # stopped before a step (a breakpoint): not resumable
            return [*out, RunErrorEvent(message="the run stopped at a breakpoint", code="breakpoint")]
        if not interrupts and not self.wrote and (text := self.answer(output)):
            message = _id("msg")
            out += [
                TextMessageStartEvent(message_id=message, role="assistant"),
                TextMessageContentEvent(message_id=message, delta=text),
                TextMessageEndEvent(message_id=message),
            ]
        pending: list[str] = []
        for i in client:
            payload = _obj(i.get("payload"))
            node = i.get("canvas_node_id")
            call_id, events = self._tool_call(
                str(payload.get("name") or "tool"), payload.get("args"), node, str(payload.get("tool_call_id") or "")
            )
            pending.append(call_id)
            out += events
        shown = {k: v for k, v in (output or {}).items() if k in self.fields}
        if shown:
            self.state.update(shown)
        if shown or self.snapshot is not None:
            out.append(StateSnapshotEvent(snapshot=dict(self.state)))
        outcome = (
            RunFinishedInterruptOutcome(interrupts=[self.ask(i) for i in asked])
            if asked
            else RunFinishedSuccessOutcome(pending_tool_call_ids=pending or None)
        )
        detail = {"run_id": self.ac_run_id, "status": ev.get("status")}
        if isinstance(ev.get("generated_by"), dict):
            detail["generated_by"] = ev["generated_by"]  # AI Act Art. 50: the output is AI-generated
        out.append(
            RunFinishedEvent(
                thread_id=self.thread_id, run_id=self.run_id, result=shown or None, outcome=outcome,
                usage=self._usage(), metadata={META: detail},
            )
        )  # fmt: skip
        return out
