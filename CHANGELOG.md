# Changelog

All notable changes to AgentCanvas. Versions follow [semver](https://semver.org); dates are UTC.

## Unreleased — milestone review after S20–S22

A whole-codebase review at the end of the framework milestone found 19 bugs; all are fixed.

### Security
- **Exports no longer run text from the workflow as code.** A workflow name is one line in exported Makefiles, compose files and scripts; secret names must be variable names (check E051, and the export refuses others); a designed agent's tool import path is checked before it is written into `agent_tools.py`; a resource may not take a name Python or the exported code reserves (check E007); an exported package is never named after a module it imports (`LangGraph`, `email`).
- **Hosted edition: no private networks.** "Allow private network" on HTTP tools and remote agents is refused (check E043) and ignored at run time, and MCP servers over HTTP must resolve to public addresses (never the cloud metadata address).
- **Release gates pin the code of code evaluators.** Saving a gate records a hash of the evaluator's workspace code (and the modules it imports); if a builder changes that code, the gate fails until an admin saves it again.
- **The Copilot's "local models only"** now also checks the model's address, and organisation policies see it.
- **Client addresses behind nginx can no longer be forged** with an `X-Forwarded-For` header: nginx believes it only from `TRUSTED_PROXIES` (Helm `web.trustedProxies`) and passes the API one address, so rate limits and the audit log use the real client.
- Project secrets can only be stored under variable-safe names.

### Fixed
- Secrets changed on one API replica reach the others: each run reloads its project's secrets when they changed, and every replica re-syncs every 10 seconds (so new values are also masked there).
- Event handlers can resume or continue the thread of the run that triggered them, and streams and `/runs/wait` no longer wait for handlers to finish.
- Two publishes at the same moment can no longer create two deployments for one environment (a migration merges any existing duplicates).
- Retrying a failed remote (A2A) agent step contacts the remote agent again instead of replaying the failure.
- Pending tool approvals of designed agents are bounded in memory.
- Editor: opening another workflow, reloading after a conflict or restoring a version clears undo/redo; a late save of the previous workflow, or its still-streaming run, no longer lands in the new one.
- Workspace: reopening a file after saving it shows the saved text, so the next save cannot overwrite it with the old one.
- Exported services forget idle conversation locks.
- Accessibility: labels on the state designer's selects and the rejection reason box.

## v0.14.0 — Whole flows, preview (not tagged yet)

### Added
- **Export a whole workflow to another framework (preview).** The Export box has a **Framework** choice: besides the AgentCanvas service, **LangGraph (through Agent Spec)**, **WayFlow** and **OpenAI Agents SDK**. The workflow is written as an Open Agent Spec flow and that framework's own Agent Spec loader runs it. The download holds `flow.json`, a runner, `steps.py` for the steps only AgentCanvas runs ("Set values" steps are written for you), pinned packages, a README, `GAPS.md` and tests against a fake model server. `GET /v1/workflows/{id}/bridges`, `GET /v1/workflows/{id}/bridges/{target}/export.zip`.
- **Gaps report.** Before downloading, the Export box lists what stops the workflow from running on that framework and what works differently there; the same list ships in `GAPS.md`.
- **CI runs the exports for real**: every template exported to each framework; the exports' own tests pass, and 4 templates run end to end on LangGraph and WayFlow, 2 on the OpenAI Agents SDK.

### Changed
- Agent Spec export: steps AgentCanvas runs read the state their settings use, a typed agent's answer is its output, outputs that a path may skip have defaults, the conversation is no longer a flow input, and prompts with template logic are listed in the report.
- The fake model server in exported tests also calls a tool the request requires.

## v0.13.0 — Design any agent (not tagged yet)

### Added
- **Design an AI agent in any of nine agent frameworks.** An AI agent block's **Designed for** now lists agent frameworks: OpenAI Agents SDK, CrewAI, Google ADK, Pydantic AI, Agno, Strands Agents, LlamaIndex, Microsoft Agent Framework and Claude Agent SDK. The block is generated as that framework's own code (instructions, model, workspace/HTTP/MCP tools, tool approvals, a typed answer as a Pydantic class) and runs as that code. `GET /v1/agent-frameworks`.
- **Model bridge.** A designed agent's model calls go to a local, per-run endpoint that speaks the OpenAI chat-completions and Anthropic messages APIs (streaming included) and answers with the model wired into the block, so runs stay traced, costed, mocked and replayable, and API keys never reach the agent's code.
- **View the code and take it away.** **View … code** in the block's settings (`GET /v1/workflows/{id}/agents/{node}/code`, `…/code.py`) and **Export as … project** (`…/export.zip`): the agent, its tools, `main.py`, pinned packages, a README and tests that run against a bundled fake model without an API key. Whole-workflow exports put each designed agent in `agents/` and add its packages.
- **Checks** P005 (the framework cannot take something wired into the agent: add-ons, other tool kinds, its answer shape) and P006 (the framework is not installed on this server). Workflow frameworks without the new `agent_designs` ability (Echo) cannot run designed agents (P001), and their badge says so.
- The Copilot knows the agent frameworks and suggests designing an agent in one when asked.
- CI runs each framework's designed agent (plain, typed answer, approval) and its exported project's tests against the real package.

### Notes
- A tool approval pauses the run; on approval the designed agent's step starts again from its beginning, and the decision applies only if the agent asks for exactly the same call again.
- Structured output uses each framework's own mechanism, so typed answers may differ slightly between frameworks.

## v0.12.0 — Framework foundation (not tagged yet)

### Added
- **Pick a workflow's framework.** Workflow settings (click empty canvas) → **Framework** lists the frameworks this server offers; switching is an undoable edit (`UpdateMeta.framework`). Runs, conversation state, history, the code view and exports use that framework's adapter.
- **Badges for where a workflow can run.** One badge per installed framework next to the workflow's name: ✓ when every block can run there, or how many blocks cannot (point at it to see which, click it to select the first). `GET /v1/frameworks`, `GET /v1/workflows/{id}/portability`.
- **Portability checks** P001 (the framework cannot run this block, or is not installed), P002 (it only imitates the block), P003 (which other installed frameworks the workflow could move to) and P004 (a block designed for another framework), with plain-language help.
- The Problems panel says "No problems" when only info notes remain; the notes are still listed.
- **IR 1.2: a block's own framework.** AI steps, AI agents and Planning Agents can be **Designed for** another framework (`engine`); until mixed-framework flows arrive they run on the workflow's framework. Older workflows load unchanged.
- **Conformance kit** (`agentcanvas.conformance`): sequence, three-way paths, parallel branches joined, a bounded loop, AI replies that fill a field or pick a path, approve, reject, approve with an edit, and a question re-asked after an invalid answer, plus checks on the events every run must send. Every adapter runs it in CI.
- **Echo demo engine** (`AGENTCANVAS_ADAPTERS=langchain,echo`): a tiny second framework that passes the kit, to prove the seams. Off by default; not for production.

### Changed
- **Adapter interface v2** (ADR 0018): a required core (generate, package, run with canonical events) and optional abilities each adapter declares (conversation state, history and replay, long-term memory, connector browsing, breakpoints). What a workflow's framework lacks is refused with a plain message instead of failing.
- **Model calls outside workflows** (the Copilot, LLM judges, the setup page's connection test) go through a framework-neutral model service, not a workflow adapter.

### Fixed (review of v0.11.0)
- A workflow's own Script steps (`flows.<workflow>.scripts.…`) ran in the server process even when the project has a sandbox; they now go to the sandbox like shared scripts.
- The Copilot's copy of the user guide had missed the v0.11.0 Workspace, Export and Copilot pages.

## v0.11.0 — Workspace and editor fixes (not tagged yet)

### Added
- **Each workflow has its own code.** The Workspace opened from a workflow (`/workflows/{id}/workspace`) shows that workflow's files (`flows/<id>/scripts/`, `tools/`, `nodes/`) and, separately, the shared files every workflow can use (`scripts/`, `tools/`, … at the workspace root, unchanged). **+ Create the code file** on a Script step now writes into the workflow's own folder (`flows.<id>.scripts.<file>:run`); the palette shows only this workflow's tools and scripts and the shared ones. `GET /v1/projects/{id}/workflow-files/{wf_id}`; the catalog takes `workflow_id`.
- **Workspace code imports other workspace code** (`from scripts.textutil import shout`, `from .helpers import x`) the same way in a run, in the sandbox and in an export.
- **Copilot help in the code editor.** Under a Python file: describe what the code should do and **Write with Copilot**, **Explain** the file or the selected lines, or **Fix** the problems ruff found. Changes come as a diff to **Accept** (an unsaved, undoable edit) or **Reject**; nothing is saved without you. Uses the project's Copilot model and privacy setting. `POST /v1/workflows/{id}/copilot/code`.
- **Starter files.** **New file** and the "+ Script step", "+ Tool for the AI" and "+ Custom block" chips start from working code for that kind of file.
- **A way back.** The Workspace says which workflow it belongs to and has **← Back to *workflow name***; going back (or the browser's Back button) returns to the same block selected and the same canvas position.

### Changed
- **Exports bundle exactly the code the workflow uses**: its own folder's code, the shared modules it imports (found by following imports), data files in its folder, and the packages in `requirements.txt` (workspace root or the workflow's folder). Other workflows' code is left out. A generated `tests/test_workspace_code.py` checks that every code path the workflow names imports inside the export, and an export whose code is missing from the workspace stops with a message naming it.

### Fixed
- **List fields in block settings** (such as "Tools that need approval" in the AI agent block) showed empty buttons and a new item failed with `Instance type "null" is invalid`. The buttons now read **+ Add**, **Remove**, **↑ Up** and **↓ Down**, and a new item starts empty. The same applies to decision cards in the Inbox.
- **Every button has a label**: Undo and Redo got names, and a browser check fails on any visible button without one, on every editor tab, dock panel and page.

## v0.10.0 — Any framework as a node, and a UI refresh (not tagged yet)

### Added
- **Agents from nine other frameworks as steps.** The **Agent from another framework** block runs an agent from your Workspace built with the OpenAI Agents SDK, CrewAI, Google ADK, Pydantic AI, Agno, Strands Agents, LlamaIndex Workflows, Microsoft Agent Framework or the Claude Agent SDK (or a plain Python function) as one step: prompt from workflow fields, answer into a field or the chat, optional approval before it runs, input and answer in the trace. A factory function gives a fresh agent per run. Exports vendor the step and add the framework's pinned package.
- **Remote agents.** **Remote agent (A2A)** calls any Agent2Agent agent by its Agent Card address; when it asks for more information the run pauses for a person and the answer goes back to the same task (no message is sent twice on resume). Private addresses are refused unless allowed. **Remote agent (MCP)** calls one tool of a connected MCP server as a step.
- **Safety for remote agents:** the A2A step connects to the address it checked (no DNS rebinding to private networks), and after a server restart a step paused on a remote question stops with a message instead of sending its first message again.
- **Framework badges** on these blocks name the framework (or A2A, MCP).
- **Open Agent Spec import and export.** **Versions → Download as an Agent Spec file** writes the workflow as an Agent Spec flow (`LlmNode`, `AgentNode`, `A2AAgent`, `BranchingNode`, `InputMessageNode`, tools) and lists what Agent Spec cannot express; **Import an Agent Spec file** on the home page builds a workflow from any Agent Spec flow or agent and lists what it approximated. Files AgentCanvas wrote import back exactly, layout included; header values typed in directly are never written to the file. `GET /v1/workflows/{id}/agent-spec`, `POST /v1/projects/{id}/workflows/agent-spec`.
- **CI `frameworks` job:** a real agent from each of the nine frameworks, at the pinned version, runs as a canvas step against a fake model server (OpenAI and Anthropic APIs), and every template's Agent Spec export loads in pyagentspec.

### Fixed (review of the v0.9.0 deploy kit)
- Azure Container Apps deployed the literal text "unset" for empty settings; the deploy script now stops and names the missing ones.
- Cloud Run's service account could not read its secrets; the script grants it access (and `GCP_SERVICE_ACCOUNT` picks another account).
- Placeholder API keys ("unset", "none", "changeme") no longer count as configured in `make doctor` or the exported service.

### Changed (UI refresh, #20)
- **A new look.** A redesigned interface across every page: a frosted top bar with grouped canvas tools, softer cards and panels, clearer tabs, rounder canvas blocks with status glows, and themed minimap, controls and tooltips.
- **Light, Medium and Dark themes**, plus System (follows your computer), from the switch in the top bar; remembered in this browser.
- **Copilot chat** has a proper chat layout: avatars, chat bubbles, a typing indicator, a welcome screen with suggestions, and a composer with the Build/Ask switch and Send inside it.

## v0.9.0 — Export deploy kit (not tagged yet)

### Added
- **Export to run in your own environment.** Pick a version and where it will run (your computer with Docker, a Linux server, Kubernetes, Google Cloud Run, AWS ECS Fargate or Azure Container Apps). The export dialog lists what that needs (tools, keys, outside services, database, size) before you download. `GET /v1/workflows/{id}/export.zip?target=…`, `GET /v1/workflows/{id}/export/plan?target=…`. See `docs/user-guide/export.md`.
- **The export is a service** with the same API as an AgentCanvas deployment (`runs/wait`, `runs/stream`, `threads/{id}/resume`, `runs/{id}/feedback`, `healthz`, `docs`) behind API keys; conversations in Postgres (`DATABASE_URL`) or SQLite. It refuses to start, with a plain message, when settings are missing.
- **Deploy files for the chosen target only:** Docker Compose (with Caddy for automatic HTTPS on a server), a Helm chart, or a Cloud Run, ECS or Container Apps script that keeps the keys in the platform's secret store. The Dockerfile runs as a non-root user with a health check and pinned base images.
- **Four guides written for the workflow:** prerequisites, setup, deploy, verify and operate (smoke test, logs, scaling, upgrade, rollback, backups, key rotation), with its keys, hosts and settings named.
- **`make doctor`** checks tools, settings, database, outside services and the port (and with `--live`, each model key), pointing each problem at the guide section that fixes it; **`make smoke`** calls the deployed service once; **`make gate`** runs the saved test cases and the service's API tests. `DEMO_MODE=true` answers with canned replies for a first try without keys.
- **CI** exports every template to every target, lints the deploy files, runs each export's own tests with its own pinned packages, and deploys exports for real with Docker Compose and on Kubernetes (kind).

### Notes
- The exported service has the deployment API (runs, resume, feedback), not the MCP and A2A addresses added in v0.7.0.
- Built ahead of S16–S17: release numbers follow merge order.

### Fixed (review against v0.7.0)
- A resume in the exported service ran without the paused run's context, so user-scoped memory failed and `context.*` prompts were empty after an approval.
- Feedback in the exported service only found runs answered by the same process (404 on another replica or after a restart).
- `make smoke` and the export's own tests failed for workflows with a required input context field.

## v0.8.0 — AI Copilot II and debug assistant (not tagged yet)

### Added
- **The Copilot tests.** It turns a brief's success criteria into test cases, tries the workflow as it would be with its proposal (dry runs: risky tools are stubbed; scripted model replies when no key is set), fixes what fails, and saves the tests when you accept.
- **The Copilot debugs.** It reads the Problems list with a plain explanation of every code, recent runs and what each step did, explains causes in plain words, and checks a fix by rerunning the failed run's input with its recorded model replies and tool outputs. **✦ Ask the Copilot to fix these** (Problems) and **✦ Ask the Copilot why** (run errors) hand the problem over.
- **One error catalog.** Plain explanations of validation codes and common run errors live in `agentcanvas/validation/help.json`, used by the editor and the Copilot (`make schema` and `pnpm gen` copy it).
- **Guide search.** The Copilot searches the user guide (shipped inside the API package).
- **Project Copilot notes**, a **model for questions** (split mode) and **Private** mode (local models only) in Copilot settings.
- **Debug eval suite.** `python -m agentcanvas.copilot.evals --suite debug` plants about 30 bugs in the starter templates and checks the Copilot's fixes.

### Fixed (review against v0.7.0)
- A follow-up Copilot turn started from the saved draft: a tests-only or notes-only proposal replaced an open proposal and dropped its changes, and test runs did not see the open fix. A turn now starts from the open proposal.
- A new or changed local (stdio) app connector could start its program during a Copilot test run before the person accepted it.
- Reproductions replaced recorded tool output with the dry-run stub for risky tools.
- Past runs of a deployment were explained against the draft instead of their version.
- A question model that is not allowed (policies, local-only mode, no tool calling) was accepted in settings and then failed on every question.

## v0.7.0 — Open doors (not tagged yet)

### Added
- **Every deployment is an MCP server.** `POST /v1/deployments/{id}/mcp` (Streamable HTTP, the deployment's key) offers one tool named after the workflow, with its input fields, and `answer` for runs that pause for a person. Claude Desktop (through `mcp-remote`), Cursor, ChatGPT and agent frameworks can call published workflows. The publish panel shows the address and a ready-to-paste Claude Desktop setting.
- **Every deployment is an A2A agent.** Agent card at `/v1/deployments/{id}/a2a/.well-known/agent-card.json`; `POST /v1/deployments/{id}/a2a` takes `message/send` (data parts are input fields, text goes to the workflow's text field) and `tasks/get`; a pause is `input-required` and is answered on the same task.
- **App connector gallery.** About thirty ready-made MCP servers (GitHub, Slack, Notion, Postgres, a browser, web search, …) in the block list under **App connectors**; each becomes a filled-in App connector block. `GET /v1/mcp-connectors`. Local (stdio) connectors take environment variables, which can be secrets.
- **Trace export.** An admin sends every run of a project to Langfuse, Arize Phoenix, Datadog (through the Agent's OTLP receiver) or any OTLP/HTTP endpoint, as OpenTelemetry traces with steps, model calls (tokens, replies) and tool calls. Secrets are masked and `trace_redact` fields redacted; content can be left out. `GET/PUT/DELETE /v1/projects/{id}/trace-export`, `POST …/trace-export/test`.
- The Copilot knows which framework a workflow targets.

### Fixed
- Chat models are cached per configuration: changing a model, its key or its address (including the Copilot's model) took effect only after the API restarted, and same-named models in different workflows of a project could share one client.
- Two Copilot turns that overlapped on different API processes left two open proposals; the older one is now superseded.
- A signed webhook for a deleted workflow was refused as "already received" (409); it now gets 404.

## v0.6.0 — AI Copilot (not tagged yet)

### Added
- **AI Copilot in the editor.** Open **✦ Copilot**, describe the workflow you want, and the Copilot writes a brief (goal, inputs, outputs, steps, tools, success criteria, open questions), says its plan, and builds it with edit commands, fixing its own validation errors. Its changes are previewed on the canvas (new in green, changed in amber) and applied only when you **Accept**, as one undoable edit. **Ask** mode answers questions without changing anything and is open to viewers. See `docs/user-guide/copilot.md`.
- **The Copilot knows the product.** A knowledge pack generated from the running product (guide, every block's ports and settings including workspace blocks, starter templates, edit commands) is sent with each message and cached by providers that support prompt caching; tools look up block details, templates and the workflow with its problems.
- **Copilot model choice.** The workflow's own model (project default, else the workflow's first AI model) or a separate model, with a token limit per conversation; organisation model policies apply to it. `GET/PUT /v1/projects/{id}/copilot-settings`; conversations at `/v1/workflows/{id}/copilot` (`POST …/messages` streams Server-Sent Events).
- **Copilot eval harness.** `python -m agentcanvas.copilot.evals --model …` builds ten workflows from plain requests and checks each validates and uses the expected blocks.
- **Any model provider, including local models.** Chat models can use Anthropic, OpenAI, Azure OpenAI, Google Gemini, Mistral, Groq, DeepSeek, xAI, any OpenAI-compatible API (`openai_compatible:` with a Base URL), and models on your own computer: Ollama (`ollama:`), LM Studio (`lmstudio:`), vLLM (`vllm:`) and llama.cpp (`llamacpp:`). Every integration is installed with the API. `GET /v1/model-providers` lists them, with each one's key variable, default address and whether it is installed.
- **API keys per provider, from the canvas.** A project secret named after the provider (e.g. `MISTRAL_API_KEY`) is used by that provider's models when their config names no key; the Chat model's **API key** field picks any project secret by name. The model field shows where the key comes from.
- **First-run setup.** Home asks for the model to use: pick a provider and model, enter the key or a local server's address, **Test connection**, and **Save**. The key becomes a project secret and the model the project's default, which new workflows from templates use. `GET/PUT /v1/projects/{id}/setup`, `POST …/setup/test`, `POST …/setup/skip`.
- **Run everything in Docker.** `make docker-up` (`infra/compose.local.yml`) builds and starts Postgres, the API and the web app on http://localhost:7777 without sign-in; local model servers on the host are reached through `host.docker.internal` (`AGENTCANVAS_LOCAL_MODELS_HOST`).
- **User guide** in `docs/user-guide/`: running AgentCanvas on your computer, and product documentation for every area.
- **Help for beginners on the canvas.** Hover any dot on a block to see what it carries and what to do with it. Click a dot to see everything that can connect there (steps that can come next, or the add-ons that fit an input) and add it already connected, in one click. Every problem in the Problems list and the Inspector says *what happened* and *how to fix it* in plain English, refused connections explain what to do instead, and failed runs explain the likely cause (API key, local model not running, misspelt field…). A beginner's guide opens the first time you open an empty workflow and is always one click away (**❓ Help**); `docs/user-guide/beginner-guide.md` has the same guide.

### Changed
- LLM judges, pairwise judges and simulated users accept any known provider's `provider:model`, not only catalog models.
- **Plain block names.** The palette uses everyday names and descriptions: *Ask AI* (was LLM call), *AI Agent*, *Planning Agent* (Deep Agent), *Helper Agent* (Subagent), *AI model* (Chat model), *Choose a path* (Router), *Repeat for each item* (Map), *Wait for all* (Join), *Group of steps* (Subgraph), *Ask for approval* (Approval), *Ask a person* (Human input), *Run Python code* (Python script), *Web API tool*, *App connector (MCP)*, and plain names for memory blocks and agent add-ons (middleware). Categories are ordered for beginners and every port has a description. Type ids (`core.llm`, …) are unchanged, so saved workflows and exported code are unaffected; new blocks get names from the new labels (e.g. `ask_ai`). Start and END show as *Start* and *Finish*. A new AI model block starts as the project's default model.
- New diagnostics: `W011` (a provider's integration is not installed on this server) and `E017` (an OpenAI-compatible model without a Base URL).

## v0.5.0 — Evaluation in production (not merged yet)

### Added
- **Online evaluators.** Attach any of a workflow's evaluators to a deployment with a sampling rate (0-100 %) and a filter (run status, the metadata the API caller sent). Sampled runs are scored in the background after the response is sent, so callers never wait. Scores show in the run list and on each run's timeline, with hourly and daily averages on the Evaluation page's *Production* tab. The judge's own cost is counted apart from the run's. `GET/POST /v1/deployments/{id}/online-evaluators`, `GET /v1/deployments/{id}/scores`, `/scored-runs`, `GET /v1/threads/{id}/scores`.
- **Drift alerts.** An online evaluator can alert when its mean over a recent window falls below a value, or drops more than a set amount below its trailing 7-day mean. Open alerts show in the app (`GET /v1/workflows/{id}/alerts`) and on `/metrics` as `agentcanvas_eval_alert_open`; `infra/observability/alerts.yaml` ships the `AgentCanvasEvaluationDrift` rule.
- **Annotation queues.** A queue per workflow is fed by rules (an online score below a threshold, a failed run, a thumbs-down) or by hand from any run. Reviewers see the input, output and timeline, score each rubric criterion, comment, and can add the run to a dataset with a corrected reference in one step. Queue progress and agreement between reviewers are shown on the *Queues* tab.
- **Feedback from API callers.** `POST /v1/deployments/{id}/runs/{run_id}/feedback` with `{"rating": "up" | "down", "comment"}`; a thumbs-down goes to the queues that ask for it.
- **Pairwise and composite evaluators.** A pairwise LLM judge compares two experiment targets on each example, asking twice with the answers swapped, and reports a win rate with a confidence interval. A composite evaluator combines others by weights or all/any rules. Both work in experiments and release gates (a pairwise gate compares the candidate with the live version).
- **Rubric self-grading.** A Rubric widget on agent and Deep Agent steps lets a grader model check the answer against your rubric and ask for revisions, up to a set number of rounds. Each grading round shows live in the run panel and is kept on the timeline; exported code carries it.
- **Simulated users.** For chat workflows, an experiment can hold a whole conversation per example with a simulated user (a catalog model given a persona, a goal and a turn limit). Each conversation is one result with its full transcript, which evaluators see.
- **LangSmith dataset sync.** Push a dataset version to LangSmith, or pull its current examples back as a new version, from the dataset page (`POST /v1/datasets/{id}/langsmith/push`, `/pull`). It uses the project secret `LANGSMITH_API_KEY` (or the server's) and only syncs when you ask.
- **Record/replay covers tools.** A recorded run now keeps what each HTTP, MCP and workspace tool was asked and answered. A regression test made from it replays those answers instead of calling the tools, so it runs without network access or side effects.

### Changed
- Fields listed in `settings.trace_redact` are also redacted in the root run and model runs sent to LangSmith and OpenTelemetry.
- A release gate keeps the dataset version and evaluator settings it was saved with, so later edits do not change what it measures until an admin saves it again.

### Fixed
- An API instance that stalls long enough for another to take over its runs or experiments now stops its copy instead of finishing it twice.
- Dry runs also stand in for workspace tool functions with a risky effect; they used to call them.

## v0.4.0 — Evaluation Studio (merged 2026-10-06, not tagged yet)

### Added
- **Datasets** (API) to evaluate a workflow on: build them from test cases, past runs, CSV or JSONL files; every change is kept as a version. `GET/POST /v1/workflows/{id}/datasets`, `/v1/datasets/{id}/…`.
- **Evaluators** (API) that score a run: exact match, regex, JSON Schema, an LLM judge with your rubric, tool-call trajectory, or your own function in `scripts/`. Each gives a 0-1 score, pass or fail at a threshold, and a comment. `GET/POST /v1/workflows/{id}/evaluators`, `POST /v1/evaluators/{id}/try`.
- **Experiments** (API): run the draft and published versions, or variants with another model or prompt, over a dataset version in the background, and compare mean scores, pass rates, latency, tokens and cost. An experiment can be cancelled and carries on after a redeploy. `POST /v1/workflows/{id}/experiments`, `/v1/experiments/{id}`, `/v1/experiments/{id}/results`.
- **Evaluation page** (editor → *Evaluate*): build datasets and evaluators, run experiments, read the results grid with each run's timeline one click away, and compare two experiments to see what improved or regressed.
- **Release gates.** An admin can require an evaluation before publishing to an environment: minimum mean scores on a dataset and how far the pass rate may drop against the live version. Publishing there evaluates the exact workflow (or reuses an evaluation that already did) and is refused on a miss; admins can publish anyway with a reason that the audit log keeps.
- **Rate limits per caller.** The public API limits each deployment key (120/min), webhook (120/min) and project (600/min) across all API instances, and answers 429 with `Retry-After` beyond that. See `AGENTCANVAS_RATE_LIMIT_*`.

### Changed
- The daily retention job deletes audit-log rows after 400 days (`AGENTCANVAS_AUDIT_RETENTION_DAYS`, 0 keeps them) and, if you set `AGENTCANVAS_MEMORY_RETENTION_DAYS`, long-term memories that were not updated for that long.

### Security
- The web app no longer needs `'unsafe-eval'`: forms validate without generating code, and the Content-Security-Policy is `script-src 'self'`.
- Base images are pinned by digest; workspace files that are symlinks out of the workspace are not listed.
- Release gates hold against builders: changing or deleting a gating dataset, or an evaluator of a gated workflow, needs an admin (403 otherwise), like the gate itself.
- Publishing pins exactly what the gate evaluated: if the draft or the workspace changes during the evaluation, the publish is refused (409). Evaluations are reused only for the same workspace commit.
- Regex evaluators run in a worker process with a 2 s limit, so a pathological pattern cannot freeze the API; code evaluators time out after 60 s.
- Masking also covers run errors sent to LangSmith, span status messages, and secrets with quotes, backslashes or non-ASCII characters.

### Fixed
- Secret values no longer reach LangSmith traces or OpenTelemetry spans; they are masked as `***`. A workflow can also name fields (`settings.trace_redact`) whose values are never traced.
- An example whose run never started now fails every evaluator instead of being left out of averages and gates, and a gate fails when not every example finished. A non-finite score (NaN) no longer counts as a pass.
- Trying an evaluator uses the workflow's own project for model keys and workspace code.
- The Evaluation page no longer crashes when switching experiments with an output diff open; forms accept Python-style regular expressions (for example in the PII middleware) again.
- Runs of an API instance that stopped without a clean shutdown (out of memory, a lost node) no longer stay "running": another instance picks them up after 60 s (`AGENTCANVAS_ORPHAN_AFTER_S`).

## v0.3.0 — Production readiness (merged 2026-10-06, not tagged yet)

### Production readiness
- **Containers, compose and Helm.** Non-root API and web images, `infra/compose.prod.yml` (two API replicas, a one-shot migration) and a Helm chart in `infra/helm/agentcanvas`. `/readyz` checks the database and the run store.
- **Observability.** OpenTelemetry traces for requests, database calls and outgoing HTTP when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; Prometheus metrics on `/metrics`; a Grafana dashboard and alert rules in `infra/observability/`.
- **Retention and backups.** Conversations older than the retention period are deleted daily; backup and restore scripts with a runbook.
- **Safe redeploy.** On shutdown an instance stops taking runs, lets running ones reach a step boundary and hands them to another instance, which resumes them. Threads move to a newly published version only when it is resume-compatible.
- **Load tested.** 200 concurrent streaming runs on compose prod: 5,875 runs, none failed, first event p95 89 ms.
- **Secrets under a master key.** Each organisation's secrets use their own data key, wrapped by a key file, AWS KMS or Google Cloud KMS (`AGENTCANVAS_KMS_KEY`). Rotate with `python -m agentcanvas.security.kms rewrap`.
- **Encrypted checkpoints** when `LANGGRAPH_AES_KEY` is set.
- **Security review** completed (`10-security-checklist.md` §7). Dependency and image scanning run in CI; the web server sends a Content Security Policy and rate-limits deployment and webhook calls.

### Changed
- **Webhook signatures include a timestamp.** Senders send `X-AgentCanvas-Timestamp` and sign `"<timestamp>." + body`; deliveries older than five minutes are refused. Update existing senders before upgrading.
- Invitations to someone who already belongs to another organisation wait until they accept them (Home shows a banner). Members who have not accepted show no profile details.
- You start in the organisation you used last.
- The web app asks for `offline_access` and renews sessions silently; enable refresh tokens at your identity provider.
- The HTTP tool refuses private, loopback and link-local addresses unless the tool allows private networks.
- The docker sandbox runs as an unprivileged user and is killed on timeout.
- Production refuses the development secret key, and the hosted edition refuses to start without a sandbox. API docs are off in production.

## v0.2.0 — Accounts & access (merged 2026-10-05, not tagged yet)

### Accounts & access
- **Sign-in with your identity provider (OIDC).** Set `AGENTCANVAS_AUTH_MODE=oidc`; the web app signs in with authorization code + PKCE and the API checks every token. Deployment API keys and signed webhooks work as before.
- **Organisations.** Everything belongs to an organisation, and nobody sees another organisation's workflows, runs, secrets or deployments. Create projects with `POST /v1/projects`.
- **Roles:** viewer, reviewer (answers approvals), builder and admin. Admins invite people by email from the new Members page.
- **Audit log.** Every change is recorded with who made it, what it touched and from where; approvals include a hash of what was approved. Admins read it on the Audit log page.
- **Policies.** Admins write organisation rules as CEL on the new Policies page, for example "every external write needs approval" or "only approved models in prod". The editor flags workflows that break them (`E095`), and publishing refuses them in a new "policies" checklist item. Rules for one environment show as warnings in the editor (`W095`) and block publishing there. Steps inside subgraphs are checked too.
- **Memory per organisation.** Long-term memory is kept per organisation, and the Memory inspector shows only your organisation's. Memories saved before this release move to the default organisation the first time the API starts.

### Changed
- Downloads and the "Open trace" link now go through the signed-in API (`GET /v1/runs/{id}/trace?format=json`).
- Requests that name an id from another organisation, or one that doesn't exist, as a query parameter (`?workflow_id=…`) now answer 404 instead of an empty list.
- Policies are enforced when publishing. Draft runs, debug runs and test-case runs are no longer blocked by a broken policy.
- Deployment API end users are kept apart from organisation members: a `user_id` passed to a deployment becomes `api:<deployment>:<user_id>` in the run context and in user-scoped memory.
- Memories from before organisations are moved once; the API records that it has done so and never moves memories again.

### Fixed
- Checking policies took seconds on large workflows (about 6 s for 120 steps); it now takes a fraction of a second.
- A workflow with a malformed embedded subgraph no longer fails to open (500) once the organisation has a policy.
- Reviewers can resume runs but can no longer add debug pins, mocks or dry runs to them.
- An organisation can no longer be left without an admin who has signed in, including through concurrent role changes.
- The web app renews an expired sign-in in the background, has Sign out and "Sign in with a different account", and hides actions your role cannot take.

## v0.1.0 — MVP (2026-10-05)

The first usable release. You can build an agentic workflow on a canvas, run and debug it, test it, export it as plain Python and publish it behind an API.

### Build
- **Canvas editor** (React Flow):
  - typed, colour-coded ports
  - connections are checked before they are made, and a refusal says why
  - autosave with conflict detection, undo/redo
  - copy/paste, auto-layout (Tidy), ⌘K command palette, multi-select
- **Workflow format.** The workflow IR (v1.1) is the single source of truth: one JSON document, validated by about 70 diagnostic rules shown in the Problems dock.
- **Widgets:**

  | Group | Widgets |
  |---|---|
  | Steps | LLM, Router, Set, Script, Approval, Human input |
  | Agents | Agent (with middleware and structured output), Deep Agent with sub-agents and file backends |
  | Structure | Map/Join (parallel fan-out), Subgraph |
  | Memory | recall / remember / forget |
  | Tools | HTTP tool, MCP server, memory tools, workspace tools |
  | Middleware | PII, call limits, retry, fallback, summarisation, to-dos, tool selection |

- **Workspaces.** Each project has its own git-backed workspace with a code editor and lint markers. It holds:
  - script nodes and custom tools
  - custom widgets (`@canvas_node`)
  - event handlers (`@on`)
- **Sandboxes.** Workspace code runs in-process, in a subprocess, in Docker, or (experimental) in a LangSmith Sandbox. The policy file `sandbox.json` sets limits, an egress allow-list and header injection.
- **Secrets.** Encrypted at rest, write-only through the API, and masked in every run event.
- **Templates:** Ticket triage, Approval workflow, Data extraction, RAG Q&A, Research brief, Classify & route, Chat assistant.
- **Guided build.** A six-step first-run tutorial that advances as the real canvas takes shape.

### Run & debug
- **Durable runs.** Runs survive a restart.
- **Human decisions.** Approvals and inputs appear in the Run panel and in a cross-workflow Inbox.
- **Run panel:**
  - breakpoints
  - a timeline with state edits, fork and replay
  - Explain mode, which narrates each step
  - live token streaming
- **Live Code Mirror.** The generated LangGraph code updates as you edit.
- **Debug aids:**
  - pinned outputs and mock models
  - cost, tokens and latency per step
  - budgets per run and per conversation
  - channel lineage
- **Blast-radius overlay.** Shows which steps can write, send, execute or spend, and whether a human approves first. A **dry run** stubs every risky tool with a recorder.
- **LangSmith tracing** with an "Open trace" link.

### Ship
- **Versions.** Semver snapshots of the workflow and its workspace code, a field-level diff shown on the canvas, and restore.
- **Saved test cases:**
  - assert on final status, output values and route
  - run against the draft or any version
- **Record/replay.** Turn any recorded run, including production runs, into a deterministic regression test.
- **Export** as a standalone Python project:
  - generated graph, vendored node library, workspace code
  - pinned dependencies, Dockerfile, `langgraph.json`
  - the project's own tests pass with `uv run pytest`
  - it serves under `langgraph dev`
- **Publish:**
  - a pre-publish checklist: validation, tests and secrets; breaking contract changes are reported
  - one deployment per environment behind a rotatable API key
  - endpoints `runs/wait`, `runs/stream` and resume, always pinned to the published version
- **Triggers.** HMAC-signed webhooks and cron schedules.
- **Contract view** of a workflow's API, with breaking-change detection between versions.

### Quality
- **Tests:**

  | Suite | Count |
  |---|---|
  | API, on SQLite and Postgres | about 200 |
  | Web unit tests | 47 |
  | Playwright journeys | 27 |

- **`make check`** runs every CI gate locally except the Playwright journeys (`make e2e`) and the strict docs build.
- **UI states.** Every screen has loading, error-with-retry and empty states. Error boundaries keep one broken panel from blanking the app.
- **Bundle size.** The web entry bundle is 0.3 MB: pages and the layout engine load on demand.

### Known limitations
- **No real authentication yet.** Every request acts as a built-in development admin. OIDC, organisations and roles arrive in v0.2.0.
- **Cron runs on one API instance only.** Multi-instance deployments need the leader lock planned for v0.3.0.
- **Record/replay replays model replies only.** Tools run again during replay.
- **The LangSmith Sandbox mode is unmeasured.** It has not run against the live service (see spike SP-2).
