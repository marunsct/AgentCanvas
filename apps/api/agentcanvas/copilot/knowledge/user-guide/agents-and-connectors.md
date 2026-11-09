# Connect to other agents and tools

Four ways AgentCanvas works with the tools you already use:

- **Agents from other frameworks as steps.** An agent built with the OpenAI Agents SDK, CrewAI, Google ADK and six
  more, or one running elsewhere (A2A or MCP), becomes one step of your workflow. Flows move in and out as
  [Open Agent Spec](https://oracle.github.io/agent-spec/) files.
- **Your workflows as tools for other agents.** Every published workflow is also an MCP server and an A2A agent,
  so Claude Desktop, Cursor, ChatGPT and other agent frameworks can call it.
- **One-click app connectors.** A gallery of ready-made MCP servers (GitHub, Slack, Notion, Postgres, a web
  browser, search and more) that your agents can use as tools.
- **Traces in your observability tool.** Every run can be sent to Langfuse, Arize Phoenix, Datadog or any
  OpenTelemetry backend.

## Use agents from other frameworks

Three blocks in the **AI** group run an agent that was not built on the canvas. Each is one step: it gets an input,
returns an answer, and shows both in the trace. A badge on the block names the framework (or **A2A**, **MCP**).

| Block | Runs | You set |
|---|---|---|
| **Agent from another framework** | an agent from your [Workspace](workspace.md), inside this server | the framework and where the agent is, e.g. `tools.agents:support` |
| **Remote agent (A2A)** | an agent running elsewhere, over the Agent2Agent protocol | its Agent Card address (`…/.well-known/agent-card.json`) |
| **Remote agent (MCP)** | one tool of an MCP server, e.g. an agent published as a tool | plug an **App connector (MCP)** into `tools`, then the tool name |

All three have **What to send the agent** (insert data with `{{ state.<field> }}`; empty sends the latest chat
message), **Save the answer in** (`messages` adds the answer to the chat) and **Ask a person before it runs**,
which shows what will be sent on an approval card; a rejection skips the agent.

**Supported frameworks** and what the step calls:

| Framework | Package (exports install this version) | Point the block at |
|---|---|---|
| OpenAI Agents SDK | `openai-agents==0.24.0` | an `Agent` |
| CrewAI | `crewai==1.15.39` | a `Crew` (its task texts can use `{input}` and other field names), an `Agent` or a `Flow` |
| Google ADK | `google-adk==2.14.0` | an agent (`LlmAgent`, …) |
| Pydantic AI | `pydantic-ai-slim==2.66.0` | an `Agent` |
| Agno | `agno==3.1.11` | an `Agent` or `Team` |
| Strands Agents | `strands-agents==1.64.0` | an `Agent` |
| LlamaIndex Workflows | `llama-index-workflows==2.25.0` | a `Workflow` (it gets `input=` and the other fields) |
| Microsoft Agent Framework | `agent-framework-core==1.25.0` | an agent (`client.as_agent(...)`) |
| Claude Agent SDK | `claude-agent-sdk==0.2.174` | a `ClaudeAgentOptions` |
| Python function | none | `async def run(input, ctx)`: return text, or a dict to set several fields |

- The agent's own package (and its model provider's, such as `openai`) must be installed on the server, and an
  export lists the framework package in its requirements. Add provider packages in the workspace if the agent needs
  them.
- A function with no arguments that returns the agent works too: a fresh agent is built for every run, so runs
  never share an agent's memory.
- The agent's tools run inside its framework, so AgentCanvas cannot see or gate them one by one. Tick what they can
  do under **Side effects** (writes, messages, code, spending) so the blast-radius view and dry runs know, and use
  **Ask a person before it runs** where that matters. The hosted edition does not offer this block, because the
  agent's code runs inside the server.
- **Remote agents (A2A)**: when the remote agent asks for more information (`input-required`), the run pauses and
  the question appears in the Inbox; the answer goes back to the same task. Addresses on private networks are
  refused unless you turn on **Allow private network**. Put keys in **Headers** as secrets. What the remote agent
  said so far is kept in the database, so the answer can reach any server, after a restart too. (An exported project
  keeps it in its own process: if that restarts while a remote agent waits, the step stops with a message instead of
  sending the first message again; start the run again.)

### Open Agent Spec files

[Open Agent Spec](https://oracle.github.io/agent-spec/) is a framework-neutral file format for agents and flows.

- **Export**: in the editor, **Versions** → **Download as an Agent Spec file**. AI steps become `LlmNode`s, agents
  `AgentNode`s (remote agents as `A2AAgent`), questions `InputMessageNode`s, routers with rules like
  `state.category == "billing"` become `BranchingNode`s, and other steps become tools the other runtime provides.
  After downloading, a list says what Agent Spec cannot express, for example models it has no config for (they
  are written as OpenAI-compatible) or an agent's tools. Header values typed in directly are left out of the file;
  secret references (names, never values) stay.
- **Import**: on the home page, **Import an Agent Spec file** reads a flow, or a single agent, as a new workflow and
  lists what it approximated before you open it. Steps AgentCanvas cannot run become placeholders to replace.
- A file AgentCanvas exported carries the whole workflow, so importing it back gives exactly the same workflow,
  layout included.

## Call a workflow from other agents

[Publish](publish.md) the workflow, then open **Versions** → the deployment → **Use from other agents (MCP, A2A)**.
The panel shows the addresses and a ready-to-paste Claude Desktop setting. Both use the deployment's API key, the
same one as the [deployment API](publish.md), with the same rate limits.

### As an MCP server

| | |
|---|---|
| Address | `https://<your AgentCanvas>/api/v1/deployments/<id>/mcp` |
| Transport | Streamable HTTP (every answer is one JSON message; no server-sent stream) |
| Protocol | MCP `2026-07-28` (no handshake: `server/discover`, the version in each request) and the earlier revisions back to `2024-11-05` for clients that open with `initialize` |
| Sign-in | `Authorization: Bearer <deployment key>` |
| Tools | one tool named after the workflow, with the workflow's input fields; plus `answer` when the workflow can pause for a person |

**Claude Desktop** connects to remote servers through [`mcp-remote`](https://www.npmjs.com/package/mcp-remote)
(needs Node.js). Add this to `claude_desktop_config.json` and restart Claude Desktop:

```json
{
  "mcpServers": {
    "my-workflow": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://<your AgentCanvas>/api/v1/deployments/<id>/mcp",
               "--header", "Authorization: Bearer <deployment key>"]
    }
  }
}
```

Clients that support remote MCP servers with headers (Cursor, VS Code, agent frameworks such as LangChain's MCP
adapter or the OpenAI Agents SDK) take the address and the header directly.

When a run pauses for an approval or a question, the tool's answer says so and gives a `thread_id`; the calling
agent continues it with `answer` (`approve: true` or `false`, or `text` for a question). To continue a
conversation, pass the `thread_id` of the earlier run to the workflow's tool.

### As an A2A agent

| | |
|---|---|
| Agent card | `https://<your AgentCanvas>/api/v1/deployments/<id>/a2a/.well-known/agent-card.json` (no key needed to read it) |
| Endpoint | `https://<your AgentCanvas>/api/v1/deployments/<id>/a2a` (JSON-RPC: A2A 1.0 with the header `A2A-Version: 1.0`, and A2A 0.3) |
| Sign-in | `Authorization: Bearer <deployment key>` |

- `message/send` runs the workflow. A **data** part gives the input fields; a **text** part goes to the workflow's
  text field (one named `message`, `question`, `query`, `input`, `text`, `request`, `prompt` or `ticket`, or its
  only text input). The answer is a task whose artifact holds the output.
- A pause becomes the `input-required` state. Send the answer to the same task (`taskId`): "yes" or "no" for an
  approval, or the answer to a question.
- `contextId` continues a conversation; `tasks/get` reads a task again.
- A2A 1.0 clients use `SendMessage` and `GetTask` (the same behaviour, in 1.0's form: `TASK_STATE_COMPLETED`,
  parts without `kind`); the agent card comes in 1.0's form when read with `A2A-Version: 1.0`, and lists both
  versions either way.
- Not supported: streaming (`message/stream`, `SendStreamingMessage`), push notifications and cancelling a task.

Runs started over MCP or A2A are ordinary runs of the deployment: they appear under **Runs**, count towards online
evaluation and keep their conversations.

## App connectors

In the editor's block list, open **App connectors** (or search for an app). Drag one onto the canvas, or
double-click it: it becomes an **App connector (MCP)** block already set up for that app. Plug its `tools` output
into an agent, and use **Browse tools** to pick tools.

- **Keys.** Connectors that need a key name the secret they read, such as `GITHUB_MCP_AUTH`. Add it under
  **Settings › Secrets**. Where a server expects `Authorization: Bearer <token>`, store the whole value,
  including `Bearer `.
- **Local connectors** start a program on the AgentCanvas server (`npx` or `uvx`). They work only in the local
  edition, after an admin turns on *local MCP servers*, and need Node.js or uv on the server.
- **Sign-in only.** A few apps (Linear, Atlassian…) accept only an interactive sign-in (OAuth). Select the block and click
  **Connect** to sign in with your own account; see [Sign-in per person](#sign-in-per-person).

Each entry was checked against npm, PyPI or the official MCP registry when the gallery was built; its tooltip says
what it needs.

### Sign-in per person

A connector that has the **Sign in with OAuth** setting (the apps above that accept only a sign-in come with it) does not use
one shared key. Each person connects their own account, once:

1. Select the connector's block and click **Connect**. You go to the app's own sign-in page and come back; the block then
   shows *connected as you*. **Settings › You › Connections** lists your connections and lets you disconnect them.
2. Set **Acts as** on the block, which says whose connection a run uses:
   - **starter** (the default): the connection of the person who starts the run, or resumes it after an approval. A run
     nobody started (a schedule, a webhook, a deployment call or a share link) has no connection of its own and stops
     with a message saying so.
   - **service**: the organisation's service connection, which an admin connects with **Connect the service account** on
     the same block. Use it for schedules, webhooks and deployments, and when the app account should be shared on purpose.
     A draft run that a builder starts in the editor does not use it (that would lend the admin's account to every
     builder): publish the workflow, to a test environment if you like, and run it there. Admins' own runs, and a
     deployment's runs resumed from the inbox, do use it.
3. **Browse tools** uses your own connection (or the service one, when the block is set to it and you are an admin).

Behind the scenes AgentCanvas follows the MCP authorization standard against whatever server the block points at: it finds
the server's sign-in settings itself, uses the server's client-metadata-document support or registers itself with the
server, and signs in with PKCE. Tokens are stored encrypted with the organisation's key and renewed as needed; if the app
revokes access, the connection shows *expired* and runs say to connect it again. A provider that supports neither client
metadata documents nor dynamic registration (GitHub and Slack style) needs an app made at the provider: an admin registers the
redirect address shown under **Settings › Connections › Sign-in credentials** there, then enters the client id and secret with
the connector's server address. The secret is stored encrypted and never shown again, and entered credentials are used before
the other two ways, for that server only: another connector whose server names the same sign-in server cannot use them
(it would receive tokens made with your app). To use them for another server too, enter the same client id with that
server's address; the secret is kept. **Connect** says which sign-in server it sends you to when that is not the app's
own host, so you can check you trust the connector's server before signing in. The operator sets `AGENTCANVAS_PUBLIC_URL`
([Deploy](deploy.md)). A workflow exported as code cannot sign in: connectors with this setting need the AgentCanvas server.

## Send traces to your observability tool

Under **Settings › Observability**, an admin picks where traces go:

| Destination | Address | Keys |
|---|---|---|
| Langfuse | `https://cloud.langfuse.com` (EU), `https://us.cloud.langfuse.com` (US), or your own | public and secret key, each as a project secret |
| Arize Phoenix | your Phoenix, e.g. `http://phoenix:6006` | API key, if your Phoenix needs one |
| Datadog | the Datadog Agent's OTLP/HTTP receiver, e.g. `http://datadog-agent:4318` | none here: the Agent holds your Datadog key |
| Other | any OTLP/HTTP traces URL, e.g. an OpenTelemetry Collector | an optional `Authorization` header, as a secret |

**Send a test trace** checks the address and keys. From then on every run of the project's workflows is sent as one
trace: the run, each step, every model call (model, tokens, reply) and every tool call (arguments, result). Spans
use the OpenTelemetry GenAI conventions and the OpenInference ones, which all three tools understand.

- Secret values are masked, and fields a workflow marks as *never in traces* are redacted. Turn off **Include
  inputs, outputs and model replies** to send only timings, models and token counts.
- Export runs in the background and never slows a run down. If the destination is down, traces are dropped and
  the server logs a warning.
- A model or tool call is shown as a point in time inside its step: the step's duration is exact.
