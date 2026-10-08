# Block reference

Blocks (also called widgets) are the building pieces in the palette on the left. **Steps** (solid outline) run
one after another along the grey arrows. **Add-ons** (dashed outline, such as an AI model or a tool) do not run
on their own: they plug into a step's coloured inputs.

New to all this? Start with the [Beginner's guide](beginner-guide.md). On the canvas you can hover any dot on a
block to see what it is for, and click it to see everything that can connect there.

The names below are what you see in the palette. The internal type id (for example `core.llm`) is shown in
brackets; it is what the saved workflow and the generated code use, and it never changes.

## AI

| Block | What it does |
|---|---|
| **Ask AI** (`core.llm`) | Sends your instructions (the prompt) to an AI model and saves its reply in a data field. Insert data with `{{ state.<field> }}`. Needs an **AI model** plugged into `model`. |
| **AI Agent** (`core.agent`) | An AI that can use tools on its own, step by step, until the task is done (LangChain `create_agent`). It reads and writes the conversation (the `messages` field). Plug an *AI model* into `model`, tools into `tools` and add-ons into `middleware`. Settings: instructions (system prompt), **Tools that need approval** (a person approves, edits or rejects each call), and **structured output**: design a response schema with **+ Field** and the final answer is validated and written to `structured_response`. |
| **Planning Agent** (`agent.deep`) | A stronger agent for big, multi-step jobs: it writes a plan, keeps notes in files and can hand work to helper agents (`create_deep_agent`). |
| **Helper Agent** *(add-on, `agent.subagent`)* | A specialist a Planning Agent can hand a task to with its `task` tool. Fill in **When to use it**. |

## AI models

| Block | What it does |
|---|---|
| **AI model** *(add-on, `model.chat`)* | The AI “brain” as `provider:model` from any provider, hosted or local: `anthropic:claude-sonnet-5-5`, `openai:gpt-5.5`, `google_genai:gemini-2.5-flash`, `ollama:llama3.3`, `lmstudio:<model>`… A new AI model block starts as your project's default model from first-run setup. A hint under the field says where its key comes from. Optional temperature, max output tokens, timeout, Base URL, and **API key**: pick a project secret by name, or leave it empty to use the secret named after the provider. One model can power several steps. See [Models and API keys](models.md). |

## Tools

| Block | What it does |
|---|---|
| **Web API tool** *(add-on, `tool.http`)* | Lets an agent call a web service (an HTTP API). The URL is a template over the tool's inputs, e.g. `https://api.example.com/orders/{{ args.order_id }}`. Private and local addresses are refused unless the tool allows private networks. |
| **App connector (MCP)** *(add-on, `mcp.server`)* | All tools of a [Model Context Protocol](https://modelcontextprotocol.io) server. **Browse tools** lists them and flags which ones change things, so you can pick and gate them. |
| **Python tool** *(add-on, `tool.python`)* | A LangChain `@tool` function from the workspace's `tools/` folder. |
| **Memory tools** *(add-on, `tool.memory`)* | Gives an agent `search_memory` and `save_memory` tools for one memory space. |

## People

| Block | What it does |
|---|---|
| **Ask for approval** (`core.approval`) | Pauses until a person approves or rejects, optionally editing the value shown. The decision (true/false) goes to a data field. |
| **Ask a person** (`core.human_input`) | Pauses and asks a person a question (a form when a response schema is set); the answer goes to a data field. |

## Flow control

| Block | What it does |
|---|---|
| **Choose a path** (`core.router`) | Sends the run down different paths: the first rule whose condition is true wins (a CEL expression such as `state.priority == "high"`), otherwise the default path. Each path is an output dot. |
| **Repeat for each item** (`core.map`) | Runs the step connected to `each` once per list item, in parallel. Results must go to an `append` (list) field. |
| **Wait for all** (`core.join`) | Waits until every incoming branch has finished, then continues. |
| **Group of steps** (`core.subgraph`) | A small workflow with its own data, run as one step. Double-click it to look inside. |

## Data and code

| Block | What it does |
|---|---|
| **Set values** (`core.set`) | Writes templated values into data fields, e.g. `summary = "Ticket: {{ state.ticket }}"`. |
| **Run Python code** (`core.script`) | Runs `def run(state) -> dict` from a file in the workspace's `scripts/` folder; return the fields to update. |
| *Workspace scripts and custom blocks* | Files in the workspace appear in the palette under **Workspace**. See [Workspace](workspace.md). |

## Memory

| Block | What it does |
|---|---|
| **Look up memory** (`core.memory_recall`) | Searches long-term memory and writes the matches to a data field. |
| **Save to memory** (`core.memory_remember`) | Saves a field's value in long-term memory under a key. |
| **Forget memory** (`core.memory_forget`) | Deletes one memory by key. |
| **Saved files folder** *(add-on, `backend.store`)* | Mounts a Planning Agent folder (e.g. `/memories/`) on long-term memory. |
| **Scratch files** *(add-on, `backend.state`)* | Planning Agent files kept with the conversation: they live and die with it. |

See [Memory](memory.md) for spaces and sharing.

## Agent add-ons

Add-ons (middleware) change how an agent's model and tool calls behave. Plug one or more into an agent's
`middleware` input; the order of the lines is the order they apply.

| Block | What it does |
|---|---|
| **Protect personal data** (`mw.pii`) | Finds personal data in messages and blocks, redacts, masks or hashes it. |
| **Limit AI calls** / **Limit tool use** (`mw.model_call_limit`, `mw.tool_call_limit`) | Stop the agent after N model or tool calls per run or conversation. |
| **Retry AI calls** / **Retry tools** (`mw.model_retry`, `mw.tool_retry`) | Retry failed calls, waiting a little longer each time. |
| **Backup models** (`mw.model_fallback`) | Try the connected models in order when the main model fails. |
| **Shorten long chats** (`mw.summarization`) | Summarise old messages when the conversation grows too long. |
| **To-do list** (`mw.todo`) | Give the agent a `write_todos` tool to plan multi-step work. |
| **Pick tools first** (`mw.tool_selector`) | Let a cheaper model pick the relevant tools before each call. |
| **Quality check** (`mw.rubric`) | Before answering, a grader model checks the work against your checklist and sends it back with what is missing, up to a set number of rounds. Each round shows live in the Run panel. |
