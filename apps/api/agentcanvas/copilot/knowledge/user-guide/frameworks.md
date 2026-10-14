# Choosing a framework

Every workflow is designed for one **framework**: the agent framework that runs it and that its export is written
for. A server offers ten:

- **LangGraph** (LangChain's graph runtime) runs every block in the palette, with saved history, time travel,
  long-term memory and breakpoints. New workflows use it.
- **OpenAI Agents SDK** runs AI steps, AI agents (tools, approvals, typed answers), paths, wait-for-all, approvals,
  questions, Python steps and knowledge search as OpenAI Agents SDK code. See
  [Run a workflow on the OpenAI Agents SDK](#run-a-workflow-on-the-openai-agents-sdk).
- **CrewAI** turns the workflow into a native CrewAI flow, with CrewAI agents for the AI blocks and two blocks of
  its own: **Crew** and **Agent task**. See [Run a workflow on CrewAI](#run-a-workflow-on-crewai).
- **Google ADK** turns the workflow into a native ADK graph workflow, with ADK agents for the AI blocks and tool
  calls that can wait for your approval. See [Run a workflow on Google ADK](#run-a-workflow-on-google-adk).
- **Pydantic AI** and **Agno** turn the workflow into a flow of async Python with the framework's own agents for the
  AI blocks (typed answers, tools that wait for your approval). See
  [Run a workflow on Pydantic AI or Agno](#run-a-workflow-on-pydantic-ai-or-agno).
- **Microsoft Agent Framework** does the same with Agent Framework agents. See
  [Run a workflow on the Microsoft Agent Framework](#run-a-workflow-on-the-microsoft-agent-framework).
- **Strands Agents** and **LlamaIndex** do the same with Strands agents and LlamaIndex function agents. See
  [Run a workflow on Strands or LlamaIndex](#run-a-workflow-on-strands-or-llamaindex).
- **Mastra**, a TypeScript framework, turns the workflow into a TypeScript flow with Mastra agents that runs on
  Node.js and exports as a TypeScript project. See [Run a workflow on Mastra](#run-a-workflow-on-mastra).

The editor shows which of them a workflow could move to.

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
| P006 | The agent framework this AI agent is designed for is not installed on this server, so runs here fail until it is (`make framework FW=<id>` fixes it) |

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
  Exporting the whole workflow puts every designed agent into one project (see
  [Mixed flows](#mixed-flows-parts-and-contracts)).
- **Approvals pause the run.** A tool that needs approval pauses the run as usual; when you approve, the agent step
  starts again from its beginning with your answer.
- **Typed answers.** An answer shape becomes the framework's own structured output (a Pydantic class), and the
  answer lands in the block's `structured_response` output.

Each framework takes a little less than LangGraph does: add-ons (middleware) and the more unusual tool kinds cannot
be wired into a designed agent, and P005 says which. Only the LangGraph framework can run agents designed for other
frameworks today, so other workflow frameworks show them as blocks they cannot run; in a workflow on the OpenAI
Agents SDK, an agent designed for the OpenAI Agents SDK is simply one of its own agents.

## Mixed flows: parts and contracts

A LangGraph workflow can mix agents of several frameworks: a researcher on the OpenAI Agents SDK, a writer on CrewAI,
a checker on Pydantic AI. The **Mixed-framework team** template is a small example. Each agent is a *part* of the flow,
and the LangGraph workflow is the host that passes the conversation from one part to the next.

**Typed contracts.** Each part has a contract: the conversation goes in as text, and its answer comes back. With an
answer shape the answer must match it; if a framework hands back something else, the step stops with *answered
outside its contract* and says which field is wrong, so the next step never gets unchecked data.

**Parts with an environment of their own.** Five frameworks pin Python packages that clash with LangGraph's (CrewAI,
Google ADK, Agno, Strands Agents and LlamaIndex), so they cannot be installed next to it. Their agents run in a
Python environment of their own, in a small worker process the host talks to. Tool calls come back to the host, so
approvals, traces and recorded replies work as usual, and the model is served by the host. The block's settings say
when an agent runs this way.

- **On this server**, such an agent runs when its framework has an environment: `make framework FW=crewai` (or
  `google_adk`, `agno`, `strands`, `llamaindex`) sets one up in `apps/api/.frameworks/`; `make crewai` makes the
  same CrewAI environment. Without one, P006 warns and a run stops with that command in the error.
- **In an export** the project holds every part: agents that fit next to LangGraph in `src/<name>/agents/`, the
  others in `parts/<agent>/` with their own `requirements.txt`. `make install` and the Docker image install each part
  in its own environment, `contracts.json` lists every part with its framework, where it runs and its contract,
  and `tests/test_parts.py` runs the flow through each part. Deploy it like any export: `make deploy`, then
  `make smoke`.

**Parts in other languages.** An agent written in TypeScript, Java or Go joins a flow over A2A: add a **Remote agent
(A2A)** block with its Agent Card address. In an export it is listed in `contracts.json` (text in, text out) and
`make doctor` checks its address can be reached.

| Framework | In a mixed flow |
|---|---|
| OpenAI Agents SDK, Pydantic AI, Microsoft Agent Framework, Claude Agent SDK | Next to LangGraph, in the host |
| CrewAI, Google ADK, Agno, Strands Agents, LlamaIndex | In an environment of its own |
| Any other language | Over A2A |

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

## Run a workflow on Google ADK

Pick **Google ADK** as the workflow's framework (click empty canvas, then **Framework**). Runs need ADK installed on
the server: `make framework FW=google_adk` puts it in a Python environment of its own (ADK's LiteLLM pins an older
`openai` package than AgentCanvas uses; the same environment runs ADK agents in mixed flows), and the Docker image
takes `--build-arg WITH_GOOGLE_ADK=true`. Without it you can still design, check and export ADK workflows; a run
says how to install it.

- **The code is an ADK workflow.** The Code tab shows one function node per block (marked `@canvas-node`) and a
  `build()` that wires them with ADK's own graph edges: `("START", first)`, a route dict after each path block, a
  tuple where a step fans out to parallel branches, and a `JoinNode` before a wait-for-all step. AI agents are ADK
  `Agent`s (instructions, model, tools, typed answers as `output_schema`) that run inside their step. A run
  executes exactly that module with ADK's own `Runner`.
- **Any model.** The canvas model blocks reach ADK through LiteLLM and AgentCanvas: every catalog model, scripted
  test replies, backup models and the answer cache all work, and runs are traced and costed.
- **Ask before a tool call.** Tools named in an AI agent's **Ask before using** wait for you: approve the call,
  change its arguments or reject it. When you answer, the agent's turn runs again up to that call, which then runs
  as you decided (with a real model, the model is asked again, so it may phrase its call differently).
- **Approvals and questions** pause the workflow with ADK's own `RequestInput`. When you answer, the workflow starts
  again from the saved fields, skips the steps that had finished (the paths they took are remembered) and gives your
  answer to the step that asked.
- **Loops** are guarded: a step that runs more often than the workflow's step limit stops the run, and a **Model
  call limit** block caps an agent's model calls in one turn.
- **What it does not have yet.** Saved conversation history, state editing, replay and breakpoints; a paused run is
  kept in the server's memory. Planning agents, maps, subflows, memory blocks, HTTP and MCP tools, guard blocks and
  other add-ons stay on LangGraph for now.
- **Export.** The Versions tab exports an ADK project: the workflow, the small runtime it needs, a command-line
  runner that saves a paused run in `.runs/` and continues it, each model block reached through LiteLLM over its
  provider's OpenAI-compatible API, and tests that replay the saved test cases with a scripted model.

`GET /v1/workflows/{id}/code?framework=google_adk` previews the converted code.

## Run a workflow on Pydantic AI or Agno

Pick **Pydantic AI** or **Agno** as the workflow's framework (click empty canvas, then **Framework**). Runs need the
framework installed on the server, each in a Python environment of its own: `make framework FW=pydantic_ai` and
`make framework FW=agno` (the same environments run their agents in mixed flows). The Docker image takes
`--build-arg WITH_PYDANTIC_AI=true` and `--build-arg WITH_AGNO=true`. Without them you can still design, check and
export these workflows; a run says how to install the framework.

Neither framework draws a graph of its own, so the workflow's shape is plain Python that AgentCanvas runs the way
the canvas draws it, and the AI blocks are the framework's own agents:

- **The code.** The Code tab shows an agent builder per AI agent block, one `async def` per block (marked
  `@canvas-node`) and a `FLOW` that says which step follows which. With Pydantic AI, agents are `Agent`s with an
  `OpenAIChatModel`, `Tool`s and `NativeOutput` for typed answers, run with `agent.run`. With Agno, agents are
  `Agent`s with an `OpenAILike` model, tools and an `output_schema`, run with `agent.arun`. A run executes exactly
  that module.
- **The shape.** Steps side by side run in the same round, a wait-for-all step waits for every branch, paths follow
  the first rule that matches, loops stop at the workflow's step limit, and fields merge as their settings say.
- **Any model.** The canvas model blocks reach the agents through AgentCanvas over the OpenAI chat API: every
  catalog model, scripted test replies, backup models and the answer cache all work, and runs are traced and costed.
- **Ask before a tool call.** Tools named in an AI agent's **Ask before using** wait for you: approve the call,
  change its arguments or reject it. This uses each framework's own mechanism, so nothing is asked twice: Pydantic
  AI's deferred tools (the agent continues from its saved message history) and Agno's tool confirmation (the paused
  agent run is saved and continued).
- **Approvals and questions** pause the run; when you answer, it continues from the step that asked.
- **Model call limit.** The block caps an agent's model calls in one turn: the agent stops with a note in the
  conversation, or the run fails if the block says so.
- **What they do not have yet.** Saved conversation history, state editing, replay and breakpoints; a paused run is
  kept in the server's memory. Planning agents, maps, subflows, memory blocks, HTTP and MCP tools, guard blocks and
  other add-ons stay on LangGraph for now.
- **Export.** The Versions tab exports a project for the framework: the flow, the small runtime it needs, a
  command-line runner that saves a paused run in `.runs/` and continues it, each model block reached over its
  provider's OpenAI-compatible API, and tests that replay the saved test cases with a scripted model.

`GET /v1/workflows/{id}/code?framework=pydantic_ai` (or `agno`) previews the converted code.

## Run a workflow on the Microsoft Agent Framework

Pick **Microsoft Agent Framework** as the workflow's framework. Runs need it installed on the server in a Python
environment of its own: `make framework FW=microsoft_agent_framework` (the Docker image takes
`--build-arg WITH_MICROSOFT_AGENT_FRAMEWORK=true`). Without it you can still design, check and export these
workflows; a run says how to install it.

It works like [Pydantic AI and Agno](#run-a-workflow-on-pydantic-ai-or-agno): the same `async def` per block and
`FLOW`, run the way the canvas draws it, with the same shape, models, approvals, questions, limits and export.
What is its own:

- **The agents** are Agent Framework `Agent`s with an `OpenAIChatCompletionClient`, tools, and a `response_format`
  for typed answers, run with `agent.run`.
- **Ask before a tool call** uses the framework's tool approvals (`approval_mode="always_require"`). The paused run
  keeps the agent's `AgentSession`, and the agent continues in it when you answer, so nothing is asked twice. When
  the model calls several tools at once, only the ones named in **Ask before using** wait for you. A rejected call
  reaches the agent as the framework's own "rejected by user" message, not the reason you typed.
- **Model call limit** is a chat middleware that counts the agent's model calls in one turn.
- **Not yet:** the flow is not written as the framework's own `WorkflowBuilder` graph; that may come later if it is
  asked for. Saved history, state editing, replay and breakpoints are missing here too.

`GET /v1/workflows/{id}/code?framework=microsoft_agent_framework` previews the converted code.

## Run a workflow on Strands or LlamaIndex

Pick **Strands Agents** or **LlamaIndex** as the workflow's framework. Runs need it installed on the server in a
Python environment of its own: `make framework FW=strands` or `make framework FW=llamaindex` (the Docker image takes
`--build-arg WITH_STRANDS=true` and `--build-arg WITH_LLAMAINDEX=true`). Without it you can still design, check and
export these workflows; a run says how to install it.

They work like [Pydantic AI and Agno](#run-a-workflow-on-pydantic-ai-or-agno): the same `async def` per block and
`FLOW`, run the way the canvas draws it, with the same shape, models, approvals, questions, limits and export.
What is each one's own:

- **Strands.** Agents are Strands `Agent`s with an `OpenAIModel`, tools and a `structured_output_model` for typed
  answers, run with `agent.invoke_async`. **Ask before using** is an `AskBefore` hook that uses Strands' own
  interrupts: the agent's messages and interrupt state are saved, and it continues with your answer (approve, change
  the arguments, or reject with a reason the agent reads). The model call limit is Strands' own `Limits(turns=...)`.
- **LlamaIndex.** Agents are `FunctionAgent`s with an `OpenAILike` LLM, tools and an `output_cls` for typed answers,
  run with `agent.run`. A tool in **Ask before using** waits inside the agent's own workflow (an
  `InputRequiredEvent`, answered with a `HumanResponseEvent`); the agent's serialized `Context` and its chat memory
  are saved, and when the model calls several such tools you answer them one at a time. The model call limit is the
  agent's own `max_iterations`.
- **Typed answers with a model that answers in text.** When the model writes the JSON answer as text instead of
  using the framework's answer tool (scripted test replies do), AgentCanvas reads the text as the typed answer.
- **Not yet:** the flow is not written as a Strands `GraphBuilder` graph or as one LlamaIndex `Workflow` with an event
  per edge. Saved history, state editing, replay and breakpoints are missing here too.

`GET /v1/workflows/{id}/code?framework=strands` (or `llamaindex`) previews the converted code.

## Run a workflow on Mastra

Pick **Mastra** as the workflow's framework. Mastra is a TypeScript framework, so its workflows run on Node.js
(22.18 or later) next to the server: `make mastra` installs Mastra and what the flow needs from npm in
`apps/api/.frameworks/mastra` (or set `AGENTCANVAS_MASTRA_DIR` to a folder whose `node_modules` has them, and
`AGENTCANVAS_NODE` to a Node.js other than the one on the `PATH`). Without it you can still design, check and export
these workflows; a run says how to install it. The Docker image has no Node.js.

The workflow becomes a TypeScript module, read in the **Code** tab: a Mastra `Agent` for each AI block, an
`async function` for each block and a `FLOW` that says which step follows which. It runs the way the canvas draws it,
like the Python flows above (steps side by side, **Wait for all**, paths by CEL rules, loops with a limit, field
reducers). What is Mastra's own:

- **Agents** have an AI SDK model that reaches the canvas model over the OpenAI chat-completions API, `createTool`
  tools and a Zod schema for typed answers (`structuredOutput`), and run with `agent.generate`.
- **Ask before using** sets `requireApproval` on those tools. Mastra suspends the agent's run at the call; the run's
  snapshot is saved with the pause, and the agent continues with your answer (approve, change the arguments, or reject
  with a reason the agent reads). When the model calls two such tools, you answer them one after the other.
- **The model call limit** is Mastra's own `maxSteps`.
- **Your tools and Python steps stay Python.** Inside AgentCanvas they run on the server as for any framework.
- **Not yet:** the flow is not written as Mastra's own `createWorkflow` chain. Saved history, state editing, replay
  and breakpoints are missing here too.

**Export** gives a TypeScript project: `npm install`, then `node src/main.ts '{"input": {...}}'` runs it (a pause is
saved in `.runs/` and continued with `{"thread_id": ..., "resume": ...}`) and `npm test` replays the saved test cases
with a scripted model. Node.js runs the TypeScript as it is, with no build step. When the workflow has tools, Python
steps or knowledge bases, a small Python helper in `python/` runs them with `python3`.

`GET /v1/workflows/{id}/code?framework=mastra` previews the converted code.

## What each framework can do

Some features need the framework's help: reading and editing a conversation's saved state, run history and replay
from an earlier step, long-term memory, browsing an app connector's tools, and breakpoints. When a workflow's
framework cannot do one of them, AgentCanvas says so plainly instead of failing; switch the workflow to LangGraph
to use it.

Which frameworks a server offers is `AGENTCANVAS_ADAPTERS` (comma-separated, default
`langchain,openai_agents,crewai,google_adk,pydantic_ai,agno,microsoft_agent_framework,strands,llamaindex,mastra`).
Frameworks other than LangGraph and the OpenAI Agents SDK also need their runtime set up on the server (`make crewai`,
`make framework FW=<id>`, `make mastra`, or in Docker `make docker-up FRAMEWORKS=<id>`). The **Framework** setting
says when the workflow's framework is not set up yet, and what to run; the badge of one that is not reads *not set
up*. The top bar shows the workflow's own framework; **+N** opens the others.

**Export for any framework.** In **Versions → Export**, **Framework** lists the workflow's own framework first, then
every other installed framework that can run it (the same workflow, written for that framework, as in the Code tab),
then the Agent Spec previews. Only a LangGraph service gets the deploy kit and **Where it will run**.

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

