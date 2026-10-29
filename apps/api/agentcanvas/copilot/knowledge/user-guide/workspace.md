# Workspace: code, secrets and events

Each project has a **workspace**: a git repository of Python code used by its workflows. Open it from the
**Workspace** link in the top bar.

![Workspace page](img/workspace.png)

| Folder | What goes there | Appears as |
|---|---|---|
| `scripts/` | Script functions `def run(state: dict) -> dict` (sync or async) | *Workspace scripts* in the palette; use with the **Run Python code** block |
| `tools/` | LangChain `@tool` functions | *Python tool* resources for agents |
| `nodes/` | Custom widgets declared with `@canvas_node(...)` | New widgets in the palette, in the category you choose |
| `events/` | Event handlers declared with `@on("type")`; JSON schemas in `events/schemas/` | The **Events** page |

Click a file to edit it (with lint markers), **New file** to add one. Every save is a git commit, and a workflow
**version** pins the workspace commit it was made with.

## Each workflow's own code, and shared code

Open the Workspace from a workflow (the **Workspace** button in the editor, or **Open … in the Workspace** on a
Script block) and you see two groups:

- **This workflow's code** lives in `flows/<workflow>/` with its own `scripts/`, `tools/` and `nodes/`. A Script
  block's **+ Create the code file** puts the file there, and its import path starts with `flows.<workflow>.`.
  Other workflows do not see it in their palette.
- **Shared code** is everything in the folders at the root (`scripts/`, `tools/`, `nodes/`, `events/`). Every
  workflow in the project can use it, and event handlers always live here.

Code can import other workspace code the usual Python way, for example `from scripts.textutil import shout` or
`from .helpers import clean`, and it works the same on the canvas, in the sandbox and in an export.

**← Back to *workflow*** at the top (or the browser's Back button) returns you to the canvas with the same zoom,
position and selected block. The Workspace opened from the home page shows every file in the project.

The **+ Script step**, **+ Tool for the AI** and **+ Custom block** buttons start a new file with working starter
code for that kind of file.

## Copilot help with code

Below the editor of a Python file, the **✦ Copilot** box helps you write it:

- **Write with Copilot**: describe what the code should do (select lines in the editor to focus on them).
- **Explain**: what the file, or the selected lines, does, step by step.
- **Fix N problems**: fixes the problems the linter reports, changing as little as possible.

The Copilot answers with an explanation and, when it changes code, a diff. **Accept changes** puts the new code in
the editor as an ordinary, undoable edit; nothing is saved until you press **Save**. It uses the Copilot's model and
privacy settings (see [Copilot](copilot.md)). Viewers can use **Explain** only.

## A script

```python
# scripts/clean.py
def run(state: dict) -> dict:
    text = str(state.get("text") or "")
    return {"text": text.strip()}   # return only the channels you want to update
```

## A tool

```python
# tools/orders.py
from langchain_core.tools import tool

@tool
def order_status(order_id: str) -> str:
    """Look up the status of an order."""
    return "shipped"
```

## A custom widget

```python
# nodes/crm.py
from agentcanvas.sdk import canvas_node

@canvas_node(name="acme.score_lead", label="Score lead", category="CRM", writes=["score"])
def score_lead(state: dict, cfg: dict) -> dict:
    return {"score": 0.8}
```

`config` can be a Pydantic model or a JSON Schema; its fields become the widget's settings form. Declare
`side_effects` so the [blast radius](canvas.md#blast-radius) and policies know what it does.

## Secrets

Store API keys and passwords in **Settings › Secrets** (**Save secret**; the Workspace page links there). Names are
`UPPER_SNAKE_CASE`, values at least 6 characters. Secrets are encrypted at rest, can't be read back through the API,
and are masked as `***` in run events, traces and error messages, also when they appear base64- or URL-encoded.

- In widget settings: `{"$secret": "NAME"}`, e.g. an HTTP tool's header. A AI model block's **API key** field lets you
  pick a secret by name; a secret named after the provider (`OPENAI_API_KEY`, `GOOGLE_API_KEY`…) is used by that
  provider's models automatically. See [Models and API keys](models.md).
- In scripts and custom widgets: `from agentcanvas.sdk import secret` then `secret("NAME")`.

## Sandbox

Workspace code runs where the server is configured to run it (`AGENTCANVAS_SCRIPT_EXECUTION`): in the API process
(local default), a child process, a Docker container, the **sandbox service** (production default: a separate
container or pod with none of the server's secrets), or a LangSmith Sandbox (experimental). **Settings › Sandbox**
edits the project's `sandbox.json`: time and memory limits, and the hosts code may reach.

Outside the API process, every piece of workspace code runs there: Script steps, custom widgets, event handlers and
Python tools (each tool call is one sandbox run; the tool's name and arguments are read from its source). A child
process gets time, memory and file limits and no network, but runs as the server's user and is not an isolation
boundary. The sandbox service is: each call sends it only the project's files, it runs them with the same limits,
and it holds no database credentials, keys or other projects' files. Use it, or Docker, for code you do not trust.

**Network.** Code has no network unless the project lists hosts (`api.github.com`, `files.example.com:8443`,
`*.googleapis.com`). With Docker, the sandbox service or LangSmith it then reaches those hosts and nothing else:
its only way out is the egress proxy, which checks every connection against the list (signed by the server for
each call, so code cannot widen it). It never reaches a link-local or cloud metadata address (`169.254.169.254`),
and reaches a private one (the database, `10.x`, `172.16.x`) only when whoever runs the server allows it with
`AGENTCANVAS_EGRESS_ALLOW_PRIVATE=1`: naming the address in `sandbox.json` is not enough, since the project writes
that file. HTTP clients that read `HTTPS_PROXY` (requests, httpx, urllib) work as usual; others get
no connection. The in-process and child-process modes cannot limit the network: Settings does not offer the list
there, and a staging or production server refuses to run code that has one. Headers injected into outgoing
requests (`inject`, so code can call an API without seeing its key) are applied by the LangSmith Sandbox only.

## Events

Events start or resume workflows when something happens elsewhere ("a lead was created", "a payment failed").

```python
# events/leads.py
from agentcanvas.sdk import on

@on("crm.lead.created", filter="event.data.amount > 50000", dedup_key="event.data.lead_id")
def qualify(event, act):
    act.start_workflow("Lead qualification", input={"lead": event.data})
```

Handlers only *describe* actions (`act.start_workflow`, `act.resume`, `act.emit`); the platform performs them, so
handler code never holds credentials. Steps can emit events during a run with `agentcanvas.sdk.emit(type, data)`.

The **Events** page shows the **event map** (which events start or resume which workflows), lets you **Send a
test event** (or **Dry run** it to see what would happen), and keeps an **event log**. Other systems send events
with `POST /v1/projects/{project_id}/events`.
