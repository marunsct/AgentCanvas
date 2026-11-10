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
| Agno | `agno==3.1.12` | an `Agent` or `Team` |
| Strands Agents | `strands-agents==1.64.0` | an `Agent` |
| LlamaIndex Workflows | `llama-index-workflows==2.26.1` | a `Workflow` (it gets `input=` and the other fields) |
| Microsoft Agent Framework | `agent-framework-core==1.25.0` | an agent (`client.as_agent(...)`) |
| Claude Agent SDK | `claude-agent-sdk==0.2.179` | a `ClaudeAgentOptions` |
| Python function | none | `async def run(input, ctx)`: return text, or a dict to set several fields |

- The agent's own package (and its model provider's, such as `openai`) must be installed on the server, and an
  export lists the framework package in its requirements. Add provider packages in the workspace if the agent needs
  them.
- A function with no arguments that returns the agent works too: a fresh agent is built for every run, so runs
  never share an agent's memory.
- The agent's tools run inside its framework, so AgentCanvas cannot see or gate them one by one. Tick what they can
  do under **Side effects** (writes, messages, code, spending) so the blast-radius view and dry runs know, and use
  **Ask a person before it runs** where that matters.
- The agent's code runs inside the server itself, not in the sandbox. So a server that runs workspace code in a
  sandbox (a production server, or any server whose `AGENTCANVAS_SCRIPT_EXECUTION` is not `inprocess`) and the
  hosted edition refuse this block with E041 and leave its templates out of the gallery. There, design the agent on
  an **AI agent** block **Designed for** its framework, or call your code from a **Script** step. Only an operator
  who trusts every builder with the server's own keys turns it on (`AGENTCANVAS_ALLOW_UNISOLATED_SCRIPTS=true` with
  `inprocess`, see [deploy.md](deploy.md)).
- **Remote agents (A2A)**: when the remote agent asks for more information (`input-required`), the run pauses and
  the question appears in the Inbox; the answer goes back to the same task. Addresses on private networks are
  refused unless you turn on **Allow private network**; a link-local or cloud metadata address (`169.254.169.254`)
  is refused even then, for remote agents, web API tools and app connectors alike. Put keys in **Headers** as secrets. What the remote agent
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
  layout included. Importing a file never adds code to your project's workspace: a template's extra files (event
  handlers, custom blocks) come only from the gallery, not from a file someone shared.

## Call a workflow from other agents

[Publish](publish.md) the workflow, then open **Versions** → the deployment → **Use from other agents (MCP, A2A)**.
The panel shows the addresses and a ready-to-paste Claude Desktop setting. Both use the deployment's API key, the
same one as the [deployment API](publish.md), with the same rate limits. A builder can also let people sign in with
the organisation's identity provider instead ([Sign-in with OAuth](publish.md#sign-in-with-oauth-mcp-and-a2a)): then
each person is their own end user, with their own memories and conversations.

### As an MCP server

| | |
|---|---|
| Address | `https://<your AgentCanvas>/api/v1/deployments/<id>/mcp` |
| Transport | Streamable HTTP (every answer is one JSON message; no server-sent stream) |
| Protocol | MCP `2026-07-28` (no handshake: `server/discover`, the version in each request) and the earlier revisions back to `2024-11-05` for clients that open with `initialize` |
| Sign-in | `Authorization: Bearer <deployment key>`, or an OAuth access token when the deployment allows it (the MCP authorization spec: a `401` names the protected resource metadata, RFC 9728) |
| Tools | one tool named after the workflow, with the workflow's input fields; plus `answer` when the workflow can pause for a person and the client cannot show forms (see below) |

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

With [OAuth sign-in](publish.md#sign-in-with-oauth-mcp-and-a2a) on, clients that follow the MCP authorization spec
(Claude's custom connectors, ChatGPT, VS Code and others) need only the address: they find the identity provider
from the `401`, sign the person in and send their access token. The identity provider must let the client
register (Client ID Metadata Documents, dynamic client registration, or a client you register for it there). Nobody
pastes a key, and every person's runs, memories and conversations are their own. A client that can only send a
fixed header keeps using a key.

Each deployment also has an **MCP Server Card** at `https://<your AgentCanvas>/api/v1/deployments/<id>/mcp/server-card`
(no key needed): its name, version, address and the header it needs, for registries and clients that discover servers
before connecting. It never contains the key. Its name is the public host reversed and the workflow's tool name
(`com.example.agents/greeter`), so set `AGENTCANVAS_PUBLIC_URL` for a stable one.

When a run pauses for an approval or a question, who answers depends on the client:

- **Clients that show forms** (MCP `2026-07-28` clients declaring `elicitation`): the client shows the person a
  form (approve or reject with a comment, or the answer to the question, shaped by the question's answer schema)
  and sends the answer back by retrying the call, and the run goes on. The calling model never answers for the
  person, so it cannot approve its own tool calls; these clients get no `answer` tool. The form's answer is tied
  to the deployment key that started the call (or, for a person signed in with OAuth, to that person, so their
  token may be refreshed meanwhile), to that call and its arguments, and to 15 minutes; a pause is
  answered once. Declining an approval rejects it; closing the form leaves the run waiting (it can still be
  answered in the **Inbox**). Each answer that leads to another pause asks again.
- **Other clients**: the tool's answer says the run paused and gives a `thread_id`; the calling agent continues
  it with `answer` (`approve: true` or `false`, or `text` for a question). When several pauses wait at once
  (steps on parallel branches), the answer lists each with its `interrupt_id`, and each `answer` call names the
  one it answers; one without an id is refused while several wait.

To continue a conversation, pass the `thread_id` of the earlier run to the workflow's tool.

**Long runs.** Clients that support the MCP **Tasks** extension (they declare `io.modelcontextprotocol/tasks`;
`server/discover` lists it) get a task instead of waiting: when the run still works after 30 seconds, or waits
for a slot in the run queue, the tool call answers with a `taskId` (the run's id). The client then polls
`tasks/get` (every 3 seconds is asked for), which gives `working`, `completed` with the tool's result, or
`cancelled`; `tasks/cancel` stops the run on whichever server runs it. A task whose run pauses for a person shows
`input_required` with the form (for clients that show forms; the answer goes back with `tasks/update`), else it
completes with the paused result, like a call without tasks. Tasks are polled with the deployment key and only
the deployment's own runs are found, so a task id alone grants nothing; a person signed in with OAuth finds only
their own tasks. Any server answers.

Other clients wait up to 5 minutes for the run. A longer run answers `running` with its `run_id` and the address
of its result (`GET /v1/deployments/<id>/runs/<run_id>` with the deployment key, see
[Publish](publish.md#long-runs-start-now-read-the-result-later)).

### As an A2A agent

| | |
|---|---|
| Agent card | `https://<your AgentCanvas>/api/v1/deployments/<id>/a2a/.well-known/agent-card.json` (no key needed to read it) |
| Endpoint | `https://<your AgentCanvas>/api/v1/deployments/<id>/a2a` (JSON-RPC: A2A 1.0 with the header `A2A-Version: 1.0`, and A2A 0.3) |
| Sign-in | `Authorization: Bearer <deployment key>`, or an OAuth access token for the A2A endpoint when the deployment allows it |

- `message/send` runs the workflow. A **data** part gives the input fields; a **text** part goes to the workflow's
  text field (one named `message`, `question`, `query`, `input`, `text`, `request`, `prompt` or `ticket`, or its
  only text input). The answer is a task whose artifact holds the output.
- A pause becomes the `input-required` state. Send the answer to the same task (`taskId`): "yes" or "no" for an
  approval, or the answer to a question. When several pauses wait at once, the status message lists them with
  their ids; add a data part `{"interrupt_id": "..."}` to each reply to say which one it answers (a reply without
  one is refused while several wait).
- `contextId` continues a conversation; `tasks/get` reads a task again, with its output or what it waits for,
  from whichever server answers: the result is kept with the run, not only in the memory of the server that ran it.
- A2A 1.0 clients use `SendMessage` and `GetTask` (the same behaviour, in 1.0's form: `TASK_STATE_COMPLETED`,
  parts without `kind`); the agent card comes in 1.0's form when read with `A2A-Version: 1.0`, and lists both
  versions either way.
- `tasks/cancel` (`CancelTask` in 1.0) stops a working task, on whichever server runs it (another server stops it
  within a few seconds: the answer may still say `working`). A task that ended, or waits for an answer, cannot be
  cancelled.
- Not supported: streaming (`message/stream`, `SendStreamingMessage`) and push notifications.
- The agent card (and the MCP Server Card) may be cached for five minutes and carry an `ETag`: a republished
  deployment's new card reaches clients that cache it within that time.
- With [OAuth sign-in](publish.md#sign-in-with-oauth-mcp-and-a2a) on, the card's `securitySchemes` lists an OpenID
  Connect scheme (`openIdConnectUrl`: the issuer's discovery document) beside the key, with the scopes the
  deployment requires; either one will do. Its description names the audience the token must have (the A2A
  endpoint's address, or the deployment's audience), which is what Microsoft 365 Copilot and Agent 365 ask for when
  you connect an A2A agent under Entra ID. The issuer must publish an OpenID Connect discovery document for A2A
  clients; a person signed in this way finds only their own tasks and conversations.

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

### Published tools are pinned

An app's server can change after you reviewed its tools: a new tool appears, or a description is rewritten to steer
the agent (a "rug pull" or tool poisoning). A published agent does not pick such changes up on its own.

- **Browse tools** records, for every tool, a fingerprint of its whole description, its arguments and its
  annotations (such as *destructive*). Publishing keeps that list with the version, narrowed by **Only these tools**.
- Publishing refuses a connector whose tools were never browsed (**E045**; in the editor it is only a note, and
  draft runs work). Naming tools in **Only these tools** is not enough: browse them.
- A connector browsed before fingerprints were recorded gets them when you publish: AgentCanvas lists the server's
  tools again and fingerprints the ones you saw. If one of them now reads differently (description, *destructive*
  or *read-only*), publishing is refused until you browse the tools again. Tools you never saw are not added.
- Runs of a deployment, and runs started by a schedule or webhook, offer the agent only the tools in that list, and
  only while their fingerprint is unchanged. A tool the server added since is left out. A tool whose description,
  arguments or annotations changed is left out too; set **When a published tool changes** to **fail** on the block
  to stop the run instead.
- The run shows a warning naming what was left out. Runs you start in the editor are not pinned, so you see what
  the server offers now.
- The editor shows the change on the block (**W045** in Problems) until you publish again, and the deployment list
  of the API carries it as `tool_drift`. Each change is written once to the audit log (`mcp_tool_drift`, with the
  run, the deployment and the tool names) and forwarded like other
  [security events](deploy.md#security-events-and-audit-forwarding).
- To accept a change, select the block, click **Browse tools**, review the tools, and publish again.

A version published before tools were pinned keeps working: tools browsed then are pinned by name only, and a block
that was never browsed offers the server's current tools, with a warning on every run (and a W045 note in the
editor). Publish again to pin them.

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
- The hosted service sends traces to public addresses only. An address that is private, internal or the server's
  own (including the Phoenix and Datadog defaults on `localhost`) is refused when you save or test it, and every
  connection is checked again as it is made.
