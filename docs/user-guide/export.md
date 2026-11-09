# Export and deploy in your own environment

**Export** turns a workflow into a project you deploy wherever you run software: your computer, a Linux server,
Kubernetes, Google Cloud Run, AWS or Azure. You choose where; the download holds everything for that place, and
step-by-step guides written for this workflow, with its keys, settings and outside services already named.

AgentCanvas never needs access to your environment or your cloud account: every step runs on your machine with your
own tools.

## Export

In the editor open **Versions** → **⬇ Export to run in your own environment…**:

1. Pick the **version** (or the current draft).
2. Pick **where it will run**. The panel lists what you will need before you download: the tools to install, the
   keys and settings to provide, the outside services it calls, whether it needs a database and why, and a
   starting size.
3. **⬇ Download.**

Or call `GET /v1/workflows/{id}/export.zip?target=<target>&version=<x.y.z>`, and
`GET /v1/workflows/{id}/export/plan?target=<target>` for the list of needs.

| Target | What you get | Database |
|---|---|---|
| `local`: your computer (Docker) | Docker Compose with the service and Postgres | included |
| `server`: Linux server or VM | Docker Compose with Postgres and Caddy (automatic HTTPS for your domain) | included, or your own |
| `kubernetes` | A Helm chart: Deployment, Service, optional Ingress, autoscaling | your managed Postgres |
| `cloudrun`: Google Cloud Run | A script: Cloud Build, Secret Manager, Cloud Run | Cloud SQL for PostgreSQL |
| `aws`: ECS Fargate | A task definition and a script: ECR, Secrets Manager, ECS | Amazon RDS for PostgreSQL |
| `azure`: Container Apps | A script: Azure Container Registry build, Container Apps with secrets | Azure Database for PostgreSQL |

A database is needed when the workflow pauses for people (approvals, questions), keeps memories, or continues chats
across messages. Without one, the service keeps conversations in a SQLite file on one machine.

A workflow on another framework (OpenAI Agents SDK, CrewAI, Google ADK, Pydantic AI, Agno, Microsoft Agent
Framework, Strands, LlamaIndex or Mastra) exports as a project of that framework instead (the targets above are for
LangGraph workflows): the generated flow, a command-line runner and offline tests. **Framework** in the export dialog
can also write the same workflow for another installed framework; see
[Export for any framework](frameworks.md#what-each-framework-can-do).

## Take one AI agent to Claude Managed Agents

An AI agent can also be exported as files for Anthropic's Claude Managed Agents: in the block's settings, choose
**Designed for → Claude Managed Agents**, then **Export for Claude Managed Agents (ant apply)**. The download holds
`agents/<agent>/agent.md` (model, tools, MCP servers, skills and the instructions), `environment.yaml`, a vault file
when an MCP server needs a credential, the workspace's skills, a session runner (`run.py`) for the custom tools,
tests, and a README. `make doctor` checks the `ant` CLI and its version, a way to sign in, the files, the settings the
tools read and the hosts they call; `make plan` and `make apply` run `ant apply`. Details and what does not carry
over: [Design an agent in any agent framework](frameworks.md#take-an-agent-to-claude-managed-agents).

## Push to GitHub

Instead of downloading, the export can go straight into a GitHub repository. In **Export**, open **Push to
GitHub…**, enter the repository (`owner/name`), a branch and, if you like, a folder, and press **Push**. The same
files the download would hold are committed in one commit. A branch that does not exist yet starts from the
repository's default branch, so you can open a pull request from it; pushing again to the same branch adds a new
commit on top.

It needs a project secret named `GITHUB_TOKEN` (Settings › Secrets) holding a GitHub token that can write to the
repository: a fine-grained personal access token with **Contents: Read and write** on that repository is enough.
The token never leaves the server. Through the API: `POST /v1/workflows/{id}/github` with
`{"repo": "acme/agents", "branch": "agentcanvas/support", "folder": "agents/support"}` (and optionally `version`,
`target`, `message`, or `token_secret` to use another secret).

## Git round trip: a pull request for every change

**Connect to Git…** (under **Push to GitHub…** in the Export box) ties the workflow to a branch of a GitHub
repository, so a change goes out as a pull request and a merged one comes back in:

1. Enter the repository (`owner/name`), a branch that is **not** the repository's default branch (for example
   `agentcanvas/support`), an optional folder, and when to open pull requests: on every **publish** (the default),
   on every saved **version**, or **off** (on demand with **Open pull request**). It uses the same project secret as
   Push to GitHub, `GITHUB_TOKEN`; the token also needs **Pull requests: read and write**, and **Workflows: read and
   write** for the CI file below.
2. **Open pull request** (or a publish) commits the export of that version, or of the draft, to the branch and opens
   a pull request into the default branch, or updates the open one. The commit holds the export, `workflow.ir.json`
   (the workflow itself) and `.github/workflows/agentcanvas-<workflow>.yml`.
3. **The description is the diff.** It lists what changed in the workflow compared with what the default branch holds:
   steps, resources, connections, state fields and settings, a table of field changes for each changed step, and the
   changes that would break callers. It is the same comparison the Versions tab draws on the canvas.
4. **The status check.** The CI file runs the export's own tests (`uv sync`, `uv run pytest -q`: offline, no API keys)
   on the pull request, as the check `export tests`. Require it in the repository's branch protection to block a
   merge that breaks them.
5. **Import from branch** reads `workflow.ir.json` from the branch into the draft, for example after the pull
   request was changed on GitHub or merged.

Neither direction overwrites what the other side changed since the last sync. A push is refused (409) when the branch
holds changes AgentCanvas does not have; an import is refused when the draft **and** the branch both changed (the
message says what the import would change). Save a version to keep the draft, then choose **overwrite**. An import
changes only the draft: it is an ordinary edit, so **Undo** and the version history keep what was there.

Files the export no longer has are removed from the folder; files elsewhere in the repository are never touched.
If publishing cannot reach GitHub, the publish still succeeds and the Git panel shows the error. Through the API:
`PUT /v1/workflows/{id}/git` (connect), `POST …/git/push` with `{"version": "1.2.0"}` (or none for the draft),
`POST …/git/pull`, `GET` and `DELETE …/git`.

## Export a whole workflow to another framework (preview)

In the Export box, **Framework** also offers **LangGraph (through Agent Spec)**, **WayFlow** and **OpenAI Agents
SDK**, marked *preview*. These write the workflow as an [Open Agent Spec](https://oracle.github.io/agent-spec) flow
and let that framework's own Agent Spec loader run it, so you get a project for that framework instead of the
AgentCanvas service.

Before you download, the box lists what does not carry over:

- **Before it runs there**: things the other framework cannot do with this workflow. Typical ones are routers
  whose rules are more than `state.x == "value"`, steps that run side by side, and steps that only AgentCanvas
  runs (memory, loops over a list, connectors). A step of the last kind is left for you as a function in
  `steps.py`; "Set values" steps are written for you. OpenAI Agents SDK flows take only AI steps, agents and
  simple paths.
- **What works differently**: for example an agent's tools are not carried over, approvals become plain
  questions, and runs, history and traces stay in the other framework.

The download holds `flow.json`, a runner (`main.py`, `flowkit.py`), `steps.py`, pinned packages, a README,
`GAPS.md` with the same list, and tests that run the flow against a fake model server (`uv run pytest`, no key
needed).

Not offered: AutoGen and Microsoft Agent Framework (their Agent Spec loaders take single agents, not flows) and
CrewAI (no Agent Spec loader). To take a single AI agent to any of nine frameworks, use **Export as … project** in
the agent's settings instead (see [Choosing a framework](frameworks.md#design-an-agent-in-any-agent-framework)).

## What is in the download

| Path | What it is |
|---|---|
| `docs/1-PREREQUISITES.md` … `4-VERIFY-AND-OPERATE.md` | The four guides: what you need, setup, deploy, then check, scale, upgrade, roll back, back up and rotate keys |
| `Makefile` | `make doctor`, `setup`, `test`, `gate`, `run`, `deploy`, `smoke`, `logs` |
| `.env.example` | Every setting, each with where to get it |
| `src/<name>/app.py` | The service (below) |
| `deploy/<target>/` | Deploy files for the target you chose, and only those |
| `Dockerfile` | Port 8000, a non-root user, a health check, pinned base images |
| `tests/` | The saved test cases (with scripted model replies) and the service's own tests, all offline |
| `workflow.ir.json` | The exported workflow; import it into AgentCanvas to see which version is deployed |
| `src/<name>/graph.py`, `src/agentcanvas/` | The generated LangGraph code and the node library it uses |

**File and image fields** travel in a LangGraph export. The service takes a file as `{"kind": "file", "name": "…",
"media_type": "…", "data": "<base64>"}` (up to 2 MB, kept in the checkpoint) or `"url": "https://…"` (fetched when a model
needs it), and Ask AI and AI Agent steps show it to their model as on the canvas. `pypdf` and `python-docx` are added to
`pyproject.toml` for PDFs and Word documents; the README has an example. Exports to other frameworks cannot do this:
their `GAPS.md` lists the file fields as carried but never shown to a model.
| `src/flows/<id>/…`, `src/scripts/…` and other workspace folders | Your workspace code that this workflow uses (below) |
| `tests/test_workspace_code.py` | Checks that every code path the workflow names (`flows.<id>.scripts.x:run` …) imports inside the export |
| `contracts.json`, `parts/<agent>/` | A mixed flow's parts: each agent's framework, where it runs and its typed contract; agents whose framework clashes with LangGraph get their own folder and environment ([Mixed flows](frameworks.md#mixed-flows-parts-and-contracts)) |

### Your workspace code in the export

The export carries exactly the [Workspace](workspace.md) code the workflow uses, not the whole workspace:

- the files its steps, tools and custom blocks name, from the workflow's own folder or the shared folders;
- every workspace module those files import, found by following their `import` lines;
- data files in the workflow's own folder (prompts, JSON, CSV …);
- the Python packages listed in `requirements.txt` at the workspace root or in the workflow's folder, added to the
  export's `pyproject.toml` (one line per package, like `rapidfuzz>=3`; options and URLs are skipped).

Other workflows' code is left out. If the workflow names code that is not in the workspace (a deleted file, say),
the export stops and names it, so a broken download never leaves AgentCanvas. `make test` runs
`tests/test_workspace_code.py` with the other tests.

## Deploy, step by step

```bash
make doctor      # checks tools, settings, database, outside services and the port; says which guide section fixes each problem
make setup       # creates .env with a fresh API key; fill in the empty values
make test        # the graph builds and the saved test cases pass
make deploy      # runs deploy/<target>/deploy.sh: read it first, every line is a plain command
make smoke URL=https://…   # calls the deployed service once and prints the answer
```

`make doctor ARGS=--live` also makes one tiny call to each AI model with your keys. To try the service before you
have model keys, set `DEMO_MODE=true` in `.env`: the models answer with canned replies.

The service checks its settings when it starts and stops with a plain message naming what is missing, instead of
failing on the first request.

## The service's API

The service answers the same API as an AgentCanvas [deployment](publish.md), so a client moves to it by changing
only the base URL and the key:

| Call | Does |
|---|---|
| `POST /v1/deployments/<name>/runs/wait` | Runs the workflow; returns the answer, or the questions it is waiting on |
| `POST /v1/deployments/<name>/runs/stream` | The same run as Server-Sent Events |
| `POST /v1/deployments/<name>/threads/<id>/resume` | Answers an approval or a question |
| `POST /v1/deployments/<name>/runs/<id>/feedback` | A thumbs up or down (logged, and written to `FEEDBACK_FILE` if set) |
| `GET /healthz`, `GET /docs` | Health and the interactive API page |

Every call except `/healthz` and `/docs` needs `Authorization: Bearer <key>`, one of the comma-separated
`API_KEYS`. `<name>` can be anything. The MCP and A2A addresses of an AgentCanvas deployment
([Connect to other agents and tools](agents-and-connectors.md)) are not part of the exported service.

## Keys and secrets

Secret values never leave AgentCanvas: the export names every secret the workflow uses, and you copy the values from
**Settings › Secrets** into `.env`. The deploy scripts put them in the platform's secret store (Secret Manager,
Secrets Manager, Container Apps secrets, or a Kubernetes secret).

## Release gate

`make gate` runs the saved test cases and the service's API tests, and fails unless every one passes; run it before
`make deploy` (or in your CI). Evaluations that need an AI judge stay in AgentCanvas's Evaluation Studio.
