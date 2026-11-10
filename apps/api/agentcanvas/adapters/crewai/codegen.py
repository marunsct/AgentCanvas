"""Workflow -> a Python module with a native CrewAI flow (S27).

The module reads like code a person would write with CrewAI: a `Flow` subclass with one method per block of the
canvas (marked with `@canvas-node` so the code view jumps back to the block), chained with CrewAI's own `@start`,
`@listen`, `@router`, `and_` and `or_`; an `Agent` builder per AI agent block and a `Crew` builder per crew block.
`@rt.step` (see `runtime`) adds what the canvas needs: events, field reducers, pauses for people and a loop guard.
"""

from __future__ import annotations

import builtins
import json
import keyword
import re

from agentcanvas import designs
from agentcanvas.adapters.langchain.codegen import doc_line, one_line
from agentcanvas.adapters.openai_agents.codegen import _camel, _lit, _py, model_settings
from agentcanvas.ir.contract import output_names
from agentcanvas.ir.models import Node, Workflow
from agentcanvas.registry import default_registry

CREWAI_VERSION = "1.15.39"
RUNTIME_IMPORT = "from agentcanvas.adapters.crewai import runtime as rt"
STEP_TYPES = {
    "core.set", "core.router", "core.join", "core.approval", "core.human_input", "core.script", "core.llm",
    "core.agent", "knowledge.search", "crewai.crew", "crewai.task", "core.decide",
}  # fmt: skip
RESOURCE_TYPES = {"model.chat", "tool.python", "mw.model_call_limit"}
PATH_STEPS = ("core.router", "core.decide")  # CrewAI `@router` methods: they return the path to follow
_TAKEN = {"rt", "Agent", "Crew", "Task", "Process", "Flow", "BaseModel", "Field", "Any", "Literal", "CANVAS",
          "annotations", "and_", "or_", "listen", "router", "start", "begin"}  # fmt: skip
# what a Flow already has (crewai 1.15): a step may not take these names
_FLOW_NAMES = set(
    "akickoff answer_from_history_turn append_agent_result append_assistant_message append_message ask astream "
    "build_agent_context build_router_context builtin_route_descriptions builtin_routes can_answer_from_history chat "
    "checkpoint checkpoint_completed_methods checkpoint_method_counts checkpoint_method_outputs checkpoint_state "
    "classify_intent construct conversation_messages conversation_start conversational conversational_config "
    "converse_turn copy defer_trace_finalization dict end_conversation entity_type execution_context "
    "extract_memories finalize_session_traces flow_definition flow_id fork from_checkpoint from_declaration from_orm "
    "from_pending handle_turn human_feedback_history initial_state input_provider internal_routes is_crewai_internal "
    "json kickoff kickoff_async last_human_feedback max_method_calls memory method_outputs name parse_file "
    "parse_obj parse_raw pending_feedback persistence plot recall receive_user_message reload remember resume "
    "resume_async route_conversation route_turn schema schema_json state stream stream_events stream_turn "
    "suppress_flow_events tracing update_forward_refs usage_metrics validate id".split()
)


class _Names:
    def __init__(self) -> None:
        self.used: set[str] = set(_TAKEN)

    def take(self, wanted: str, method: bool = False) -> str:
        name = wanted
        if (
            keyword.iskeyword(name) or hasattr(builtins, name) or name in self.used or name.startswith(("model_", "_"))
            or (method and name in _FLOW_NAMES)
        ):  # fmt: skip
            name = f"{wanted}_step"
        i = 2
        while name in self.used:
            name, i = f"{wanted}_step{i}", i + 1
        self.used.add(name)
        return name


def class_name(wf: Workflow) -> str:
    words = re.findall(r"[A-Za-z0-9]+", wf.name)
    base = "".join(w[:1].upper() + w[1:] for w in words) or "Canvas"
    return ("Flow" + base if base[0].isdigit() else base) + ("" if base.endswith("Flow") else "Flow")


def generate(wf: Workflow) -> str:
    names = _Names()
    fns = {n.id: names.take(n.name, method=True) for n in wf.nodes}
    helpers: list[str] = []
    methods: list[str] = []
    imports = {"Agent": False, "Crew": False}
    pydantic_names: set[str] = set()
    typing_names: set[str] = set()
    defined = {"begin"}  # methods a decorator can name directly; later ones go by their name as a string
    for n in wf.nodes:
        helper, body, models = _step(wf, n, fns[n.id], names)
        if helper:
            helpers.append(helper)
        if models:
            pydantic_names |= {"BaseModel"} | ({"Field"} if "Field(" in models else set())
            typing_names |= {t for t in ("Literal",) if re.search(rf"\b{t}\[", models)}
        imports["Agent"] |= "Agent(" in helper
        imports["Crew"] |= "Crew(" in helper
        methods.append(_method(wf, n, fns, body, defined))
        defined.add(fns[n.id])
    cls = class_name(wf)
    crewai_names = (["Agent"] if imports["Agent"] else []) + (["Crew", "Process", "Task"] if imports["Crew"] else [])
    flow_names = ["Flow", "listen", "start"]
    used = "\n".join(methods)
    flow_names += [f for f in ("and_", "or_", "router") if f"{f}(" in used]
    head = [
        f'"""{doc_line(wf.name)}: a CrewAI flow, generated by AgentCanvas.',
        "",
        f"`{cls}` has one method per block of the canvas, chained with CrewAI's @start, @listen and @router",
        "(each block's id is in its `@canvas-node` comment). `@rt.step` runs each method as the canvas does: it merges",
        "the fields a step returns into `self.state`, pauses for people and reports progress. `CANVAS` at the end says",
        "which fields the flow has and returns. Change the workflow on the canvas and generate it again rather than",
        "editing this file.",
        '"""',
        "",
        "from typing import " + ", ".join(sorted({"Any", *typing_names})),
        "",
        *([f"from crewai import {', '.join(sorted(crewai_names))}"] if crewai_names else []),
        f"from crewai.flow import {', '.join(sorted(flow_names))}",
        *([f"from pydantic import {', '.join(sorted(pydantic_names))}"] if pydantic_names else []),
        "",
        RUNTIME_IMPORT,
    ]
    doc = f'    """{doc_line(wf.description)}"""' if wf.description else ""
    begin = (
        "    @start()\n    def begin(self) -> None:\n"
        '        """The canvas\'s Start: the run\'s input is already in `self.state`."""'
    )
    klass = f"class {cls}(Flow[dict]):\n" + (doc + "\n\n" if doc else "") + "\n\n".join([begin, *methods])
    parts = [*helpers, klass, _canvas(wf, cls)]
    return "\n".join(head) + "\n\n\n" + "\n\n\n".join(parts) + "\n"


def _marker(n: Node) -> str:
    return f"# ── @canvas-node {one_line(n.id)} {json.dumps(one_line(n.name))} ({n.type}) ──"


def _triggers(wf: Workflow, n: Node, fns: dict[str, str], defined: set[str]) -> list[str]:
    routers = {x.id for x in wf.nodes if x.type in PATH_STEPS}
    out: list[str] = []
    for e in wf.flow_edges():
        if e.target.node != n.id:
            continue
        src = e.source.node
        if src == "START":
            t = "begin"
        elif src in routers:
            t = _py(f"{fns[src]}:{e.source.route}")
        elif src in fns:
            t = fns[src] if fns[src] in defined else _py(fns[src])
        else:
            continue
        if t not in out:
            out.append(t)
    return out


def _method(wf: Workflow, n: Node, fns: dict[str, str], body: str, defined: set[str]) -> str:
    triggers = _triggers(wf, n, fns, defined)
    if not triggers:
        when = ""  # nothing leads here: CrewAI never calls it
    elif len(triggers) == 1:
        when = triggers[0]
    else:
        when = ("and_" if n.type == "core.join" else "or_") + f"({', '.join(triggers)})"
    deco = "router" if n.type in PATH_STEPS else "listen"
    step_args = _py(n.id) + (", disabled=True" if default_registry().bypassed(n) else "")
    returns = "str" if n.type in PATH_STEPS else "dict[str, Any]"
    lines = [
        f"    {_marker(n)}",
        *([f"    @{deco}({when})"] if when else []),
        f"    @rt.step({step_args})",
        f"    async def {fns[n.id]}(self, run: rt.Run) -> {returns}:",
        *[f"        {line}" if line else "" for line in body.split("\n")],  # so strings in a body are one-line (`_py`)
    ]
    return "\n".join(lines)


def _llm(wf: Workflow, n: Node) -> str:
    """`run.llm("<model block>", temperature=...)` for the block's wired model."""
    models = wf.wired_into(n.id, "model")
    if not models:
        raise ValueError(f"'{n.name}' needs an AI model")
    m = models[0]
    settings = [f", {k}={_py(v)}" for k, v in model_settings(m).items()]
    return f"run.llm({_py(m.name)}{''.join(settings)})"


def _tools(wf: Workflow, n: Node) -> str:
    tools = [r.name for r in wf.wired_into(n.id, "tools")]
    return f"run.tools({_py(tools)})" if tools else ""


def _step(wf: Workflow, n: Node, fn: str, names: _Names) -> tuple[str, str, str]:
    """(module-level code the step needs, the method body, output classes) of one block."""
    c = n.config
    if n.type == "core.router":
        cases = [(case["when"], case["route"]) for case in c.get("cases", [])]
        default = c.get("default", "default")
        tail = f", default={_py(default)})" if default != "default" else ")"
        return "", f"return run.route({_py(cases)}{tail}", ""
    if n.type == "core.join":
        return "", '"""Waits for every branch before going on."""\nreturn {}', ""
    if n.type == "core.decide":
        prompt, choices = names.take(f"{n.name}_prompt".upper()), names.take(f"{n.name}_choices".upper())
        options = {case["route"]: case.get("description", "") for case in c.get("cases", [])}
        kw = [f"output={_py(c['output'])}"]
        kw += [f"default={_py(c['default'])}"] if c.get("default", "default") != "default" else []
        kw += [f"min_confidence={_py(c['min_confidence'])}"] if c.get("min_confidence") is not None else []
        return (
            f"{prompt} = {_lit(c['prompt'])}\n{choices} = {_py(options)}",
            f"reply = await run.text({_llm(wf, n)}, run.decide_prompt(rt.render({prompt}, self.state), {choices}))\n"
            f"return run.decide(reply, {choices}, {', '.join(kw)})",
            "",
        )
    if n.type == "core.set":
        items = ", ".join(
            f"{_py(k)}: " + (f"rt.render({_py(v)}, self.state)" if isinstance(v, str) else _py(v))
            for k, v in (c.get("values") or {}).items()
        )
        return "", f"return {{{items}}}", ""
    if n.type == "core.llm":
        prompt = names.take(f"{n.name}_prompt".upper())
        return (
            f"{prompt} = {_lit(c['prompt'])}",
            f"return {{{_py(c['output'])}: await run.text({_llm(wf, n)}, rt.render({prompt}, self.state))}}",
            "",
        )
    if n.type == "core.agent":
        return _agent(wf, n, names)
    if n.type == "core.approval":
        return "", f"return run.approval(show={_py(c['show'])}, output={_py(c['output'])})", ""
    if n.type == "core.human_input":
        schema = f", schema={_py(c['response_schema'])}" if c.get("response_schema") else ""
        return "", f"return run.ask(prompt={_py(c['prompt'])}, output={_py(c['output'])}{schema})", ""
    if n.type == "core.script":
        return "", f"return await run.script({_py(c['import_path'])})", ""
    if n.type == "knowledge.search":
        kw = [f"output={_py(c.get('output', 'passages'))}"]
        kw += [f"mode={_py(c['mode'])}"] if c.get("mode", "hybrid") != "hybrid" else []
        kw += [f"top_k={_py(c['top_k'])}"] if c.get("top_k", 5) != 5 else []
        kw += [f"min_score={_py(c['min_score'])}"] if c.get("min_score") else []
        kw += [f"sources_output={_py(c['sources_output'])}"] if c.get("sources_output") else []
        query = c.get("query", "{{ state.question }}")
        return "", (
            f"return await run.search(\n    {_py(c['knowledge_base'])}, rt.render({_py(query)}, self.state), "
            + ", ".join(kw) + ",\n)"
        ), ""  # fmt: skip
    if n.type in ("crewai.crew", "crewai.task"):
        return _crew(wf, n, names)
    raise NotImplementedError(f"a CrewAI flow cannot run '{n.type}' blocks")


def _max_iter(wf: Workflow, n: Node) -> str:
    for mw in wf.wired_into(n.id, "middleware"):
        if mw.type == "mw.model_call_limit":  # CrewAI's max_iter: model calls in one run of the agent
            return str(int(mw.config.get("run_limit", 20)))
    return "run.max_iter"


def _agent(wf: Workflow, n: Node, names: _Names) -> tuple[str, str, str]:
    c = n.config
    schema = c.get("response_format")
    structured = bool(schema) and designs.schema_problem(schema) is None
    instructions = names.take(f"{n.name}_instructions".upper())
    builder = names.take(f"{n.name}_agent")
    answer = names.take(f"{_camel(n.name)}Answer") if structured else ""
    models = designs.output_models(schema, answer) if structured and schema else ""
    tools = _tools(wf, n)
    args = [
        f"role={_py((n.label or n.name).replace('_', ' '))}",
        f'goal={instructions} or "Help the user."',
        'backstory="Designed on AgentCanvas."',
        f"llm={_llm(wf, n)}",
        *([f"tools={tools}"] if tools else []),
        f"max_iter={_max_iter(wf, n)}",
        "verbose=False",
    ]
    body = ",\n".join(f"        {a}" for a in args)
    helper = (
        f"{instructions} = {_lit(c.get('system_prompt') or '')}\n\n\n"
        + (f"{models}\n\n\n" if models else "")
        + f"def {builder}(run: rt.Run) -> Agent:\n    return Agent(\n{body},\n    )"
    )
    fmt = f", response_format={answer}" if structured else ""
    return helper, f'return await run.agent({builder}(run), self.state.get("messages"){fmt})', models


def _crew(wf: Workflow, n: Node, names: _Names) -> tuple[str, str, str]:
    """A crew block (agents with tasks) or a task block (one agent, one task) as a `Crew` builder."""
    c = n.config
    builder = names.take(f"{n.name}_crew")
    llm, tools = _llm(wf, n), _tools(wf, n)
    if n.type == "crewai.task":
        agents = [{"name": "agent", "role": c.get("role") or n.name, "goal": c.get("goal") or "",
                   "backstory": c.get("backstory") or ""}]  # fmt: skip
        tasks = [{"description": c.get("description") or "", "expected_output": c.get("expected_output") or "",
                  "agent": "agent", "guardrail": c.get("guardrail") or ""}]  # fmt: skip
        process = "sequential"
    else:
        agents, tasks, process = list(c.get("agents") or []), list(c.get("tasks") or []), c.get("process")
    local = _Names()
    local.used |= {"llm", "run", "tools"}
    vars_ = {a.get("name") or f"agent{i + 1}": local.take(_ident(a.get("name") or f"agent{i + 1}"))
             for i, a in enumerate(agents)}  # fmt: skip
    lines = [f"def {builder}(run: rt.Run, state: dict[str, Any]) -> Crew:", f"    llm = {llm}"]
    if tools:
        lines.append(f"    tools = {tools}")
    for a in agents:
        var = vars_[a.get("name") or f"agent{agents.index(a) + 1}"]
        lines += [
            f"    {var} = Agent(",
            f"        role={_py(a.get('role') or a.get('name') or 'agent')},",
            f"        goal={_render(a.get('goal') or 'Do your task well.')},",
            f"        backstory={_render(a.get('backstory') or 'Designed on AgentCanvas.')},",
            "        llm=llm,",
            *(["        tools=tools,"] if tools else []),
            "        max_iter=run.max_iter,",
            "        verbose=False,",
            "    )",
        ]
    lines.append("    return Crew(")
    lines.append(f"        agents=[{', '.join(vars_.values())}],")
    lines.append("        tasks=[")
    for t in tasks:
        kw = [f"description={_render(t.get('description') or '')}",
              f"expected_output={_render(t.get('expected_output') or 'A clear answer.')}"]  # fmt: skip
        if t.get("agent") in vars_:
            kw.append(f"agent={vars_[t['agent']]}")
        elif process != "hierarchical" and vars_:
            kw.append(f"agent={next(iter(vars_.values()))}")
        if t.get("guardrail"):
            kw.append(f"guardrail={_py(t['guardrail'])}")
            kw.append(f"guardrail_max_retries={int(c.get('guardrail_max_retries', 3))}")
        lines.append("            Task(")
        lines += [f"                {k}," for k in kw]
        lines.append("            ),")
    lines.append("        ],")
    if process == "hierarchical":
        lines += ["        process=Process.hierarchical,", "        manager_llm=llm,"]
    else:
        lines.append("        process=Process.sequential,")
    lines += ["        verbose=False,", "    )"]
    body = f"return await run.crew({builder}(run, self.state), output={_py(c.get('output', 'result'))})"
    return "\n".join(lines), body, ""


def _render(text: str) -> str:
    """A crew text: fields filled in from the flow's state when it has `{{ ... }}`, else a plain string."""
    return f"rt.render({_lit(text)}, state)" if "{{" in text or "{%" in text else _lit(text)


def _ident(name: str) -> str:
    out = re.sub(r"\W+", "_", name.strip().lower()).strip("_") or "agent"
    return f"agent_{out}" if out[0].isdigit() else out


def _canvas(wf: Workflow, cls: str) -> str:
    channels = [f"        {_py(ch.name)}: ({_py(ch.reducer)}, {_py(ch.default)})," for ch in wf.state.channels]
    steps = [f"        {_py(n.id)}: {_py(n.name)}," for n in wf.nodes]
    wanted = output_names(wf)
    return "\n".join(
        [
            "CANVAS = rt.Canvas(",
            f"    flow={cls},",
            f"    name={_py(wf.name)},",
            "    steps={",
            *steps,
            "    },",
            "    channels={",
            *channels,
            "    },",
            f"    outputs={_py(wanted)},",
            f"    recursion_limit={wf.settings.recursion_limit},",
            ")",
        ]
    )
