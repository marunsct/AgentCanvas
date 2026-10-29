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
