"""The Echo adapter (S20): a tiny second framework that proves the adapter seams.

It runs simple flows itself, with no framework underneath: set values, paths (CEL rules), wait for all, approvals,
questions, Python steps from the Workspace, and AI steps that answer with the scripted reply for their model (the
same `mocks` saved test cases use) or repeat their prompt. Steps run in LangGraph-style supersteps; a paused run
keeps its state in memory, so a server restart forgets it. It declares no optional abilities, so the routes for
state, history, memory and MCP browsing refuse its workflows with a plain message.

It passes the conformance kit (`agentcanvas.conformance`) next to the LangChain adapter. Not for production.
"""

from __future__ import annotations

import inspect
import io
import json
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from types import SimpleNamespace
from typing import Any

from jsonschema import Draft202012Validator

from agentcanvas.errors import WorkflowHasErrors
from agentcanvas.expr import eval_condition, render_template
from agentcanvas.ir.models import Node, Workflow
from agentcanvas.registry import default_registry
from agentcanvas.registry.adapters import all_manifests
from agentcanvas.runtime import paused as paused_runs
from agentcanvas.runtime.types import InterruptInfo, RunSpec
from agentcanvas.workspace.loader import load_attr

YES_WORDS = {"yes", "y", "true", "approve", "approved", "ok"}


class Interrupted(Exception):
    def __init__(self, payload: dict[str, Any]):
        super().__init__("interrupted")
        self.payload = payload


@dataclass
class Paused:
    """A run waiting for a person: where it stopped and what it had done."""

    values: dict[str, Any]
    node_id: str  # the step that asked
    frontier: list[str]  # steps still to run in that superstep (the asking one first)
    done: set[str]
    payload: dict[str, Any]
    interrupt_id: str
    steps: int
    scripted: dict[str, list[Any]] = field(default_factory=dict)
    next_round: list[str] = field(default_factory=list)  # steps the round's finished steps lead to
    arrived: dict[str, set[str]] = field(default_factory=dict)  # "wait for all" step -> branches in since it ran

    def to_dict(self) -> dict[str, Any]:
        """JSON for the paused-run store (runtime/paused.py): any replica, or a restarted server, continues it."""
        out = asdict(self)
        out["done"] = sorted(self.done)
        out["arrived"] = {k: sorted(v) for k, v in self.arrived.items()}
        return out

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Paused:
        return cls(
            **{
                **d,
                "done": set(d.get("done") or []),
                "arrived": {k: set(v) for k, v in (d.get("arrived") or {}).items()},
            }
        )


def _reduce(reducer: str, old: Any, new: Any) -> Any:
    if reducer == "append":
        return [*(old or []), *(new if isinstance(new, list) else [new])]
    if reducer == "add_messages":
        return [*(old or []), *(new if isinstance(new, list) else [new])]
    if reducer == "sum":
        return (old or 0) + (new or 0)
    if reducer == "merge":
        return {**(old or {}), **(new or {})}
    return new


@dataclass
class _Rounds:
    """Which steps run next, as in `adapters/supersteps.py`: a "wait for all" step runs once every branch into it
    has arrived since it last ran; a resumed round goes on as it was and the round after it is kept apart."""

    following: list[str]
    arrived: dict[str, set[str]]
    resumed: bool = False

    def lead(self, echo: EchoAdapter, wf: Workflow, node_id: str, values: dict[str, Any]) -> list[str]:
        nxt = echo._next(wf, node_id, values)
        for target in nxt:
            node = wf.node(target)
            if node is not None and node.type == "core.join":
                self.arrived.setdefault(target, set()).add(node_id)
        return nxt

    def ready(self, echo: EchoAdapter, wf: Workflow, frontier: list[str]) -> list[str]:
        if self.resumed:
            self.resumed = False
            return frontier
        out = []
        for node_id in frontier:
            if wf.require_node(node_id).type != "core.join":
                out.append(node_id)
            elif echo._before(wf, node_id) <= self.arrived.get(node_id, set()):
                self.arrived[node_id] = set()
                out.append(node_id)
        return out


class EchoAdapter:
    id = "echo"
    manifest = all_manifests()["echo"]

    # paused runs live with their conversation (runtime/paused.py), like the other frameworks' runs

    # ------------------------------------------------------------ compile ----
    def _check(self, wf: Workflow, registry: Any = None) -> None:
        from agentcanvas.validation import validate

        errors = [d.model_dump() for d in validate(wf, registry or default_registry()) if d.severity == "error"]
        if errors:
            raise WorkflowHasErrors("; ".join(f"{d['code']} {d['message']}" for d in errors), errors=errors)

    def generate(self, wf: Workflow, registry: Any = None) -> str:
        """A readable outline of how Echo runs the workflow (it has no code of its own to generate)."""
        self._check(wf, registry)
        lines = [
            f'"""{wf.name}: run by the Echo demo engine (no framework code is generated)."""',
            "",
            "STEPS = {",
        ]
        for n in wf.nodes:
            nxt = sorted({e.target.node for e in wf.flow_edges() if e.source.node == n.id})
            lines.append(f"    {n.name!r}: {{'type': {n.type!r}, 'next': {nxt!r}}},")
        lines += ["}", f"START = {sorted({e.target.node for e in wf.flow_edges() if e.source.node == 'START'})!r}", ""]
        return "\n".join(lines)

    def package(
        self,
        wf: Workflow,
        registry: Any = None,
        *,
        workspace: Any = None,
        test_cases: list[dict[str, Any]] | None = None,
        version: str = "0.0.0",
        target: str = "local",
    ) -> tuple[str, bytes]:
        source = self.generate(wf, registry)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("workflow.ir.json", json.dumps(wf.model_dump(mode="json"), indent=2))
            z.writestr("echo_outline.py", source)
            z.writestr(
                "README.md",
                f"# {wf.name} {version}\n\nDesigned for the Echo demo engine, which only runs inside AgentCanvas. "
                "Switch the workflow to LangGraph (Workflow settings, Framework) to export a service you can deploy.\n",
            )
        return f"{wf.name.lower().replace(' ', '_')}-{version}-echo.zip", buf.getvalue()

    # ------------------------------------------------------------ runtime ----
    @asynccontextmanager
    async def persistence(self, database_url: str | None) -> AsyncIterator[None]:
        yield  # paused runs are kept by runtime/paused.py (the database on a server)

    def default_resources(self) -> Any:
        return SimpleNamespace(workspace=None, script_runner=None)

    def new_resources(self, *, resources_class: Any = None, **kw: Any) -> Any:
        return SimpleNamespace(workspace=kw.get("workspace"), script_runner=kw.get("script_runner"))

    def script(self, resources: Any, import_path: str) -> Any:
        runner = getattr(resources, "script_runner", None)
        if runner is not None:  # sandboxed: every workspace path, not only scripts.
            return runner(import_path)
        fn = load_attr(import_path, getattr(resources, "workspace", None))

        async def call(state: dict[str, Any]) -> Any:
            out = fn(state)
            return await out if inspect.isawaitable(out) else out

        return call

    async def execute(self, spec: RunSpec, resources: Any) -> AsyncIterator[dict[str, Any]]:
        wf = spec.workflow
        try:
            if spec.resume is not None:
                saved = await paused_runs.load(spec.thread_id)
                await paused_runs.store().drop(spec.thread_id)
                paused = Paused.from_dict(saved) if saved is not None else None
                if paused is None:
                    yield {"type": "RUN_ERROR", "code": "error", "message": "nothing to resume on this conversation"}
                    return
                values, frontier, done, steps = paused.values, paused.frontier, paused.done, paused.steps
                scripted = paused.scripted
                answers = {paused.node_id: spec.resume.value}
                rounds = _Rounds(list(paused.next_round), {k: set(v) for k, v in paused.arrived.items()}, True)
            else:
                await paused_runs.store().drop(spec.thread_id)
                values = {c.name: c.default for c in wf.state.channels}
                values.update(spec.input or {})
                rounds = _Rounds([], {})
                frontier, done, steps = rounds.lead(self, wf, "START", values), set(), 0
                scripted = {k: list(v) for k, v in spec.mocks.items()}
                answers = {}
            async for ev in self._loop(wf, spec, resources, values, frontier, done, steps, scripted, answers, rounds):
                yield ev
        except Exception as exc:  # noqa: BLE001 - every failure reaches the UI as RUN_ERROR
            yield {"type": "RUN_ERROR", "code": "error", "message": f"{type(exc).__name__}: {exc}"}

    async def _loop(
        self,
        wf: Workflow,
        spec: RunSpec,
        resources: Any,
        values: dict[str, Any],
        frontier: list[str],
        done: set[str],
        steps: int,
        scripted: dict[str, list[Any]],
        answers: dict[str, Any],
        rounds: _Rounds,
    ) -> AsyncIterator[dict[str, Any]]:
        reducers = {c.name: c.reducer for c in wf.state.channels}
        while frontier := rounds.ready(self, wf, [n for n in frontier if n != "END"]):
            steps += 1
            if steps > wf.settings.recursion_limit:
                yield {
                    "type": "RUN_ERROR",
                    "code": "recursion_limit",
                    "message": f"stopped after {wf.settings.recursion_limit} steps without reaching END",
                }
                return
            following, rounds.following = rounds.following, []
            for i, node_id in enumerate(frontier):
                node = wf.require_node(node_id)
                yield {"type": "STEP_STARTED", "stepName": node.name, "canvas_node_id": node_id, "task_id": node_id}
                try:
                    update = {} if node.disabled else await self._run(wf, node, values, resources, scripted, answers)
                except Interrupted as intr:
                    iid = f"{spec.run_id}:{node_id}"
                    paused = Paused(
                        values=values, node_id=node_id, frontier=[node_id, *frontier[i + 1 :]],
                        done=done, payload=intr.payload, interrupt_id=iid, steps=steps - 1, scripted=scripted,
                        next_round=following, arrived={k: set(v) for k, v in rounds.arrived.items() if v},
                    )  # fmt: skip
                    await paused_runs.save(spec.thread_id, json.loads(json.dumps(paused.to_dict(), default=str)))
                    yield {
                        "type": "STEP_FINISHED", "stepName": node.name, "canvas_node_id": node_id,
                        "status": "interrupted", "task_id": node_id,
                    }  # fmt: skip
                    info = InterruptInfo(
                        id=iid, kind=intr.payload["kind"], canvas_node_id=node_id, payload=intr.payload
                    )
                    yield {"type": "CUSTOM", "name": "ac.interrupt", "value": asdict(info)}
                    yield {
                        "type": "RUN_FINISHED", "outcome": "interrupt", "breakpoint": False,
                        "interrupts": [asdict(info)], "next": [node_id], "checkpoint_id": None,
                        "output": self._outputs(wf, values),
                    }  # fmt: skip
                    return
                answers.pop(node_id, None)
                for k, v in update.items():
                    values[k] = _reduce(reducers.get(k, "replace"), values.get(k), v)
                done.add(node_id)
                yield {
                    "type": "STEP_FINISHED", "stepName": node.name, "canvas_node_id": node_id,
                    "status": "ok", "task_id": node_id,
                }  # fmt: skip
                if update:
                    yield {"type": "STATE_DELTA", "canvas_node_id": node_id, "delta": update}
                for nxt in rounds.lead(self, wf, node_id, values):
                    if nxt not in following:
                        following.append(nxt)
            frontier = [n for n in following if n != "END"]
        yield {
            "type": "RUN_FINISHED", "outcome": "success", "breakpoint": False, "interrupts": [], "next": [],
            "checkpoint_id": None, "output": self._outputs(wf, values),
        }  # fmt: skip

    # -------------------------------------------------------------- steps ----
    async def _run(
        self,
        wf: Workflow,
        node: Node,
        values: dict[str, Any],
        resources: Any,
        scripted: dict[str, list[Any]],
        answers: dict[str, Any],
    ) -> dict[str, Any]:
        cfg = node.config
        if node.type == "core.set":
            return {k: render_template(v, values) for k, v in cfg.get("values", {}).items()}
        if node.type in ("core.router", "core.join"):
            return {}
        if node.type == "core.llm":
            prompt = render_template(cfg["prompt"], values)
            model = self._model_name(wf, node.id)
            replies = scripted.get(model) if model else None
            if replies:
                reply = replies.pop(0)
                text = reply.get("content", "") if isinstance(reply, dict) else str(reply)
            else:
                text = prompt
            return {cfg["output"]: text}
        if node.type == "core.approval":
            if node.id not in answers:
                raise Interrupted({"kind": "approval", "canvas_node_id": node.id, "value": values.get(cfg["show"])})
            decision = answers[node.id]
            decision = decision if isinstance(decision, dict) else {"approved": decision}
            approved = decision.get("approved")
            if isinstance(approved, str):
                approved = approved.strip().lower() in YES_WORDS
            out: dict[str, Any] = {cfg["output"]: bool(approved)}
            if decision.get("value") is not None:
                out[cfg["show"]] = decision["value"]
            return out
        if node.type == "core.human_input":
            request: dict[str, Any] = {
                "kind": "input", "canvas_node_id": node.id, "prompt": render_template(cfg["prompt"], values),
            }  # fmt: skip
            if cfg.get("response_schema"):
                request["response_schema"] = cfg["response_schema"]
            if node.id not in answers:
                raise Interrupted(request)
            answer = answers[node.id]
            if cfg.get("response_schema"):
                problems = sorted(Draft202012Validator(cfg["response_schema"]).iter_errors(answer), key=str)
                if problems:
                    answers.pop(node.id)
                    raise Interrupted({**request, "error": problems[0].message})
            return {cfg["output"]: answer}
        if node.type == "core.script":
            out = await self.script(resources, cfg["import_path"])(dict(values))
            return dict(out or {})
        raise NotImplementedError(f"the Echo engine cannot run '{node.type}' blocks")

    # ------------------------------------------------------------ helpers ----
    def _model_name(self, wf: Workflow, node_id: str) -> str | None:
        for e in wf.edges:
            if e.kind == "wiring" and e.target.node == node_id and e.target.port == "model":
                res = wf.resource(e.source.node)
                return res.name if res else None
        return None

    def _next(self, wf: Workflow, node_id: str, values: dict[str, Any]) -> list[str]:
        edges = [e for e in wf.flow_edges() if e.source.node == node_id]
        node = wf.node(node_id)
        if node is not None and node.type == "core.router":
            route = node.config.get("default", "default")
            for case in node.config.get("cases", []):
                if eval_condition(case["when"], values):
                    route = case["route"]
                    break
            edges = [e for e in edges if e.source.route == route]
        return [e.target.node for e in edges]

    def _before(self, wf: Workflow, node_id: str) -> set[str]:
        return {e.source.node for e in wf.flow_edges() if e.target.node == node_id}

    def _outputs(self, wf: Workflow, values: dict[str, Any]) -> dict[str, Any]:
        wanted = wf.settings.output_channels
        if wanted is None:
            wanted = [c.name for c in wf.state.channels if c.io != "private"]
        return {k: values.get(k) for k in wanted if k in values}
