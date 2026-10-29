"""n8n workflows (the JSON n8n's Download or copy-paste gives) as workflows (S31).

An n8n workflow is a list of nodes and a `connections` map: `main` connections carry items from step to step, and
AI connections (`ai_languageModel`, `ai_tool`, `ai_memory`) plug sub-nodes into an AI Agent or LLM Chain. The
importer keeps the AI Agent, Basic LLM Chain, If, Switch and Edit Fields (Set) nodes as steps, chat-model sub-nodes as
model blocks and HTTP Request tools as HTTP tool blocks. A Chat Trigger makes the workflow a chatbot; any other
trigger starts it with input fields. Expressions such as `{{ $json.chatInput }}` or `{{ $('Node').item.json.output }}`
become `{{ state.<field> }}`; ones that do not translate are kept as text and listed in the report.
"""

from __future__ import annotations

import re
from collections import deque
from typing import Any

from agentcanvas.importers import (
    AGENT_REPLY,
    Builder,
    ImportError_,
    compare,
    model_id,
    obj,
    position,
    route_name,
)

LC = "@n8n/n8n-nodes-langchain."
BASE = "n8n-nodes-base."
CHAT_TRIGGERS = {LC + "chatTrigger", LC + "manualChatTrigger"}
AGENTS = {LC + "agent", LC + "openAiAssistant"}
CHAINS = {LC + "chainLlm"}
MODELS = {
    "lmChatOpenAi": "openai", "lmOpenAi": "openai", "lmChatAnthropic": "anthropic", "lmChatGoogleGemini": "google",
    "lmChatGoogleVertex": "google", "lmChatOllama": "ollama", "lmOllama": "ollama", "lmChatMistralCloud": "mistral",
    "lmChatGroq": "groq", "lmChatDeepSeek": "deepseek", "lmChatAzureOpenAi": "azure", "lmChatXAiGrok": "xai",
    "lmChatOpenRouter": "openrouter", "lmChatAwsBedrock": "bedrock",
}  # fmt: skip
HTTP_TOOLS = {LC + "toolHttpRequest", BASE + "httpRequestTool"}
IGNORED = {BASE + "stickyNote", BASE + "noOp"}
_EXPR = re.compile(r"\{\{\s*(.+?)\s*\}\}", re.S)
_JSON = re.compile(r"^\$json(?:\.([A-Za-z_][A-Za-z0-9_]*)|\[['\"]([^'\"]+)['\"]\])$")
_NODE = re.compile(r"""^\$\(\s*['"](.+?)['"]\s*\)\.(?:item|first\(\)|last\(\))\.json(?:\.([A-Za-z_][A-Za-z0-9_]*))?$""")


def read(doc: dict[str, Any], *, wf_id: str, name: str | None = None) -> Builder:
    nodes = {str(n["name"]): n for n in doc.get("nodes") or [] if isinstance(n, dict) and n.get("name")}
    if not nodes:
        raise ImportError_("the n8n file has no nodes")
    b = Builder("n8n", wf_id, name or doc.get("name") or "Imported n8n workflow", "")
    return _N8n(b, nodes, doc.get("connections") or {}).read()


class _N8n:
    def __init__(self, b: Builder, nodes: dict[str, dict[str, Any]], connections: dict[str, Any]):
        self.b, self.nodes = b, nodes
        self.main: list[tuple[str, int, str]] = []  # (source, output index, target)
        self.ai: list[tuple[str, str, str]] = []  # (sub-node, connection type, parent)
        for src, kinds in connections.items():
            if src not in nodes or not isinstance(kinds, dict):
                continue
            for ctype, outputs in kinds.items():
                for index, targets in enumerate(outputs if isinstance(outputs, list) else []):
                    for t in targets if isinstance(targets, list) else []:
                        dst = str((t or {}).get("node") or "")
                        if dst not in nodes:
                            continue
                        if ctype == "main":
                            self.main.append((src, index, dst))
                        else:
                            self.ai.append((src, ctype, dst))
        self.sub = {s for s, _, _ in self.ai}

    def type(self, name: str) -> str:
        return str(self.nodes[name].get("type") or "")

    def params(self, name: str) -> dict[str, Any]:
        p = self.nodes[name].get("parameters")
        return p if isinstance(p, dict) else {}

    def pos(self, name: str) -> dict[str, float] | None:
        p = self.nodes[name].get("position")
        return position(p[0], p[1]) if isinstance(p, list) and len(p) == 2 else None

    def is_trigger(self, name: str) -> bool:
        t = self.type(name)
        return t in CHAT_TRIGGERS or t.lower().endswith("trigger") or t == BASE + "webhook"

    # ---- expressions ----
    def text(self, value: Any, here: str) -> str:
        """An n8n parameter (`=Hello {{ $json.name }}` is an expression) as a template for the step `here`."""
        if not isinstance(value, str):
            return "" if value is None else str(value)
        if not value.startswith("="):
            return value
        return _EXPR.sub(lambda m: self.expr(m.group(1).strip(), here), value[1:])

    def expr(self, code: str, here: str) -> str:
        m = _JSON.match(code)
        if m:
            return self.field(m.group(1) or m.group(2), self.previous(here))
        m = _NODE.match(code)
        if m and m.group(1) in self.nodes:
            return self.field(m.group(2) or "", m.group(1))
        self.b.note(f"'{here}': the expression {{{{ {code} }}}} was kept as text; rewrite it with state fields.")
        return f"[{code}]"

    def previous(self, name: str) -> str | None:
        found = [s for s, _, t in self.main if t == name]
        return found[0] if found else None

    def field(self, key: str, source: str | None) -> str:
        """`$json.<key>` as seen after `source` (the node that produced the item)."""
        passed: set[str] = set()  # a loop of pass-through nodes has no producer: stop at the first repeat
        while source is not None and self.type(source) in IGNORED | {BASE + "if", BASE + "switch", BASE + "filter"}:
            passed.add(source)
            source = self.previous(source)  # these pass items through unchanged
            if source in passed:
                source = None
        if key == "chatInput" or (source is not None and self.type(source) in CHAT_TRIGGERS):
            return self.b.use_chat()
        if source is not None and source in self.b.steps:
            st = self.b.steps[source]
            if st.type == "core.agent" and key in ("output", ""):
                return AGENT_REPLY
            if st.type == "core.llm" and key in ("text", "output", "response", ""):
                return "{{ state." + st.config["output"] + " }}"
            if st.type == "core.set" and key:
                return "{{ state." + self.b.channel(key) + " }}"
        if not key:
            return AGENT_REPLY
        return (
            "{{ state." + self.b.channel(key, io="in" if source is None or self.is_trigger(source) else "inout") + " }}"
        )

    # ---- the workflow ----
    def read(self) -> Builder:
        b = self.b
        steps = [n for n in self.nodes if n not in self.sub and not self.is_trigger(n) and self.type(n) not in IGNORED]
        for n in self.nodes:  # triggers: a chat, or input fields
            t = self.type(n)
            if t in CHAT_TRIGGERS:
                b.use_chat()
            elif self.is_trigger(n) and t != BASE + "manualTrigger":
                b.note(f"'{n}' ({t.rsplit('.', 1)[-1]}) started the n8n workflow; here runs start with its input "
                       "fields (use a trigger on the workflow page for webhooks and schedules).")  # fmt: skip
        ordered = self.order(steps)
        for n in ordered:  # keys first, so expressions can name earlier steps
            self.declare(n)
        for n in ordered:
            self.fill(n)
        into: dict[str, list[tuple[str, int]]] = {}
        for src, index, dst in self.main:
            into.setdefault(dst, []).append((src, index))
        for n in ordered:
            for src, index in into.get(n, ()):
                earlier = self.through(src)
                for key in earlier:
                    route = None
                    if key in b.steps and b.steps[key].type == "core.router":
                        cases = b.steps[key].config["cases"]
                        if self.type(key) == BASE + "if":
                            route = "false" if index == 1 else "true"
                        else:
                            route = cases[index]["route"] if index < len(cases) else "fallback"
                    b.after(n, key, route)
        for n in self.nodes:
            t = self.type(n)
            if t.startswith(LC + "memory"):
                b.note(f"'{n}' (chat memory) was left out: a conversation keeps its messages on its own.")
            elif (
                n in self.sub
                and not t.startswith(LC + "lm")
                and not any(s == n and c == "ai_tool" for s, c, _ in self.ai)
            ):
                b.note(f"'{n}' ({t.rsplit('.', 1)[-1]}) was plugged into an AI node; it has no counterpart and was "
                       "left out.")  # fmt: skip
        if not b.outputs:
            later = {k for s in b.steps.values() for k, _ in s.after}
            for key, st in b.steps.items():
                if key not in later:
                    if st.type == "core.llm":
                        b.output(st.config["output"])
                    elif st.type == "core.agent":
                        b.output("messages")
        return b

    def order(self, steps: list[str]) -> list[str]:
        """Steps from the trigger onwards (so expressions see earlier steps first), then any others."""
        nexts: dict[str, list[str]] = {}
        for s, _, t in self.main:
            nexts.setdefault(s, []).append(t)
        seen: dict[str, None] = {}  # insertion-ordered set: linear on large files
        queue = deque(n for n in self.nodes if self.is_trigger(n))
        while queue:
            n = queue.popleft()
            if n in seen:
                continue
            seen[n] = None
            queue.extend(nexts.get(n, ()))
        wanted = set(steps)
        return [n for n in seen if n in wanted] + [n for n in steps if n not in seen]

    def through(self, src: str, seen: frozenset[str] = frozenset()) -> list[str]:
        """The step keys an item from `src` comes from: triggers are the start, ignored nodes are looked through
        (a loop of ignored nodes is looked through once)."""
        if self.is_trigger(src):
            return ["START"]
        if src in self.b.steps:
            return [src]
        found: list[str] = []
        for s, _, t in self.main:
            if t == src and s not in seen:
                found += [k for k in self.through(s, seen | {src}) if k not in found]
        return found or ["START"]

    def declare(self, n: str) -> None:
        b, t, pos = self.b, self.type(n), self.pos(n)
        if t in AGENTS:
            b.step(n, "core.agent", n, {"system_prompt": ""}, label=n, position=pos)
        elif t in CHAINS:
            b.step(n, "core.llm", n, {"prompt": "", "output": b.channel(f"{n}_reply", io="out")}, label=n,
                   position=pos)  # fmt: skip
        elif t in (BASE + "if", BASE + "filter"):
            b.step(n, "core.router", n, {"cases": [], "default": "false"}, label=n, position=pos)
        elif t == BASE + "switch":
            b.step(n, "core.router", n, {"cases": [], "default": "fallback"}, label=n, position=pos)
        elif t == BASE + "set":
            b.step(n, "core.set", n, {"values": {}}, label=n, position=pos)
        else:
            b.placeholder(n, n, f"the n8n node '{t.rsplit('.', 1)[-1] or t}'", position=pos, label=n)

    def fill(self, n: str) -> None:
        b, t, p = self.b, self.type(n), self.params(n)
        st = b.steps[n]
        if t in AGENTS or t in CHAINS:
            st.model = self.model(n)
            options = obj(p.get("options"))
            prompt = self.text(p.get("text"), n) if p.get("promptType") == "define" or p.get("text") else ""
            if t in AGENTS:
                st.config["system_prompt"] = self.text(options.get("systemMessage") or "", n)
                st.tools = [r for r in (self.tool(s) for s, c, d in self.ai if d == n and c == "ai_tool") if r]
                if prompt and prompt != b.use_chat():
                    b.note(f"Agent '{st.name}' read '{_short(prompt)}' in n8n; here it answers the chat, so start "
                           "runs with a chat message.")  # fmt: skip
                b.use_chat()
            else:
                messages = (p.get("messages") or {}).get("messageValues") if isinstance(p.get("messages"), dict) else []
                system = "\n\n".join(
                    self.text(m.get("message"), n)
                    for m in messages or []
                    if isinstance(m, dict)
                    and m.get("type", "SystemMessagePromptTemplate") == "SystemMessagePromptTemplate"
                )
                user = prompt or self.field("chatInput", self.previous(n))
                st.config["prompt"] = "\n\n".join(x for x in (system, user) if x)
        elif t in (BASE + "if", BASE + "filter"):
            rule = self.rule(p.get("conditions"), n)
            st.config["cases"] = [{"when": rule, "route": "true"}]
        elif t == BASE + "switch":
            rules = (p.get("rules") or {}).get("values") or (p.get("rules") or {}).get("rules") or []
            for i, r in enumerate(x for x in rules if isinstance(x, dict)):
                route = route_name(r.get("outputKey"), i)
                st.config["cases"].append({"when": self.rule(r.get("conditions"), n), "route": route})
            if p.get("mode") == "expression":
                b.note(f"Switch '{n}' picks its output with an expression in n8n; write its rules on the path step.")
        elif t == BASE + "set":
            values: dict[str, str] = {}
            rows = ((p.get("assignments") or {}).get("assignments") or []) if isinstance(p.get("assignments"), dict) \
                else []  # fmt: skip
            for kind_rows in (p.get("values") or {}).values() if isinstance(p.get("values"), dict) else []:
                rows += [r for r in kind_rows if isinstance(r, dict)]  # older Set nodes: values.string[...]
            for r in rows:
                if isinstance(r, dict) and r.get("name"):
                    values[b.channel(str(r["name"]))] = self.text(r.get("value"), n)
            if not values:
                values[b.channel(f"{n}_result")] = ""
                b.note(f"Edit Fields '{n}' set no fields that could be imported.")
            st.config["values"] = values

    def model(self, n: str) -> str:
        for s, c, d in self.ai:
            if d == n and c == "ai_languageModel":
                kind = self.type(s).removeprefix(LC)
                p = self.params(s)
                raw = p.get("model") or p.get("modelName") or ""
                name = raw.get("value") if isinstance(raw, dict) else raw
                options = obj(p.get("options"))
                return self.b.model(model_id(MODELS.get(kind, kind), name, self.b),
                                    temperature=options.get("temperature"), max_tokens=options.get("maxTokens"),
                                    base_url=options.get("baseURL") or p.get("baseUrl"))  # fmt: skip
        self.b.note(f"'{n}' had no chat model plugged in; it uses a default model.")
        return self.b.model(model_id("openai", None, self.b))

    def tool(self, s: str) -> str | None:
        t, p = self.type(s), self.params(s)
        if t in HTTP_TOOLS:
            about = str(p.get("toolDescription") or p.get("description") or "")
            found = self.b.http_tool(
                s, str(p.get("url") or ""), method=str(p.get("method") or "GET"), description=about
            )
            if found:
                return found
        self.b.note(f"Tool '{s}' ({t.rsplit('.', 1)[-1]}) was not imported; add a tool block for it on the canvas.")
        return None

    def rule(self, conditions: Any, here: str) -> str:
        """An If / Switch / Filter condition list (v2: conditions.conditions[], v1: conditions.string[]) as CEL."""
        rules: list[str] = []
        combinator = "&&"
        if isinstance(conditions, dict) and isinstance(conditions.get("conditions"), list):
            combinator = "||" if conditions.get("combinator") == "or" else "&&"
            for c in conditions["conditions"]:
                if isinstance(c, dict):
                    op = (c.get("operator") or {}).get("operation") if isinstance(c.get("operator"), dict) else None
                    rules.append(self.one(c.get("leftValue"), op, c.get("rightValue"), here))
        elif isinstance(conditions, dict):  # v1
            for rows in conditions.values():
                for c in rows if isinstance(rows, list) else []:
                    if isinstance(c, dict):
                        rules.append(self.one(c.get("value1"), c.get("operation") or "equal", c.get("value2"), here))
        if not rules:
            self.b.note(f"'{here}' had no conditions that could be imported; write its rule on the path step.")
            return "false"
        return f" {combinator} ".join(f"({r})" if len(rules) > 1 else r for r in rules)

    def one(self, left: Any, op: Any, right: Any, here: str) -> str:
        expr = self.text(left, here).strip()
        field = expr[len("{{ state.") : -3].strip() if expr.startswith("{{ state.") and expr.count("{{") == 1 else None
        if field is not None and field.startswith("messages["):
            field = "messages[size(state.messages) - 1].content"
        value = self.text(right, here) if isinstance(right, str) else right
        rule = compare(f"state.{field}", str(op or "equals"), "" if value is None else value) if field else None
        if rule is None:
            self.b.note(f"'{here}': the condition '{expr} {op} {value}' could not be imported; write it on the path "
                        "step.")  # fmt: skip
            return "false"
        return rule


def _short(text: str) -> str:
    return text if len(text) <= 60 else text[:57] + "..."
