# 05 — Workspaces, Scripting, Custom Nodes & Custom Events

The Workspace is the "escape hatch" that keeps AgentCanvas honest: anything the widgets can't express is written as code, **but code stays attached to the canvas** as first-class nodes, tools, middleware, and event handlers.

## 1. Workspace concept

- **One workspace per project**, backed by a Git repository (internal or the customer's GitHub/GitLab/Bitbucket).
- Opened in-browser: file tree, Monaco editor with Python LSP (pyright/basedpyright + ruff), terminal, test runner, package manager, preview of generated code, diff view.
- Executes in a **dev sandbox** (microVM) with the project's Python environment (`uv`), same base image as the data plane runtime, so "works in workspace" ⇒ "works in prod".
- **Local dev parity**: `agentcanvas` CLI to clone the workspace locally, run `agentcanvas dev` (starts `langgraph dev` with the interpreted graph + hot reload of workspace code), and push back. VS Code extension (P2) renders the canvas inside VS Code.

### 1.1 Workspace layout (convention)

```
/                                   (Git root of the project)
├── agentcanvas.toml                # project config: stack version, python version, registries, env names
├── pyproject.toml / uv.lock        # user dependencies (merged with stack deps at build)
├── workflows/                      # IR documents (canonical JSON/YAML) + layout files — managed by canvas
│   └── support_triage.ir.json
├── nodes/                          # custom node types (@canvas_node)
├── scripts/                        # script-node bodies
├── tools/                          # custom @tool functions
├── middleware/                     # custom AgentMiddleware classes
├── reducers/                       # custom state reducers
├── events/                         # custom event handlers & event schemas
├── skills/<skill>/SKILL.md …       # Deep Agents skills (scripts/, references/, assets/)
├── memories/AGENTS.md              # seed memory files
├── prompts/                        # prompt templates (.md / .jinja) with front-matter
├── evaluators/                     # custom code evaluators
├── tests/                          # pytest tests (unit + workflow tests)
└── .agentcanvas/                   # generated build output (gitignored)
```

## 2. Script nodes

A Script node is a runtime graph node whose body lives in `scripts/<name>.py`.

```python
# scripts/score_lead.py
from agentcanvas import script, Runtime          # thin helpers; plain LangGraph functions also accepted
from langgraph.types import Command
from pydantic import BaseModel

class In(BaseModel):          # ports inferred from these models (or from reads/writes)
    lead: dict
    enrichment: dict | None = None

class Out(BaseModel):
    score: float
    tier: str

@script(inputs=In, outputs=Out, reads=["lead", "enrichment"], writes=["score", "tier"])
def run(inp: In, runtime: Runtime) -> Out | Command:
    score = 0.4 * inp.lead.get("employees", 0) / 1000 + (0.6 if inp.enrichment else 0)
    runtime.stream_writer({"progress": "scored", "score": score})     # custom stream event → canvas
    if score > 0.9:
        return Command(goto="fast_track", update={"score": score, "tier": "A"})
    return Out(score=score, tier="A" if score > 0.7 else "B")
```

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-SCR-01 | Creating a Script node on canvas scaffolds the file with ports matching the connected state channels; editing ports in code updates the node (and vice-versa). | P0 |
| FR-SCR-02 | Signatures accepted: plain LangGraph node `def run(state: State, runtime: Runtime[Context]) -> dict | Command`, or typed `@script` form above (sugar that compiles to the plain form). Async supported. | P0 |
| FR-SCR-03 | Execution modes: **trusted in-process** (runs inside the Agent Server worker; requires admin-approved project setting) or **sandboxed** (each call proxied to a sandbox; higher latency, strong isolation). Default: sandboxed for shared-tenant, in-process for dedicated. | P0 |
| FR-SCR-04 | Access to `runtime.context`, `runtime.store`, `runtime.stream_writer`, secrets (`runtime.secrets["NAME"]` resolved at run time), and the platform HTTP client with egress policy. | P0 |
| FR-SCR-05 | Inline "Quick script" for small expressions (single function edited in a node popover) — still stored as a file. | P1 |
| FR-SCR-06 | Unit test scaffold per script (`tests/scripts/test_score_lead.py`) with fixtures from pinned node inputs. | P1 |
| FR-SCR-07 | Other languages via sandbox: JavaScript/TypeScript (QuickJS in-process for pure transforms; Node in sandbox), SQL nodes (parameterized query against a connection). | P2 |

## 3. Custom nodes SDK (the ComfyUI "custom nodes" equivalent)

```python
# nodes/salesforce_upsert.py
from agentcanvas.sdk import canvas_node, Port, Config, NodeContext
from pydantic import BaseModel, Field

class Cfg(BaseModel):
    object_type: str = Field("Lead", description="Salesforce object")
    dry_run: bool = False

@canvas_node(
    name="acme.salesforce_upsert", version="1.0.0",
    label="Salesforce Upsert", category="CRM", icon="cloud",
    kind="runtime",
    inputs=[Port("record", "JSON<Lead>")],
    outputs=[Port("sf_id", "Text")],
    config=Cfg,
    connections=["salesforce"],            # requires a connection of this kind
    targets={"python": "full", "typescript": "none"},
)
async def salesforce_upsert(record: dict, cfg: Cfg, ctx: NodeContext) -> dict:
    client = ctx.connection("salesforce")
    if cfg.dry_run:
        return {"sf_id": "DRY-RUN"}
    res = await client.upsert(cfg.object_type, record)
    return {"sf_id": res.id}
```

Also supported: `@canvas_tool` (resource producing a `Tool`), `@canvas_middleware` (resource producing `AgentMiddleware` with config form), `@canvas_backend`, `@canvas_trigger` (custom trigger source), `@canvas_evaluator`.

| ID | Requirement | Pri |
|----|-------------|-----|
| FR-CN-01 | Discovery by AST scan (no import in control plane) + validation import inside sandbox; appear in palette within 2 s of save (hot reload). | P0 |
| FR-CN-02 | Node manifest generated from decorator (see doc 04 §12); config form from the Pydantic model. | P0 |
| FR-CN-03 | Custom node code is copied verbatim into generated projects and imported by the generated graph. | P0 |
| FR-CN-04 | Packaging: `agentcanvas node build` → wheel with manifest; publish to org registry; semantic versioning; deprecation flags. | P1 |
| FR-CN-05 | Marketplace publishing: signing, SBOM, automated security scan, human review for "verified" badge. | P2 |
| FR-CN-06 | Custom *frontend* widget for a node (React component in a sandboxed iframe, e.g., a map picker or chart preview) via a declared `ui` entry. | P2 |

## 4. Custom tools & middleware

- **Tools**: any `@tool`-decorated function or `BaseTool` subclass in `tools/` appears in the palette as a Tool resource. Docstring → description, type hints/Pydantic → args schema. `ToolRuntime` access for state/context/store/stream writer. Optional `return_direct`, artifacts (`response_format="content_and_artifact"`).
- **Middleware**: any `AgentMiddleware` subclass or decorator-based middleware (`@before_model`, `@after_model`, `@wrap_model_call`, `@wrap_tool_call`, `@before_agent`, `@after_agent`, `@dynamic_prompt`) in `middleware/` appears in the palette; constructor args become the config form; can declare `state_schema` extensions (new channels shown in the state designer) and `tools`.
- **Reducers**: `def reducer(left, right) -> value` in `reducers/` selectable in the state designer.

## 5. Custom events

"Custom events" covers three distinct things, all supported:

### 5.1 Inbound events (things that start or resume runs)

| Concept | Description |
|---------|-------------|
| **Event source** | Webhook, schedule, bus topic (NATS/Kafka/SQS/PubSub), DB CDC (Postgres logical replication/Debezium), SaaS webhooks (Stripe, GitHub, Zendesk…), email, channel messages, file drops, custom trigger nodes. |
| **Event schema** | Declared in `events/schemas/*.json` or Pydantic in `events/*.py`; versioned. |
| **Event handler** | Python function subscribed to an event pattern; decides what to do: start a workflow, resume an interrupted thread, send to another topic, or ignore. Runs in a sandbox with timeout. |

```python
# events/on_high_value_lead.py
from agentcanvas.events import on, Event, Actions

@on("crm.lead.created", filter="event.data.amount > 50000", dedup_key="event.data.lead_id")
async def handle(event: Event, act: Actions):
    await act.start_workflow(
        "lead_qualification",
        input={"lead": event.data},
        thread_id=f"lead-{event.data['lead_id']}",     # idempotent thread
        env="prod",
    )

@on("docusign.envelope.completed")
async def resume_contract(event: Event, act: Actions):
    # resume a run waiting on a Human/External-wait node
    await act.resume(thread_id=event.data["custom_fields"]["thread_id"],
                     value={"signed": True, "envelope": event.data["id"]})
```

Requirements: at-least-once delivery with dedup keys; retries with backoff; dead-letter queue with replay UI; event log with payload inspection (redacted); filter expressions evaluated before sandbox spin-up; per-handler concurrency limits; handler test harness with sample events (FR-EVT-01…08, P1).

### 5.2 Runtime events (things that happen during a run)

Lifecycle hooks users can subscribe to — per workflow or project-wide:

| Event | Payload |
|-------|---------|
| `run.started`, `run.completed`, `run.failed`, `run.cancelled` | run/thread ids, input/output (redacted), cost |
| `node.started`, `node.completed`, `node.failed` | canvas node id, duration, error |
| `tool.called`, `tool.failed` | tool name, args (redacted) |
| `interrupt.raised`, `interrupt.resolved`, `interrupt.sla_breached` | interrupt payload, decision |
| `custom.<name>` | anything emitted by `get_stream_writer()` / `runtime.stream_writer` / `act.emit()` from scripts, tools, middleware |
| `eval.score_below_threshold` | evaluator, score |

Delivery targets: event handlers (`@on("node.failed", workflow="support_triage")`), outgoing webhooks, Slack, the bus (for chaining workflows), and the UI. Implementation: the Run Orchestrator converts the Agent Server stream + Agent Server run webhooks into platform events on the bus; custom stream events use LangGraph's `custom` stream mode.

### 5.3 Custom UI events (generative UI)
Scripts/tools can emit typed UI payloads (e.g. `{"ui": "chart", "props": {...}}`) through the custom stream; the chat widget renders registered components (AG-UI / LangGraph generative UI pattern). Custom React components are registered per project (sandboxed iframe, P2).

## 6. Workspace ↔ canvas synchronization

| Change | Effect |
|--------|--------|
| Save a script with changed `@script` ports | Node ports update; broken wires flagged |
| Rename a node on canvas | File rename offered (refactor) |
| Delete a script node | File kept (orphan warning) unless "delete file" chosen |
| Add a `@canvas_node` | Palette updates (hot reload) |
| Dependency added to `pyproject.toml` | `uv lock` in sandbox; dev runtime image layer rebuilt incrementally; validator re-imports |
| Git pull with IR conflicts | Node-level 3-way merge UI (P2) |

## 7. Workspace service — technical notes

- File API over the Git working tree; every save is a working-tree write; commits explicit (or auto-commit on publish).
- LSP proxied per user session to a language-server process inside the dev sandbox (so it sees installed packages).
- Sandboxes: warm pool per region; snapshotting of `.venv` keyed by `uv.lock` hash for fast start; idle shutdown 15 min; persistent volume per workspace.
- Build: merges stack deps + user deps; resolves conflicts (the stack pins win; user can override with a warning); produces wheel of user code + requirements for the compiled project.
- Secrets never written to the workspace filesystem; available only via runtime secret API in sandbox processes with the right env scope.
- Tests: `pytest` with `agentcanvas.testing` fixtures: `fake_model(responses=[…])`, `cassette(tool, file)`, `run_workflow(name, input, mocks=…)`, trajectory assertions (`assert_tool_sequence([...])`).
