"""Flowise flows (the JSON Flowise's Export Chatflow / Export Agentflow downloads) as workflows (S31).

Flowise has two kinds of flow. A chatflow is a LangChain graph: a chain or agent at the end (LLM Chain, Conversation
Chain, Tool Agent...) with a chat model, a prompt and tools plugged into it. Each chain or agent becomes a step, the
chat model its model block, the prompt its instructions (`{question}` reads the chat) and request tools HTTP tool
blocks. An agentflow (Agentflow v2) is already a sequence of steps: LLM, Agent, Condition, Human Input and Direct
Reply map onto AI steps, agents, paths, approvals and Set values; anything else becomes a placeholder.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agentcanvas.importers import (
    AGENT_REPLY,
    CHAT_INPUT,
    Builder,
    ImportError_,
    brace_template,
    compare,
    model_id,
    obj,
    position,
    route_name,
)

CHAT_MODELS = {
    "chatOpenAI": "openai", "chatOpenAICustom": "openai_compatible", "azureChatOpenAI": "azure",
    "chatAnthropic": "anthropic", "chatGoogleGenerativeAI": "google", "chatOllama": "ollama",
    "chatMistralAI": "mistral", "groqChat": "groq", "chatGroq": "groq", "chatDeepseek": "deepseek",
    "chatXAI": "xai", "chatLocalAI": "openai_compatible", "chatOpenRouter": "openai_compatible",
}  # fmt: skip
LLM_CHAINS = {"llmChain", "conversationChain"}
AGENTS = {"toolAgent", "conversationalAgent", "openAIFunctionAgent", "openAIToolAgent", "reactAgentChat",
          "mrklAgentChat", "anthropicAgent"}  # fmt: skip
PROMPTS = {"promptTemplate", "chatPromptTemplate", "fewShotPromptTemplate"}
REQUEST_TOOLS = {"requestsGet": "GET", "requestsPost": "POST"}
MEMORY = {"bufferMemory", "bufferWindowMemory", "conversationSummaryMemory", "redisBackedChatMemory",
          "zepMemory", "motorheadMemory", "upstashRedisBackedChatMemory", "agentMemory"}  # fmt: skip
_REF = re.compile(r"^\{\{\s*([A-Za-z0-9_\-]+)\.data\.instance\s*\}\}$")


def read(doc: dict[str, Any], *, wf_id: str, name: str | None = None) -> Builder:
    nodes = [n for n in doc.get("nodes") or [] if isinstance(n, dict) and isinstance(n.get("data"), dict)]
    if not nodes:
        raise ImportError_("the Flowise file has no nodes")
    title = name or doc.get("name") or "Imported Flowise flow"
    b = Builder("flowise", wf_id, title, str(doc.get("description") or ""))
    if any(str(n["data"].get("name", "")).endswith("Agentflow") for n in nodes):
        _agentflow(b, nodes, [e for e in doc.get("edges") or [] if isinstance(e, dict)])
    else:
        _chatflow(b, nodes, [e for e in doc.get("edges") or [] if isinstance(e, dict)])
    return b


def _inputs(n: dict[str, Any]) -> dict[str, Any]:
    found = n["data"].get("inputs")
    return found if isinstance(found, dict) else {}


def _label(n: dict[str, Any]) -> str:
    return str(n["data"].get("label") or n["data"].get("name") or n.get("id") or "step")


def _pos(n: dict[str, Any]) -> dict[str, float] | None:
    p = n.get("position") or {}
    return position(p.get("x"), p.get("y"))


# ------------------------------------------------------------------ chatflows ----
def _chatflow(b: Builder, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    by_id = {str(n["id"]): n for n in nodes if n.get("id")}

    def ref(value: Any) -> dict[str, Any] | None:
        m = _REF.match(str(value or "").strip())
        return by_id.get(m.group(1)) if m else None

    def refs(value: Any) -> list[dict[str, Any]]:
        values = value if isinstance(value, list) else [value]
        return [n for n in (ref(v) for v in values) if n is not None]

    def model_of(n: dict[str, Any]) -> str | None:
        for key in ("model", "llm", "languageModel"):
            found = ref(_inputs(n).get(key))
            if found is not None:
                return _chat_model(b, found)
        return None

    steps = [n for n in nodes if n["data"].get("name") in LLM_CHAINS | AGENTS]
    if not steps:
        kinds = sorted({str(n["data"].get("name")) for n in nodes})
        raise ImportError_(f"the Flowise chatflow has no chain or agent AgentCanvas can import (found {kinds})")
    chat = b.use_chat()
    for n in steps:
        kind, label, inputs = n["data"]["name"], _label(n), _inputs(n)
        model = model_of(n)
        if model is None:
            model = b.model(model_id("openai", None, b))
            b.note(f"'{label}' had no chat model plugged in; it uses a default model.")
        if kind in AGENTS:
            tools = []
            for t in refs(inputs.get("tools")):
                tool = _chat_tool(b, t)
                if tool:
                    tools.append(tool)
            system = str(inputs.get("systemMessage") or inputs.get("systemMessagePrompt") or "")
            b.step(str(n["id"]), "core.agent", label, {"system_prompt": system}, label=label, model=model,
                   tools=tools, position=_pos(n))  # fmt: skip
            b.output("messages")
            continue
        prompt_node = ref(inputs.get("prompt"))
        if prompt_node is not None:
            prompt = _prompt(b, prompt_node, chat)
        elif kind == "conversationChain":
            system = str(inputs.get("systemMessagePrompt") or "").strip()
            prompt = "\n\n".join(p for p in (system, chat) if p)
        else:
            prompt = chat
        out = b.channel(f"{label}_reply", io="out")
        b.step(str(n["id"]), "core.llm", label, {"prompt": prompt, "output": out}, label=label, model=model,
               position=_pos(n))  # fmt: skip
    for e in edges:  # a chain whose input is another chain's answer runs after it
        src, dst = str(e.get("source")), str(e.get("target"))
        if src in b.steps and dst in b.steps:
            b.after(dst, src)
    for key, st in b.steps.items():  # the last steps answer the chat
        if not any(k == key for s in b.steps.values() for k, _ in s.after) and st.type == "core.llm":
            b.output(st.config["output"])
    for n in nodes:
        kind = str(n["data"].get("name"))
        if kind in MEMORY:
            b.note(f"'{_label(n)}' (chat memory) was left out: a conversation keeps its messages on its own.")
        elif kind not in CHAT_MODELS | REQUEST_TOOLS and kind not in PROMPTS | LLM_CHAINS | AGENTS:
            if any(n is t for s in steps for t in refs(_inputs(s).get("tools"))):
                continue  # a tool: already in the report
            used = any(n is t for s in steps for v in _inputs(s).values() for t in refs(v))
            what = "plugged into a step" if used else "not connected to a step"
            b.note(f"'{_label(n)}' ({kind}, {what}) has no counterpart in AgentCanvas and was left out.")


def _chat_model(b: Builder, n: dict[str, Any]) -> str:
    kind, inputs = str(n["data"].get("name")), _inputs(n)
    provider = CHAT_MODELS.get(kind)
    if provider is None:
        b.note(f"The chat model '{_label(n)}' ({kind}) became an OpenAI-compatible model; check it.")
        provider = "openai_compatible"
    name = inputs.get("modelName") or inputs.get("model") or inputs.get("customModelName")
    base = inputs.get("basePath") or inputs.get("baseUrl") or inputs.get("baseURL")
    return b.model(model_id(provider, name, b), temperature=_number(inputs.get("temperature")),
                   max_tokens=_int(inputs.get("maxTokens")), base_url=str(base) if base else None)  # fmt: skip


def _chat_tool(b: Builder, n: dict[str, Any]) -> str | None:
    kind, inputs, label = str(n["data"].get("name")), _inputs(n), _label(n)
    if kind in REQUEST_TOOLS:
        found = b.http_tool(label, str(inputs.get("url") or ""), method=REQUEST_TOOLS[kind],
                            description=str(inputs.get("description") or ""))  # fmt: skip
        if found:
            return found
    b.note(f"Tool '{label}' ({kind}) was not imported; add a tool block for it on the canvas.")
    return None


def _prompt(b: Builder, n: dict[str, Any], chat: str) -> str:
    inputs = _inputs(n)
    values = inputs.get("promptValues")
    if isinstance(values, str):
        try:
            values = json.loads(values) if values.strip() else {}
        except ValueError:
            values = {}
    values = values if isinstance(values, dict) else {}

    def var(name: str) -> str | None:
        given = str(values.get(name) or "").strip()
        if name in ("question", "input") and not given:
            return chat
        if given in ("{{question}}", "{{ question }}", "{{chat_history}}"):
            return chat
        m = _REF.match(given)
        if m and m.group(1) in b.steps and b.steps[m.group(1)].type == "core.llm":
            return "{{ state." + b.steps[m.group(1)].config["output"] + " }}"
        if given and "{{" not in given:
            return given
        return "{{ state." + b.channel(name, io="in") + " }}"

    if str(n["data"].get("name")) == "chatPromptTemplate":
        parts = [str(inputs.get("systemMessagePrompt") or ""), str(inputs.get("humanMessagePrompt") or "")]
        text = "\n\n".join(p for p in parts if p.strip())
    else:
        text = str(inputs.get("template") or "")
    return brace_template(text, var) or chat


def _number(v: Any) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ agentflows (v2) ----
_V2_VAR = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _agentflow(b: Builder, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
    by_id = {str(n["id"]): n for n in nodes if n.get("id") and n.get("type") != "stickyNote"}

    def text(value: Any) -> str:
        """An agentflow text (`{{ question }}`, `{{ $flow.state.x }}`, `{{ llmAgentflow_0 }}`) as a template."""

        def sub(m: re.Match[str]) -> str:
            v = m.group(1).strip()
            if v in ("question", "$flow.input"):
                return b.use_chat()
            if v.startswith("$flow.state."):
                return "{{ state." + b.channel(v.removeprefix("$flow.state."), io="inout") + " }}"
            if v in b.steps:
                return _answer(b, v)
            if v.startswith("$vars.") or v.startswith("$flow."):
                b.note(f"The variable '{v}' has no counterpart in AgentCanvas; it was left as text.")
            return v

        return _V2_VAR.sub(sub, _plain(value))

    order = [n for n in nodes if str(n.get("id")) in by_id and n["data"].get("name") != "stickyNoteAgentflow"]
    for n in order:  # keys and outputs first: later texts refer to earlier steps by id
        nid, kind, label = str(n["id"]), str(n["data"].get("name")), _label(n)
        inputs = _inputs(n)
        if kind == "startAgentflow":
            if inputs.get("startInputType") == "formInput":
                for f in inputs.get("formInputTypes") or []:
                    if isinstance(f, dict) and f.get("name"):
                        b.channel(str(f["name"]), io="in")
            else:
                b.use_chat()
            for s in inputs.get("startState") or []:
                if isinstance(s, dict) and s.get("key"):
                    b.channel(str(s["key"]))
            continue
        if kind == "llmAgentflow":
            b.step(nid, "core.llm", label, {"prompt": "", "output": b.channel(f"{label}_reply", io="out")},
                   label=label, position=_pos(n))  # fmt: skip
        elif kind == "agentAgentflow":
            b.step(nid, "core.agent", label, {"system_prompt": ""}, label=label, position=_pos(n))
        elif kind == "conditionAgentflow":
            b.step(nid, "core.router", label, {"cases": [], "default": "else"}, label=label, position=_pos(n))
        elif kind == "humanInputAgentflow":
            out = b.channel(f"{label}_approved", io="out", type_={"type": "boolean"})
            b.step(nid, "core.approval", label, {"show": "", "output": out}, label=label, position=_pos(n))
            p = _pos(n)
            b.step(f"{nid}__check", "core.router", f"{label}_answer", {
                "cases": [{"when": f"state.{out} == true", "route": "proceed"}], "default": "reject"},
                label=f"{label}: approved?", position=p and {"x": p["x"] + 200, "y": p["y"]})  # fmt: skip
            b.after(f"{nid}__check", nid)
        elif kind == "directReplyAgentflow":
            out = b.channel(f"{label}_message", io="out")
            b.step(nid, "core.set", label, {"values": {out: ""}}, label=label, position=_pos(n))
            b.output(out)
        else:
            b.placeholder(nid, label, f"the Flowise node '{kind}'", position=_pos(n), label=label)
    for n in order:
        nid, kind, label, inputs = str(n["id"]), str(n["data"].get("name")), _label(n), _inputs(n)
        st = b.steps.get(nid)
        if st is None:
            continue
        if kind in ("llmAgentflow", "agentAgentflow"):
            prefix = "llm" if kind == "llmAgentflow" else "agent"
            conf = obj(inputs.get(f"{prefix}ModelConfig"))
            provider = CHAT_MODELS.get(str(inputs.get(f"{prefix}Model") or "chatOpenAI"), "openai_compatible")
            st.model = b.model(model_id(provider, conf.get("modelName") or conf.get("model"), b),
                               temperature=_number(conf.get("temperature")))  # fmt: skip
            msgs = [m for m in inputs.get(f"{prefix}Messages") or [] if isinstance(m, dict)]
            system = "\n\n".join(text(m.get("content")) for m in msgs if m.get("role") == "system")
            user = "\n\n".join(text(m.get("content")) for m in msgs if m.get("role") != "system")
            if kind == "llmAgentflow":
                user = user or text(inputs.get("llmUserMessage")) or b.use_chat()
                st.config["prompt"] = "\n\n".join(p for p in (system, user) if p)
                if _plain(inputs.get("llmUpdateState")):
                    b.note(f"'{label}' updates the flow state in Flowise; here its answer is in "
                           f"'{st.config['output']}'.")  # fmt: skip
            else:
                st.config["system_prompt"] = system
                b.use_chat()
                b.output("messages")
                for t in inputs.get("agentTools") or []:
                    tool = t.get("agentSelectedTool") if isinstance(t, dict) else None
                    b.note(f"Agent '{label}': the tool '{tool}' was not imported; add a tool block for it.")
        elif kind == "conditionAgentflow":
            cond = inputs.get("conditions")
            rows = cond if isinstance(cond, list) else []
            for i, c in enumerate(r for r in rows if isinstance(r, dict)):
                left = text(c.get("value1"))
                field = _state_field(left)
                rule = compare(f"state.{field}", str(c.get("operation") or "equal"), _plain(c.get("value2")))
                if field is None or rule is None:
                    b.note(f"Condition '{label}', branch {i}: '{left} {c.get('operation')}' could not be imported; "
                           "write its rule on the path step.")  # fmt: skip
                    rule = "false"
                st.config["cases"].append({"when": rule, "route": route_name(None, i)})
        elif kind == "humanInputAgentflow":
            shows = [s for s in b.steps.values() if s.type == "core.llm"]
            st.config["show"] = shows[-1].config["output"] if shows else (b.outputs or ["messages"])[0]
        elif kind == "directReplyAgentflow":
            out = next(iter(st.config["values"]))
            st.config["values"][out] = text(inputs.get("directReplyMessage")) or AGENT_REPLY
    for e in edges:
        src, dst = str(e.get("source")), str(e.get("target"))
        if dst not in b.steps:
            continue
        if src not in b.steps:
            b.after(dst, "START")
            continue
        kind, route = by_id[src]["data"].get("name"), None
        handle = str(e.get("sourceHandle") or "")
        index = handle.rsplit("-", 1)[-1] if "-output-" in handle else ""
        if kind == "conditionAgentflow":
            cases = b.steps[src].config["cases"]
            route = cases[int(index)]["route"] if index.isdigit() and int(index) < len(cases) else "else"
        elif kind == "humanInputAgentflow":  # Flowise: output 0 proceeds, output 1 rejects
            src, route = f"{src}__check", "reject" if index == "1" else "proceed"
        b.after(dst, src, route)
    for st in b.steps.values():
        if st.type == "core.llm" and not any(k == st.key for s in b.steps.values() for k, _ in s.after):
            b.output(st.config["output"])
    if b.chat:
        for st in b.steps.values():
            if st.type == "core.llm" and not st.config["prompt"]:
                st.config["prompt"] = CHAT_INPUT


def _answer(b: Builder, key: str) -> str:
    st = b.steps[key]
    if st.type == "core.llm":
        return "{{ state." + st.config["output"] + " }}"
    if st.type == "core.set":
        return "{{ state." + next(iter(st.config["values"])) + " }}"
    return AGENT_REPLY


def _state_field(text: str) -> str | None:
    t = text.strip()
    if t.startswith("{{ state.") and t.endswith(" }}") and t.count("{{") == 1:
        found = t[len("{{ state.") : -3].strip()
        return None if found.startswith("messages[") else found
    return None


_TAGS = re.compile(r"<[^>]+>")


def _plain(value: Any) -> str:
    """Agentflow text fields hold HTML from the rich editor (`<p>...</p>`, variable spans): plain text."""
    if value is None or isinstance(value, bool | dict | list):
        return ""
    s = str(value)
    if "<" in s and ">" in s:
        s = re.sub(r"</p>\s*<p>|<br\s*/?>", "\n", s)
        s = re.sub(r'<span[^>]*data-type="mention"[^>]*data-id="([^"]+)"[^>]*>.*?</span>', r"{{ \1 }}", s)
        s = _TAGS.sub("", s)
        s = s.replace("&nbsp;", " ").replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return s.strip()
