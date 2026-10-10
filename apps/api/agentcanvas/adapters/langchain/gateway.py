"""The model gateway (S26): backup models and an answer cache for a canvas model block.

`Resources.model` wraps a model block in a `GatewayModel` when its config names backup models (`fallbacks`) or turns
on the answer cache (`cache_ttl`). Every framework reaches canvas models through `Resources.model` (the OpenAI Agents
SDK through the model bridge), so the gateway works the same for all of them, and LangGraph exports carry it.

* Backups: when a call fails (the provider is down, out of quota, times out...), the same call goes to each backup in
  order and the first answer wins. A streamed call fails over only before its first token.
* Answer cache: an identical request (same models, messages, tools and options) within `cache_ttl` seconds gets the
  stored answer without calling a provider, so it costs nothing. One cache per project, in the server's memory.

Each answer carries `response_metadata["ac_gateway"]`: which model answered, whether it came from the cache, and the
models that failed first. The real model calls run as child runs of the gateway's, so usage is counted, and priced,
for the model that answered; the usage tracker skips the gateway's own run (its metadata has `ac_gateway`).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.callbacks import AsyncCallbackManager, CallbackManager
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, message_chunk_to_message
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict, Field

CACHE_SIZE = 1000  # answers kept per project
NOSTREAM = "nostream"  # LangGraph's tag: the gateway streams the answer, its inner calls must not stream it again


class AnswerCache:
    """Stored model answers by request key, oldest dropped first."""

    def __init__(self, size: int = CACHE_SIZE):
        self.size = size
        self._items: OrderedDict[str, tuple[float, AIMessage]] = OrderedDict()

    def get(self, key: str, ttl: float) -> AIMessage | None:
        item = self._items.get(key)
        if item is None:
            return None
        if time.monotonic() - item[0] > ttl:
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return item[1].model_copy(deep=True)

    def put(self, key: str, answer: AIMessage) -> None:
        self._items[key] = (time.monotonic(), answer.model_copy(deep=True))
        self._items.move_to_end(key)
        while len(self._items) > self.size:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)


class GatewayModel(BaseChatModel):
    """A canvas model block with backups and an answer cache. `models[0]` is the block's own model."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    models: list[Any]  # BaseChatModel, tried in order
    names: list[str]  # their catalog ids (provider:model)
    settings: str = ""  # the block's config, as part of the cache key
    cache_ttl: float | None = None  # seconds; None = no cache
    answers: Any = Field(default=None, exclude=True)  # AnswerCache shared by the project's models

    def __init__(self, **kwargs: Any):
        super().__init__(**{"cache": False, **kwargs})
        self.metadata = {**(self.metadata or {}), "ac_gateway": True}

    @property
    def _llm_type(self) -> str:
        return "agentcanvas-gateway"

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any) -> Any:  # type: ignore[override]
        options = {**kwargs, **({"tool_choice": tool_choice} if tool_choice is not None else {})}
        return self.bind(ac_tools=list(tools), ac_tool_options=options)

    # ------------------------------------------------------------ helpers ----
    def _key(self, messages: list[BaseMessage], stop: Any, tools: Any, options: Any, kwargs: dict[str, Any]) -> str:
        if not self.cache_ttl or self.answers is None:
            return ""
        request = {
            "models": self.names,
            "settings": self.settings,
            "messages": [_message(m) for m in messages],
            "stop": stop,
            "tools": [_tool_spec(t) for t in tools or []],
            "options": options or {},
            "kwargs": kwargs,
        }
        return hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()

    def _runnable(self, model: Any, tools: Any, options: Any) -> Any:
        return model.bind_tools(tools, **(options or {})) if tools else model

    def _answer(self, reply: Any, i: int, failed: list[dict[str, str]], key: str) -> AIMessage:
        if not isinstance(reply, AIMessage):
            reply = AIMessage(content=getattr(reply, "content", str(reply)))
        if key and self.answers is not None:
            self.answers.put(key, reply)
        reply.response_metadata = {**reply.response_metadata, "ac_gateway": _note(self.names[i], False, failed)}
        return reply

    def _hit(self, key: str) -> AIMessage | None:
        hit = self.answers.get(key, self.cache_ttl) if key and self.cache_ttl else None
        if hit is not None:
            hit.usage_metadata = None  # nothing was spent on it
            hit.response_metadata = {**hit.response_metadata, "ac_gateway": _note(self.names[0], True, [])}
        return hit

    def _failed(self, i: int, exc: Exception, failed: list[dict[str, str]]) -> None:
        failed.append({"model": self.names[i], "error": f"{type(exc).__name__}: {exc}"[:300]})
        if i == len(self.models) - 1 and len(failed) > 1:
            exc.add_note("models tried: " + "; ".join(f"{f['model']} ({f['error']})" for f in failed))

    # -------------------------------------------------------------- calls ----
    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None,
        ac_tools: Any = None, ac_tool_options: Any = None, **kwargs: Any,
    ) -> ChatResult:  # fmt: skip
        key = self._key(messages, stop, ac_tools, ac_tool_options, kwargs)
        if (hit := self._hit(key)) is not None:
            return ChatResult(generations=[ChatGeneration(message=hit)])
        failed: list[dict[str, str]] = []
        for i, model in enumerate(self.models):
            try:
                reply = self._runnable(model, ac_tools, ac_tool_options).invoke(
                    messages, config=_child(run_manager, CallbackManager), stop=stop, **kwargs
                )
            except Exception as exc:
                self._failed(i, exc, failed)
                if i == len(self.models) - 1:
                    raise
                continue
            return ChatResult(generations=[ChatGeneration(message=self._answer(reply, i, failed, key))])
        raise AssertionError("unreachable")

    async def _agenerate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None,
        ac_tools: Any = None, ac_tool_options: Any = None, **kwargs: Any,
    ) -> ChatResult:  # fmt: skip
        key = self._key(messages, stop, ac_tools, ac_tool_options, kwargs)
        if (hit := self._hit(key)) is not None:
            return ChatResult(generations=[ChatGeneration(message=hit)])
        failed: list[dict[str, str]] = []
        for i, model in enumerate(self.models):
            try:
                reply = await self._runnable(model, ac_tools, ac_tool_options).ainvoke(
                    messages, config=_child(run_manager, AsyncCallbackManager), stop=stop, **kwargs
                )
            except Exception as exc:
                self._failed(i, exc, failed)
                if i == len(self.models) - 1:
                    raise
                continue
            return ChatResult(generations=[ChatGeneration(message=self._answer(reply, i, failed, key))])
        raise AssertionError("unreachable")

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        yield ChatGenerationChunk(message=_chunk(self._generate(*args, **kwargs).generations[0].message))

    async def _astream(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: Any = None,
        ac_tools: Any = None, ac_tool_options: Any = None, **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:  # fmt: skip
        key = self._key(messages, stop, ac_tools, ac_tool_options, kwargs)
        if (hit := self._hit(key)) is not None:
            yield ChatGenerationChunk(message=_chunk(hit))
            return
        failed: list[dict[str, str]] = []
        for i, model in enumerate(self.models):
            total: Any = None
            try:
                stream = self._runnable(model, ac_tools, ac_tool_options).astream(
                    messages, config=_child(run_manager, AsyncCallbackManager), stop=stop, **kwargs
                )
                async for part in stream:
                    chunk = part if isinstance(part, AIMessageChunk) else AIMessageChunk(content=str(part.content))
                    total = chunk if total is None else total + chunk
                    yield ChatGenerationChunk(message=chunk)
            except Exception as exc:
                if total is not None:  # the answer has started: a backup cannot take it over
                    raise
                self._failed(i, exc, failed)
                if i == len(self.models) - 1:
                    raise
                continue
            if total is None:
                total = AIMessageChunk(content="")
            self._answer(message_chunk_to_message(total), i, failed, key)  # stores it
            note = {"ac_gateway": _note(self.names[i], False, failed)}
            yield ChatGenerationChunk(message=AIMessageChunk(content="", response_metadata=note))
            return


def _note(model: str, cached: bool, failed: list[dict[str, str]]) -> dict[str, Any]:
    return {"model": model, "cached": cached, "failed": list(failed)}


def _child(run_manager: Any, manager_class: Any) -> dict[str, Any]:
    """The config for an inner model call: a child run of the gateway's, hidden from LangGraph's token stream."""
    if run_manager is None:
        return {"tags": [NOSTREAM]}
    manager = manager_class(handlers=[], parent_run_id=run_manager.run_id)
    manager.set_handlers(run_manager.inheritable_handlers)
    manager.add_tags(run_manager.inheritable_tags)
    manager.add_metadata(run_manager.inheritable_metadata)
    manager.add_tags([NOSTREAM], inherit=False)
    return {"callbacks": manager}


def _chunk(message: Any) -> AIMessageChunk:
    """A whole answer as one stream chunk."""
    calls = [
        {"name": c["name"], "args": json.dumps(c.get("args") or {}), "id": c.get("id"), "index": i,
         "type": "tool_call_chunk"}
        for i, c in enumerate(getattr(message, "tool_calls", None) or [])
    ]  # fmt: skip
    return AIMessageChunk(
        content=message.content,
        id=message.id,
        additional_kwargs=dict(message.additional_kwargs),
        response_metadata=dict(message.response_metadata),
        usage_metadata=getattr(message, "usage_metadata", None),
        tool_call_chunks=calls,  # type: ignore[arg-type]
    )


def _message(m: BaseMessage) -> dict[str, Any]:
    """What a message says, for the cache key: not its random id, nor what a provider reported about it."""
    return m.model_dump(exclude={"id", "response_metadata", "usage_metadata"}, mode="json")


def _tool_spec(tool: Any) -> Any:
    try:
        return convert_to_openai_tool(tool)
    except Exception:  # noqa: BLE001 - anything the model accepts still needs a stable key
        return repr(tool)
