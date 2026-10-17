# Changelog

All notable changes to AgentCanvas. Versions follow [semver](https://semver.org); dates are UTC.

## v0.27.0 — Sign-in through Matura (not tagged yet)

### Added
- **`AGENTCANVAS_AUTH_MODE=matura`**: sign-in, organisations, members and roles come from Matura, the owner's identity
  and tenancy platform (its sprint TS9a). The token names the organisation (`tid`) and what the person may do there
  (`perms`); the highest role whose permissions the token carries in full decides the role (a role edited in Matura
  that skips a rung gets only the rungs below it), so every existing role check works unchanged. People in several organisations switch with a picker that signs in again for the one they choose.
- **Matura's webhooks** (`POST /v1/matura/events`, HMAC-signed) and **its change feed** keep organisations, people and
  roles current: a role granted, changed or revoked in Matura, a member removed or AgentCanvas turned off for an
  organisation applies here at once, and whatever a missed webhook carried arrives at the next feed read.
- **Matura contract tables** (`matura_*`, migration `f2b6d0a4c8e1`) on every install; empty unless Matura is used.
- **`matura/manifest.json`**: what Matura needs to register AgentCanvas (four permissions, four starter roles).
  Its `conformance` section lets Matura's `matura-conformance` check AgentCanvas from outside; it passes all 19 checks
  (token refusals, permissions, organisation isolation on projects, signed webhooks).
- `GET /v1/me` also returns `org_names`.

### Changed
- In Matura mode the member and invitation endpoints answer 409 and the Members page is read-only with a link to
  Matura's portal. `dev` and `oidc` modes are unchanged.

## v0.26.3 — Every framework in the Docker app by default (not tagged yet)

### Changed
- **`make docker-up` builds every framework's runtime into the API image** (CrewAI, Google ADK, Pydantic AI, Agno, the Microsoft Agent Framework, Strands, LlamaIndex and Mastra), so their workflows run in the Docker app without extra steps. `docker compose -f infra/compose.local.yml up --build` does the same. The image is several GB; `FRAMEWORKS="crewai mastra"` builds only some, `FRAMEWORKS=none` none. An image built with v0.26.2 or earlier needs one `make docker-up` to rebuild. The production images (`compose.prod.yml`, Helm) are unchanged and still opt in.

## v0.26.2 — Framework setup, export to any framework, Full mode fits (not tagged yet)

### Fixed
- **"Runtime not available" when the app runs in Docker.** `make crewai` / `make framework FW=…` on your computer does not reach the server inside Docker. The API image can now include them: `make docker-up FRAMEWORKS=all` (or a comma list such as `FRAMEWORKS=crewai,google_adk,mastra`). The server says in Docker which command to run instead of the from-source one.
- **The Framework setting says when a framework is not set up** and what to run (`/frameworks` gives `ready` and `setup`); the top bar marks such badges "not set up". The list refreshes every 30 seconds, so a framework set up meanwhile shows as ready.
- **Export lists every framework.** *Download code* can package a workflow for any installed framework (`export.zip?framework=`), not only its own; the workflow itself is unchanged.
- **"Where it will run" no longer fails** on a workflow that is not LangGraph: the deploy kit is for LangGraph services, so it shows only for them, with a note for the others.
- **Full mode fits the window.** The top bar needed about 1,830 px and cut off its right side. Rarely used links and tools moved into a **More ▾** menu, the framework badges collapse into "+N", and the bar fits at 1,280 px.

## v0.26.1 — Review after the competitive roadmap (not tagged yet)

A whole-codebase review after S23–S36 found 40 bugs; all are fixed.

### Security
- **A workflow's name, description or block ids can no longer become code.** Generated Pydantic AI, Agno, Microsoft Agent Framework, Strands, LlamaIndex, OpenAI Agents SDK, CrewAI and Google ADK modules escape them in docstrings and comments, and Mastra's TypeScript escapes `*/` in its header. Before, a name such as `x"""; import os; ...` ran on the server when the flow loaded, and a name with `C:\Users` or a quote at the end could not run or export.
- **Mastra prompt templates are sandboxed** like Python's: `constructor`, `prototype` and `__proto__` are refused, so a template cannot run JavaScript in the worker.
- **Public share links:** a visitor no longer sees the raw tokens of internal steps or the text before a safety block redacts it (only the final step's text streams, and nothing streams when the workflow has a safety block); a visitor can answer questions but cannot approve an approval step (it waits in the inbox); each visitor address has its own rate limit (`rate_limit_per_share_client`, default 30 a minute), so one visitor cannot use up an API key's quota.
- **Knowledge uploads:** a Markdown heading with a long run of spaces no longer freezes the server for minutes (the heading pattern is linear); a Word file that unpacks to more than 100 MB is refused before it is read; parsing and chunking run off the event loop.
- **Imports:** an n8n, Langflow or Flowise file with many branches that join again no longer takes exponential time, and a very deep one gives a clear error instead of a 500.
- **Personal data patterns** are matched across the whole text, not only its first 100,000 characters.

### Fixed
- **The flow engine** (Pydantic AI, Agno, Microsoft Agent Framework, Strands, LlamaIndex and Mastra): a *Wait for all* step inside a loop waits for every branch on each pass instead of running early and twice; one after a path that was not taken ends the run instead of failing at the step limit; a step after an approval no longer runs twice when the run continues.
- **Tool approvals** never read a "no" as a "yes": `"reject"`, `{"approved": false}` and a missing decision all reject, and one edit is no longer copied onto other calls.
- **A failed continue keeps the paused run** (on the server and in exports), so the person can try again; a worker that stops mid-run ends the run with an error.
- **Microsoft Agent Framework:** each decision applies to its own call when a turn calls the same tool twice, and a rejection's reason reaches the model.
- **LlamaIndex:** every call of a turn that needs a person is asked about, not just the first; the model bridge streams one tool call per chunk, as OpenAI does, so LlamaIndex agents see parallel tool calls.
- **Model call limits** hold across approvals on the Microsoft Agent Framework and Strands.
- **Google ADK:** *Ask before using* works with the tool block's name (the one validation accepts); a call approved earlier is not run again when the run continues; a call whose arguments changed is asked about again. LangChain designed agents also accept the block name.
- **Exports:** a workflow named after a package it imports (`Strands`, `Agent Framework`, `workflows`) gets a `wf_` package name; the export download keeps its `.zip` name when the workflow searches a knowledge base.
- **Mastra:** an empty field prints `None` and booleans `True`/`False` as in Python, empty lists are false in `{% if %}`, `tojson` works; an answer schema with `$id` no longer breaks later answers; cancelling a run ends it even while a tool runs; protocol writes retry when stdout is busy.
- **Budgets** count the spend of runs still going, so runs at the same time cannot all spend up to the limit (per API instance).
- **Knowledge bases:** two uploads at once no longer leave search on a stale index, and a reindex no longer misses a document added meanwhile.
- Two first opens of a deployment's share settings at once no longer give a 500.
- **Web:** typing in the share settings is kept when the switch is flipped; a knowledge base name can be typed with spaces and dashes; *Alert at* takes at most 5 values; Clear in the Playground drops a reply still on its way; the Code tab's highlight stops before `FLOW`.

## v0.26.0 — Mastra flows in TypeScript (not tagged yet)

### Added
- **Mastra as a workflow framework, the first TypeScript one.** Pick it in the workflow settings and the workflow is generated as a TypeScript module: a Mastra `Agent` for each AI block (an AI SDK model over an OpenAI-compatible address, `createTool` tools, a Zod schema for typed answers through `structuredOutput`), an `async function` per block and a `FLOW` that says which step follows which (`@mastra/core` 1.76.0). It passes the 12-case conformance kit; 22 of the 27 templates run on it.
- **A TypeScript twin of the flow engine** (`adapters/mastra/flow.ts`) runs the flow as the canvas draws it, like `supersteps.py` does for the Python frameworks: steps side by side, Wait for all, paths by CEL rules (`@marcbachmann/cel-js`, with whole numbers read as CEL ints as in cel-python), loops stopped at the step limit, field reducers, prompts with `{{ state.<field> }}` (Nunjucks; lists and objects are written as JSON), answers checked against a schema (Ajv). Node.js 22.18+ runs the TypeScript as it is, with no build step.
- **A Node.js worker** (`adapters/mastra/worker.ts`) runs Mastra flows next to the server and speaks the same JSON lines as the Python frameworks' workers, so models, tools, knowledge bases, workspace code, usage and recorded tool calls all stay on the server. `make mastra` installs Mastra and the runtime's packages from npm into `apps/api/.frameworks/mastra`.
- **Ask before a tool call, the Mastra way.** Tools named in *Ask before using* get `requireApproval`: the agent's run suspends, its snapshot is saved with the pause (so it continues in another process too), and the agent continues with `approveToolCallGenerate` or `declineToolCallGenerate` (the person's reason reaches the model). Edited arguments are applied when the approved call runs. Two calls that need a person are asked about one after the other.
- **Model call limits** use Mastra's `maxSteps`: past the limit the agent stops with a note, or the run fails when the block says so.
- **Export to a TypeScript project**: `package.json` with pinned packages, `src/workflow.ts`, the vendored `flow.ts` and `runtime.ts`, a host that reaches each model over its provider's OpenAI-compatible API, a command-line runner (`node src/main.ts`) that saves a paused run in `.runs/` and continues it, and `node --test` tests that replay saved test cases with a scripted model. Workspace code stays Python: tools, code steps and knowledge-base search run in a small Python helper (`python/sidecar.py`) when the flow has them. CI exports every template Mastra can run and runs each project's tests.
- Servers offer Mastra by default (`AGENTCANVAS_ADAPTERS=...,llamaindex,mastra`).

### Changed
- `CrewWorker.prepare()` lays out a worker's files and names its command, so a worker can be something other than Python.

### Fixed
- The demo model picks the first choice for a tool argument that has a list of choices, instead of "demo". Typed answers on Strands and LlamaIndex (which ask for them through a tool) are valid again with the demo model, so the Lead qualification template runs on both.

### Known limits
- The flow is not written as Mastra's own `createWorkflow` chain (the roadmap's "Full" tier, if users ask). The API image has no Node.js: Mastra workflows run there only once the image is extended with Node.js and `make mastra`.

## v0.25.0 — Strands and LlamaIndex flows (not tagged yet)

### Added
- **Strands Agents and LlamaIndex as workflow frameworks.** Pick either in the workflow settings and the workflow is generated as a flow of async Python on the shared flow engine. Strands agents are `Agent`s with an `OpenAIModel`, tools and a `structured_output_model` (`strands-agents` 1.58.1); LlamaIndex agents are `FunctionAgent`s with an `OpenAILike` LLM, tools and an `output_cls` (`llama-index-core` 0.14.25). Both pass the 12-case conformance kit; 22 of the 27 templates run on each.
- **Ask before a tool call, each framework's way.** On Strands, an `AskBefore` hook interrupts the call with `BeforeToolCallEvent.interrupt`; the agent's messages and interrupt state are saved and it continues with an `interruptResponse` (approve, edited arguments, or a rejection whose reason the agent reads). On LlamaIndex, the tool waits inside the agent's workflow with an `InputRequiredEvent` and a `HumanResponseEvent`; the serialized `Context` and the chat memory are saved, and calls are asked about one at a time.
- **Model call limits** use Strands' `Limits(turns=...)` and LlamaIndex's `max_iterations`.
- **Exports** to a Strands or LlamaIndex project, like the other flow frameworks; their tests script the model with an OpenAI-compatible endpoint in memory that streams. CI exports every template each can run and runs each project's tests.
- Servers offer both by default; `make framework FW=strands` / `FW=llamaindex`, and the API image takes `--build-arg WITH_STRANDS=true` and `WITH_LLAMAINDEX=true`.

### Changed
- `flowgen.Dialect` names the class an agent builder returns (`FunctionAgent` for LlamaIndex).

### Fixed
- The demo model no longer fails with "unhashable type: 'list'" on a tool argument whose type is a list such as `["string", "null"]` (Strands sends typed answers that way).

## v0.24.0 — Microsoft Agent Framework flows (not tagged yet)

### Added
- **The Microsoft Agent Framework as a workflow framework.** Pick it in the workflow settings and the workflow is generated as a flow of async Python on the shared flow engine, with Agent Framework `Agent`s for the AI blocks: an `OpenAIChatCompletionClient`, tools, and a `response_format` for typed answers (`agent-framework-core` 1.20.0, `agent-framework-openai` 1.16.0). It passes the 12-case conformance kit; 22 of the 27 templates run on it.
- **Ask before a tool call, the Agent Framework way.** Tools named in *Ask before using* use `approval_mode="always_require"`. The pause saves the agent's `AgentSession` and the response holding the approval requests; the agent continues in that session with a function approval response per call (approve, edit or reject). When the model calls several tools in one turn, only the ones that need a person are asked about. Edited arguments are applied when the approved call runs.
- **Model call limits** as a chat middleware that counts an agent's model calls in one turn; a model block's temperature and token limit reach every call the same way.
- **Export** to an Agent Framework project, like the Pydantic AI and Agno ones; its tests script the model with a chat middleware, so the chat client is the framework's own and nothing reaches a provider. CI exports every template it can run and runs each project's tests.
- Servers offer it by default (`AGENTCANVAS_ADAPTERS=...,agno,microsoft_agent_framework`). Its workflows run in a worker process from the environment `make framework FW=microsoft_agent_framework` makes; the API image takes `--build-arg WITH_MICROSOFT_AGENT_FRAMEWORK=true`.

### Known limits
- The flow is not written as the framework's own `WorkflowBuilder` graph (the roadmap's "Full" tier, if users ask). A rejected tool call reaches the agent as the framework's fixed message, not the reason a person typed.

## v0.23.0 — Pydantic AI and Agno flows (not tagged yet)

### Added
- **Pydantic AI and Agno as workflow frameworks.** Pick either in the workflow settings and the workflow is generated as a flow of async Python with the framework's own agents: an agent builder per AI agent block, an `async def` per block and a `FLOW` that says which step follows which. Pydantic AI agents are `Agent`s with an `OpenAIChatModel`, `Tool`s and `NativeOutput` for typed answers (`pydantic-ai-slim` 2.54.0); Agno agents are `Agent`s with an `OpenAILike` model, tools and an `output_schema` (`agno` 3.1.1). Both pass the 12-case conformance kit; 22 of the 27 templates run on each.
- **A shared flow engine** (`adapters/supersteps.py`) runs those flows as the canvas draws them: steps side by side in the same round, Wait for all, paths by CEL rules, loops stopped at the workflow's step limit, field reducers; a pause is saved as JSON with the asking step's own framework state.
- **Ask before a tool call, the framework's way.** On Pydantic AI, tools named in *Ask before using* are `requires_approval` tools: the run pauses on `DeferredToolRequests` and continues from the saved message history with `DeferredToolResults` (approve, edited arguments through `ToolApproved(override_args=...)`, or `ToolDenied`). On Agno they use `requires_confirmation`: the paused `RunOutput` is saved and continued with `acontinue_run`. The agent's turn is not run again.
- **Model call limits** cap an agent's model calls in one turn on both (Pydantic AI's `UsageLimits`; on Agno the runtime counts them): the agent stops with a note, or the run fails when the block says so.
- **Exports** to a Pydantic AI or Agno project: the flow, the vendored engine and runtime (no LangChain), a host that reaches each model over its provider's OpenAI-compatible API, a command-line runner that saves a paused run in `.runs/` and continues it, and tests that replay saved test cases with a scripted model. CI exports every template each can run and runs each project's tests.
- Servers offer both by default (`AGENTCANVAS_ADAPTERS=langchain,openai_agents,crewai,google_adk,pydantic_ai,agno`). Their workflows run in a worker process from the environment `make framework FW=pydantic_ai` / `FW=agno` makes (the ones mixed flows use for their agents); the API image takes `--build-arg WITH_PYDANTIC_AI=true` and `WITH_AGNO=true`.

### Changed
- The new adapters reuse the CrewAI adapter's server side through `adapters/flowadapter.py`; their worker loop, `adapters/flowworker.py`, is the Google ADK worker's made framework-neutral.

### Fixed
- The framework badges in the editor's top bar stay on one line (the bar scrolls sideways); with six frameworks they wrapped out of sight and could not be clicked.

## v0.22.0 — Onboarding and switching (not tagged yet)

### Added
- **Import from Langflow, Flowise and n8n.** Home's *Import a file…* takes the JSON those builders export (and Open Agent Spec files, as before) and creates a workflow: Langflow chat inputs, prompts, models, agents, API Request tools and If-Else routers; Flowise chatflows (LLM chains, conversation chains, tool agents, chat models, request tools) and Agentflow v2 (LLM, Agent, Condition, Human Input, Direct Reply); n8n chat triggers, AI Agent, Basic LLM Chain, chat-model sub-nodes, HTTP Request tools, If, Switch and Edit Fields with `$json` and `$('Node')` expressions. Prompts' `{variables}` become `{{ state.<field> }}`; a step that needs several earlier ones gets a Wait for all (not when they are exclusive branches); what has no counterpart becomes a placeholder step, and a note lists everything to check. `POST /v1/projects/{id}/workflows/import` returns the workflow, the `format` and the `report`.
- **Playground.** Chat with the project's default model, or any catalogue model, and its instructions before building anything; *Make it a workflow* creates a workflow with one AI agent that has those instructions and that model (`/v1/projects/{id}/playground/chat`, `/playground/workflow`).
- **Simple mode.** A *Simple / Full* switch in the editor: Simple shows only the blocks a first workflow needs and the Inspector, Copilot and Run panels, and hides the power tools; the choice is remembered in the browser.
- **Push to GitHub.** Export's *Push to GitHub…* commits the export to a repository as one commit, on an existing branch or a new one started from the default branch (`POST /v1/workflows/{id}/github`); the token is the project secret `GITHUB_TOKEN`.
- User guide: *Coming from Langflow, Flowise or n8n*, the Playground and Simple mode in the Beginner's guide, Push to GitHub in *Export and deploy*.

### Changed
- Home's *Import an Agent Spec file…* became *Import a file…* (the Agent Spec endpoint stays).

## v0.21.0 — Google ADK flows (not tagged yet)

### Added
- **Google ADK as a workflow framework.** Pick it in the workflow settings and the workflow is generated as a native ADK 2 graph workflow (`google-adk` 2.11.0): one function node per block and a `build()` that wires them with ADK's own edges (`("START", first)`, a route dict after each path block, a tuple for parallel branches, a `JoinNode` before Wait for all), ADK `Agent`s with LiteLLM models for AI agents (Python tools, typed answers as `output_schema`, model call limits as a `before_model_callback`), run inside their step with `ctx.run_node`. A run executes the generated module with ADK's `Runner`. It passes the 12-case conformance kit; 22 of the 27 templates run on it.
- **Ask before a tool call on ADK.** Tools named in an AI agent's *Ask before using* pause the run with a `tool_approval` (approve, edit the arguments, or reject), as on LangGraph and the OpenAI Agents SDK; the agent's turn runs again up to the call when the person answers. Several approvals in one turn are answered one after another.
- Approvals and questions pause an ADK workflow with ADK's own `RequestInput`; the answer continues it from the saved fields without running finished steps again. A step that loops past the workflow's step limit stops the run (`recursion_limit`).
- **Export to an ADK project:** the workflow, the runtime it needs (vendored, no LangChain), a host that reaches each model through LiteLLM over its provider's OpenAI-compatible API, a command-line runner that saves a paused run in `.runs/` and continues it, and tests that replay saved test cases with a scripted ADK model. CI exports every template ADK can run and runs each project's tests.
- Servers offer Google ADK by default (`AGENTCANVAS_ADAPTERS=langchain,openai_agents,crewai,google_adk`). Its workflows run in a worker process in the environment `make framework FW=google_adk` makes (the one mixed flows use for ADK agents, which now also gets `cel-python`, `jinja2` and `jsonschema`); the API image takes `--build-arg WITH_GOOGLE_ADK=true`.

### Changed
- The CrewAI adapter's worker and server side are shared: `CrewWorker` and `shared_worker` take the framework's worker files and module, and `CrewAIAdapter` names its Python, worker and code generator as methods the ADK adapter overrides.

## v0.20.0 — Mixed-flow export (not tagged yet)

### Added
- **Framework parts.** CrewAI, Google ADK, Agno, Strands Agents and LlamaIndex pin packages that clash with LangGraph's (for example `openai<3` against `openai>=3`), so their agents can no longer be installed next to the host. An agent designed for one of them now runs as a *part*: its generated module in a Python environment of its own, driven by a small worker (`part_worker.py`, standard library only) over JSON lines. Tool calls come back to the host (traced, approved, recorded), the model is the host's model bridge on 127.0.0.1. The other four frameworks keep running in the host. `scripts/check_part_envs.py` (in CI) proves which is which by resolving each framework's pins with the host's.
- **Typed contracts between parts.** Every designed agent has a contract (`interop.part_contract`, `designs.contract`): the conversation goes in as text, and its answer must match its structured output schema (or be text). An answer outside the contract stops the step with `answered outside its contract: <field>: <why>`.
- **Mixed flows on this server.** `make framework FW=<id>` sets up a framework's environment in `apps/api/.frameworks/<id>` (or `AGENTCANVAS_<ID>_PYTHON`); CrewAI's is the one `make crewai` makes. A LangGraph workflow with a CrewAI agent now runs on a server that has it.
- **One deployable project for a mixed flow.** The export keeps agents that fit in `src/<pkg>/agents/` and their packages in `pyproject.toml`, and puts each part in `parts/<agent>/` (`agent.py`, `worker.py`, `requirements.txt`, `contract.json`). `make install` and the Dockerfile install each part in its own environment (`parts/<agent>/.venv`, or `PART_<AGENT>_PYTHON`); `contracts.json` lists every part (host, own environment, or A2A) with its contract; `tests/test_parts.py` runs the flow through each part; the README explains the parts.
- **A2A parts** for agents in other languages: Remote agent (A2A) blocks are listed in `contracts.json`, and their hosts are checked by `make doctor`.
- **Mixed-framework team** template (27 templates): an OpenAI Agents SDK researcher hands its notes to a CrewAI writer whose typed brief is checked at the boundary. Exported for Docker Compose, it deploys with `make deploy` and passes `make smoke` (also a CI step).
- The AI agent's settings say when it runs as a part; `GET /v1/agent-frameworks` gains `own_env`.

### Changed
- `installed` for an agent framework now also counts a part environment; P006's hint names `make framework`.
- Exported projects' `make test` installs the parts first; CI's export job runs `make test` for every LangGraph export.

### Fixed
- A whole-workflow export with an agent designed for CrewAI, Google ADK, Agno, Strands or LlamaIndex could not be installed (its packages conflicted with LangGraph's).

## v0.19.0 — CrewAI flows (not tagged yet)

### Added
- **CrewAI as a workflow framework.** Pick it in the workflow settings and the workflow is generated as a native CrewAI flow (`crewai` 1.15.25): a `Flow` class with one method per block, chained with CrewAI's `@start`, `@listen` and `@router` (paths become `@router` methods, Wait for all listens with `and_()`, a step reached from several places with `or_()`), CrewAI `LLM`s for AI steps and `Agent`s for AI agents (Python tools, typed answers, model call limits as `max_iter`). A run executes the generated module. It passes the 12-case conformance kit; 22 of the 26 templates run on it.
- **Crew** block (`crewai.crew`, CrewAI only): a team of agents with roles, goals and backstories works through a list of tasks, in order or handed out by a manager; the last task's answer lands in a field.
- **Agent task** block (`crewai.task`, CrewAI only): one agent does one task, with an optional **guardrail** in plain words that the model checks; a failing answer is redone up to the number of tries.
- Approvals and questions pause a CrewAI flow with CrewAI's own `HumanFeedbackPending`; the answer continues it from the saved fields without running finished steps again. A step that loops past the workflow's step limit stops the run (`recursion_limit`).
- **Export to a CrewAI project:** the flow, the runtime it needs (vendored, no LangChain), a host that reaches each model over its provider's OpenAI-compatible API, a command-line runner that saves a paused run in `.runs/` and continues it, and tests that replay saved test cases with a scripted model.
- `make crewai` installs CrewAI in its own Python environment (`apps/api/.frameworks/crewai`, or `AGENTCANVAS_CREWAI_PYTHON`); the API image takes `--build-arg WITH_CREWAI=true`. Without it CrewAI workflows can still be designed, checked and exported, and a run says how to install it.

### Changed
- Servers offer LangGraph, the OpenAI Agents SDK and CrewAI by default (`AGENTCANVAS_ADAPTERS=langchain,openai_agents,crewai`). CrewAI is not an API dependency: its flows run in a worker process (CrewAI pins `openai<3`, the API needs `openai>=3`).
- CI's api job installs CrewAI, so the conformance kit and the CrewAI tests run there; the template-export job also runs every CrewAI template export's tests.

## v0.18.0 — Model gateway and cost controls (not tagged yet)

### Added
- **Backup models** on every AI model block (`fallbacks`): when the block's model fails (provider down, out of quota, too slow), the same request goes to each backup in order and the first answer wins; a streamed answer fails over before its first token. Backups use their provider's own key and the block's temperature, output limit and timeout. Works on LangGraph and on the OpenAI Agents SDK (through the model bridge), in exported LangGraph projects (the gateway is vendored) and in exported SDK projects (`FallbackModel`). Usage and cost are counted for the model that answered.
- **Answer cache** on every AI model block (`cache_ttl`, "Reuse answers for (s)"): an identical request (messages, tools and settings; message ids ignored) within the time gets the stored answer without calling the provider, at no cost. One cache per project in the server's memory (1,000 answers).
- **Day and month budgets** for a workflow (`daily_max_cost_usd`, `monthly_max_cost_usd`): all its runs together per UTC day and calendar month; a run that spends the rest stops with `budget_exceeded`, and no new run starts until the next period.
- **Cost alerts** (`alert_at`, default 80%; reaching the budget always alerts): raised once per workflow, period and share across runs and servers (table `cost_alert`), shown as a warning in the run that crossed it (`ac.cost_alert` event), under **Spending** in the workflow settings, in the server log and as `agentcanvas_cost_alerts_total`; Prometheus rules `AgentCanvasCostAlert` and `AgentCanvasModelFailover`.
- **Spending** in the workflow settings and `GET /v1/workflows/{id}/spend`: today's and this month's spend against the budgets, runs, tokens, cached answers and backup answers, and the latest cost alerts. The run panel and each step show cached and backup answers.
- Validation: W016 (a backup repeats the model or an earlier backup); W015 now also covers backup models and day and month budgets.
- Metrics `agentcanvas_model_cache_hits_total`, `agentcanvas_model_fallbacks_total`.

### Changed
- Migration `e7a1c5f9b3d6` (`run.cache_hits`, `run.fallbacks`, table `cost_alert`).
- Run usage (`RUN_FINISHED.usage`) carries `cache_hits` and `fallbacks`.

### Fixed
- The Knowledge page showed **Load the sample handbook** for a moment while its list loaded, even when the sample was already there.

## v0.17.0 — OpenAI Agents SDK flows (not tagged yet)

### Added
- **OpenAI Agents SDK as a workflow framework.** Pick it in the workflow settings and the workflow is generated as OpenAI Agents SDK code (`openai-agents` 0.23.1): an `Agent` per AI step and AI agent, a function per block, and a `FLOW` of what follows what, run in supersteps like the canvas draws it. A run executes the generated module. Runs on the SDK: AI steps, AI agents (Python tools, tool approvals with approve, reject and edit, typed answers, model call limits as `max_turns`), model blocks of any catalog provider (through the model bridge, so usage, cost, scripted replies and recordings work as on LangGraph), Set values, Choose a path, Wait for all, Approval, Ask a person, Python steps and Search knowledge. It passes the 12-case conformance kit; 20 of the 26 templates run on it.
- **Export to an OpenAI Agents SDK project:** the flow, the runtime it needs (vendored, no LangChain), a host that reaches each model over its provider's OpenAI-compatible API, a command-line runner that saves a paused run in `.runs/` and continues it, knowledge bases, workspace code, and tests that replay saved test cases with the SDK's scripted model (`uv run pytest`, no keys).
- **Convert and compare in Evaluation Studio.** An experiment target can run on another framework (`"framework"` on a target; **on OpenAI Agents SDK** or **+ Compare on the other frameworks** in the Experiments tab): the same workflow and dataset run on LangGraph and the SDK side by side, with scores, tokens, cost and latency per target. CI runs the Classify and route template on both in one experiment and checks equal answers, paths and token counts.
- `GET /v1/workflows/{id}/code?framework=…` previews a workflow's code converted to another installed framework.

### Changed
- Servers offer LangGraph and the OpenAI Agents SDK by default (`AGENTCANVAS_ADAPTERS=langchain,openai_agents`); `openai-agents==0.23.1` is now an API dependency.
- An AI block designed for the workflow's own framework is native there (no P001 for an OpenAI agent in an OpenAI Agents SDK flow).

### Fixed
- Agents designed for other frameworks (S21) failed to generate when an answer-shape field had a nullable type such as `["string", "null"]`; such fields are now optional.

## v0.16.0 — Knowledge Studio (not tagged yet)

### Added
- **Knowledge page and knowledge bases.** A project's knowledge bases hold documents your workflows search: upload PDF (pages kept for citations), Word, HTML, Markdown, text, CSV and JSON files (20 at a time, 20 MB each), add public web pages by address (private and internal addresses refused, also after redirects), or paste text. Click a document to see its chunks; try searches right on the page. API: `/v1/projects/{id}/knowledge`, `/v1/knowledge/{id}` (`documents`, `documents/url`, `documents/text`, `documents/{doc}/chunks`, `search`, `reindex`).
- **Chunking options:** by heading (default), paragraph, sentence or fixed windows with overlap, about 800 characters each by default. Changing chunking or the embedding model marks the knowledge base stale until **Reindex**.
- **Hybrid search with citations.** Keyword (BM25), meaning (embeddings) or hybrid (reciprocal-rank fusion of both); every passage carries a citation number, title, page and source. A built-in offline embedder needs no key (it matches word forms and misspellings); provider embedding models (`openai:text-embedding-3-small`, `ollama:…`) plug in through LangChain.
- **Search knowledge block** (`knowledge.search`, new palette category Knowledge): saves the best passages and, optionally, the citation list. Exported projects ship each knowledge base the workflow uses as `src/knowledge/<name>.jsonl` and search it the same way.
- **Retrieval evaluator** (`retrieval`): recall, precision, hit rate or MRR at k of the passages a run found against an example's `reference.relevant` (match by document, title, chunk id or a phrase).
- **Sample handbook and eval set:** 22 handbook pages of a made-up company and 50 questions with typos and paraphrases. On it, hybrid search finds the right page in the top 3 for 95% of questions against 93% for keyword search (MRR 0.94 against 0.92), measured in CI through an experiment.
- **Ask your documents** template (26 templates): search a knowledge base, answer only from the passages, cite them.
- User-guide page: Knowledge.

### Changed
- Migration `d5f9b3a7c1e4` (`knowledge_base`, `knowledge_document`, `knowledge_chunk`).
- New API dependencies: `pypdf`, `python-docx`, `python-multipart`.

## v0.15.0 — Ship to users (not tagged yet)

### Added
- **Share a deployment as an app.** Switch on a share link per deployment (Versions → **Share as an app or a chat widget**) and anyone with the link can chat with it on a clean public page (`/s/<token>`): no account, phone-friendly, light and dark, streaming answers, suggested questions, thumbs up/down, and approvals asked as Yes/No. Links can be switched off or replaced, which ends conversations started on the old link. Visitors see only their own conversations; their runs count against the deployment's rate limits and show in Runs as end user `share:<deployment>:<visitor>`. Public API `/v1/share/{token}` (info, `runs/stream`, `threads/{id}/resume`, `runs/{id}/feedback`); builder API `/v1/deployments/{id}/share` (GET, PUT, POST `regenerate`).
- **Chat widget for any website:** `<script src="https://<your AgentCanvas>/widget.js" data-share="sh_…" async></script>` adds a chat button that opens the shared app in a panel.
- **Safety blocks (guardrails):** **Hide personal data** (`guard.pii`), **Check for prompt injection** (`guard.injection`), **Check for harmful content** (`guard.moderation`) and **Check the output format** (`guard.schema`). Each checks a field or the newest message and can stop the run, or flag it (a yes/no field to branch on with Choose a path, plus details); personal data can also be redacted, masked or hashed in place. An optional AI model adds a model verdict, real moderation, or one repair of invalid JSON. Every block ships labelled eval cases (91 in all, importable as a dataset with `python -m agentcanvas.guardrails <type>`), measured at precision and recall 1.0 in CI. New warning W019 (a flagging block that saves its verdict nowhere). Exported projects run the blocks too.
- **Template gallery: 25 use-case templates** (18 new) in nine categories: getting started, customer support, content, sales, data extraction, research, HR, engineering and operations. Home shows them in a searchable gallery with category filters; each card shows category, tags, block count and difficulty. `GET /v1/templates` adds `block_count`, `category`, `tags`, `difficulty` and `sample_input`. New user-guide page: Template gallery.

### Changed
- Exports to other agent frameworks list two more gaps up front: OpenAI Agents SDK flows whose result does not come from an agent, and WayFlow flows where a Set values step writes text into a list field.
- Migrations `b3c7d1e5f9a2` (one deployment per environment, project `secrets_version`) and `c4e8a2f6b0d3` (`deployment_share`, `share_thread`).

### Milestone review after S20–S22

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
