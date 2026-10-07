# Connect to other agents and tools

Three ways AgentCanvas works with the tools you already use:

- **Your workflows as tools for other agents.** Every published workflow is also an MCP server and an A2A agent,
  so Claude Desktop, Cursor, ChatGPT and other agent frameworks can call it.
- **One-click app connectors.** A gallery of ready-made MCP servers (GitHub, Slack, Notion, Postgres, a web
  browser, search and more) that your agents can use as tools.
- **Traces in your observability tool.** Every run can be sent to Langfuse, Arize Phoenix, Datadog or any
  OpenTelemetry backend.

## Call a workflow from other agents

[Publish](publish.md) the workflow, then open **Versions** → the deployment → **Use from other agents (MCP, A2A)**.
The panel shows the addresses and a ready-to-paste Claude Desktop setting. Both use the deployment's API key, the
same one as the [deployment API](publish.md), with the same rate limits.

### As an MCP server

| | |
|---|---|
| Address | `https://<your AgentCanvas>/api/v1/deployments/<id>/mcp` |
| Transport | Streamable HTTP (every answer is one JSON message; no server-sent stream) |
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
| Endpoint | `https://<your AgentCanvas>/api/v1/deployments/<id>/a2a` (JSON-RPC, A2A 0.3; v1.0 method names also work) |
| Sign-in | `Authorization: Bearer <deployment key>` |

- `message/send` runs the workflow. A **data** part gives the input fields; a **text** part goes to the workflow's
  text field (one named `message`, `question`, `query`, `input`, `text`, `request`, `prompt` or `ticket`, or its
  only text input). The answer is a task whose artifact holds the output.
- A pause becomes the `input-required` state. Send the answer to the same task (`taskId`): "yes" or "no" for an
  approval, or the answer to a question.
- `contextId` continues a conversation; `tasks/get` reads a task again.
- Not supported: streaming (`message/stream`), push notifications and cancelling a task.

Runs started over MCP or A2A are ordinary runs of the deployment: they appear under **Runs**, count towards online
evaluation and keep their conversations.

## App connectors

In the editor's block list, open **App connectors** (or search for an app). Drag one onto the canvas, or
double-click it: it becomes an **App connector (MCP)** block already set up for that app. Plug its `tools` output
into an agent, and use **Browse tools** to pick tools.

- **Keys.** Connectors that need a key name the secret they read, such as `GITHUB_MCP_AUTH`. Add it under
  **Workspace → Secrets**. Where a server expects `Authorization: Bearer <token>`, store the whole value,
  including `Bearer `.
- **Local connectors** start a program on the AgentCanvas server (`npx` or `uvx`). They work only in the local
  edition, after an admin turns on *local MCP servers*, and need Node.js or uv on the server.
- **Sign-in only.** A few apps accept only an interactive sign-in (OAuth). They are listed so you can find them,
  but AgentCanvas cannot sign in to them yet.

Each entry was checked against npm, PyPI or the official MCP registry when the gallery was built; its tooltip says
what it needs.

## Send traces to your observability tool

Under **Setup** → **Send traces to your observability tool**, an admin picks a destination:

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
