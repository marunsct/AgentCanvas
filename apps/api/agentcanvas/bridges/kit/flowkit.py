# pyright: reportMissingImports=false, reportAttributeAccessIssue=false
# (this file runs inside the exported project, where the framework packages are installed)
"""Run a workflow exported from AgentCanvas as an Agent Spec flow, on another framework (preview).

`flow.json` is the workflow in Agent Spec (https://oracle.github.io/agent-spec). The framework's own Agent Spec
loader turns it into a runnable flow; this file gives that loader what the flow needs from outside: the model
addresses and keys (`models.json`, keys from the environment), and the steps AgentCanvas runs itself, written as
Python functions (`steps.py`).
"""

from __future__ import annotations

import copy
import importlib
import json
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


# ------------------------------------------------------------------ the flow ----
def load(path: Path | None = None) -> dict[str, Any]:
    """The flow, with each model's address and key filled in (`MODEL_BASE_URL` sends every model to one server)."""
    doc = json.loads((path or HERE / "flow.json").read_text())
    models = json.loads((HERE / "models.json").read_text())
    for comp in _components(doc):
        where = models.get(comp.get("id", ""))
        if where is None:
            continue
        base = os.environ.get("MODEL_BASE_URL")
        if base:  # e.g. the tests' fake model server, or a local proxy
            comp["component_type"] = "OpenAiCompatibleConfig"
        comp["url"] = base or where["url"] or comp.get("url") or None
        comp["api_key"] = os.environ.get(where["key_env"]) or os.environ.get("MODEL_API_KEY") or "not-needed"
    return doc


def _components(x: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(x, dict):
        if "component_type" in x:
            out.append(x)
        for v in x.values():
            out += _components(v)
    elif isinstance(x, list):
        for v in x:
            out += _components(v)
    return out


def inputs_of(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return list(doc.get("inputs") or [])


# ------------------------------------------------------------------ steps AgentCanvas runs itself ----
def step_functions(doc: dict[str, Any]) -> dict[str, Callable[..., Any]]:
    """name -> function for every step the flow leaves to its host: yours from `steps.py` first, else the built-in
    one (setting values), else one that says it is missing."""
    try:
        mine = importlib.import_module("steps")
    except ModuleNotFoundError:
        mine = None
    found: dict[str, Callable[..., Any]] = {}
    for comp in _components(doc):
        if comp.get("component_type") != "ServerTool":
            continue
        name = comp["name"]
        found[name] = getattr(mine, name, None) or getattr(mine, f"{name}_", None) or _builtin(comp)
    return found


def _builtin(tool: dict[str, Any]) -> Callable[..., Any]:
    meta = (tool.get("metadata") or {}).get("agentcanvas") or {}
    outputs = [o["title"] for o in tool.get("outputs") or []]
    if meta.get("type") == "core.set":
        from jinja2.sandbox import SandboxedEnvironment

        env = SandboxedEnvironment()
        values = (meta.get("config") or {}).get("values") or {}

        def set_values(**state: Any) -> Any:
            got = {k: env.from_string(str(v)).render(state=state) for k, v in values.items()}
            return got[outputs[0]] if len(outputs) == 1 else got

        return set_values

    def missing(**state: Any) -> Any:
        raise NotImplementedError(
            f"the step '{tool['name']}' ({meta.get('type')}) runs inside AgentCanvas; write it as a function named "
            f"{tool['name']} in steps.py (it gets {sorted(state)} and returns {outputs or 'nothing'})"
        )

    return missing


# ------------------------------------------------------------------ runtimes ----
def run(target: str, inputs: dict[str, Any], message: str, answer: Callable[[str], str]) -> dict[str, Any]:
    """One run: the flow's outputs, plus `reply` (the last thing an agent said) when there is one. `answer` replies
    to the flow's questions."""
    doc = load()
    return {"langgraph": _langgraph, "wayflow": _wayflow, "openai_agents": _openai_agents}[target](
        doc, inputs, message, answer
    )


def _langgraph(doc: dict[str, Any], inputs: dict[str, Any], message: str, answer: Callable[[str], str]) -> dict:
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    from pyagentspec.adapters.langgraph import AgentSpecLoader

    doc = questions_shown(doc)
    graph = AgentSpecLoader(tool_registry=step_functions(doc), checkpointer=InMemorySaver()).load_json(json.dumps(doc))
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    out = graph.invoke({"inputs": inputs, "messages": [{"role": "user", "content": message}]}, config)
    questions: list[str] = []
    while out.get("__interrupt__"):
        asked = out["__interrupt__"][0].value
        if not asked:  # the question's text is the flow's last message
            said = [m for m in graph.get_state(config).values.get("messages") or [] if getattr(m, "type", "") == "ai"]
            asked = said[-1].content if said else ""
        asked = asked if isinstance(asked, str) else json.dumps(asked)
        questions.append(asked)
        out = graph.invoke(Command(resume=answer(asked)), config)
    result = dict(out.get("outputs") or {})
    said = [m for m in out.get("messages") or [] if getattr(m, "type", "") == "ai" and m.content
            and m.content not in questions]  # fmt: skip
    if said:
        result.setdefault("reply", said[-1].content)
    return result


def _wayflow(doc: dict[str, Any], inputs: dict[str, Any], message: str, answer: Callable[[str], str]) -> dict:
    from wayflowcore.agentspec import AgentSpecLoader
    from wayflowcore.property import Property
    from wayflowcore.tools import ServerTool

    doc = questions_shown(doc)
    tools = {}
    specs = {c["name"]: c for c in _components(doc) if c.get("component_type") == "ServerTool"}
    for name, fn in step_functions(doc).items():
        spec = specs[name]
        tools[name] = ServerTool(
            name=name, description=spec.get("description") or name, func=fn,
            input_descriptors=[Property.from_json_schema(p) for p in spec.get("inputs") or []],
            output_descriptors=[Property.from_json_schema(p) for p in spec.get("outputs") or []],
        )  # fmt: skip
    flow = AgentSpecLoader(tool_registry=tools).load_json(json.dumps(doc))
    conversation = flow.start_conversation(inputs=inputs)
    conversation.append_user_message(message)
    status = conversation.execute()
    for _ in range(20):  # questions; an agent that waits for the next chat message ends the run
        if type(status).__name__ != "UserMessageRequestStatus" or not _question(conversation):
            break
        last = conversation.get_last_message()
        conversation.append_user_message(answer(last.content if last else ""))
        status = conversation.execute()
    result = dict(getattr(status, "output_values", None) or {})
    last = conversation.get_last_message()
    if last is not None and str(last.message_type).endswith("AGENT") and not _question(conversation):
        result.setdefault("reply", last.content)
    return result


def _question(conversation: Any) -> bool:
    """Whether the flow waits at a question (an agent waiting for the next chat message ends the run instead)."""
    step = conversation.state.current_step_name
    return type(conversation.component.steps.get(step)).__name__ in ("InputMessageStep", "OutputMessageStep")


def questions_shown(doc: dict[str, Any]) -> dict[str, Any]:
    """The loaders ask a question without its text, so each question gets a message step before it."""
    doc = copy.deepcopy(doc)
    refs = doc.get("$referenced_components") or {}
    for qid, q in list(refs.items()):
        if q.get("component_type") != "InputMessageNode" or not (q.get("inputs") or q.get("message")):
            continue
        show = {
            "component_type": "OutputMessageNode", "id": f"{qid}-show", "name": f"{q['name']}_message",
            "description": None, "metadata": {}, "inputs": q["inputs"], "outputs": [], "branches": ["next"],
            "message": q.get("message") or "",
        }  # fmt: skip
        refs[show["id"]] = show
        q["inputs"], q["message"] = [], None
        doc["nodes"].append({"$component_ref": show["id"]})
        for e in doc.get("control_flow_connections") or []:
            if e["to_node"]["$component_ref"] == qid:
                e["to_node"] = {"$component_ref": show["id"]}
        doc["control_flow_connections"].append({
            "component_type": "ControlFlowEdge", "id": f"{qid}-show-edge", "name": f"{q['name']}_show",
            "description": None, "metadata": {}, "from_node": {"$component_ref": show["id"]}, "from_branch": None,
            "to_node": {"$component_ref": qid},
        })  # fmt: skip
        for e in doc.get("data_flow_connections") or []:
            if e["destination_node"]["$component_ref"] == qid:
                e["destination_node"] = {"$component_ref": show["id"]}
    return doc


def _openai_agents(doc: dict[str, Any], inputs: dict[str, Any], message: str, answer: Callable[[str], str]) -> dict:
    import asyncio

    from agents import set_default_openai_api, set_default_openai_client, set_tracing_disabled
    from openai import AsyncOpenAI
    from pyagentspec.adapters.openaiagents import AgentSpecLoader

    models = [c for c in _components(doc) if c.get("component_type", "").endswith("Config") and "model_id" in c]
    if models:  # the generated code names models only: one client for all of them
        url = models[0].get("url")
        if url and models[0]["component_type"] == "OllamaConfig" and not url.rstrip("/").endswith("/v1"):
            url = url.rstrip("/") + "/v1"  # Ollama's OpenAI-compatible API
        set_default_openai_client(AsyncOpenAI(base_url=url, api_key=models[0].get("api_key")))
        set_default_openai_api("chat_completions")
    for m in models:  # keys stay out of the document pyagentspec rewrites
        m.pop("api_key", None)
    set_tracing_disabled(True)
    source = AgentSpecLoader().load_json(json.dumps(doc))
    module: dict[str, Any] = {"__name__": "agentcanvas_flow"}
    exec(compile(str(source), "flow_generated.py", "exec"), module)  # code generated from flow.json
    run_input = module["WorkflowInput"](**{"input_as_text": message, **inputs})
    return dict(asyncio.run(module["run_workflow"](run_input)) or {})
