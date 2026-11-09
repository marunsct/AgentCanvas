# 00 — Concepts Primer (read this first)

You do not need prior AI experience. Each section explains one idea, shows the smallest possible code, and tells you where it appears in AgentCanvas. All code in this primer was run against the versions listed in [01-environment-setup.md](./01-environment-setup.md).

---

## 1. LLM and chat model

A **large language model (LLM)** takes text in and produces text out. A **chat model** takes a list of *messages* (system / user / assistant) and returns an assistant message. LangChain gives all providers the same interface:

```python
from langchain.chat_models import init_chat_model

model = init_chat_model("openai:gpt-5-mini", temperature=0)   # "provider:model"
reply = model.invoke("Say hi in 3 words")
print(reply.content)
```

- Needs an API key in an environment variable (e.g. `OPENAI_API_KEY`).
- **In AgentCanvas:** the *Chat Model* widget (resource `model.chat`) becomes `init_chat_model(...)`.

## 2. Tools and tool calling

A **tool** is a normal function the model may ask you to run. The model does not run it; it returns a *tool call* (name + arguments), your code runs the function and sends the result back.

```python
from langchain_core.tools import tool

@tool
def get_weather(city: str) -> str:
    """Return the weather for a city."""   # the docstring is shown to the model
    return f"Sunny in {city}"
```

- **In AgentCanvas:** *Tool* widgets (`tool.python`, MCP tools, HTTP tools) are wired into an Agent's `tools` port.

## 3. Agent

An **agent** is a loop: *model → maybe call tools → give results to model → repeat until the model answers without tool calls*. LangChain builds it for you:

```python
from langchain.agents import create_agent

agent = create_agent(model, tools=[get_weather], system_prompt="You are helpful.")
result = agent.invoke({"messages": [("user", "Weather in Paris?")]})
print(result["messages"][-1].content)
```

- **Deep Agents** (`create_deep_agent`) is a richer agent with a virtual filesystem, sub-agents, skills and memory.
- **In AgentCanvas:** the *Agent* and *Deep Agent* widgets.

## 4. Graph, node, edge, state (LangGraph)

A **graph** is a flowchart the computer runs. **Nodes** are steps (Python functions). **Edges** say which step comes next. All nodes share one **state** (a dictionary). A node receives the state and returns only the keys it wants to change.

```python
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, START, END

class State(TypedDict, total=False):
    ticket: str
    urgency: str

def classify(state: State) -> dict:
    return {"urgency": "high" if "down" in state["ticket"] else "low"}

builder = StateGraph(State)
builder.add_node("classify", classify)
builder.add_edge(START, "classify")
builder.add_edge("classify", END)
graph = builder.compile()
print(graph.invoke({"ticket": "site is down"}))   # {'ticket': 'site is down', 'urgency': 'high'}
```

**Conditional edge (router):** a function chooses the next node.

```python
builder.add_conditional_edges("classify", lambda s: s["urgency"], {"high": "escalate", "low": "reply"})
```

- **In AgentCanvas:** every runtime widget is a node; *flow* edges are graph edges; the *Router* widget is a conditional edge.

## 5. Reducer

If two nodes write the same state key, how are values combined? A **reducer** decides. Default: last write wins. For lists you usually want *append*:

```python
import operator
from typing import Annotated

class State(TypedDict, total=False):
    log: Annotated[list[str], operator.add]   # each node's list is appended
```

Chat messages use the special reducer `add_messages` (appends and de-duplicates by message id).

- **In AgentCanvas:** the *State designer* lets users pick `replace`, `append`, `add_messages`, `sum`, `merge`.

## 6. Checkpoint, thread, persistence

A **checkpointer** saves the state after every step. A **thread** (`thread_id`) is one conversation/run history. With checkpoints you can continue later, recover from crashes, look at history and "time travel".

```python
from langgraph.checkpoint.memory import InMemorySaver          # dev only (lost on restart)
graph = builder.compile(checkpointer=InMemorySaver())
config = {"configurable": {"thread_id": "t1"}}
graph.invoke({"ticket": "hello"}, config)
print(graph.get_state(config).values)                           # latest saved state
for snap in graph.get_state_history(config):                    # all checkpoints, newest first
    print(snap.metadata["step"], snap.next)
```

Production uses Postgres: `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`. The **store** (`AsyncPostgresStore`) is a separate key-value memory that is shared *across* threads (long-term memory).

- **In AgentCanvas:** persistence is on by default; the *Persistence panel* picks the database (doc 08).

## 7. Interrupt (human-in-the-loop)

`interrupt(value)` pauses the graph and saves a checkpoint. Later you resume with `Command(resume=answer)`; `interrupt()` then **returns** that answer.

```python
from langgraph.types import interrupt, Command

def approve(state):
    decision = interrupt({"question": "Send this?", "draft": state["draft"]})
    return {"approved": decision["approved"]}

graph.invoke({"draft": "Hi"}, config)                      # pauses
graph.invoke(Command(resume={"approved": True}), config)   # continues
```

**Important:** on resume, the node runs again **from its beginning** (the `interrupt()` call now returns immediately). Never put side effects (sending email, writing to a DB) *before* `interrupt()` in the same node.

- Agents can require approval for specific tools with `HumanInTheLoopMiddleware(interrupt_on={"send_reply": True})`; resume with `Command(resume={"decisions": [{"type": "approve"}]})`.
- **In AgentCanvas:** *Approval* and *Human input* widgets; the Inbox lists pending interrupts.

## 8. Streaming and Server-Sent Events (SSE)

Graphs can report progress while running:

```python
async for part in graph.astream(inputs, config, stream_mode=["tasks", "updates", "messages"],
                                subgraphs=True, version="v2"):
    print(part["type"], part["ns"], part["data"])   # "tasks" = node started/finished, "messages" = tokens
```

**SSE** is a simple HTTP format for pushing events to a browser: the server keeps the response open and writes lines like `data: {...}\n\n`. AgentCanvas converts LangGraph stream parts into **canonical events** (`STEP_STARTED`, `TEXT_MESSAGE_CONTENT`, …) and sends them as SSE; the canvas colours nodes from them.

## 9. IR, validator, interpreter, compiler (the AgentCanvas core)

| Term | Plain meaning | File in reference |
|------|---------------|-------------------|
| **IR** (intermediate representation) | The JSON description of a workflow: state channels, resources, nodes, edges. The single source of truth. | `agentcanvas/ir/models.py` |
| **Validator** | Checks the IR and returns *diagnostics* (errors block running). | `agentcanvas/ir/validate.py` |
| **Node library** | Functions that create the real LangGraph node for each widget type. | `agentcanvas/runtime/nodes.py` |
| **Interpreter** | Builds a runnable graph from IR in memory (used for canvas test runs). | `agentcanvas/runtime/interpreter.py` |
| **Compiler / code generator** | Writes a readable Python file from IR (used for export/deploy). Uses the same node library, so behaviour is identical. | `agentcanvas/compiler/codegen.py` |
| **Canonical events** | Framework-neutral run events the UI understands. | `agentcanvas/runtime/events.py` |
| **Diagnostic** | `{code, severity, message, node_id}` e.g. `E001 needs a Chat Model`. | validator |

## 10. Glossary

| Word | Meaning |
|------|---------|
| Canvas | The drag-and-drop editor surface. |
| Widget / node | A box on the canvas. *Runtime nodes* run as graph steps; *resources* (models, tools) are injected into nodes. |
| Port | A connection point on a widget (e.g. an Agent's `model` port). |
| Flow edge / wiring edge | Flow = "what runs next". Wiring = "this model/tool is used by that node". |
| Channel | One field of the graph state. |
| Thread | One conversation/run history (a series of checkpoints). |
| Run | One execution on a thread (may stop at an interrupt). |
| Interrupt | A pause waiting for a human decision. |
| Resume | Continue a paused run with an answer. |
| Time travel | Re-run or fork from an older checkpoint. |
| CEL | Common Expression Language — safe expressions for router conditions. |
| MCP | Model Context Protocol — a standard way to connect external tool servers. |
| ADR | Architecture Decision Record — a short note explaining a design decision. |

## 11. Where to learn more (optional)

- LangGraph concepts: graph API, persistence, interrupts, streaming (docs.langchain.com, "LangGraph" section).
- LangChain agents and middleware (docs.langchain.com, "LangChain" section).
- React Flow "Quickstart" and "Custom nodes" (reactflow.dev).
- FastAPI tutorial (fastapi.tiangolo.com), Pydantic v2 docs.
