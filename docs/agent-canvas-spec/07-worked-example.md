# 07 — Worked Example: "Support Triage" end-to-end

> **v0.5 note (review F-03 / C-03):** §3 shows the long-term *inlined* code style (P2 "clean eject"). The MVP's generated code calls the shared node library instead — see the verified output in [build-guide/03-walking-skeleton-tutorial.md Part 6](./build-guide/03-walking-skeleton-tutorial.md#part-6--exporting-code-generator-agentcanvascompilercodegenpy--templatesgraphpyj2) and `build-guide/reference/`. The `make_graph` async factory below is illustrative; the MVP exposes `build_graph(resources, checkpointer, store)`.

This walks one workflow from canvas → IR → generated Python → deployment, to make the WYSIWYG contract concrete.

## 1. What the user builds on the canvas

```
 [Webhook: zendesk.ticket.created]
            │ flow
            ▼
   [Classify ticket]  (Extract/Classify node, structured output: urgency, category)
     ▲ model: fast_model
            │ flow
            ▼
      [Router: urgency] ──high──►  [Escalate (Script: scripts/escalate.py)] ──► END
            │ low/medium
            ▼
   [Deep Agent: Reply drafter] ──────────────► END
     ▲ model: smart_model
     ▲ tools: [MCP Server: zendesk (search_tickets, get_ticket, send_reply)]
     ▲ subagents: [Subagent: kb_researcher ◄ tools: Retriever tool "help_center"]
     ▲ middleware: [PII guard (email → redact)] [Model call limit (run=15)]
     ▲ backend: [Composite: default=State, /memories/ → Store(user)]
     config: interrupt_on = { send_reply: approve|edit|reject }
```

State (state designer): `messages` (add_messages), `ticket` (in), `urgency`, `category` (out), `escalated` (bool).

## 2. IR excerpt

```jsonc
{
  "ir_version": "1.0", "id": "wf_support_triage", "name": "Support Triage",
  "state": { "channels": [
    { "name": "messages", "type": {"$ref": "#/types/Messages"}, "reducer": "add_messages", "io": "inout" },
    { "name": "ticket",   "type": {"$ref": "#/types/Ticket"}, "reducer": "replace", "io": "in" },
    { "name": "urgency",  "type": {"enum": ["low","medium","high"]}, "reducer": "replace", "io": "out" },
    { "name": "category", "type": {"type": "string"}, "reducer": "replace", "io": "out" },
    { "name": "escalated","type": {"type": "boolean"}, "reducer": "replace", "io": "out" }
  ]},
  "resources": [
    { "id": "r_fast",  "type": "model.chat@1.0.0", "name": "fast_model",  "config": {"model": "anthropic:claude-haiku-4-5", "temperature": 0} },
    { "id": "r_smart", "type": "model.chat@1.0.0", "name": "smart_model", "config": {"model": "anthropic:claude-sonnet-5"} },
    { "id": "r_zd",    "type": "mcp.server@1.0.0", "name": "zendesk",
      "config": {"transport": "http", "url": "{{ env.ZENDESK_MCP_URL }}", "auth": {"kind": "bearer", "secret": "ZENDESK_MCP_TOKEN"},
                 "tool_filter": ["search_tickets", "get_ticket", "send_reply"]} },
    { "id": "r_kbsub", "type": "subagent.declarative@1.0.0", "name": "kb_researcher",
      "config": {"description": "Searches the help center and returns cited answers.",
                 "system_prompt": "Search the help center. Return at most 5 bullet points with article URLs."} },
    { "id": "r_pii",   "type": "mw.pii@1.0.0", "name": "pii_email", "config": {"pii_type": "email", "strategy": "redact", "apply_to_input": true} },
    { "id": "r_limit", "type": "mw.model_call_limit@1.0.0", "name": "call_limit", "config": {"run_limit": 15} },
    { "id": "r_fs",    "type": "backend.composite@1.0.0", "name": "files",
      "config": {"default": {"kind": "state"}, "routes": {"/memories/": {"kind": "store", "namespace": "user"}}} }
  ],
  "nodes": [
    { "id": "n_cls", "type": "data.classify@1.0.0", "name": "classify_ticket",
      "config": {"schema": {"urgency": {"enum": ["low","medium","high"]}, "category": {"type": "string"}},
                 "prompt": "Classify this support ticket:\n{{ state.ticket.subject }}\n{{ state.ticket.body }}"},
      "policies": {"retry": {"max_attempts": 3}} },
    { "id": "n_rt",  "type": "logic.switch@1.0.0", "name": "route_urgency",
      "config": {"on": "state.urgency", "cases": {"high": "high"}, "default": "normal"} },
    { "id": "n_esc", "type": "script@1.0.0", "name": "escalate", "config": {"file": "scripts/escalate.py"} },
    { "id": "n_ag",  "type": "agent.deep@1.3.0", "name": "reply_drafter",
      "config": {"system_prompt": {"kind": "file", "value": "prompts/reply_drafter.md"},
                 "interrupt_on": {"send_reply": {"allowed_decisions": ["approve","edit","reject"]}},
                 "planning": false},
      "policies": {"timeout": {"run_timeout": 600}} }
  ],
  "edges": [
    { "id": "e1", "kind": "flow", "from": {"node": "START"}, "to": {"node": "n_cls"} },
    { "id": "e2", "kind": "flow", "from": {"node": "n_cls"}, "to": {"node": "n_rt"} },
    { "id": "e3", "kind": "flow", "from": {"node": "n_rt", "port": "route:high"},   "to": {"node": "n_esc"} },
    { "id": "e4", "kind": "flow", "from": {"node": "n_rt", "port": "route:normal"}, "to": {"node": "n_ag"} },
    { "id": "e5", "kind": "flow", "from": {"node": "n_esc"}, "to": {"node": "END"} },
    { "id": "e6", "kind": "flow", "from": {"node": "n_ag"},  "to": {"node": "END"} },
    { "id": "w1", "kind": "wiring", "from": {"node": "r_fast"},  "to": {"node": "n_cls", "port": "model"} },
    { "id": "w2", "kind": "wiring", "from": {"node": "r_smart"}, "to": {"node": "n_ag", "port": "model"} },
    { "id": "w3", "kind": "wiring", "from": {"node": "r_zd"},    "to": {"node": "n_ag", "port": "tools"} },
    { "id": "w4", "kind": "wiring", "from": {"node": "r_kbsub"}, "to": {"node": "n_ag", "port": "subagents"} },
    { "id": "w5", "kind": "wiring", "from": {"node": "r_pii"},   "to": {"node": "n_ag", "port": "middleware"}, "order": 0 },
    { "id": "w6", "kind": "wiring", "from": {"node": "r_limit"}, "to": {"node": "n_ag", "port": "middleware"}, "order": 1 },
    { "id": "w7", "kind": "wiring", "from": {"node": "r_fs"},    "to": {"node": "n_ag", "port": "backend"} }
  ],
  "triggers": [ { "id": "t1", "kind": "webhook", "config": {"path": "/zendesk/ticket-created", "hmac_secret": "ZENDESK_WEBHOOK_SECRET"},
                  "input_mapping": {"ticket": "event.body.ticket"} } ]
}
```

## 3. Generated Python (abridged)

`src/support_triage/state.py`
```python
# Generated by AgentCanvas — do not edit outside code islands. ir_hash=3f9c…
from typing import Annotated, Literal
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel
from typing_extensions import TypedDict


class Ticket(TypedDict, total=False):
    id: str
    subject: str
    body: str
    requester_email: str


class State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    ticket: Ticket
    urgency: Literal["low", "medium", "high"]
    category: str
    escalated: bool


class InputState(TypedDict):
    ticket: Ticket


class OutputState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    urgency: Literal["low", "medium", "high"]
    category: str
    escalated: bool


# ── @canvas-node n_cls schema ──
class ClassifyTicketResult(BaseModel):
    urgency: Literal["low", "medium", "high"]
    category: str
```

`src/support_triage/resources.py`
```python
import os
from functools import cache

from langchain.chat_models import init_chat_model
from langchain.mcp import MCPAdapter
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend


# ── @canvas-node r_fast "fast_model" ──
@cache
def fast_model():
    return init_chat_model("anthropic:claude-haiku-4-5", temperature=0)


# ── @canvas-node r_smart "smart_model" ──
@cache
def smart_model():
    return init_chat_model("anthropic:claude-sonnet-5")


# ── @canvas-node r_zd "zendesk" ──
_ZENDESK_TOOLS = {"search_tickets", "get_ticket", "send_reply"}

async def zendesk_tools():
    config = {
        "mcpServers": {
            "zendesk": {
                "url": os.environ["ZENDESK_MCP_URL"],
                "auth": os.environ["ZENDESK_MCP_TOKEN"],   # bearer-token string (or "oauth" / httpx.Auth)
            }
        }
    }
    async with MCPAdapter(config) as adapter:
        tools = await adapter.list_tools()
    return [t for t in tools if t.name in _ZENDESK_TOOLS]


# ── @canvas-node r_fs "files" ──
def files_backend():
    return CompositeBackend(
        default=StateBackend(),
        routes={"/memories/": StoreBackend(namespace=lambda rt: (rt.server_info.user.identity,))},
    )
```

`src/support_triage/agents/reply_drafter.py`
```python
# ── @canvas-node n_ag "Reply drafter" (agent.deep@1.3.0) ──
from pathlib import Path

from deepagents import create_deep_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, PIIMiddleware

from support_triage import resources
from support_triage.tools.help_center import help_center_search   # workspace tool (code island)

SYSTEM_PROMPT = (Path(__file__).parents[3] / "prompts" / "reply_drafter.md").read_text()

kb_researcher = {                                  # @canvas-node r_kbsub
    "name": "kb_researcher",
    "description": "Searches the help center and returns cited answers.",
    "system_prompt": "Search the help center. Return at most 5 bullet points with article URLs.",
    "tools": [help_center_search],
}


async def build_reply_drafter():
    return create_deep_agent(
        model=resources.smart_model(),
        tools=await resources.zendesk_tools(),
        system_prompt=SYSTEM_PROMPT,
        middleware=[
            PIIMiddleware("email", strategy="redact", apply_to_input=True),   # @canvas-node r_pii
            ModelCallLimitMiddleware(run_limit=15),                             # @canvas-node r_limit
        ],
        subagents=[kb_researcher],
        backend=resources.files_backend(),
        interrupt_on={"send_reply": {"allowed_decisions": ["approve", "edit", "reject"]}},
        name="reply_drafter",
    )
# ── end n_ag ──
```

`src/support_triage/graph.py`
```python
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy, TimeoutPolicy

from support_triage import resources
from support_triage.agents.reply_drafter import build_reply_drafter
from support_triage.scripts import escalate                  # code island: scripts/escalate.py
from support_triage.state import ClassifyTicketResult, InputState, OutputState, State


# ── @canvas-node n_cls "Classify ticket" (data.classify@1.0.0) ──
async def classify_ticket(state: State) -> dict:
    t = state["ticket"]
    model = resources.fast_model().with_structured_output(ClassifyTicketResult)
    result = await model.ainvoke(f"Classify this support ticket:\n{t.get('subject', '')}\n{t.get('body', '')}")
    return {"urgency": result.urgency, "category": result.category}


# ── @canvas-node n_rt "Route urgency" (logic.switch@1.0.0) ──
def route_urgency(state: State) -> str:
    return "high" if state.get("urgency") == "high" else "normal"


async def make_graph():
    builder = StateGraph(State, input_schema=InputState, output_schema=OutputState)
    builder.add_node("classify_ticket", classify_ticket,
                     retry_policy=RetryPolicy(max_attempts=3),
                     metadata={"canvas_node_id": "n_cls"})
    builder.add_node("escalate", escalate.run, metadata={"canvas_node_id": "n_esc"})
    builder.add_node("reply_drafter", await build_reply_drafter(),
                     timeout=TimeoutPolicy(run_timeout=600),
                     metadata={"canvas_node_id": "n_ag"})

    builder.add_edge(START, "classify_ticket")
    builder.add_conditional_edges("classify_ticket", route_urgency,
                                  {"high": "escalate", "normal": "reply_drafter"})
    builder.add_edge("escalate", END)
    builder.add_edge("reply_drafter", END)
    # Checkpointer & store are injected by Agent Server at runtime.
    return builder.compile(name="support_triage")
```

`langgraph.json`
```json
{
  "dependencies": ["."],
  "graphs": { "support_triage": "./src/support_triage/graph.py:make_graph" },
  "env": ".env",
  "python_version": "3.12"
}
```
(Async graph factories are supported by Agent Server; when all resources are synchronous the compiler emits a module-level compiled `graph` instead, which the server loads once — the recommended mode.)

## 4. What the user experiences when running it

1. Presses ▶ with a sample ticket. `classify_ticket` pulses → green, showing `{urgency: "low", category: "billing"}` on its output preview.
2. The `normal` edge animates; `reply_drafter` expands a live sub-view: `kb_researcher` subagent badge spins, then the `send_reply` tool call appears — the node turns **amber** (interrupt).
3. The right panel shows the approval card with the drafted reply (editable). The user edits and approves → the orchestrator resumes with `Command(resume={"decisions": [{"type": "edit", …}]})`; the tool executes; run ends green.
4. The Timeline shows 4 super-steps; clicking step 2 shows state after classification; the user changes `urgency` to `high` and forks → the `escalate` branch runs. Both runs are LangSmith traces with `canvas_node_id` on every span.
5. "Save as test case" twice; later these become the eval gate for publishing to prod.

## 4b. Adding persistence, memory and data (v0.2)

- **Persistence panel**: dev → SQLite checkpointer; prod → platform Postgres, `durability="sync"` (the workflow sends customer emails), thread TTL 30 days, `messages` as `DeltaChannel`, encryption on. Compiles to `langgraph.json`:
  ```json
  { "checkpointer": { "ttl": { "strategy": "delete", "default_ttl": 43200, "sweep_interval_minutes": 10 } },
    "store": { "index": { "embed": "openai:text-embedding-3-small", "dims": 1536, "fields": ["summary"] },
               "ttl": { "default_ttl": 129600, "refresh_on_read": true } } }
  ```
- **Memory Space** `customer_notes` (namespace `("tenants", context.tenant_id, "customers", state.ticket.requester_id)` — note the requester id comes from the webhook payload, not the model) bound to the agent's `/memories/` route via `CompositeBackend`; the agent reads prior notes and appends new ones with provenance.
- **Data Studio** `CRM` model (Customer, Ticket) mapped to the company Postgres; *Get Customer* node before the agent populates `state.customer`; the `update_ticket_status` data tool is exposed to the agent with approval on write and an idempotency key `thread_id:reply_drafter:tool_call_id`.
- **Learning**: HITL edits on `send_reply` are captured as feedback (`hitl.edited`) and feed the *Nightly self-improvement loop* (doc 09 §3.1), which proposes new `reply_drafter` prompt versions behind the eval gate.

## 5. Publishing

- **Target: AgentCanvas Cloud / LangSmith Deployment** → the generated project is built and deployed; the webhook trigger is registered; the workflow is reachable at `/threads/{id}/runs/stream`, via MCP (`/mcp`) as tool `support_triage`, and via A2A.
- **Target: MDA** → disabled for this workflow (graph-tier router & script node present); the validator explains that only the `reply_drafter` agent could be published as an MDA project on its own ("Publish node as agent…").
- **Export** → the project above is pushed to `github.com/acme/support-triage` with CI (`ruff`, `pyright`, `pytest`, `agentcanvas verify`).
