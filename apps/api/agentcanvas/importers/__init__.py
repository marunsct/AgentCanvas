"""Importers from other visual builders (S31): Langflow, Flowise and n8n files become workflows.

`import_document(doc)` recognises the file (`detect`) and returns the closest workflow (an IR document, not yet
validated), a report of everything that did not carry over or needs a look, and the canvas positions of the
original. Each importer (`langflow`, `flowise`, `n8n`) reads its builder's graph and describes it to a `Builder`:
steps (AI steps, agents, paths, values, approvals), the model and tool blocks they use, and which step follows
which. The Builder writes the IR: the chat as the `messages` field when the original was a chatbot, one field per
step's answer, a Wait for all where a step needs several earlier ones, and placeholders (with a report line) for
what AgentCanvas cannot run. Pure functions over dicts: nothing from those builders is installed or called.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from agentcanvas.agentspec import ident

CHAT_INPUT = "{{ state.messages[-1].content }}"  # the person's latest chat message
AGENT_REPLY = "{{ state.messages[-1].content }}"  # an agent's answer: the last message of the chat
MAX_STEPS = 2000  # steps in one imported file


class ImportError_(ValueError):
    """The file is not one this importer understands."""


@dataclass
class _Step:
    key: str
    id: str
    type: str
    name: str
    config: dict[str, Any]
    label: str | None = None
    model: str | None = None
    tools: list[str] = field(default_factory=list)
    after: list[tuple[str, str | None]] = field(default_factory=list)  # (earlier step key or START, route)
    position: dict[str, float] | None = None


class Builder:
    """Collects steps, blocks and fields and writes them as a workflow."""

    def __init__(self, source: str, wf_id: str, name: str, description: str = ""):
        self.source, self.wf_id = source, wf_id
        self.name = (str(name or "").strip() or "Imported workflow")[:200]
        self.description = str(description or "")[:2000]
        self.report: list[str] = []
        self.channels: dict[str, dict[str, Any]] = {}
        self.steps: dict[str, _Step] = {}
        self.resources: list[dict[str, Any]] = []
        self._models: dict[str, str] = {}
        self._node_names: set[str] = set()
        self.outputs: list[str] = []
        self.chat = False
        self._unders: dict[str, dict[str, str]] = {}  # step key -> _under(key), reset by build()

    # ---- fields ----
    def channel(self, wanted: str, *, io: str = "inout", type_: dict[str, Any] | None = None) -> str:
        """A field named after `wanted` (made a valid name); the same `wanted` gives the same field."""
        if wanted in self.channels:
            if io != "inout" and self.channels[wanted]["io"] == "inout":
                self.channels[wanted]["io"] = io
            return self.channels[wanted]["name"]
        taken = {c["name"] for c in self.channels.values()} | {"messages"}
        name = ident(wanted, taken, "field")
        self.channels[wanted] = {"name": name, "type": type_ or {"type": "string"}, "io": io}
        return name

    def use_chat(self) -> str:
        """The workflow is a chatbot: its input is the chat (`messages`); returns how a prompt reads the message."""
        self.chat = True
        return CHAT_INPUT

    def output(self, channel: str) -> None:
        if channel not in self.outputs:
            self.outputs.append(channel)

    # ---- blocks ----
    def model(self, model: str, *, temperature: Any = None, max_tokens: Any = None, base_url: str | None = None,
              label: str | None = None) -> str:  # fmt: skip
        """A model block (`provider:model`), shared by the steps that use the same settings; returns its id."""
        conf: dict[str, Any] = {"model": model}
        if isinstance(temperature, int | float) and not isinstance(temperature, bool) and 0 <= temperature <= 2:
            conf["temperature"] = float(temperature)
        if isinstance(max_tokens, int) and not isinstance(max_tokens, bool) and max_tokens > 0:
            conf["max_tokens"] = max_tokens
        if base_url and str(base_url).startswith(("http://", "https://")):
            conf["base_url"] = str(base_url)
        key = repr(sorted(conf.items()))
        if key not in self._models:
            rid = f"r_{len(self.resources) + 1}"
            taken = {r["name"] for r in self.resources}
            name = ident(label or model.partition(":")[2] or "model", taken, "model")
            self.resources.append({"id": rid, "type": "model.chat", "name": name, "config": conf})
            self._models[key] = rid
        return self._models[key]

    def http_tool(self, name: str, url: str, *, method: str = "GET", description: str = "") -> str | None:
        """An HTTP tool block for a URL (its host becomes the only allowed domain); None when the URL is unusable."""
        url = str(url or "").strip()
        host = urlparse(url).hostname if url.startswith(("http://", "https://")) else None
        if not host or "{{" in url.split("?")[0].split("//", 1)[-1].split("/")[0]:
            return None
        rid = f"r_{len(self.resources) + 1}"
        taken = {r["name"] for r in self.resources}
        method = method.upper() if method.upper() in ("GET", "POST", "PUT", "PATCH", "DELETE") else "GET"
        self.resources.append({"id": rid, "type": "tool.http", "name": ident(name, taken, "tool"), "config": {
            "description": (description or f"Calls {host}")[:500], "method": method, "url": url,
            "allowed_domains": [host]}})  # fmt: skip
        return rid

    # ---- steps ----
    def step(self, key: str, type_: str, name: str, config: dict[str, Any], *, label: str | None = None,
             model: str | None = None, tools: list[str] | None = None,
             position: dict[str, float] | None = None) -> _Step:  # fmt: skip
        if key in self.steps:
            raise ImportError_(f"two steps share the id '{key}'")
        if len(self.steps) >= MAX_STEPS:
            raise ImportError_(f"the {self.source} file has more than {MAX_STEPS} steps; split it before importing")
        st = _Step(key=key, id=f"n_{len(self.steps) + 1}", type=type_, name=ident(name, self._node_names, "step"),
                   config=config, label=(label or None) and str(label)[:120], model=model, tools=list(tools or []),
                   position=position)  # fmt: skip
        self.steps[key] = st
        return st

    def placeholder(self, key: str, name: str, what: str, *, position: dict[str, float] | None = None,
                    label: str | None = None) -> _Step:  # fmt: skip
        """A stand-in step for something AgentCanvas cannot run (a Set values step that writes a TODO)."""
        out = self.channel(f"{name}_result")
        st = self.step(key, "core.set", name, {"values": {out: f"TODO: replace this step ({what})"}}, label=label,
                       position=position)  # fmt: skip
        self.note(f"Step '{st.name}': {what} has no counterpart in AgentCanvas; it is a placeholder to replace.")
        return st

    def after(self, key: str, earlier: str, route: str | None = None) -> None:
        """`key` runs after `earlier` ("START" for the beginning), on `route` when `earlier` is a path step."""
        st = self.steps[key]
        if (earlier, route) not in st.after:
            st.after.append((earlier, route))

    def note(self, line: str) -> None:
        if line not in self.report:
            self.report.append(line)

    # ---- the workflow ----
    def _under(self, key: str) -> dict[str, str]:
        """The path choices a step is always behind ({path step key: route}). Iterative and memoized: chains of
        diamonds would take exponential time and long chains would overflow the stack if walked recursively. An
        earlier step that is also later (a loop) adds nothing."""
        memo = self._unders
        stack, open_ = [key], set()  # open_: steps waiting for their earlier steps
        while stack:
            k = stack[-1]
            if k in memo:
                stack.pop()
                continue
            if k == "START" or k not in self.steps:
                memo[k] = {}
                stack.pop()
                continue
            earlier = self.steps[k].after or [("START", None)]
            if k not in open_:
                open_.add(k)
                stack.extend(e for e, _ in earlier if e not in memo and e not in open_)
                continue
            found: dict[str, str] | None = None
            for e, route in earlier:
                mine = memo.get(e, {}) | ({e: route} if route else {})
                found = mine if found is None else {r: v for r, v in found.items() if mine.get(r) == v}
            memo[k] = found or {}
            open_.discard(k)
            stack.pop()
        return memo[key]

    def _exclusive(self, sources: list[tuple[str, str | None]]) -> bool:
        """Earlier steps on different routes of one path step: only one of them runs, so nothing to wait for."""
        unders = [self._under(k) | ({k: r} if r else {}) for k, r in sources]
        return any(a.get(r) not in (None, v) for i, a in enumerate(unders) for b in unders[i + 1 :]
                   for r, v in b.items())  # fmt: skip

    def build(self) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
        if not self.steps:
            raise ImportError_(f"the {self.source} file has no steps AgentCanvas can import")
        self._unders = {}
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        layout: dict[str, Any] = {}
        later = {k for st in self.steps.values() for k, _ in st.after}
        steps = list(self.steps.values())
        waits = 0
        for st in steps:
            node: dict[str, Any] = {"id": st.id, "type": st.type, "name": st.name, "config": st.config}
            if st.label and st.label != st.name:
                node["label"] = st.label
            nodes.append(node)
            if st.position:
                layout[st.id] = st.position
            if st.model:
                edges.append(_wire(st.model, st.id, "model"))
            for rid in st.tools:
                edges.append(_wire(rid, st.id, "tools"))
            sources = [(k, r) for k, r in st.after if k == "START" or k in self.steps] or [("START", None)]
            target = st.id
            if len(sources) > 1 and not self._exclusive(sources):  # needs every earlier step: wait for all of them
                waits += 1
                wait_id = f"n_{len(steps) + waits}"
                wait_name = ident(f"wait_for_{st.name}", self._node_names, "wait")
                nodes.append({"id": wait_id, "type": "core.join", "name": wait_name, "config": {}})
                if st.position:
                    layout[wait_id] = {"x": st.position["x"] - 160, "y": st.position["y"]}
                edges.append(_flow(wait_id, st.id))
                target = wait_id
            for k, route in sources:
                edges.append(_flow("START" if k == "START" else self.steps[k].id, target, route))
        for st in steps:
            if st.key not in later and st.type != "core.router":
                edges.append(_flow(st.id, "END"))
        for st in self.steps.values():  # a path step's routes that lead nowhere end the run
            if st.type == "core.router":
                used = {r for s in self.steps.values() for k, r in s.after if k == st.key}
                for route in [*(c["route"] for c in st.config.get("cases", [])), st.config.get("default", "default")]:
                    if route not in used:
                        edges.append(_flow(st.id, "END", route))
                        used.add(route)
        channels = [c for c in self.channels.values()]
        if self.chat or any(n["type"] == "core.agent" for n in nodes):
            channels.insert(0, {"name": "messages", "type": {"type": "array"}, "reducer": "add_messages",
                                "io": "inout"})  # fmt: skip
        ir: dict[str, Any] = {
            "ir_version": "1.2",
            "id": self.wf_id,
            "name": self.name,
            "description": self.description,
            "state": {"channels": channels},
            "resources": self.resources,
            "nodes": nodes,
            "edges": _dedupe(edges),
            "metadata": {"imported_from": self.source},
        }
        outs = [c for c in self.outputs if any(ch["name"] == c for ch in channels)]
        if outs and not (self.chat and outs == ["messages"]):
            ir["settings"] = {"output_channels": list(dict.fromkeys((["messages"] if self.chat else []) + outs))}
        return ir, self.report, layout


def _wire(res: str, node: str, port: str) -> dict[str, Any]:
    return {"id": f"w_{res}_{node}_{port}", "kind": "wiring", "source": {"node": res},
            "target": {"node": node, "port": port}}  # fmt: skip


def _flow(src: str, dst: str, route: str | None = None) -> dict[str, Any]:
    source: dict[str, Any] = {"node": src} | ({"port": f"route:{route}"} if route else {})
    return {"id": f"e_{src}_{dst}" + (f"_{route}" if route else ""), "kind": "flow", "source": source,
            "target": {"node": dst}}  # fmt: skip


def _dedupe(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out = []
    for e in edges:
        if e["id"] not in seen:
            seen.add(e["id"])
            out.append(e)
    return out


# ------------------------------------------------------------------ shared helpers ----
PROVIDERS = {  # what the builders call a provider -> AgentCanvas's provider id
    "openai": "openai", "anthropic": "anthropic", "google": "google_genai", "gemini": "google_genai",
    "google generative ai": "google_genai", "googlegenerativeai": "google_genai", "mistral": "mistralai",
    "mistralai": "mistralai", "groq": "groq", "deepseek": "deepseek", "xai": "xai", "grok": "xai",
    "ollama": "ollama", "azure": "azure_openai", "azure openai": "azure_openai", "azureopenai": "azure_openai",
    "lmstudio": "lmstudio", "lm studio": "lmstudio", "vllm": "vllm",
}  # fmt: skip
DEFAULT_MODELS = {"openai": "gpt-5.5", "anthropic": "claude-haiku-4-5", "google_genai": "gemini-3.6-flash",
                  "mistralai": "mistral-large-latest", "groq": "llama-3.3-70b-versatile", "ollama": "llama3.3",
                  "deepseek": "deepseek-chat", "xai": "grok-4", "azure_openai": "gpt-5.5"}  # fmt: skip


def model_id(provider: str, name: Any, report: Builder | None = None) -> str:
    """`provider:model` from a builder's provider and model names (a default model when the name is missing)."""
    pid = PROVIDERS.get(str(provider or "").strip().lower(), str(provider or "").strip().lower())
    if pid not in DEFAULT_MODELS and pid not in ("lmstudio", "vllm"):
        if report is not None:
            report.note(f"Model provider '{provider}' became an OpenAI-compatible model; check its address.")
        pid = "openai_compatible"
    model = str(name or "").strip().removeprefix("models/")  # Gemini names come as models/<name>
    if not model:
        model = DEFAULT_MODELS.get(pid, "model")
        if report is not None:
            report.note(f"A {provider} model had no model name; it uses {pid}:{model}. Pick the one you want.")
    return f"{pid}:{model}"


_BRACE_VAR = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")


def brace_template(text: str, value_of: Any) -> str:
    """A `{name}` template (Langflow, Flowise prompts) as `{{ state.<field> }}` text; `value_of(name)` gives each
    variable's expression (already wrapped in `{{ }}`) or None to keep it as written."""

    def sub(m: re.Match[str]) -> str:
        found = value_of(m.group(1))
        return found if found is not None else m.group(0)

    return _BRACE_VAR.sub(sub, str(text or ""))


def cel_string(value: Any) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def compare(field_expr: str, operation: str, value: Any) -> str | None:
    """A CEL rule comparing a field with a value, for the usual builder operations (None: not supported)."""
    op = str(operation or "").strip().lower().replace("_", " ").replace("-", " ")
    v = cel_string(value)
    rules = {
        "equals": f"{field_expr} == {v}", "equal": f"{field_expr} == {v}", "is equal to": f"{field_expr} == {v}",
        "not equals": f"{field_expr} != {v}", "not equal": f"{field_expr} != {v}", "notequals": f"{field_expr} != {v}",
        "contains": f"{field_expr}.contains({v})", "not contains": f"!{field_expr}.contains({v})",
        "notcontains": f"!{field_expr}.contains({v})", "starts with": f"{field_expr}.startsWith({v})",
        "startswith": f"{field_expr}.startsWith({v})", "ends with": f"{field_expr}.endsWith({v})",
        "endswith": f"{field_expr}.endsWith({v})", "regex": f"{field_expr}.matches({v})",
        "matches": f"{field_expr}.matches({v})", "is empty": f'{field_expr} == ""', "empty": f'{field_expr} == ""',
        "is not empty": f'{field_expr} != ""', "not empty": f'{field_expr} != ""', "notempty": f'{field_expr} != ""',
        "exists": f'{field_expr} != ""',
    }  # fmt: skip
    if op in rules:
        return rules[op]
    numeric = {"larger": ">", "gt": ">", "greater than": ">", "smaller": "<", "lt": "<", "less than": "<",
               "larger equal": ">=", "gte": ">=", "smaller equal": "<=", "lte": "<="}  # fmt: skip
    if op in numeric:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return f"double({field_expr}) {numeric[op]} {number}"
    return None


def obj(value: Any) -> dict[str, Any]:
    """`value` when it is a JSON object, else an empty one (files from other tools vary)."""
    return value if isinstance(value, dict) else {}


def route_name(text: Any, index: int) -> str:
    """A valid route name (lower-case letters, digits and _) from a builder's output name or position."""
    name = re.sub(r"[^a-z0-9_]+", "_", str(text or "").lower()).strip("_")
    return name if name and name[0].isalpha() else f"branch_{index + 1}"


def position(x: Any, y: Any) -> dict[str, float] | None:
    if isinstance(x, int | float) and isinstance(y, int | float) and not isinstance(x, bool):
        return {"x": float(x), "y": float(y)}
    return None


# ------------------------------------------------------------------ entry point ----
FORMATS = {"langflow": "Langflow", "flowise": "Flowise", "n8n": "n8n", "agentspec": "Open Agent Spec"}


def detect(doc: Any) -> str | None:
    """Which builder wrote `doc`: langflow, flowise, n8n or agentspec (None: none of them)."""
    if not isinstance(doc, dict):
        return None
    if "component_type" in doc:
        return "agentspec"
    data = doc.get("data")
    if isinstance(data, dict) and isinstance(data.get("nodes"), list) and isinstance(data.get("edges"), list):
        return "langflow"
    nodes = doc.get("nodes")
    if isinstance(nodes, list) and isinstance(doc.get("connections"), dict):
        return "n8n"
    if isinstance(nodes, list) and isinstance(doc.get("edges"), list):
        if any(isinstance(n, dict) and str(n.get("type", "")).startswith("genericNode") for n in nodes):
            return "langflow"  # a Langflow flow's inner graph
        return "flowise"
    return None


def import_document(doc: Any, *, wf_id: str, name: str | None = None) -> tuple[str, dict[str, Any], list[str],
                                                                              dict[str, Any]]:  # fmt: skip
    """(format, IR document, report, layout) for a Langflow, Flowise, n8n or Agent Spec file."""
    kind = detect(doc)
    if kind is None:
        raise ImportError_(
            "this file is not a Langflow, Flowise, n8n or Agent Spec export (expected the JSON each one downloads)"
        )
    if kind == "agentspec":
        from agentcanvas import agentspec

        try:
            ir, report, layout = agentspec.import_flow(doc, wf_id=wf_id, name=name)
        except agentspec.AgentSpecError as exc:
            raise ImportError_(str(exc)) from exc
        return kind, ir, report, layout
    from agentcanvas.importers import flowise, langflow, n8n

    module = {"langflow": langflow, "flowise": flowise, "n8n": n8n}[kind]
    try:
        builder = module.read(doc, wf_id=wf_id, name=name)
        ir, report, layout = builder.build()
    except ImportError_:
        raise
    except (AttributeError, KeyError, TypeError, ValueError, IndexError) as exc:  # a list where an object belongs...
        raise ImportError_(f"the {FORMATS[kind]} file could not be read ({type(exc).__name__}: {exc})") from exc
    except RecursionError as exc:  # nesting deeper than any real file
        raise ImportError_(f"the {FORMATS[kind]} file is nested too deeply to import") from exc
    return kind, ir, report, layout
