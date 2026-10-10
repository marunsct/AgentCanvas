# Choosing a framework

Every workflow is designed for one **framework**: the agent framework that runs it and that its export is written
for. A server offers three:

- **LangGraph** (LangChain's graph runtime) runs every block in the palette, with saved history, time travel,
  long-term memory and breakpoints. New workflows use it.
- **OpenAI Agents SDK** runs AI steps, AI agents (tools, approvals, typed answers), paths, wait-for-all, approvals,
  questions, Python steps and knowledge search as OpenAI Agents SDK code. See
  [Run a workflow on the OpenAI Agents SDK](#run-a-workflow-on-the-openai-agents-sdk).
- **CrewAI** turns the workflow into a native CrewAI flow, with CrewAI agents for the AI blocks and two blocks of
  its own: **Crew** and **Agent task**. See [Run a workflow on CrewAI](#run-a-workflow-on-crewai).

More frameworks arrive in the next releases; the editor already shows which ones a workflow could move to.

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
be wired into a designed agent, and P005 says which. Only the LangGraph framework can run agents designed for other
frameworks today, so other workflow frameworks show them as blocks they cannot run; in a workflow on the OpenAI
Agents SDK, an agent designed for the OpenAI Agents SDK is simply one of its own agents.

## Run a workflow on the OpenAI Agents SDK

Pick **OpenAI Agents SDK** as the workflow's framework (click empty canvas, then **Framework**). Its badge says
whether every block can run there; the ones that cannot are listed with P001.

- **The code is SDK code.** The Code tab shows the workflow as a Python module: an `Agent` for each AI block, a
  function for each other block (marked `@canvas-node` so clicking a block finds its code), and a `FLOW` that says
  which step follows which. A run executes exactly that module.
- **Any model.** The canvas model blocks reach the SDK through AgentCanvas, so Anthropic, Gemini, local models and
  scripted test replies all work, and runs are traced, costed and recorded as usual.
- **Tools and approvals.** Python tools wired into an AI agent become SDK function tools. A tool listed under
  *Tools that need approval* pauses the run with the SDK's own approval; approve, reject, or edit the arguments (the
  SDK runs a call as the model made it, so an edit is sent back to the agent, which makes the call again with your
  arguments, and that call runs without asking twice).
- **Typed answers** become the agent's `output_type`, and the answer lands in `structured_response`.
- **Call limits.** A *Model call limit* add-on becomes the agent's `max_turns`; when it is reached the agent stops
  with a note (or the run fails, if the add-on says so).
- **What it does not have.** No saved conversation history, state editing, replay or breakpoints, and a paused run
  is kept in the server's memory (a restart forgets it). Planning agents, maps, subflows, memory blocks, HTTP and MCP
  tools, guard blocks and other add-ons stay on LangGraph for now.
- **Export.** The Versions tab exports an OpenAI Agents SDK project: the flow, the small runtime it needs, a
  command-line runner (`python -m <name> '{"input": {...}}'`; a paused run is saved in `.runs/` and continued with
  `{"thread_id": ..., "resume": <answer>}`), each model block reached over its provider's OpenAI-compatible API, and
  tests that replay the saved test cases with the SDK's scripted model. No LangChain inside.

### Convert and compare

To see how a workflow does on the other framework before switching, run an experiment with a target on it. In the
Evaluation page, **Experiments** tab, give a target **on OpenAI Agents SDK** (or click **+ Compare on the other
frameworks**), and run: each example runs on both, and the results grid shows scores, tokens, cost and latency side
by side. The draft itself does not change. The API takes `"framework"` on a target, and `GET
/v1/workflows/{id}/code?framework=openai_agents` previews the converted code.

## Run a workflow on CrewAI

Pick **CrewAI** as the workflow's framework (click empty canvas, then **Framework**). Runs need CrewAI installed on
the server: `make crewai` puts it in a Python environment of its own (CrewAI pins an older `openai` package than
AgentCanvas uses), and the Docker image takes `--build-arg WITH_CREWAI=true`. Without it you can still design, check
and export CrewAI workflows; a run says how to install it.

- **The code is a CrewAI flow.** The Code tab shows a `Flow` class with one method per block (marked `@canvas-node`),
  chained with CrewAI's own `@start`, `@listen` and `@router`: a path becomes a `@router` method whose routes the
  next steps listen for, a wait-for-all step listens with `and_()`, and a step reached from several places with
  `or_()`. AI steps call a CrewAI `LLM`, AI agents are CrewAI `Agent`s (role, goal from the instructions, tools,
  typed answers). A run executes exactly that module.
- **Any model.** As on the OpenAI Agents SDK, the canvas model blocks reach CrewAI through AgentCanvas: every
  catalog model, scripted test replies, backup models and the answer cache all work, and runs are traced and costed.
- **Crew** (CrewAI only): a team of agents, each with a role, a goal and a backstory, works through a list of tasks,
  one after another or handed out by a manager. The last task's answer lands in the field you pick.
- **Agent task** (CrewAI only): one agent does one task. Its **guardrail** is a rule in plain words that the answer
  must follow (for example *Never promise a refund*): the model checks each answer, and the agent redoes a failing
  one up to the number of tries before the step fails.
- **Approvals and questions** stop the flow with CrewAI's own `HumanFeedbackPending`. When you answer, the flow
  starts again from the saved fields, skips the steps that had finished (the paths they took are remembered) and
  gives your answer to the step that asked.
- **Loops** are guarded: a step that runs more often than the workflow's step limit stops the run.
- **What it does not have yet.** Asking before a tool call, saved conversation history, state editing, replay and
  breakpoints; a paused run is kept in the server's memory. Planning agents, maps, subflows, memory blocks, HTTP and
  MCP tools, guard blocks and other add-ons stay on LangGraph for now. The Crew and Agent task blocks run only on
  CrewAI.
- **Export.** The Versions tab exports a CrewAI project: the flow, the small runtime it needs, a command-line runner
  that saves a paused run in `.runs/` and continues it, each model block reached over its provider's
  OpenAI-compatible API, and tests that replay the saved test cases with a scripted model.

An experiment can compare a workflow on LangGraph and on CrewAI side by side (a target **on CrewAI**), and
`GET /v1/workflows/{id}/code?framework=crewai` previews the converted code.

## What each framework can do

Some features need the framework's help: reading and editing a conversation's saved state, run history and replay
from an earlier step, long-term memory, browsing an app connector's tools, and breakpoints. When a workflow's
framework cannot do one of them, AgentCanvas says so plainly instead of failing; switch the workflow to LangGraph
to use it.

Which frameworks a server offers is `AGENTCANVAS_ADAPTERS` (comma-separated, default `langchain,openai_agents,crewai`).

## The Echo demo engine

Servers started with `AGENTCANVAS_ADAPTERS=langchain,echo` also offer **Echo**, a tiny built-in engine that shows
how a second framework plugs in. It runs simple flows (set values, paths, wait for all, approvals, questions,
Python steps), and its AI steps answer with scripted replies or repeat their prompt. It keeps paused runs in memory
and exports only the workflow file. Use it to try the framework picker, not in production.

For developers: a new framework implements the adapter core, declares what else it can do in a manifest, and must
pass the conformance kit (`apps/api/agentcanvas/conformance`); see ADR 0018.

## A whole workflow on another framework (preview)

The Export box (Versions tab) can also write a whole workflow for LangGraph through Agent Spec, WayFlow or the
OpenAI Agents SDK, and lists first what does not carry over. See
[Export a whole workflow to another framework](export.md#export-a-whole-workflow-to-another-framework-preview).

