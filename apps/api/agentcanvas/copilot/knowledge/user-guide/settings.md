# Settings and moving around

## The top bar

Every page has the same bar: the project you are working in (click it to switch to another project or, if you
build, make a new one), the pages (**Workflows**, **Runs**, **Playground**, **Inbox** with the number of decisions waiting,
**Knowledge**, **Memory**, **Workspace**, **Events**), **Settings**, and your initial. Your initial opens a menu with
your email and role, the organisation (and, with Matura, a switch to your other organisations), the colour theme,
**User guide** (this guide, inside the app) and **Sign out**. The workflow editor keeps its own bar of canvas tools and has the same menu at its right.

Links your role cannot use are not shown: viewers see no Playground, for example.

## Your workflows and the Runs page

**Workflows** (Home) lists your workflows five to a page, newest change first: search by name, filter by when they
changed (last 7 days, last 30 days, older) and sort; each row's **⋯** menu renames, duplicates or deletes it (deleting
asks first). Below the list is the template gallery.

**Runs** lists the project's conversations, newest first: status (done, running, waiting for a person, failed),
which workflow, where the run came from (the editor, a deployment, a schedule, an event), how long it took and what
it cost. Filter by status, workflow or when it started (last 24 hours, 7 or 30 days); open one to see it in the editor's timeline, or delete it. Builders can tick
conversations and add them to an [evaluation dataset](evaluation.md#datasets) as examples.

## Settings

**Settings** groups everything that is configured rather than built, by who it applies to. Sections your role
cannot change are shown read-only, with a sentence saying who can; sections your role cannot read are not listed.

| Section | What it holds | Who changes it |
|---|---|---|
| Project › **General** | The project's name and id; deleting the project with everything in it (you type its name to confirm; an organisation keeps at least one project) | admins |
| Project › **Models** | The project's model and its key (the first-run screen), and the [default model for each purpose](models.md#which-model-is-used-when-nothing-names-one): judges, simulated users, knowledge embeddings | builders |
| Project › **Copilot** | Whether the Copilot is on, its model (the project's, or a separate one with its own address and key), a model for questions, privacy, the project notes it always reads, its token limit | builders |
| Project › **Secrets** | API keys and passwords workflows use; values are write-only. Deleting one asks first | builders |
| Project › **Sandbox** | Time and memory limits for workspace code, and the hosts it may reach, where the server can enforce that ([Workspace › Sandbox](workspace.md#sandbox)) | builders |
| Project › **Knowledge** | How new knowledge bases split their documents (by heading, paragraph, sentence or fixed windows; passage size; overlap). Existing knowledge bases keep their own ([Knowledge](knowledge.md#chunking-and-embeddings)) | builders |
| Project › **Observability** | Sending traces to Langfuse, Phoenix or any OpenTelemetry collector | admins |
| Organisation › **General** | The organisation's name, how long conversations are kept (the server's default, a number of days, or forever), and the **Environments** workflows can be published to (publishing to a name not on the list is refused). Below it, **Limits and policies**: this organisation's own setting for local MCP servers, the four rate limits, and how long the audit log and long-term memories are kept (see below) | admins |
| Organisation › **Spending** | What the organisation's runs cost over a date range (the last 30 days to start with; **Last 7 / 30 / 90 days** buttons), in total and by project, by workflow and by person, with runs, tokens, answers taken from the cache and answers from backup models. A person is whoever started the conversation; runs from deployments, schedules and events are listed as no person. Cost counts models with a price. The same report is `GET /v1/org/spending?since=&until=` | admins |
| Organisation › **Members**, **Policies**, **Audit log** | See [Organisations, roles and policies](accounts.md) | admins |
| Organisation › **Server** | How this server is set up (sign-in, where workspace code runs, frameworks offered, limits, retention), each with the environment variable an operator sets it with. Read-only | admins can read |
| You › **Appearance** | Your colour theme, and whether the editor opens in Simple or Full mode (both saved with your account) | you |
| You › **Keyboard shortcuts** | Every shortcut of the editor and the canvas (Ctrl/⌘+K opens the command palette) | read only |

The addresses pages had before Settings existed (`/setup`, `/members`, `/audit`, `/policies`) still work and open
the matching section.

### What an organisation may override

The server's environment variable is the default for these, and for most of them the ceiling: the operator who
runs the server decides how much an organisation may ask of it.

| Setting | The organisation may | Never |
|---|---|---|
| Local MCP servers (`AGENTCANVAS_ALLOW_LOCAL_MCP`) | switch them off for itself | switch them on when the server has them off |
| The four rate limits (`AGENTCANVAS_RATE_LIMIT_PER_DEPLOYMENT`, `_PER_WEBHOOK`, `_PER_PROJECT`, `_PER_SHARE_CLIENT`) | set a lower limit | set one above the server's (unless the server's is 0, no limit) |
| Audit log kept (`AGENTCANVAS_AUDIT_RETENTION_DAYS`) | keep it longer, or forever | keep it shorter than the server does |
| Long-term memories kept (`AGENTCANVAS_MEMORY_RETENTION_DAYS`) | choose any number of days, or forever | |

Leave a field empty to use the server's value. The **Applies now** column shows what is in force. If the operator
later lowers a server limit, an organisation's higher value stops applying at once. A per-environment limit the
operator set with `AGENTCANVAS_RATE_LIMIT_BY_ENVIRONMENT` keeps applying to that environment's deployments. Other
replicas of the server pick up a change within about 15 seconds.
