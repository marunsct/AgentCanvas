# Widget reference

Widgets are the building blocks in the palette. **Steps** run in order along the edges; **resources** (marked
*resource* in the palette) are wired into steps' ports.

## Agents

| Widget | What it does |
|---|---|
| **Agent** | A tool-using agent (LangChain `create_agent`) that reads and writes the `messages` channel. Wire a *Chat model* into `model`, tools into `tools` and middleware into `middleware`. Settings: system prompt, **Pause for a human before these tools run** (approve, edit or reject each call), and **structured output**: design a response schema with **+ Field** and the final answer is validated and written to `structured_response`. |
| **Deep Agent** | A planning agent with a virtual filesystem, subagents and long-term memory (`create_deep_agent`). Good for long, multi-step tasks. |
| **Subagent** *(resource)* | A specialist a Deep Agent can delegate to with its `task` tool. |

## AI

| Widget | What it does |
|---|---|
| **LLM call** | One chat-model call: renders the prompt from the state and writes the answer text to a channel. |

## Logic

| Widget | What it does |
|---|---|
| **Router** | Chooses the next step: the first case whose CEL condition is true wins, otherwise `default`. Each case is an output handle. |
| **Map (for each)** | Runs the step wired to `each` once per list item, in parallel. Results must go to an `append` channel. |
| **Join (wait for all)** | Waits until every incoming branch has finished, then continues. |
| **Subgraph** | A group of steps with its own state, run as one step. |

## Data and code

| Widget | What it does |
|---|---|
| **Set values** | Writes templated values into channels, e.g. `summary = "Ticket: {{ state.ticket }}"`. |
| **Python script** | Runs `def run(state) -> dict` from a file in the workspace's `scripts/` folder; return the channels to update. |
| *Workspace scripts and custom widgets* | Files in the workspace appear in the palette under **Workspace**. See [Workspace](workspace.md). |

## Human in the loop

| Widget | What it does |
|---|---|
| **Approval** | Pauses for a person to approve or reject, optionally editing the value shown. The decision (true/false) goes to a channel. |
| **Human input** | Pauses and asks a person for input (a form when a response schema is set); the answer goes to a channel. |

## Memory

| Widget | What it does |
|---|---|
| **Recall** | Searches long-term memory and writes the matches to a channel. |
| **Remember** | Saves a channel's value in long-term memory under a key. |
| **Forget** | Deletes one memory by key. |
| **Memory tools** *(resource)* | Gives an agent `search_memory` and `save_memory` tools for one memory space. |
| **Long-term files** *(resource)* | Mounts a Deep Agent folder (e.g. `/memories/`) on long-term memory. |
| **State files** *(resource)* | Deep Agent files kept in the thread's state: they live and die with the conversation. |

See [Memory](memory.md) for spaces and sharing.

## Models

| Widget | What it does |
|---|---|
| **Chat model** *(resource)* | A model as `provider:model` from any provider, hosted or local: `anthropic:claude-sonnet-5-5`, `openai:gpt-5.5`, `google_genai:gemini-2.5-flash`, `ollama:llama3.3`, `lmstudio:<model>`… A hint under the field says where its key comes from. Optional temperature, max output tokens, timeout, Base URL, and **API key**: pick a project secret by name, or leave it empty to use the secret named after the provider. One model can serve several steps. See [Models and API keys](models.md). |

## Tools

| Widget | What it does |
|---|---|
| **HTTP request tool** *(resource)* | Calls an HTTP API. The URL is a template over the tool's arguments, e.g. `https://api.example.com/orders/{{ args.order_id }}`. Private and local addresses are refused unless the tool allows private networks. |
| **MCP server** *(resource)* | All tools of a [Model Context Protocol](https://modelcontextprotocol.io) server. **Browse tools** lists them and flags which are destructive, so you can pick and gate them. |
| **Python tool** *(resource)* | A LangChain `@tool` function from the workspace's `tools/` folder. |

## Middleware

Middleware wraps an agent's model and tool calls. Wire one or more into an Agent's `middleware` port; the order
of the edges is the order they apply.

| Widget | What it does |
|---|---|
| **PII guard** | Detects personal data in messages and blocks, redacts, masks or hashes it. |
| **Model call limit** / **Tool call limit** | Stop the agent after N model or tool calls per run or thread. |
| **Model retry** / **Tool retry** | Retry failed calls with exponential backoff. |
| **Model fallback** | Try the wired models in order when the main model fails. |
| **Summarization** | Summarise old messages when the conversation grows too long. |
| **Planning (to-dos)** | Give the agent a `write_todos` tool to plan multi-step work. |
| **Tool selector** | Let a cheaper model pick the relevant tools before each call. |
| **Rubric** | Self-grading: before answering, a grader model checks the work against your rubric and sends it back with what is missing, up to a set number of rounds. Each round shows live in the Run panel. |
