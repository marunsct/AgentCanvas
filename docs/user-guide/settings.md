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
asks first). Deleting a workflow, or its project, stops its running conversations and deletes them with their saved
state, so a new conversation that reuses one of their ids starts empty. Below the list is the template gallery.

**Runs** lists the project's conversations, newest first: status (done, running, waiting for a person, failed),
which workflow, where the run came from (the editor, a deployment, a schedule, an event), how long it took and what
it cost. Filter by status, workflow or when it started (last 24 hours, 7 or 30 days); open one to see it in the editor's timeline, or delete it. Builders can tick
conversations and add them to an [evaluation dataset](evaluation.md#datasets) as examples. The Inbox, Runs and a share page are laid out
for a phone (the rest of the app is a desktop tool; the editor is usable on a phone but not designed for it).

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
| Organisation › **General** | The organisation's name, how long conversations are kept (the server's default, a number of days, or forever), and the **Environments** workflows can be published to (publishing to a name not on the list is refused). Below it, **Limits and policies**: this organisation's own setting for local MCP servers, approving your own runs, the four rate limits, how many runs may be in flight at once, the longest an API token may live, and how long the audit log and long-term memories are kept (see below). Last, **Stop all runs**: the kill switch for an emergency; every run in flight in the organisation stops within about 10 seconds on every server (runs waiting to be handed over are cancelled), with a reason kept in the audit log. It does not refuse new runs: stop a deployment on its page for that. `POST /v1/org/runs/stop` takes the same `reason`, and optionally a `deployment_id` or `workflow_id` to stop only those runs | admins |
| Organisation › **Spending** | What the organisation's runs cost over a date range (the last 30 days to start with; **Last 7 / 30 / 90 days** buttons), in total and by project, by workflow, by person and by deployment, with runs, tokens, answers taken from the cache and answers from backup models. A person is whoever started the conversation; runs from deployments, schedules and events are listed as no person, and the deployment table shows which deployment (the workflow and its environment) they came through, with the rest listed as not through a deployment. Cost counts models with a price. What the Copilot and AI judges spent is listed apart, under **Outside runs** (it is not in the runs' total). Conversations deleted since (by hand or by the retention period) still count, by the hour their runs started. The same report is `GET /v1/org/spending?since=&until=`. Above it, **Spending caps**: what the organisation and each project may spend a day and a month, how much of it is used today and this month, and the latest cap alerts (see [Spending caps](#spending-caps)) | admins |
| Organisation › **Security** | The **allowed networks** the organisation may be used from: sign-ins, API tokens, deployment keys, share links and SCIM ([Allowed networks](accounts.md#allowed-networks)); whether members must sign in with a second factor ([Multi-factor sign-in](accounts.md#multi-factor-sign-in)) | admins |
| Organisation › **Members**, **Policies**, **Audit log** | See [Organisations, roles and policies](accounts.md) | admins |
| Organisation › **Server** | How this server is set up (sign-in, where workspace code runs, frameworks offered, limits, retention), each with the environment variable an operator sets it with. Read-only | admins can read |
| You › **Appearance** | Your colour theme, and whether the editor opens in Simple or Full mode (both saved with your account) | you |
| You › **Keyboard shortcuts** | Every shortcut of the editor and the canvas (Ctrl/⌘+K opens the command palette) | read only |
| You › **About** | The release the server runs and the commit its image was built from, and the same for the web app; quote them when you report a problem. If the two releases differ, reload the page; if they still differ, an upgrade is not finished. The same is `GET /v1/version` | everyone |

The addresses pages had before Settings existed (`/setup`, `/members`, `/audit`, `/policies`) still work and open
the matching section.

### What an organisation may override

The server's environment variable is the default for these, and for most of them the ceiling: the operator who
runs the server decides how much an organisation may ask of it.

| Setting | The organisation may | Never |
|---|---|---|
| Local MCP servers (`AGENTCANVAS_ALLOW_LOCAL_MCP`) | switch them off for itself | switch them on when the server has them off |
| Approving your own runs (`AGENTCANVAS_APPROVE_OWN_RUNS`, allowed by default) | switch it off: someone else must approve a run you started ([Run and debug](run-and-debug.md)) | switch it on when the server has it off |
| The four rate limits (`AGENTCANVAS_RATE_LIMIT_PER_DEPLOYMENT`, `_PER_WEBHOOK`, `_PER_PROJECT`, `_PER_SHARE_CLIENT`) | set a lower limit | set one above the server's (unless the server's is 0, no limit) |
| Runs at once (`AGENTCANVAS_MAX_CONCURRENT_RUNS`) | set a lower limit | set one above the server's (unless the server's is 0, no limit) |
| Longest API token lifetime (`AGENTCANVAS_MAX_TOKEN_DAYS`, in days) | set a shorter one: tokens then must expire within it | set one above the server's (unless the server's is 0, no limit) |
| Audit log kept (`AGENTCANVAS_AUDIT_RETENTION_DAYS`) | keep it longer, or forever | keep it shorter than the server does |
| Long-term memories kept (`AGENTCANVAS_MEMORY_RETENTION_DAYS`) | choose any number of days, or forever | |
| Spending caps a day and a month (`AGENTCANVAS_MAX_COST_USD_DAY`, `_MONTH`, in USD; no cap by default) | set a lower cap, or one when the server has none | set one above the server's |
| Cap alert share (`AGENTCANVAS_COST_WARN_PERCENT`, 80) | choose any percent from 1 to 100 | |

**Runs at once and the run queue.** With a limit, a run past it waits in the organisation's queue instead of starting,
on every server alike: the run panel says "Waiting for a free run slot" and its place in line. The next free slot goes
to the highest priority, then the oldest run. A run's priority is `high`, `normal` (the default) or `low` (experiments
and the test cases you run, which can wait; the test cases a publish runs and the Copilot's test runs are `normal`, as
someone waits for them); `priority` on `POST /v1/workflows/{id}/runs/stream` sets it, and a run handed over to another
server keeps its own priority and its place in line. The time limits of experiment examples, test cases and Copilot
test runs count from when the run takes its slot, not while it waits. A server that is shutting down starts no waiting
run: it hands each one over at once, with the message or answer it was sent, and the run waits for a slot on another
server. A waiting run counts as in flight for **Stop all runs** and can be stopped like any other. A run that waits
longer than `AGENTCANVAS_MAX_QUEUE_WAIT_S` (an hour by default; 0 waits as long as it takes) ends with an error that
says so (code `queue_timeout`): send it again later, or ask an admin to raise the limit. Runs of a server that stopped
answering stop counting after about a minute. A run that calls another deployment of the organisation (a **Remote
agent** step over A2A, or an MCP tool that points at a deployment) keeps its slot while it waits for the answer, so
the run it calls works within that slot instead of waiting behind it: the call carries the caller's run id, signed by
the server, in an `X-AgentCanvas-Run` header (sent on every A2A and MCP call a run makes; only this server can check
it).

Leave a field empty to use the server's value. The **Applies now** column shows what is in force. If the operator
later lowers a server limit, an organisation's higher value stops applying at once. A per-environment limit the
operator set with `AGENTCANVAS_RATE_LIMIT_BY_ENVIRONMENT` keeps applying to that environment's deployments. Other
replicas of the server pick up a change within about 15 seconds.

### Spending caps

A workflow's own [budget](canvas.md#budgets) is set, and can be removed, by whoever builds the workflow. Spending caps
are the administrator's: what the whole organisation, or one project, may spend on models a **UTC day** and a
**calendar month** (UTC), in USD. Set them in **Settings › Organisation › Spending**: the organisation's caps (also
`limits.max_cost_usd_day` and `limits.max_cost_usd_month` of `PUT /v1/org/settings`) and each project's
(`PUT /v1/projects/{id}/spending-caps {"max_cost_usd_day": 50, "max_cost_usd_month": 800}`; `null` removes one). A
project's cap is never above the organisation's (the save is refused), and the organisation's caps bind every project
too. An operator can cap every organisation of the server with `AGENTCANVAS_MAX_COST_USD_DAY` and
`AGENTCANVAS_MAX_COST_USD_MONTH`; an organisation can then only set a lower cap. Only admins set or see caps, and
every change is in the audit log.

What counts: every run of the organisation or project, whatever started it (the editor's draft and debug runs, test
cases, experiments, deployments, share links, schedules, webhooks and event handlers), at the cost of models with a
price (the catalog's, or `AGENTCANVAS_MODEL_PRICES`; a model without one adds no cost). Conversations deleted since,
by hand or by the retention period, still count. So do the model calls made outside runs: the **Copilot**'s answers
(and its code help) and the **AI judges** of experiments, online evaluators and evaluator tests, in the project of
their workflow. At a cap a new Copilot question is refused like a run; judges are counted but not stopped, as they
only score runs, which the cap stops.

At a cap:

- **New runs are refused** with `429` (problem type `/errors/spending-cap`, with `scope`, `period` and a `Retry-After`
  until the day or month ends): in the editor, on the deployment API, MCP and A2A, on share links, and for schedules,
  webhooks, event handlers and experiments, which record the refusal as their outcome. Answering an Inbox item that
  would continue a run is refused the same way; the item stays open.
- **Runs in flight stop** after the model call that reached the cap, with the error code `spending_cap`. Runs going
  at once, on any server, count each other's spending within a few seconds, so together they can pass a cap by about
  one model call each.
- Runs start again when the next day or month begins (UTC), or as soon as an admin raises or removes the cap.

**Alerts.** When spending reaches the alert share of a cap (80% unless the organisation sets its own **Alert at**
percent, `limits.cost_warn_percent`) and when it reaches the cap, a cost alert is raised, once per organisation or
project, period and share, however many runs cross it together. It shows in the run that crossed it, on the
Spending page, and as an audit-log entry (`spend_cap_warning`, `spend_cap_reached`) that is also forwarded to your
SIEM or webhook like the other security-relevant entries ([Security](security.md)); Prometheus counts them in
`agentcanvas_spend_cap_alerts_total`.
