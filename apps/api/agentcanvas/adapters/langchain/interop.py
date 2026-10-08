"""Agents from other frameworks as canvas steps (S16): wrapped agents, remote A2A agents and MCP agents.

Part of the node library the interpreter and the generated code share (ADR 0004), so it is vendored into exports.
Each wrapped agent is a black box: one step on the canvas runs the whole agent through its framework's own entry
point. Frameworks are imported only when such a step runs, so none of them is a dependency of AgentCanvas itself.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.config import get_stream_writer
from langgraph.types import interrupt

from agentcanvas.expr import render_template


@dataclass(frozen=True)
class Framework:
    label: str
    package: str  # what an export installs (tested in CI's `frameworks` job with these versions)
    module: str  # importable when the package is installed
    entry: str  # how the step calls the agent, for docs and errors


FRAMEWORKS: dict[str, Framework] = {
    "openai_agents": Framework("OpenAI Agents SDK", "openai-agents==0.23.1", "agents", "Runner.run(agent, input)"),
    "crewai": Framework("CrewAI", "crewai==1.15.25", "crewai", "crew.kickoff_async(inputs=…) or agent.kickoff_async"),
    "google_adk": Framework("Google ADK", "google-adk==2.11.0", "google.adk", "InMemoryRunner(agent).run_async"),
    "pydantic_ai": Framework("Pydantic AI", "pydantic-ai-slim==2.54.0", "pydantic_ai", "agent.run(input)"),
    "agno": Framework("Agno", "agno==3.1.1", "agno", "agent.arun(input)"),
    "strands": Framework("Strands Agents", "strands-agents==1.58.1", "strands", "agent.invoke_async(input)"),
    "llamaindex": Framework(
        "LlamaIndex Workflows", "llama-index-workflows==2.25.0", "workflows", "workflow.run(input=…)"
    ),
    "microsoft_agent_framework": Framework(
        "Microsoft Agent Framework", "agent-framework-core==1.20.0", "agent_framework", "agent.run(input)"
    ),
    "claude_agent_sdk": Framework(
        "Claude Agent SDK", "claude-agent-sdk==0.2.164", "claude_agent_sdk", "query(prompt, options)"
    ),
    "callable": Framework("Python function", "", "", "async def run(input: dict, ctx) -> str | dict"),
}


class AgentError(RuntimeError):
    """A wrapped or remote agent failed; the message says which agent and why, in plain words."""


def _text(value: Any) -> str:
    """The plain text of an agent's answer (str, a Pydantic model, a dict or anything with `.text`/`.raw`)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    for attr in ("raw", "text", "content", "final_output", "output", "result"):
        inner = getattr(value, attr, None)
        if isinstance(inner, str):
            return inner
    if hasattr(value, "model_dump_json"):
        return value.model_dump_json()
    if isinstance(value, dict | list):
        return json.dumps(value, default=str)
    return str(value)


def _require(framework: str) -> None:
    fw = FRAMEWORKS[framework]
    try:
        __import__(fw.module)
    except ImportError as exc:
        raise AgentError(
            f"{fw.label} is not installed here: add `{fw.package}` to the project's packages ({exc})"
        ) from None


# ------------------------------------------------------------------ runners ----
async def _openai_agents(agent: Any, text: str, state: dict[str, Any]) -> Any:
    from agents import Runner

    return (await Runner.run(agent, text)).final_output


async def _crewai(obj: Any, text: str, state: dict[str, Any]) -> Any:
    inputs = {"input": text, **_scalars(state)}
    if hasattr(obj, "tasks") and hasattr(obj, "agents"):  # a Crew: its task texts use {input} and {field} names
        return await obj.kickoff_async(inputs=inputs)
    if hasattr(obj, "role") and hasattr(obj, "goal"):  # a single Agent
        return await obj.kickoff_async(text)
    return await obj.kickoff_async(inputs=inputs)  # a Flow


async def _google_adk(agent: Any, text: str, state: dict[str, Any]) -> Any:
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    runner = InMemoryRunner(agent=agent, app_name="agentcanvas")
    session = await runner.session_service.create_session(app_name="agentcanvas", user_id="agentcanvas")
    final = ""
    message = types.Content(role="user", parts=[types.Part(text=text)])
    async for event in runner.run_async(user_id="agentcanvas", session_id=session.id, new_message=message):
        if event.is_final_response() and event.content and event.content.parts:
            final = "".join(p.text or "" for p in event.content.parts)
    return final


async def _pydantic_ai(agent: Any, text: str, state: dict[str, Any]) -> Any:
    return (await agent.run(text)).output


async def _agno(agent: Any, text: str, state: dict[str, Any]) -> Any:
    return (await agent.arun(text)).content


async def _strands(agent: Any, text: str, state: dict[str, Any]) -> Any:
    return str(await agent.invoke_async(text))


async def _llamaindex(workflow: Any, text: str, state: dict[str, Any]) -> Any:
    return await workflow.run(input=text, **_scalars(state))


async def _microsoft(agent: Any, text: str, state: dict[str, Any]) -> Any:
    return (await agent.run(text)).text


async def _claude(options: Any, text: str, state: dict[str, Any]) -> Any:
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query

    said: list[str] = []
    result: str | None = None
    async for message in query(prompt=text, options=options):
        if isinstance(message, AssistantMessage):
            said += [b.text for b in message.content if isinstance(b, TextBlock)]
        elif isinstance(message, ResultMessage):
            if message.is_error:
                raise AgentError(f"the Claude agent stopped with an error: {message.result or message.subtype}")
            result = message.result
    return result if result is not None else "\n".join(said)


async def _callable(fn: Any, text: str, state: dict[str, Any]) -> Any:
    args = ({"input": text, "state": dict(state)}, {"framework": "callable"})
    if inspect.iscoroutinefunction(fn):
        return await fn(*args)
    return await asyncio.to_thread(fn, *args)


RUNNERS: dict[str, Callable[[Any, str, dict[str, Any]], Awaitable[Any]]] = {
    "openai_agents": _openai_agents,
    "crewai": _crewai,
    "google_adk": _google_adk,
    "pydantic_ai": _pydantic_ai,
    "agno": _agno,
    "strands": _strands,
    "llamaindex": _llamaindex,
    "microsoft_agent_framework": _microsoft,
    "claude_agent_sdk": _claude,
    "callable": _callable,
}


def _scalars(state: Any) -> dict[str, Any]:
    return {k: v for k, v in dict(state).items() if isinstance(v, str | int | float | bool)}


def _input_text(prompt: str, state: Any) -> str:
    """The rendered prompt, or (empty prompt) the latest message of the conversation."""
    if prompt.strip():
        return render_template(prompt, state)
    messages = state.get("messages") or []
    if messages:
        last = messages[-1]
        content = getattr(last, "content", None) if not isinstance(last, dict) else last.get("content")
        return content if isinstance(content, str) else json.dumps(content, default=str)
    return ""


def _approve(node_id: str, name: str, framework: str, text: str) -> bool:
    """Ask a person before the agent runs (the same approval card as an Approval step)."""
    decision = interrupt({"kind": "approval", "canvas_node_id": node_id,
                          "value": {"agent": name, "framework": framework, "input": text}})  # fmt: skip
    if isinstance(decision, dict):
        decision = decision.get("approved")
    if isinstance(decision, str):
        return decision.strip().lower() in ("yes", "y", "approve", "approved", "ok", "okay", "true")
    return bool(decision)


def _update(output: str, name: str, text: str) -> dict[str, Any]:
    if output == "messages":
        return {"messages": [AIMessage(content=text, name=name)]}
    return {output: text}


def _trace(**event: Any) -> None:
    """One `ac.agent` event per call (input, answer, framework), shown in the run's trace and exported with it."""
    try:
        write = get_stream_writer()
    except RuntimeError:  # called outside a graph run (tests)
        return
    write({"ac_agent": {k: (v[:4000] if isinstance(v, str) else v) for k, v in event.items()}})


def framework_agent_node(
    *,
    node_id: str,
    name: str,
    framework: str,
    import_path: str,
    workspace: Path | None = None,
    prompt: str = "",
    output: str = "messages",
    approve: bool = False,
) -> Callable:
    """An agent built with another framework, run as one step. `import_path` names the agent (or crew, workflow,
    options or function) in the workspace; a zero-argument factory there is called once per run."""
    if framework not in RUNNERS:
        raise AgentError(f"unknown framework '{framework}'")
    runner = RUNNERS[framework]
    label = FRAMEWORKS[framework].label

    async def run(state: Any) -> dict:
        text = _input_text(prompt, state)
        if approve and not _approve(node_id, name, framework, text):
            _trace(node=name, framework=framework, input=text, skipped=True)
            return {}
        if framework != "callable":
            _require(framework)
        from agentcanvas.adapters.langchain.nodes import safe_import

        obj = safe_import(import_path, workspace)
        if framework != "callable" and inspect.isfunction(obj):
            obj = obj()  # a factory: build a fresh agent for this run
            if inspect.isawaitable(obj):
                obj = await obj
        try:
            answer = await runner(obj, text, dict(state))
        except AgentError:
            raise
        except Exception as exc:
            raise AgentError(f"the {label} agent '{name}' failed: {type(exc).__name__}: {exc}") from exc
        if framework == "callable" and isinstance(answer, dict):  # a function may update several fields
            _trace(node=name, framework=framework, input=text, output=json.dumps(answer, default=str))
            return answer
        reply = _text(answer)
        _trace(node=name, framework=framework, input=text, output=reply)
        return _update(output, name, reply)

    run.__name__ = name
    return run


# ------------------------------------------------------------- remote: A2A ----
def _a2a_text(result: dict[str, Any]) -> tuple[str, str | None, str | None, str]:
    """(text, task id, context id, state) of a `message/send` result: a Task or a Message."""
    if result.get("kind") == "message" or ("parts" in result and "status" not in result):
        return _parts_text(result.get("parts") or []), result.get("taskId"), result.get("contextId"), "completed"
    status = result.get("status") or {}
    state = str(status.get("state") or "completed")
    texts = [_parts_text(a.get("parts") or []) for a in result.get("artifacts") or []]
    question = _parts_text((status.get("message") or {}).get("parts") or [])
    text = (
        question
        if state in ("input-required", "auth-required", "failed", "rejected")
        else "\n".join(t for t in texts if t) or question
    )
    return text, result.get("id"), result.get("contextId"), state


def _parts_text(parts: list[dict[str, Any]]) -> str:
    out = []
    for p in parts:
        if "text" in p:
            out.append(str(p["text"]))
        elif "data" in p:
            out.append(json.dumps(p["data"], default=str))
    return "\n".join(out)


_SENT: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()  # step key -> remote answers, in order
_SENT_MAX = 1000


def _step_key(node_id: str) -> str | None:
    """Identifies this step within its run, so that a resumed step (LangGraph runs it again from the top) reuses the
    answers it already got instead of sending the same messages to the remote agent twice."""
    from langgraph.config import get_config

    try:
        cfg = get_config().get("configurable", {})
    except RuntimeError:
        return None
    thread = cfg.get("thread_id")
    return f"{thread}|{cfg.get('checkpoint_ns', '')}|{node_id}" if thread else None


async def _rpc(client: Any, url: str, method: str, params: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}
    r = await client.post(url, json=body, headers=headers)
    if r.status_code >= 400:
        raise AgentError(f"the remote agent answered HTTP {r.status_code}: {r.text[:300]}")
    reply = r.json()
    if reply.get("error"):
        raise AgentError(f"the remote agent refused: {reply['error'].get('message', reply['error'])}")
    return reply.get("result") or {}


def a2a_agent_node(
    *,
    node_id: str,
    name: str,
    url: str,
    prompt: str = "",
    output: str = "messages",
    headers: dict[str, Any] | None = None,
    approve: bool = False,
    allow_private_network: bool = False,
    timeout: float = 120,
    resolve: Callable[[Any], Any] = lambda v: v,
    transport: Any = None,
) -> Callable:
    """A remote agent over A2A (`message/send`). Its questions (`input-required`) pause the run as an Input step
    would; the answer goes back on the same task."""
    import httpx

    async def run(state: Any) -> dict:
        text = _input_text(prompt, state)
        if approve and not _approve(node_id, name, "a2a", text):
            _trace(node=name, framework="a2a", input=text, skipped=True)
            return {}
        sent = {k: str(resolve(v)) for k, v in (headers or {}).items()}
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            endpoint = await _a2a_endpoint(client, url, sent, allow_private_network or transport is not None)
            task_id = context_id = None
            message_text = text
            key = _step_key(node_id)
            done = list(_SENT.get(key, [])) if key else []  # answers received before this step paused
            for turn in range(20):  # an agent asking more than 20 questions in one step is stuck
                message: dict[str, Any] = {"kind": "message", "role": "user", "messageId": uuid.uuid4().hex,
                                           "parts": [{"kind": "text", "text": message_text}]}  # fmt: skip
                if task_id:
                    message["taskId"] = task_id
                if context_id:
                    message["contextId"] = context_id
                if turn < len(done):
                    result = done[turn]
                else:
                    result = await _rpc(client, endpoint, "message/send", {"message": message}, sent)
                    if key:
                        _SENT.setdefault(key, []).append(result)
                        _SENT.move_to_end(key)
                        while len(_SENT) > _SENT_MAX:
                            _SENT.popitem(last=False)
                reply, task_id, context_id, status = _a2a_text(result)
                if status in ("input-required", "auth-required"):
                    answer = interrupt({"kind": "input", "canvas_node_id": node_id,
                                        "prompt": reply or "The remote agent needs more information."})  # fmt: skip
                    message_text = answer if isinstance(answer, str) else json.dumps(answer, default=str)
                    continue
                if status in ("failed", "rejected", "canceled"):
                    raise AgentError(f"the remote agent '{name}' {status} the task: {reply or 'no reason given'}")
                _SENT.pop(key or "", None)
                _trace(node=name, framework="a2a", input=text, output=reply, task=task_id)
                return _update(output, name, reply)
        raise AgentError(f"the remote agent '{name}' kept asking questions; stopped after 20")

    run.__name__ = name
    return run


async def _a2a_endpoint(client: Any, url: str, headers: dict[str, str], private_ok: bool) -> str:
    """The JSON-RPC address: `url` itself, or the `url` its Agent Card names when `url` is a card."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise AgentError(f"'{url}' is not an http(s) address")
    if not private_ok:
        from agentcanvas.adapters.langchain.nodes import _public_address

        await _public_address(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    if not parts.path.endswith(".json"):
        return url
    r = await client.get(url, headers=headers)
    if r.status_code >= 400:
        raise AgentError(f"could not read the agent card at {url}: HTTP {r.status_code}")
    endpoint = str((r.json() or {}).get("url") or "")
    if not endpoint:
        raise AgentError(f"the agent card at {url} names no address")
    target = urlsplit(endpoint)
    if not private_ok and target.hostname != parts.hostname:
        from agentcanvas.adapters.langchain.nodes import _public_address

        await _public_address(target.hostname or "", target.port or (443 if target.scheme == "https" else 80))
    return endpoint


# ------------------------------------------------------------- remote: MCP ----
def mcp_agent_node(
    *, node_id: str, name: str, tools: list[Any], tool: str, arguments: dict[str, str], output: str = "messages",
    approve: bool = False,
) -> Callable:  # fmt: skip
    """One tool of a wired MCP server (often an agent published as a tool), called once as a step with templated
    arguments; its answer is written to `output`."""
    found = {getattr(t, "name", ""): t for t in tools}

    async def run(state: Any) -> dict:
        if tool not in found:
            have = ", ".join(found) or "none"
            raise AgentError(f"'{name}': the connected server has no tool '{tool}' (it has: {have})")
        args = {k: render_template(v, state) for k, v in arguments.items()}
        if approve and not _approve(node_id, name, "mcp", json.dumps(args)):
            _trace(node=name, framework="mcp", input=json.dumps(args), skipped=True)
            return {}
        try:
            answer = await found[tool].ainvoke(args)
        except Exception as exc:
            raise AgentError(f"the MCP agent '{name}' failed: {type(exc).__name__}: {exc}") from exc
        if isinstance(answer, list):  # MCP content blocks
            answer = "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in answer)
        reply = _text(answer)
        _trace(node=name, framework="mcp", input=json.dumps(args), output=reply, tool=tool)
        return _update(output, name, reply)

    run.__name__ = name
    return run
