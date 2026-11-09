# 03 — Walking-Skeleton Tutorial

The **walking skeleton** is the thinnest version of AgentCanvas that works end-to-end: a canvas shows a workflow, you press Run, nodes light up live, a human approves a step, and the same workflow can be exported as Python code that behaves identically. It lives in [`reference/`](./reference/) and is fully tested.

Do this tutorial **before** writing product code: every later ticket extends one of these files.

Prerequisite: [01-environment-setup.md](./01-environment-setup.md) §4–5 work on your machine.

---

## Part 1 — The data: Graph IR (`agentcanvas/ir/models.py`)

Open `reference/backend/examples/support_triage.ir.json` next to `models.py`.

| JSON section | Model | Meaning |
|--------------|-------|---------|
| `state.channels[]` | `Channel` | Fields of the shared state. `reducer` says how concurrent writes combine. |
| `resources[]` | `Resource` | Things injected into nodes: chat models (`model.chat`), Python tools (`tool.python`). |
| `nodes[]` | `Node` | Runtime steps. `type` picks the behaviour, `config` holds its settings, `name` becomes the LangGraph node name. |
| `edges[]` | `Edge` | `kind: "flow"` = runs next; `kind: "wiring"` = resource → node port (`model`, `tools`). Router outputs use ports `route:<name>`. |

Key design points (read the code comments):
- `extra="forbid"`: unknown JSON keys raise errors (typos never pass silently).
- Names are validated as snake_case because they become Python identifiers in generated code.
- Helper methods (`wired_into`, `require_node`, `Endpoint.route`) keep other modules short.

**Try it:**
```bash
cd reference/backend
uv run python -c "
import json; from agentcanvas.ir.models import Workflow
wf = Workflow.model_validate(json.load(open('examples/support_triage.ir.json')))
print([n.name for n in wf.nodes]); print(wf.wired_into('n_ag', 'tools'))"
```
Then change a node `name` to `Reply Drafter` and re-run → you get a validation error explaining the snake_case rule.

## Part 2 — Checking: Validator (`agentcanvas/ir/validate.py`)

`validate(wf)` returns a list of `Diagnostic(code, severity, message, node_id)`. Errors block running and code generation. Implemented rules:

| Code | Rule |
|------|------|
| E000 | duplicate ids or node names |
| E001 | LLM/Agent node without a model wired to its `model` port |
| E003 | edge points to an unknown node |
| E010 | no path from START to END |
| E011 | node unreachable from START |
| E012 | router route without an outgoing edge |
| E013 | router condition is not valid CEL |
| E021 | node writes a channel that does not exist |
| E080 | `import_path` outside the workspace packages (`scripts.`, `tools.`, `examples.`) |
| W001 | loop without a router exit |

**Try it:** `uv run pytest -q tests/test_validate.py` then read each test — every test breaks the example in one way and checks the code.

## Part 3 — Safe expressions (`agentcanvas/expr.py`)

- Router conditions are **CEL**: `state.urgency == "high"`, `size(state.items) > 3`. CEL cannot run arbitrary code, loop forever or touch files.
- Prompt templates are **sandboxed Jinja**: `"Classify: {{ state.ticket }}"`. `StrictUndefined` makes a missing variable an error instead of silently printing nothing.

## Part 4 — Behaviour: Node library (`agentcanvas/runtime/nodes.py`)

Each widget type has a *builder* that returns a LangGraph node:

| IR type | Builder | What the node does |
|---------|---------|--------------------|
| `core.llm` | `llm_node(model, prompt, output)` | renders the prompt, calls the model once, writes the text into `output` |
| `core.agent` | `agent_node(name, model, tools, system_prompt, approve_tools)` | `create_agent(...)`; adds `HumanInTheLoopMiddleware` for tools needing approval; uses the `messages` channel |
| `core.router` | `router_fn(cases, default)` + `passthrough` | first CEL case that is true picks the route |
| `core.approval` | `approval_node(node_id, show, output)` | `interrupt()` showing a channel; resume with `{"approved": bool, "value": optional edit}` |
| `core.set` | `set_node(values)` | writes templated strings into channels |
| `core.script` | `script_node(import_path)` | calls a user function `module:function(state) -> dict`; `safe_import` refuses modules outside the workspace (e.g. `os:system`) |

`make_state_type()` turns IR channels into a `TypedDict` with the right reducers at runtime. `Resources` turns resource configs into objects (`init_chat_model(...)`, imported tools) and lets tests **override** them with fakes.

> **Why one library?** The interpreter (dev runs) and the generated code (export) both call these builders. That is how the canvas behaviour equals the deployed behaviour — and `test_generated_code_matches_interpreter` proves it on every commit.

## Part 5 — Running: Interpreter (`agentcanvas/runtime/interpreter.py`)

`build_graph(wf, resources, checkpointer)`:
1. builds the `State` type from channels;
2. adds one LangGraph node per IR node (`metadata={"canvas_node_id": ...}` for tracing);
3. adds normal edges for flow edges;
4. adds `add_conditional_edges(router_name, route_fn, {route: target})` for routers;
5. compiles with the checkpointer (needed for approvals, history and time travel).

## Part 6 — Exporting: Code generator (`agentcanvas/compiler/codegen.py` + `templates/graph.py.j2`)

`generate(wf)` validates, prepares edge lists, renders the Jinja template, then runs `ruff format` so the output is deterministic and readable. Look at the output:
```bash
curl -s localhost:8000/workflows/wf_support_triage/code | less
```
Each block starts with `# ── @canvas-node <id> "<label>" (<type>) ──` — the **source map** that links code back to the canvas.

Template rules (learned the hard way):
- Use the `py` filter (`repr`) for Python literals — JSON escapes quotes as `'`.
- Read optional config keys with `n.config.get("key", default)`; the environment uses `StrictUndefined`, so `n.config.key` fails when the key is missing.

## Part 7 — Live events: Translator (`agentcanvas/runtime/events.py`)

The runner calls `graph.astream(..., stream_mode=["tasks","updates","messages","custom"], subgraphs=True, version="v2")` and converts each part into **canonical events**:

| LangGraph part | Canonical event |
|----------------|-----------------|
| `tasks` start (top level) | `STEP_STARTED {canvas_node_id}` |
| `tasks` result | `STEP_FINISHED {status: ok / error / interrupted}` |
| `updates` with `__interrupt__` | `CUSTOM ac.interrupt {id, payload}` |
| other `updates` | `STATE_DELTA {canvas_node_id, delta}` |
| `messages` (AI chunks only) | `TEXT_MESSAGE_CONTENT {canvas_node_id, delta}` |
| `custom` | `CUSTOM ac.custom` |

Nested agent activity arrives with a non-empty namespace `ns` (e.g. `('reply_drafter:<task-id>',)`); the translator maps it to the parent canvas node.

## Part 8 — The API (`agentcanvas/api/app.py`)

| Endpoint | Purpose |
|----------|---------|
| `POST /workflows` | store IR, return diagnostics |
| `GET /workflows/{id}` | IR for the canvas |
| `GET /workflows/{id}/code` | generated Python |
| `POST /workflows/{id}/runs/stream` | start a run → SSE events |
| `POST /threads/{id}/resume` | answer an interrupt → SSE events |
| `GET /threads/{id}/state` | values, next nodes, pending interrupts |
| `GET /threads/{id}/history` | checkpoints (newest first) |
| `POST /threads/{id}/state` | edit state (time-travel edit) |

`resources_factory(thread_id)` decides which models/tools a thread uses. Real models share one `Resources`; fakes are per thread (see FAQ P-03).

## Part 9 — The canvas (`reference/web/src/`)

| File | Role |
|------|------|
| `ir.ts` | TypeScript types for IR and events (generated from JSON Schema later, M2-T2) |
| `irToFlow.ts` | IR → React Flow nodes/edges with a simple layered layout; wiring edges dashed purple; router routes as edge labels |
| `sse.ts` | `postStream()` — POST + read SSE with `fetch` (the browser `EventSource` only supports GET) |
| `runStore.ts` | Zustand store + pure `reduce(state, event)` (unit-tested) |
| `App.tsx` | loads the IR, renders `<ReactFlow>`, colours nodes by status, Run / Approve / Reject |

Colour code: grey idle · blue running · green ok · red error · amber interrupted.

## Part 10 — Exercise: add your first node type (`core.counter`)

Goal: a node that adds 1 to an integer channel, so users can build a guarded loop (increment → router `state.count >= 3`). This touches every layer — exactly what M3 tickets do for real node types. The solution below is verified (21 tests pass with it).

1. **IR** — allow the type in `Node.type`: add `"core.counter"` to the `Literal[...]`.
2. **Node library** — add to `runtime/nodes.py`:
   ```python
   def counter_node(*, channel: str, step: int = 1) -> Callable:
       """Add `step` to a numeric channel (use with reducer "replace"), e.g. a loop counter."""
       def run(state: Any) -> dict:
           return {channel: (state.get(channel) or 0) + step}
       return run
   ```
3. **Interpreter** — in `build_graph`, before the `core.script` branch:
   ```python
   elif n.type == "core.counter":
       fn = ac.counter_node(channel=cfg["channel"], step=cfg.get("step", 1))
   ```
4. **Template** — before the `core.script` branch in `graph.py.j2`:
   ```jinja
   {%- elif n.type == "core.counter" %}
       builder.add_node({{ n.name | py }}, ac.counter_node(channel={{ n.config.channel | py }}, step={{ n.config.get("step", 1) }}), metadata={"canvas_node_id": {{ n.id | py }}})
   ```
5. **Validator** — nothing new: `E021` already checks `config.channel` exists.
6. **Test** — `tests/test_counter.py`: a loop workflow (START → increment → router → back to increment or END) that must end with `count == 3` **both** interpreted and generated:
   ```python
   async def test_counter_loop_interpreted_and_generated(tmp_path):
       wf = Workflow.model_validate(LOOP)            # LOOP = the IR dict described above
       assert [d.code for d in validate(wf)] == []    # router exit => no W001 warning
       out = await build_graph(wf, Resources(), checkpointer=InMemorySaver()).ainvoke(
           {}, {"configurable": {"thread_id": "t"}})
       assert out["count"] == 3
       path = tmp_path / "loop_graph.py"; path.write_text(generate(wf))
       spec = importlib.util.spec_from_file_location("loop_graph", path)
       mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
       assert (await mod.build_graph().ainvoke({}))["count"] == 3
   ```
   `LOOP` channels: `[{"name": "count", "type": {"type": "integer"}}]`; nodes: `n_inc` (`core.counter`, `config: {"channel": "count"}`) and `n_rt` (`core.router`, case `state.count >= 3` → `done`, default `again`); edges START→n_inc, n_inc→n_rt, `route:again`→n_inc, `route:done`→END.
7. **Frontend** — add `"core.counter"` to the `IRNode["type"]` union in `ir.ts`.
8. Run `uv run pytest -q && uv run ruff check . && uv run pyright` → all green.

## Part 11 — What the skeleton deliberately leaves out

Persistence of workflows (memory only), auth, multi-user, editing on the canvas (it displays and runs), inspector forms, node palette, versioning, secrets, MCP tools, Deep Agents widget, workspaces, deployment. Each is a ticket in [04-implementation-plan.md](./04-implementation-plan.md).
