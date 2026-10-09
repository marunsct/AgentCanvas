# Choosing a framework

Every workflow is designed for one **framework**: the agent framework that runs it and that its export is written
for. Today that is **LangGraph** (LangChain's graph runtime), which runs every block in the palette. More frameworks
arrive in the next releases; the editor already shows which ones a workflow could move to.

## Pick the workflow's framework

Click empty canvas to open the workflow settings, then choose **Framework**. The list shows the frameworks this
server offers, with a line on what each one does. Changing it is an ordinary edit: **Undo** brings the old one back.

## See where a workflow can run

Next to the workflow's name, one badge per installed framework says whether it can run this workflow:

- **✓ LangGraph** — every block can run there.
- **✕ Echo · 2** — two blocks cannot run there. Point at the badge to see which; click it to select the first one.

The Problems tab explains the rest:

| Code | Meaning |
|---|---|
| P001 | The workflow's framework cannot run this block, or the framework is not installed on this server |
| P002 | The framework only imitates this block, so it may behave a little differently (the message says how) |
| P003 | The workflow could move to some installed frameworks but not others |
| P004 | This AI block is designed for another workflow framework than the workflow's; for now it runs on the workflow's |
| P005 | The agent framework this AI agent is designed for cannot take something wired into it (add-ons, some tools, its answer shape) |
| P006 | The agent framework this AI agent is designed for is not installed on this server, so runs here fail until it is |

## An AI block of its own framework

An AI step, AI agent or Planning Agent can be **Designed for** another framework than the workflow (in the block's
settings). Until mixed-framework flows arrive, the block still runs on the workflow's framework and P004 reminds you.

## Design an agent in any agent framework

An **AI agent** block can be designed as a native agent of a popular agent framework. In the block's settings,
**Designed for** lists them under *Agent frameworks*:

| Framework | Packages it uses | Model API |
|---|---|---|
| OpenAI Agents SDK | `openai-agents` | OpenAI-compatible |
| CrewAI | `crewai` | OpenAI-compatible |
| Google ADK | `google-adk`, `litellm` | OpenAI-compatible |
| Pydantic AI | `pydantic-ai-slim[openai]` | OpenAI-compatible |
| Agno | `agno`, `openai` | OpenAI-compatible |
| Strands Agents | `strands-agents[openai]` | OpenAI-compatible |
| LlamaIndex | `llama-index-core`, `llama-index-llms-openai-like` | OpenAI-compatible |
| Microsoft Agent Framework | `agent-framework-core`, `agent-framework-openai` | OpenAI-compatible |
| Claude Agent SDK | `claude-agent-sdk` | Anthropic |

Pick one and the block is generated as that framework's own code. You design it exactly as before: its
instructions, the model wired into it, its tools (Python functions from the workspace, HTTP tools and MCP servers),
which tools need approval, and an answer shape. What changes:

- **It runs as that framework.** A run calls the generated agent. Its model calls still go through the model wired
  into the block, so the run is traced, costed, mocked and replayed like any other AI step, and your API keys stay
  on the server.
- **View the code.** **View … code** in the block's settings shows the module that runs, updated as you edit.
- **Take it away.** **Export as … project** downloads a ready-to-run project for that framework: the agent, its
  tools, a small `main.py`, pinned packages, a README and tests that run without an API key (`uv run pytest`).
  Exporting the whole workflow also puts each designed agent's code in `agents/` and adds its packages.
- **Approvals pause the run.** A tool that needs approval pauses the run as usual; when you approve, the agent step
  starts again from its beginning with your answer.
- **Typed answers.** An answer shape becomes the framework's own structured output (a Pydantic class), and the
  answer lands in the block's `structured_response` output.

Each framework takes a little less than LangGraph does: add-ons (middleware) and the more unusual tool kinds cannot
be wired into a designed agent, and P005 says which. Only the LangGraph framework can run designed agents today,
so other workflow frameworks show them as blocks they cannot run.

## What each framework can do

Some features need the framework's help: reading and editing a conversation's saved state, run history and replay
from an earlier step, long-term memory, browsing an app connector's tools, and breakpoints. When a workflow's
framework cannot do one of them, AgentCanvas says so plainly instead of failing; switch the workflow to LangGraph
to use it.

## The Echo demo engine

Servers started with `AGENTCANVAS_ADAPTERS=langchain,echo` also offer **Echo**, a tiny built-in engine that shows
how a second framework plugs in. It runs simple flows (set values, paths, wait for all, approvals, questions,
Python steps), and its AI steps answer with scripted replies or repeat their prompt. It keeps paused runs in memory
and exports only the workflow file. Use it to try the framework picker, not in production.

For developers: a new framework implements the adapter core, declares what else it can do in a manifest, and must
pass the conformance kit (`apps/api/agentcanvas/conformance`); see ADR 0018.
