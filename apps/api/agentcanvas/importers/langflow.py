"""Langflow flows (the JSON Langflow's Export downloads) as workflows (S31).

A Langflow flow is a data-flow graph of components: Chat Input feeds a Prompt, the Prompt feeds a model, the model
feeds Chat Output. The importer keeps what does work as steps (models that answer, agents, If-Else routers) and
folds the rest into them: inputs become the chat or input fields, prompts become the AI step's instructions with
`{{ state.<field> }}` in place of `{variables}`, model components wired into an agent become its model block, and
Chat Output marks the field the workflow returns. Components with no counterpart become placeholders, listed in
the report.
"""

from __future__ import annotations

import json
from typing import Any

from agentcanvas.importers import (
    AGENT_REPLY,
    Builder,
    ImportError_,
    brace_template,
    compare,
    model_id,
    obj,
    position,
)

INPUTS = {"ChatInput", "TextInput"}
OUTPUTS = {"ChatOutput", "TextOutput"}
PROMPTS = {"Prompt", "PromptTemplate", "Prompt Template"}
ROUTERS = {"ConditionalRouter", "If-Else"}
AGENTS = {"Agent", "ToolCallingAgent", "OpenAIToolsAgent"}
MODEL_PROVIDERS = {
    "OpenAIModel": "openai", "AnthropicModel": "anthropic", "GoogleGenerativeAIModel": "google",
    "OllamaModel": "ollama", "GroqModel": "groq", "MistralModel": "mistral", "AzureOpenAIModel": "azure",
    "DeepSeekModelComponent": "deepseek", "XAIModel": "xai", "LMStudioModel": "lmstudio",
}  # fmt: skip
MEMORY = {"Memory", "MessageHistory", "ChatMemory", "StoreMessage"}
HTTP_TOOLS = {"APIRequest"}
TOOL_FIELDS = {"tools"}
MODEL_FIELDS = {"agent_llm", "llm", "model", "language_model"}
KNOWLEDGE = "a knowledge base (the Knowledge page) and a Search knowledge step"
WEB = "an app connector (MCP) for web search, or an HTTP tool for one site"
# components with no counterpart here: what to use instead (Langflow 1.13 made Chroma DB and Local DB legacy and
# removed ALTK: https://docs.langflow.org/release-notes)
INSTEAD = {
    "Knowledge": "a Search knowledge step on a knowledge base (the Knowledge page)",
    "KnowledgeRetrieval": "a Search knowledge step on a knowledge base (the Knowledge page)",
    "KnowledgeIngestion": "uploading the documents to a knowledge base (the Knowledge page)",
    "File": f"{KNOWLEDGE}, or a Run Python code step that reads the file from the Workspace",
    "SplitText": "a knowledge base (it splits documents itself)",
    "Chroma": f"{KNOWLEDGE} (Chroma DB is legacy in Langflow 1.13)",
    "LocalDB": f"{KNOWLEDGE} (Local DB is legacy in Langflow 1.13)",
    "ALTK": "an agent (Langflow 1.13 removed ALTK for its Agent)",
    "LoopComponent": "a Repeat for each item step",
    "BatchRunComponent": "a Repeat for each item step around an Ask AI step",
    "ParserComponent": "a Set values step with a {{ state.<field> }} template",
    "parser": "a Set values step with a {{ state.<field> }} template",
    "StructuredOutput": "an Ask AI step that answers in the fields you need",
    "MemoryBase": "Memory tools on the agent",
    "UnifiedWebSearch": WEB, "WebSearch": WEB, "URLComponent": WEB, "URL": WEB,
    "CalculatorComponent": "a Python tool", "PythonREPLComponent": "a Python tool",
    "SQLComponent": "an app connector (MCP) for the database",
}  # fmt: skip
VECTOR_STORES = {"AstraDB", "Pinecone", "Qdrant", "PGVector", "Weaviate", "Milvus", "MongoDBAtlasVector", "Redis",
                 "Elasticsearch", "OpenSearch", "Supabase", "Cassandra", "Couchbase", "Upstash", "Vectara"}  # fmt: skip


def instead(kind: str) -> str | None:
    """What to use here for a Langflow component that has no counterpart (None when there is no suggestion)."""
    if kind in INSTEAD:
        return INSTEAD[kind]
    if kind in VECTOR_STORES or kind.endswith(("VectorStore", "VectorStoreComponent")):
        return KNOWLEDGE
    return None


def picked(value: Any) -> tuple[str, str] | None:
    """(provider, model name) from a Langflow 1.13 model selector: a list of the picked options (or one)."""
    first = value[0] if isinstance(value, list) and value else value
    if isinstance(first, dict) and first.get("name"):
        return str(first.get("provider") or "openai"), str(first["name"])
    return None


def _handle(raw: Any) -> dict[str, Any]:
    """An edge handle: a dict in recent exports, a JSON string with œ for quotes in older ones."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.startswith("{"):
        try:
            found = json.loads(raw.replace("œ", '"'))
        except ValueError:
            return {}
        return found if isinstance(found, dict) else {}
    return {}


class _Flow:
    def __init__(self, doc: dict[str, Any], b: Builder):
        data = obj(doc.get("data")) or doc
        self.b = b
        self.nodes: dict[str, dict[str, Any]] = {}
        for n in data.get("nodes") or []:
            if not isinstance(n, dict) or n.get("type") == "noteNode" or not n.get("id"):
                continue
            self.nodes[str(n["id"])] = n
        self.edges: list[tuple[str, str, str, str]] = []  # (source, output name, target, input field)
        for e in data.get("edges") or []:
            if (
                not isinstance(e, dict)
                or str(e.get("source")) not in self.nodes
                or str(e.get("target")) not in self.nodes
            ):
                continue
            ed = obj(e.get("data"))
            sh = _handle(ed.get("sourceHandle")) or _handle(e.get("sourceHandle"))
            th = _handle(ed.get("targetHandle")) or _handle(e.get("targetHandle"))
            self.edges.append((str(e["source"]), str(sh.get("name") or ""), str(e["target"]),
                               str(th.get("fieldName") or "")))  # fmt: skip
        self._expr: dict[str, str | None] = {}
        self.step_of: dict[str, str] = {}  # node id -> step key
        self.building: set[str] = set()  # steps being made (a loop of steps reads nothing from the one it is in)

    # ---- reading components ----
    def kind(self, nid: str) -> str:
        d = self.nodes[nid].get("data") or {}
        return str(d.get("type") or (d.get("node") or {}).get("key") or "")

    def label(self, nid: str) -> str:
        d = self.nodes[nid].get("data") or {}
        return str((d.get("node") or {}).get("display_name") or self.kind(nid) or nid)

    def value(self, nid: str, name: str) -> Any:
        field = ((self.nodes[nid].get("data") or {}).get("node") or {}).get("template", {}).get(name)
        return field.get("value") if isinstance(field, dict) else None

    def pos(self, nid: str) -> dict[str, float] | None:
        p = self.nodes[nid].get("position") or {}
        return position(p.get("x"), p.get("y"))

    def into(self, nid: str, field: str | None = None) -> list[tuple[str, str, str]]:
        """(source, output name, field) of the edges into `nid` (into one field)."""
        return [(s, o, f) for s, o, t, f in self.edges if t == nid and (field is None or f == field)]

    def out_of(self, nid: str) -> list[tuple[str, str, str]]:
        return [(t, o, f) for s, o, t, f in self.edges if s == nid]

    def is_model(self, nid: str) -> bool:
        k = self.kind(nid)
        return k in MODEL_PROVIDERS or k == "LanguageModelComponent" or k.endswith("Model")

    def model_block(self, nid: str) -> str:
        k = self.kind(nid)
        provider = MODEL_PROVIDERS.get(k) or str(self.value(nid, "provider") or "openai")
        name = self.value(nid, "model_name") or self.value(nid, "model")
        chosen = picked(self.value(nid, "model"))  # Langflow 1.13: one "model" selector with its provider
        if chosen:
            provider, name = (MODEL_PROVIDERS.get(k) or chosen[0]), chosen[1]
        elif not isinstance(name, str):
            name = None
        base = self.value(nid, "base_url") or self.value(nid, "openai_api_base") or None
        return self.b.model(model_id(provider, name, self.b), temperature=self.value(nid, "temperature"),
                            max_tokens=self.value(nid, "max_tokens"), base_url=base)  # fmt: skip

    # ---- what each component is ----
    def is_step(self, nid: str) -> bool:
        k = self.kind(nid)
        if k in INPUTS or k in OUTPUTS or k in PROMPTS or k in MEMORY:
            return False
        if k in AGENTS or k in ROUTERS:
            return True
        outs = self.out_of(nid)
        if self.is_model(nid):  # a model answers when its text goes somewhere; wired into an agent it is a block
            return any(f not in MODEL_FIELDS for _, o, f in outs if o != "model_output") or not outs
        if outs and all(f in TOOL_FIELDS for _, _, f in outs):
            return False  # a tool of an agent
        return bool(outs or self.into(nid))

    def expr(self, nid: str) -> str | None:
        """How a later step reads what `nid` produces (a `{{ ... }}` text), None when it produces nothing usable."""
        if nid in self._expr:
            return self._expr[nid]
        self._expr[nid] = None  # a cycle reads nothing
        k = self.kind(nid)
        found: str | None = None
        if k == "ChatInput":
            found = self.b.use_chat()
        elif k == "TextInput":
            found = "{{ state." + self.b.channel(self.label(nid), io="in") + " }}"
        elif k in PROMPTS:
            found = self.prompt_text(nid)
        elif k in ROUTERS:
            found = self.field_text(nid, "input_text")
        elif k in AGENTS:
            found = AGENT_REPLY
        elif nid in self.step_of:
            self.build(nid)
            st = self.b.steps.get(self.step_of[nid])
            if st is None:  # a loop of steps: the one being made reads nothing from itself
                pass
            elif st.type == "core.llm":
                found = "{{ state." + st.config["output"] + " }}"
            elif st.type == "core.set":
                found = "{{ state." + next(iter(st.config["values"])) + " }}"
        self._expr[nid] = found
        return found

    def field_text(self, nid: str, field: str) -> str | None:
        """A component input as text: what is wired into it, else the value typed in it."""
        for src, _, _ in self.into(nid, field):
            found = self.expr(src)
            if found is not None:
                return found
        value = self.value(nid, field)
        return value if isinstance(value, str) and value.strip() else None

    def prompt_text(self, nid: str) -> str:
        def var(name: str) -> str | None:
            wired = self.into(nid, name)
            if wired:
                return self.expr(wired[0][0])
            typed = self.value(nid, name)
            if isinstance(typed, str) and typed:
                return typed
            return "{{ state." + self.b.channel(name, io="in") + " }}"

        return brace_template(self.value(nid, "template") or "", var)

    def build(self, nid: str) -> None:
        """Make the step for `nid` now, when it is not made yet: a step may read one that comes later in the file."""
        if self.step_of[nid] not in self.b.steps and nid not in self.building:
            self.building.add(nid)
            try:
                _step(self, nid)
            finally:
                self.building.discard(nid)

    # ---- order ----
    def earlier(self, nid: str, seen: set[str] | None = None) -> list[tuple[str, str | None]]:
        """The steps `nid` waits for, looking through prompts and inputs: (step key or START, route)."""
        seen = seen if seen is not None else set()
        out: list[tuple[str, str | None]] = []
        for src, output, field in self.into(nid):
            if src in seen or field in TOOL_FIELDS or (field in MODEL_FIELDS and self.is_model(src)):
                continue
            if self.kind(nid) == "LoopComponent" and field != "data":  # the loop body handing an item back
                self.b.note(f"The loop back from '{self.label(src)}' into '{self.label(nid)}' was left out; a "
                            "Repeat for each item step runs steps once per item.")  # fmt: skip
                continue
            seen.add(src)
            if src in self.step_of:
                route = None
                if self.kind(src) in ROUTERS:
                    route = "true" if output.startswith("true") else "false"
                out.append((self.step_of[src], route))
            else:
                out += self.earlier(src, seen)
        return out


def read(doc: dict[str, Any], *, wf_id: str, name: str | None = None) -> Builder:
    b = Builder("langflow", wf_id, name or doc.get("name") or "Imported Langflow flow", doc.get("description") or "")
    f = _Flow(doc, b)
    if not f.nodes:
        raise ImportError_("the Langflow file has no components")
    steps = [nid for nid in f.nodes if f.is_step(nid)]
    for nid in steps:  # keys first, so steps can refer to each other in any order
        f.step_of[nid] = nid
    for nid in steps:
        f.build(nid)
    for nid in steps:
        for key, route in f.earlier(nid):
            b.after(nid, key, route)
    for nid in f.nodes:
        k = f.kind(nid)
        if k in OUTPUTS:
            for src, _, _ in f.into(nid, "input_value"):
                st = b.steps.get(src)
                if st is not None and st.type == "core.llm":
                    b.output(st.config["output"])
                elif st is not None and st.type == "core.agent":
                    b.output("messages")
                elif st is not None and st.type == "core.set":  # a placeholder: its field, once it is replaced
                    b.output(next(iter(st.config["values"])))
                elif src in f.nodes and f.kind(src) in ROUTERS:
                    continue
                else:
                    b.note(f"'{f.label(nid)}' shows '{f.label(src)}', which is not a step; it was left out.")
        elif k in MEMORY:
            b.note(f"'{f.label(nid)}' (chat memory) was left out: a conversation keeps its messages on its own.")
    return b


def _step(f: _Flow, nid: str) -> None:
    b, k, label = f.b, f.kind(nid), f.label(nid)
    pos = f.pos(nid)
    if k in AGENTS:
        wired = [s for s, o, fld in f.into(nid) if fld in MODEL_FIELDS and f.is_model(s)]
        chosen = picked(f.value(nid, "model"))
        if wired:
            model = f.model_block(wired[0])
        elif chosen:
            model = b.model(model_id(chosen[0], chosen[1], b), temperature=f.value(nid, "temperature"))
        else:
            provider = str(f.value(nid, "agent_llm") or "openai")
            model = b.model(model_id(provider, f.value(nid, "model_name"), b),
                            temperature=f.value(nid, "temperature"))  # fmt: skip
        tools = []
        for src, _, _ in f.into(nid, "tools"):
            tool = _tool(f, src)
            if tool:
                tools.append(tool)
        b.step(nid, "core.agent", label, {"system_prompt": str(f.value(nid, "system_prompt") or "")}, label=label,
               model=model, tools=tools, position=pos)  # fmt: skip
        source = f.into(nid, "input_value")
        if source and f.kind(source[0][0]) != "ChatInput":
            b.note(f"Agent '{label}' answers the chat (its input was '{f.label(source[0][0])}'); start runs with a "
                   "chat message.")  # fmt: skip
        b.use_chat()
    elif k in ROUTERS:
        text = f.field_text(nid, "input_text") or ""
        # the rule compares a field: the text itself when it is one, else the latest chat message
        target = _field_of(text) or "messages[size(state.messages) - 1].content"
        rule = compare(f"state.{target}", f.value(nid, "operator") or "equals", f.value(nid, "match_text") or "")
        if rule is None:
            b.note(f"Router '{label}': its operator '{f.value(nid, 'operator')}' was not imported; it checks equality.")
            rule = compare(f"state.{target}", "equals", f.value(nid, "match_text") or "")
        if f.value(nid, "case_sensitive") is False and "==" in str(rule):
            b.note(f"Router '{label}' compares case-insensitively in Langflow; here it is case-sensitive.")
        b.step(nid, "core.router", label, {"cases": [{"when": rule, "route": "true"}], "default": "false"},
               label=label, position=pos)  # fmt: skip
    elif f.is_model(nid):
        user = f.field_text(nid, "input_value") or ""
        system = f.field_text(nid, "system_message") or ""
        prompt = "\n\n".join(p for p in (system, user) if p) or "Answer."
        out = b.channel(f"{label}_reply", io="out")
        b.step(nid, "core.llm", label, {"prompt": prompt, "output": out}, label=label, model=f.model_block(nid),
               position=pos)  # fmt: skip
    else:
        b.placeholder(nid, label, f"the Langflow component '{k}'", position=pos, label=label, instead=instead(k))


def _field_of(text: str) -> str | None:
    t = text.strip()
    if t.startswith("{{ state.") and t.endswith(" }}") and t.count("{{") == 1:
        found = t[len("{{ state.") : -3].strip()
        return None if found.startswith("messages[") else found  # CEL has no negative index: the default reads it
    return None


def _tool(f: _Flow, nid: str) -> str | None:
    k, label = f.kind(nid), f.label(nid)
    if k in HTTP_TOOLS:
        urls = f.value(nid, "urls") or f.value(nid, "url")
        url = urls[0] if isinstance(urls, list) and urls else urls
        found = f.b.http_tool(label, str(url or ""), method=str(f.value(nid, "method") or "GET"),
                              description=str(f.value(nid, "tool_description") or ""))  # fmt: skip
        if found:
            return found
    use = instead(k)
    f.b.note(
        f"Tool '{label}' ({k}) was not imported; {'use ' + use if use else 'add a tool block for it on the canvas'}."
    )
    return None
