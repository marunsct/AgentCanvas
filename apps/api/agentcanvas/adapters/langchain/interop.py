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
    from agents import Runner  # pyright: ignore[reportMissingImports]

    return (await Runner.run(agent, text)).final_output


async def _crewai(obj: Any, text: str, state: dict[str, Any]) -> Any:
    inputs = {**_scalars(state), "input": text}  # the step's input wins over a field that is also called "input"
    if hasattr(obj, "tasks") and hasattr(obj, "agents"):  # a Crew: its task texts use {input} and {field} names
        return await obj.kickoff_async(inputs=inputs)
    if hasattr(obj, "role") and hasattr(obj, "goal"):  # a single Agent
        return await obj.kickoff_async(text)
    return await obj.kickoff_async(inputs=inputs)  # a Flow


async def _google_adk(agent: Any, text: str, state: dict[str, Any]) -> Any:
    from google.adk.runners import InMemoryRunner  # pyright: ignore[reportMissingImports]
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


_LLAMA_RESERVED = {"input", "ctx", "stepwise", "start_event", "checkpoint_callback"}  # Workflow.run's own arguments


async def _llamaindex(workflow: Any, text: str, state: dict[str, Any]) -> Any:
    extra = {k: v for k, v in _scalars(state).items() if k not in _LLAMA_RESERVED}
    return await workflow.run(input=text, **extra)


async def _microsoft(agent: Any, text: str, state: dict[str, Any]) -> Any:
    return (await agent.run(text)).text


async def _claude(options: Any, text: str, state: dict[str, Any]) -> Any:
    from claude_agent_sdk import (  # pyright: ignore[reportMissingImports]
        AssistantMessage,
        ResultMessage,
        TextBlock,
        query,
    )

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


def _answers_given() -> int:
    """How many answers this step has been resumed with (LangGraph replays them to `interrupt()` in order)."""
    from langgraph.config import get_config

    try:
        pad = get_config().get("configurable", {}).get("__pregel_scratchpad")  # LangGraph's CONFIG_KEY_SCRATCHPAD
    except RuntimeError:
        return 0
    if pad is None:
        return 0
    pending = pad.get_null_resume(False) if callable(getattr(pad, "get_null_resume", None)) else None
    return len(getattr(pad, "resume", None) or []) + (pending is not None)


async def _rpc(client: Any, target: _Target, method: str, params: dict[str, Any]) -> dict[str, Any]:
    body = {"jsonrpc": "2.0", "id": uuid.uuid4().hex, "method": method, "params": params}
    r = await client.post(target.url, json=body, headers=target.headers, extensions=target.extensions)
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
            if len(done) < _answers_given():
                # the remote replies are kept in this process only: after a restart, carrying on would send the
                # first message again and start a second remote task, so stop with a clear message instead
                raise AgentError(
                    f"AgentCanvas restarted while the remote agent '{name}' was waiting for an answer, so that "
                    "conversation cannot be continued without sending its first message again. Run it again."
                )
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
                    result = await _rpc(client, endpoint, "message/send", {"message": message})
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


@dataclass(frozen=True)
class _Target:
    url: str  # with the checked IP in place of the host name unless private addresses are allowed
    headers: dict[str, str]
    extensions: dict[str, Any] | None  # TLS still checks the certificate of the name


async def _pinned(url: str, headers: dict[str, str], private_ok: bool) -> _Target:
    """`url`, checked to be public and pinned to the address that was checked, so a second DNS answer cannot send
    the request to a private network (as the HTTP tool does)."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise AgentError(f"'{url}' is not an http(s) address")
    if private_ok:
        return _Target(url, headers, None)
    from agentcanvas.adapters.langchain.nodes import _public_address

    port = parts.port or (443 if parts.scheme == "https" else 80)
    ip = await _public_address(parts.hostname, port)
    netloc = f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"
    return _Target(
        parts._replace(netloc=netloc).geturl(),
        {**headers, "Host": parts.netloc.rsplit("@", 1)[-1]},
        {"sni_hostname": parts.hostname},
    )


async def _a2a_endpoint(client: Any, url: str, headers: dict[str, str], private_ok: bool) -> _Target:
    """The JSON-RPC address: `url` itself, or the `url` its Agent Card names when `url` is a card."""
    card = await _pinned(url, headers, private_ok)
    if not url.split("?", 1)[0].endswith(".json"):
        return card
    r = await client.get(card.url, headers=card.headers, extensions=card.extensions)
    if r.status_code >= 400:
        raise AgentError(f"could not read the agent card at {url}: HTTP {r.status_code}")
    endpoint = str((r.json() or {}).get("url") or "")
    if not endpoint:
        raise AgentError(f"the agent card at {url} names no address")
    return await _pinned(endpoint, headers, private_ok)


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


# ------------------------------------------------------- designed on the canvas (S21) ----
_JSON_PY: dict[str, Any] = {"string": str, "integer": int, "number": float, "boolean": bool}


def _py_type(prop: Any) -> Any:
    """The Python type a framework reads from a tool argument's JSON Schema."""
    from typing import Literal

    if not isinstance(prop, dict):
        return Any
    if "enum" in prop and prop["enum"] and all(isinstance(v, str) for v in prop["enum"]):
        return Literal[tuple(prop["enum"])]  # pyright: ignore[reportInvalidTypeForm]
    t = prop.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    if t in _JSON_PY:
        return _JSON_PY[t]
    if t == "array":
        return list[_py_type(prop.get("items") or {})]  # pyright: ignore[reportInvalidTypeForm]
    if t == "object":
        return dict[str, Any]
    for key in ("anyOf", "oneOf"):
        options = [o for o in prop.get(key) or [] if isinstance(o, dict) and o.get("type") != "null"]
        if options:
            return _py_type(options[0])
    return Any


def _identifier(name: str) -> str:
    import re

    out = re.sub(r"\W", "_", name) or "tool"
    return out if not out[0].isdigit() else f"tool_{out}"


def plain_tool(t: Any, call: Callable[[Any, dict[str, Any]], Awaitable[Any]]) -> Callable:
    """A LangChain tool as a plain async function with a real signature and docstring, which every framework can
    turn into a tool of its own. `call(tool, args)` runs it (through LangChain, so it is traced and recorded)."""
    from langchain_core.utils.function_calling import convert_to_openai_tool

    spec = convert_to_openai_tool(t)["function"]
    schema = spec.get("parameters") or {}
    props: dict[str, Any] = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    params, annotations, doc_args = [], {}, []
    for arg in sorted(props, key=lambda a: a not in required):  # required arguments first
        hint = _py_type(props[arg])
        if arg in required:
            params.append(inspect.Parameter(arg, inspect.Parameter.KEYWORD_ONLY, annotation=hint))
        else:
            hint = hint | None
            params.append(inspect.Parameter(arg, inspect.Parameter.KEYWORD_ONLY, annotation=hint, default=None))
        annotations[arg] = hint
        if props[arg].get("description"):
            doc_args.append(f"    {arg}: {props[arg]['description']}")

    async def fn(**kwargs: Any) -> str:
        args = {k: v for k, v in kwargs.items() if v is not None or k in required}
        out = await call(t, args)
        return out if isinstance(out, str) else json.dumps(out, default=str)

    fn.__name__ = fn.__qualname__ = _identifier(spec["name"])
    fn.__doc__ = (spec.get("description") or spec["name"]) + ("\n\nArgs:\n" + "\n".join(doc_args) if doc_args else "")
    fn.__signature__ = inspect.Signature(params, return_annotation=str)  # pyright: ignore[reportFunctionMemberAccess]
    fn.__annotations__ = {**annotations, "return": str}
    return fn


def _conversation(state: Any) -> str:
    """What a designed agent is asked: the latest message, after the earlier turns of the conversation if any."""
    messages = list(state.get("messages") or [])

    def line(m: Any) -> tuple[str, str]:
        role = getattr(m, "type", None) or (m.get("role") if isinstance(m, dict) else "")
        content = getattr(m, "content", None) if not isinstance(m, dict) else m.get("content")
        if not isinstance(content, str):
            content = json.dumps(content, default=str)
        return {"human": "user", "ai": "assistant"}.get(str(role), str(role)), content

    turns = [line(m) for m in messages if line(m)[0] in ("user", "assistant") and line(m)[1]]
    if not turns:
        return ""
    if len(turns) == 1:
        return turns[-1][1]
    earlier = "\n".join(f"{role}: {text}" for role, text in turns[:-1])
    return f"The conversation so far:\n{earlier}\n\nThe latest message:\n{turns[-1][1]}"


# (thread, namespace, step, nth approval) -> the tool call a person was asked about, so that a designed agent's rerun
# after the pause only uses the decision for that exact call (interrupts resume by position, not by content)
_ASKED: dict[tuple[str, str, str, int], str] = {}


def design_module(package: str | None, name: str) -> Any:
    """The generated module of the designed agent `name` in an exported project (`<package>/agents/<name>.py`)."""
    import importlib

    return importlib.import_module(f"{package}.agents.{name}" if package else f"agents.{name}")


def designed_agent_node(
    *,
    node_id: str,
    name: str,
    framework: str,
    label: str,
    api: str,
    requires: str,
    source: str | None = None,
    module: Any = None,
    model: Any,
    tools: list[Any],
    approve_tools: list[str] | None = None,
    structured: bool = False,
) -> Callable:
    """An Agent block designed for another framework, run as one step of a LangGraph flow (S21 mixed flows).

    `source` (or an imported `module`) is the agent written in that framework's own style by `agentcanvas.designs`:
    `build(tools, model=, base_url=, api_key=)` and `ask(agent, text)`. The canvas model reaches it through the model
    bridge, the canvas tools as plain functions; tools in `approve_tools` ask a person first, as in a LangGraph agent
    (the step then runs again from the start with the answer, so the model is asked again)."""
    from langgraph.config import get_config
    from langgraph.errors import GraphInterrupt

    from agentcanvas.adapters.langchain.bridge import model_bridge

    approve = set(approve_tools or [])
    loaded: dict[str, Any] = {"module": module}

    def agent_module() -> Any:
        if loaded["module"] is None:
            import types

            mod = types.ModuleType(f"agentcanvas_design_{name}")
            exec(compile(source or "", f"<{label} agent {name}>", "exec"), mod.__dict__)  # our generated code
            loaded["module"] = mod
        return loaded["module"]

    async def run(state: Any) -> dict:
        try:
            __import__(requires)
        except ImportError as exc:
            raise AgentError(
                f"{label} is not installed on this server, so the agent '{name}' cannot run ({exc})"
            ) from None
        config = get_config()
        waiting: list[GraphInterrupt] = []
        conf = config.get("configurable") or {}
        where = (str(conf.get("thread_id")), str(conf.get("checkpoint_ns", "")), node_id)
        asked_count = 0

        async def call(t: Any, args: dict[str, Any]) -> Any:
            nonlocal asked_count
            if t.name in approve:
                from langchain_core.runnables.config import var_child_runnable_config

                asking = json.dumps({"tool": t.name, "args": args}, sort_keys=True, default=str)
                slot = (*where, asked_count)
                asked_count += 1
                token = var_child_runnable_config.set(config)  # frameworks may run tools outside the step's context
                try:
                    allowed = _approve(node_id, name, framework, asking)
                except GraphInterrupt as asked:
                    _ASKED[slot] = asking
                    waiting.append(asked)
                    return (
                        "This tool call is waiting for a person's approval. Do not call it again; say you are waiting."
                    )
                finally:
                    var_child_runnable_config.reset(token)
                # the step reran from its start: a decision only counts for the very call the person saw
                if _ASKED.pop(slot, None) != asking:
                    return (
                        "This tool call was not run: it differs from the call a person approved or rejected. "
                        "Tell the person what you wanted to do so they can ask again."
                    )
                if not allowed:
                    return "A person rejected this tool call. Do not try it again."
            return await t.ainvoke(args, config=config)

        functions = [plain_tool(t, call) for t in tools]
        text = _conversation(state)
        bridge = await model_bridge()
        with bridge.serving(model, config) as key:
            base_url = bridge.root + ("/v1" if api == "openai" else "")
            try:
                mod = agent_module()
                agent = mod.build(functions, model="canvas", base_url=base_url, api_key=key)
                answer = await mod.ask(agent, text)
            except Exception as exc:
                if waiting:
                    raise waiting[0] from None
                raise AgentError(f"the {label} agent '{name}' failed: {type(exc).__name__}: {exc}") from exc
        if waiting:  # a tool asked for approval: pause here, as a LangGraph agent would
            raise waiting[0]
        update: dict[str, Any] = {}
        if structured and answer is not None and not isinstance(answer, str):
            data = answer.model_dump(mode="json") if hasattr(answer, "model_dump") else answer
            update["structured_response"] = data
            reply = json.dumps(data, default=str)
        else:
            reply = _text(answer)
        _trace(node=name, framework=framework, input=text, output=reply)
        return {**update, "messages": [AIMessage(content=reply, name=name)]}

    run.__name__ = name
    return run
