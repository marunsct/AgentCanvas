"""Open Agent Spec import and export (S16).

Agent Spec (https://oracle.github.io/agent-spec) is a framework-neutral JSON format for agents and flows. A workflow
exports as an Agent Spec ``Flow``: models become LLM configs, AI steps ``LlmNode``s, agents ``AgentNode``s (remote A2A
agents included), questions ``InputMessageNode``s, simple routers ``BranchingNode``s; every other step becomes a
``ToolNode`` with a ``ServerTool`` the host runtime provides. The whole workflow also rides in the flow's metadata, so
a file exported here imports back exactly; anything Agent Spec cannot say is listed in the report.

Import reads any Agent Spec flow (or a single agent) and builds the closest workflow, again with a report. Pure
functions over dicts: no LangChain, no pyagentspec (the CI ``frameworks`` job checks files against pyagentspec).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from agentcanvas.ir.models import IDENT, RESERVED_NAMES, Workflow

VERSION = "25.4.2"  # 25.4.2 adds A2AAgent; nothing here needs a later one
_NS = uuid.UUID("6b1f3c0e-6a0e-4f5e-9a51-6167656e7473")
_STATE_VAR = re.compile(r"\{\{\s*state\.([a-z][a-z0-9_]*)\s*\}\}")
_SPEC_VAR = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_EQUALS = re.compile(r'^\s*state\.([a-z][a-z0-9_]*)\s*==\s*"([^"\\]*)"\s*$')
_LOCAL = {
    "ollama": "http://localhost:11434",
    "lmstudio": "http://localhost:1234/v1",
    "vllm": "http://localhost:8000/v1",
    "llamacpp": "http://localhost:8080/v1",
}


class AgentSpecError(ValueError):
    """The document is not an Agent Spec flow or agent this importer understands."""


# ------------------------------------------------------------------ export ----
def export_flow(wf: Workflow, layout: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    """The workflow as an Agent Spec ``Flow`` document, and what it could not express. `layout` (canvas positions)
    rides along so an import back looks the same."""
    doc, report = _Exporter(wf).run()
    if layout:
        doc["metadata"]["agentcanvas"]["layout"] = layout
    return doc, report


class _Exporter:
    def __init__(self, wf: Workflow):
        self.wf = wf
        self.report: list[str] = []
        self.refs: dict[str, dict[str, Any]] = {}
        self.outputs: dict[str, list[str]] = {}  # IR node id (or the start's spec id) -> channels it writes
        self.writers: dict[str, list[str]] = {}  # channel -> spec node ids that write it

    def uid(self, *parts: str) -> str:
        return str(uuid.uuid5(_NS, "/".join((self.wf.id, *parts))))

    def ref(self, comp: dict[str, Any]) -> dict[str, str]:
        self.refs[comp["id"]] = comp
        return {"$component_ref": comp["id"]}

    def text(self, name: str) -> dict[str, Any]:
        """A string property (LLM and question outputs are text in Agent Spec)."""
        return {**{k: v for k, v in self.channel_type(name).items() if k in ("title", "description")}, "type": "string"}

    def channel_type(self, name: str) -> dict[str, Any]:
        ch = next((c for c in self.wf.state.channels if c.name == name), None)
        schema = dict(ch.type) if ch else {"type": "string"}
        if ch and ch.description:
            schema.setdefault("description", ch.description)
        return {**schema, "title": name}

    def comp(self, kind: str, key: str, name: str, **fields: Any) -> dict[str, Any]:
        return {
            "component_type": kind,
            "id": self.uid(key),
            "name": name,
            "description": None,
            "metadata": {},
            **fields,
        }

    # models -------------------------------------------------------------
    def llm_configs(self) -> dict[str, dict[str, str]]:
        out: dict[str, dict[str, str]] = {}
        for r in self.wf.resources:
            if r.type != "model.chat":
                continue
            model = str(r.config.get("model", ""))
            provider, _, model_id = model.partition(":")
            url = r.config.get("base_url") or _LOCAL.get(provider)
            meta = {"agentcanvas": {"model": model}}
            if provider == "openai":
                cfg = self.comp("OpenAiConfig", r.id, r.name, model_id=model_id)
                if url:
                    cfg["url"] = url
            elif provider == "ollama":
                cfg = self.comp("OllamaConfig", r.id, r.name, model_id=model_id, url=url)
            elif provider == "vllm":
                cfg = self.comp("VllmConfig", r.id, r.name, model_id=model_id, url=url)
            else:
                cfg = self.comp("OpenAiCompatibleConfig", r.id, r.name, model_id=model_id, url=url or "")
                if provider not in ("lmstudio", "llamacpp", "openai_compatible"):
                    self.report.append(
                        f"Model '{r.name}' ({model}): Agent Spec has no {provider} config, so it is written as an "
                        "OpenAI-compatible one; the original model id is kept in its metadata."
                    )
            params = {k: r.config[k] for k in ("temperature", "max_tokens") if k in r.config}
            if params:
                cfg["default_generation_parameters"] = params
            cfg["metadata"] = meta
            out[r.id] = self.ref(cfg)
        return out

    # nodes -------------------------------------------------------------
    def node(self, n: Any, model: dict[str, str] | None) -> dict[str, Any]:
        cfg = n.config
        meta = {"agentcanvas": {"id": n.id, "type": n.type, "label": n.label, "config": cfg}}
        base = {"metadata": meta, "branches": ["next"]}

        def needs_model() -> dict[str, str]:
            if model is None:
                self.report.append(f"Step '{n.name}' has no model wired; Agent Spec needs one, so it is a tool step.")
            return model or {}

        if n.type == "core.llm" and needs_model():
            prompt = str(cfg.get("prompt", ""))
            names = list(dict.fromkeys(_STATE_VAR.findall(prompt)))
            out_name = str(cfg.get("output", "generated_text"))
            self.outputs[n.id] = [out_name]
            return self.comp(
                "LlmNode", n.id, n.name, **base,
                inputs=[self.channel_type(v) for v in names], outputs=[self.text(out_name)],
                llm_config=model, prompt_template=_STATE_VAR.sub(r"{{\1}}", prompt),
            )  # fmt: skip
        if n.type in ("core.agent", "agent.deep") and needs_model():
            agent = self.comp(
                "Agent", f"{n.id}/agent", n.name, inputs=[], outputs=[], llm_config=model,
                system_prompt=str(cfg.get("system_prompt") or cfg.get("instructions") or ""), tools=[],
            )  # fmt: skip
            self.report.append(f"Agent '{n.name}': its tools and middleware are not exported (Agent Spec lists tools "
                               "per agent; wire them again in the target runtime).")  # fmt: skip
            return self.comp("AgentNode", n.id, n.name, **base, inputs=[], outputs=[], agent=agent)
        if n.type == "agent.a2a":
            conn = self.comp("A2AConnectionConfig", f"{n.id}/conn", f"{n.name}_connection",
                             timeout=float(cfg.get("timeout", 120)), headers=None, verify=True,
                             key_file=None, cert_file=None, ssl_ca_cert=None)  # fmt: skip
            if cfg.get("headers"):
                self.report.append(f"Remote agent '{n.name}': headers are not exported (they may hold secrets).")
            agent = self.comp(
                "A2AAgent", f"{n.id}/agent", n.name, inputs=[], outputs=[], agent_url=str(cfg.get("url", "")),
                connection_config=conn, session_parameters={"timeout": 60.0, "poll_interval": 2.0, "max_retries": 5},
            )  # fmt: skip
            return self.comp("AgentNode", n.id, n.name, **base, inputs=[], outputs=[], agent=agent)
        if n.type in ("core.human_input", "core.approval"):
            if n.type == "core.human_input":
                message = str(cfg.get("prompt", ""))
            else:
                message = "Approve {{ state." + str(cfg.get("show", "")) + " }}?"
                self.report.append(f"Approval '{n.name}': written as a question; the answer is text, not yes/no.")
            names = list(dict.fromkeys(_STATE_VAR.findall(message)))
            out_name = str(cfg.get("output", "user_input"))
            self.outputs[n.id] = [out_name]
            return self.comp(
                "InputMessageNode", n.id, n.name, **base, inputs=[self.channel_type(v) for v in names],
                outputs=[self.text(out_name)], message=_STATE_VAR.sub(r"{{\1}}", message),
            )  # fmt: skip
        if n.type == "core.router":
            cases = cfg.get("cases") or []
            matches = [_EQUALS.match(str(c.get("when", ""))) for c in cases]
            keys = {m.group(1) for m in matches if m}
            if all(matches) and len(keys) <= 1:
                key = next(iter(keys), None)
                mapping = {m.group(2): c["route"] for m, c in zip(matches, cases, strict=True) if m}
                branches = sorted({"default", *mapping.values()})
                return self.comp(
                    "BranchingNode", n.id, n.name, metadata={**meta, "branching_key": key},
                    inputs=[{"title": "branching_mapping_key", "type": "string", "description": key or ""}],
                    outputs=[], branches=branches, mapping=mapping,
                )  # fmt: skip
            self.report.append(f"Router '{n.name}': only rules like state.x == \"value\" on one field map to Agent "
                               "Spec branches; here the branch is picked by its path name and the rules are kept in "
                               "metadata.")  # fmt: skip
            routes = sorted({c.get("route", "") for c in cases} - {"", "default"})
            return self.comp("BranchingNode", n.id, n.name, metadata=meta, outputs=[], mapping={r: r for r in routes},
                             branches=sorted({"default", *routes}),
                             inputs=[{"title": "branching_mapping_key", "type": "string"}])  # fmt: skip
        writes = _writes(n)
        self.outputs[n.id] = writes
        tool = self.comp(
            "ServerTool", f"{n.id}/tool", n.name, description=n.label or n.type, metadata=meta,
            inputs=[], outputs=[self.channel_type(w) for w in writes], requires_confirmation=False,
        )  # fmt: skip
        if n.type not in ("core.set", "core.script"):
            self.report.append(f"Step '{n.name}' ({n.type}) is a tool the host runtime must provide.")
        return self.comp("ToolNode", n.id, n.name, **base, inputs=[], outputs=tool["outputs"], tool=tool)

    def run(self) -> tuple[dict[str, Any], list[str]]:
        wf = self.wf
        models = self.llm_configs()
        wired = {e.target.node: e.source.node for e in wf.edges if e.kind == "wiring" and e.target.port == "model"}
        ins = [c.name for c in wf.state.channels if c.io == "in"] or [
            c.name for c in wf.state.channels if c.io == "inout" and c.name in (wf.settings.input_channels or [])
        ]
        outs = wf.settings.output_channels or [c.name for c in wf.state.channels if c.io == "out"]
        start = self.comp("StartNode", "START", "start", inputs=[self.channel_type(c) for c in ins],
                          outputs=[self.channel_type(c) for c in ins], branches=["next"])  # fmt: skip
        end = self.comp("EndNode", "END", "end", inputs=[self.channel_type(c) for c in outs],
                        outputs=[self.channel_type(c) for c in outs], branches=[], branch_name="next")  # fmt: skip
        self.outputs[start["id"]] = ins
        spec_id = {"START": start["id"], "END": end["id"]}
        nodes = [start]
        for n in wf.nodes:
            if n.disabled:
                self.report.append(f"Step '{n.name}' is turned off and is not exported.")
                continue
            comp = self.node(n, models.get(wired.get(n.id, "")))
            spec_id[n.id] = comp["id"]
            nodes.append(comp)
        nodes.append(end)
        by_id = {c["id"]: c for c in nodes}
        for sid, names in self.outputs.items():
            for name in names:
                self.writers.setdefault(name, []).append(spec_id.get(sid, sid))

        control = []
        for e in wf.edges:
            if e.kind != "flow" or e.source.node not in spec_id or e.target.node not in spec_id:
                continue
            src = by_id[spec_id[e.source.node]]
            branch = None
            if src["component_type"] == "BranchingNode":
                route = e.source.route
                branch = route if route in src["branches"] else "default"
            control.append(self.comp("ControlFlowEdge", e.id, e.id, from_node=self.ref(src), from_branch=branch,
                                     to_node=self.ref(by_id[spec_id[e.target.node]])))  # fmt: skip
        seen: dict[tuple[str, str | None], int] = {}
        for c in control:
            k = (c["from_node"]["$component_ref"], c["from_branch"])
            seen[k] = seen.get(k, 0) + 1
        if any(v > 1 for v in seen.values()):
            self.report.append("Steps that run in parallel: Agent Spec flows follow one path at a time, so a runtime "
                               "may run only one of the next steps.")  # fmt: skip

        data = []
        for comp in nodes:
            for inp in comp.get("inputs", []) if comp["component_type"] != "StartNode" else []:
                name = inp["title"]
                if comp["component_type"] == "BranchingNode":
                    name = comp["metadata"].get("branching_key") or ""
                for sid in self.writers.get(name, []):
                    if sid == comp["id"]:
                        continue
                    given = next((o for o in by_id[sid].get("outputs", []) if o["title"] == name), {})
                    if given.get("type") != inp.get("type"):
                        self.report.append(f"'{name}' is {given.get('type')} where it is written but "
                                           f"{inp.get('type')} where '{comp['name']}' reads it; Agent Spec needs one "
                                           "type, so that data link is left out.")  # fmt: skip
                        continue
                    key = f"{sid}/{name}->{comp['id']}"
                    data.append(self.comp(
                        "DataFlowEdge", key, f"{name}_to_{comp['name']}", source_node=self.ref(by_id[sid]),
                        source_output=name, destination_node=self.ref(comp), destination_input=inp["title"],
                    ))  # fmt: skip
        for comp in nodes:
            self.refs[comp["id"]] = comp
        flow = self.comp(
            "Flow", "flow", wf.name, inputs=start["inputs"], outputs=end["outputs"],
            start_node={"$component_ref": start["id"]}, nodes=[{"$component_ref": c["id"]} for c in nodes],
            control_flow_connections=control, data_flow_connections=data,
        )  # fmt: skip
        flow["description"] = wf.description or None
        flow["metadata"] = {"agentcanvas": {"ir": wf.model_dump(mode="json")}}
        flow["$referenced_components"] = self.refs
        flow["agentspec_version"] = VERSION
        return flow, list(dict.fromkeys(self.report))


def _writes(n: Any) -> list[str]:
    cfg = n.config
    if n.type == "core.set":
        return list((cfg.get("values") or {}).keys())
    out = cfg.get("output")
    return [out] if isinstance(out, str) and out else []


# ------------------------------------------------------------------ import ----
def import_flow(
    doc: dict[str, Any], *, wf_id: str, name: str | None = None
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    """An IR document (not yet validated) built from an Agent Spec flow or agent, what was approximated, and the
    canvas layout when the file carries one."""
    if not isinstance(doc, dict) or "component_type" not in doc:
        raise AgentSpecError("not an Agent Spec document: it has no component_type")
    refs: dict[str, Any] = {}
    _collect(doc, refs)

    def deref(x: Any) -> Any:
        while isinstance(x, dict) and "$component_ref" in x:
            if x["$component_ref"] not in refs:
                raise AgentSpecError(f"reference {x['$component_ref']} is not defined in the document")
            x = refs[x["$component_ref"]]
        return x

    ours = (doc.get("metadata") or {}).get("agentcanvas") or {}
    kept = ours.get("ir") if isinstance(ours, dict) else None
    if doc["component_type"] == "Flow" and isinstance(kept, dict):
        ir = {**kept, "id": wf_id, "name": name or kept.get("name") or doc.get("name") or "Imported flow"}
        layout = ours.get("layout")
        return (
            ir,
            ["Restored exactly from the AgentCanvas data inside the file."],
            layout if isinstance(layout, dict) else {},
        )
    if doc["component_type"] in ("Agent", "A2AAgent"):
        doc = _wrap_agent(doc)
    if doc["component_type"] != "Flow":
        raise AgentSpecError(f"cannot import a {doc['component_type']}: expected a Flow or an Agent")
    ir, report = _Importer(doc, deref, wf_id, name).run()
    return ir, report, {}


def _collect(x: Any, refs: dict[str, Any]) -> None:
    if isinstance(x, dict):
        for k, v in (x.get("$referenced_components") or {}).items():
            refs[k] = v
        if "component_type" in x and isinstance(x.get("id"), str):
            refs.setdefault(x["id"], x)
        for v in x.values():
            _collect(v, refs)
    elif isinstance(x, list):
        for v in x:
            _collect(v, refs)


def _wrap_agent(agent: dict[str, Any]) -> dict[str, Any]:
    node = {"component_type": "AgentNode", "id": "agent_node", "name": agent.get("name") or "agent", "agent": agent}
    start = {"component_type": "StartNode", "id": "start", "name": "start", "inputs": []}
    end = {"component_type": "EndNode", "id": "end", "name": "end", "outputs": []}
    return {
        "component_type": "Flow", "name": agent.get("name") or "Imported agent", "start_node": start,
        "nodes": [start, node, end], "data_flow_connections": [],
        "control_flow_connections": [{"from_node": start, "to_node": node}, {"from_node": node, "to_node": end}],
    }  # fmt: skip


def ident(text: str, taken: set[str], fallback: str = "step") -> str:
    """A snake_case name (IR identifiers) not in `taken`; adds it."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(text or ""))
    s = re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")
    if not s or not s[0].isalpha():
        s = f"{fallback}_{s}".strip("_")
    s = s[:58]
    out, i = s, 2
    while out in taken or out in RESERVED_NAMES or not IDENT.match(out):
        out, i = f"{s}_{i}", i + 1
    taken.add(out)
    return out


class _Importer:
    def __init__(self, flow: dict[str, Any], deref: Any, wf_id: str, name: str | None):
        self.flow, self.deref, self.wf_id, self.name = flow, deref, wf_id, name
        self.report: list[str] = []
        self.channels: dict[str, dict[str, Any]] = {}
        self.channel_names: set[str] = set()
        self.node_names: set[str] = set()
        self.resources: list[dict[str, Any]] = []
        self.models: dict[str, str] = {}  # spec llm config id -> resource id
        self.edges: list[dict[str, Any]] = []

    def channel(self, title: str, schema: dict[str, Any] | None = None, io: str = "inout") -> str:
        key = str(title)
        if key not in self.channels:
            name = ident(key, self.channel_names, "field")
            typ = {k: v for k, v in (schema or {}).items() if k not in ("title", "description", "default")}
            ch: dict[str, Any] = {"name": name, "type": typ or {"type": "string"}, "io": io}
            if (schema or {}).get("description"):
                ch["description"] = schema["description"]  # type: ignore[index]
            self.channels[key] = ch
        elif io != "inout" and self.channels[key]["io"] == "inout":
            self.channels[key]["io"] = io
        return self.channels[key]["name"]

    def model(self, cfg: Any) -> str | None:
        cfg = self.deref(cfg)
        if not isinstance(cfg, dict):
            return None
        if cfg.get("id") in self.models:
            return self.models[cfg["id"]]
        kind, model_id = cfg.get("component_type"), str(cfg.get("model_id", ""))
        kept = (cfg.get("metadata") or {}).get("agentcanvas", {}).get("model")
        conf: dict[str, Any] = {}
        if kept:
            conf["model"] = kept
        elif kind == "OpenAiConfig":
            conf["model"] = f"openai:{model_id}"
        elif kind == "OllamaConfig":
            conf["model"] = f"ollama:{model_id}"
        elif kind == "VllmConfig":
            conf["model"] = f"vllm:{model_id}"
        elif kind == "GeminiConfig":
            conf["model"] = f"google_genai:{model_id}"
        else:
            conf["model"] = f"openai_compatible:{model_id}"
            if kind != "OpenAiCompatibleConfig":
                self.report.append(f"Model '{cfg.get('name')}' ({kind}) became an OpenAI-compatible model; check it.")
        provider = conf["model"].partition(":")[0]
        if cfg.get("url") and cfg["url"] != _LOCAL.get(provider):
            conf["base_url"] = cfg["url"]
        params = cfg.get("default_generation_parameters") or {}
        conf.update({k: params[k] for k in ("temperature", "max_tokens") if k in params})
        rid = f"r_{len(self.resources) + 1}"
        taken = {r["name"] for r in self.resources}
        res_name = ident(cfg.get("name") or "model", taken, "model")
        self.resources.append({"id": rid, "type": "model.chat", "name": res_name, "config": conf})
        self.models[str(cfg.get("id"))] = rid
        return rid

    def run(self) -> tuple[dict[str, Any], list[str]]:
        flow, deref = self.flow, self.deref
        nodes = [deref(n) for n in flow.get("nodes") or []]
        start = deref(flow.get("start_node")) or next(
            (n for n in nodes if n.get("component_type") == "StartNode"), None
        )
        ids: dict[int, str] = {}  # id(spec node) -> IR node id or START/END
        for n in nodes:
            if n is start or n.get("component_type") == "StartNode":
                ids[id(n)] = "START"
            elif n.get("component_type") == "EndNode":
                ids[id(n)] = "END"
        data_in: dict[tuple[int, str], str] = {}  # (node, input) -> channel name
        for e in flow.get("data_flow_connections") or []:
            src, dst = deref(e.get("source_node")), deref(e.get("destination_node"))
            out = str(e.get("source_output"))
            schema = next((o for o in (src or {}).get("outputs") or [] if o.get("title") == out), None)
            data_in[(id(dst), str(e.get("destination_input")))] = self.channel(out, schema)
        for n in nodes:
            if ids.get(id(n)) == "START":
                for p in n.get("inputs") or n.get("outputs") or []:
                    self.channel(str(p.get("title")), p, "in")
            if ids.get(id(n)) == "END":
                for p in n.get("outputs") or n.get("inputs") or []:
                    self.channel(data_in.get((id(n), str(p.get("title"))), str(p.get("title"))), p, "out")

        ir_nodes: list[dict[str, Any]] = []
        for n in nodes:
            if id(n) in ids:
                continue
            node = self.node(n, lambda inp, n=n: data_in.get((id(n), inp)) or self.channel(inp))
            node["id"] = f"n_{len(ir_nodes) + 1}"
            ids[id(n)] = node["id"]
            if rid := node.pop("_model", None):
                self.edges.append({"id": f"w_{node['id']}", "kind": "wiring", "source": {"node": rid},
                                   "target": {"node": node["id"], "port": "model"}})  # fmt: skip
            ir_nodes.append(node)

        for i, e in enumerate(flow.get("control_flow_connections") or []):
            src, dst = deref(e.get("from_node")), deref(e.get("to_node"))
            if id(src) not in ids or id(dst) not in ids:
                raise AgentSpecError("a control flow edge points at a step that is not in the flow")
            source: dict[str, Any] = {"node": ids[id(src)]}
            if (src or {}).get("component_type") == "BranchingNode":
                source["port"] = f"route:{self.route(e.get('from_branch') or 'default')}"
            self.edges.append({"id": f"e{i + 1}", "kind": "flow", "source": source, "target": {"node": ids[id(dst)]}})

        channels = list(self.channels.values())
        if any(n["type"] in ("core.agent", "agent.a2a") for n in ir_nodes) and "messages" not in self.channel_names:
            channels.insert(0, {"name": "messages", "type": {"type": "array"}, "reducer": "add_messages",
                                "storage": "delta"})  # fmt: skip
        ir = {
            "ir_version": "1.1",
            "id": self.wf_id,
            "name": self.name or flow.get("name") or "Imported flow",
            "description": flow.get("description") or "",
            "state": {"channels": channels},
            "resources": self.resources,
            "nodes": ir_nodes,
            "edges": self.edges,
            "metadata": {"imported_from": "agentspec"},
        }
        outs = [c["name"] for c in channels if c.get("io") == "out"]
        if outs:
            ir["settings"] = {"output_channels": outs}
        return ir, list(dict.fromkeys(self.report))

    def route(self, branch: str) -> str:
        return ident(branch, set(), "route")

    def node(self, n: dict[str, Any], channel_for: Any) -> dict[str, Any]:
        kind = n.get("component_type")
        name = ident(n.get("name") or kind or "step", self.node_names)
        kept = (n.get("metadata") or {}).get("agentcanvas") or {}
        out: dict[str, Any] = {"type": "core.set", "name": name, "label": n.get("name") or None}
        if kept.get("type") and isinstance(kept.get("config"), dict):
            out.update(type=kept["type"], config=kept["config"], label=kept.get("label") or out["label"])
            return out

        def template(text: str) -> str:
            return _SPEC_VAR.sub(lambda m: "{{ state." + channel_for(m.group(1)) + " }}", text or "")

        def first_output(default: str) -> str:
            outs = n.get("outputs") or []
            return self.channel(outs[0].get("title"), outs[0]) if outs else self.channel(default)

        if kind == "LlmNode":
            out.update(type="core.llm", config={"prompt": template(n.get("prompt_template", "")) or "Answer.",
                                                "output": first_output("generated_text")})  # fmt: skip
            self.wire(out, n.get("llm_config"))
        elif kind == "AgentNode":
            agent = self.deref(n.get("agent")) or {}
            self.report.append(f"Agent '{name}' works on the chat (the messages field); start the run with a chat "
                               "message, or set the input it needs in an earlier step.")  # fmt: skip
            if agent.get("component_type") == "A2AAgent":
                out.update(type="agent.a2a", config={"url": agent.get("agent_url", ""), "output": "messages"})
                timeout = (self.deref(agent.get("connection_config")) or {}).get("timeout")
                if timeout:
                    out["config"]["timeout"] = min(float(timeout), 3600.0)
            elif agent.get("component_type") == "Agent":
                out.update(type="core.agent", config={"system_prompt": agent.get("system_prompt") or ""})
                self.wire(out, agent.get("llm_config"))
                if agent.get("tools"):
                    self.report.append(f"Agent '{name}': its {len(agent['tools'])} tools were not imported; add "
                                       "them on the canvas.")  # fmt: skip
            else:
                self.placeholder(out, n, f"its {agent.get('component_type')} agent")
        elif kind == "InputMessageNode":
            out.update(type="core.human_input", config={"prompt": template(n.get("message") or "") or "Your input?",
                                                         "output": first_output("user_input")})  # fmt: skip
        elif kind == "OutputMessageNode":
            out["config"] = {"values": {self.channel("message"): template(n.get("message") or "")}}
        elif kind == "BranchingNode":
            key = channel_for("branching_mapping_key")
            cases = [{"when": f'state.{key} == "{v}"', "route": self.route(b)}
                     for v, b in (n.get("mapping") or {}).items()]  # fmt: skip
            out.update(type="core.router", config={"cases": cases, "default": "default"})
        else:
            self.placeholder(out, n, "this step")
        return out

    def placeholder(self, out: dict[str, Any], n: dict[str, Any], what: str) -> None:
        out["config"] = {"values": {self.channel(f"{out['name']}_result"): f"TODO: {n.get('component_type')}"}}
        self.report.append(f"Step '{out['name']}': AgentCanvas cannot run {what} ({n.get('component_type')}); it is "
                           "a placeholder to replace.")  # fmt: skip

    def wire(self, node: dict[str, Any], llm: Any) -> None:
        rid = self.model(llm)
        if rid:
            node["_model"] = rid
