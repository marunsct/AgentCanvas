# AgentCanvas: from working prototype to finished product

A plan for the owner, written 2026-10-28 after a survey of the code, the existing plans (`HARDENING.md`, `HANDOVER.md`,
`docs/build-guide`) and the open-source landscape. It is ordered by what changes the product's feel the most for the
least risk. Each item says what, why, how, and how we will know it is done. Effort is in working days for one person
who knows the code; "found" means the survey found it, "spec" means the functional spec asked for it and it was never
built.

Sections 2–5 are the product plan. Section 6 is the hardening backlog carried over. Section 7 is the order of work.

## Progress (updated 2026-10-28, branch `claude/hardening-3`)

| Area | Done | Still to do |
|---|---|---|
| §4.1 Shell | One top bar on every page (project switcher with create, page links with active state, Inbox count, Settings, person menu with theme and sign-out); breadcrumbs on a workflow's pages | Role-aware hiding is done for Playground; "Simple mode hides blocks, not pages" |
| §3 Settings | `/settings` with Project (General: rename/delete; Models + defaults by purpose; Copilot; Secrets; Sandbox; Observability), Organisation (General: name, retention; Members; Policies; Audit; Server read-only), You (Appearance, Keyboard shortcuts); one model rule with `models/resolve`; Copilot PUT merges | Environments list; policy edit in place; per-user preferences saved server-side |
| §2 Copilot | Deep Agents engine behind `AGENTCANVAS_COPILOT_ENGINE` passing the same suite as the loop; token streaming, plan checklist, Stop (keeps what was said), Markdown, error card with Retry, history (New chat archives), multi-replica lease, partial accept with removed blocks drawn, best-practice suggestions, skills, investigator/researcher sub-agents, "Ask about this block", nightly real-model evaluation (needs a key) | Interrupts for real runs and workspace files (needs a persistent checkpointer per conversation); attributed commits in version history; per-turn cost; making Deep Agents the default after the evaluation |
| §4.2 Runs | Runs page (status, source, duration, cost, errors, filters, paging); Inbox with names, run links, filter | A Runs tab inside the editor |
| §4.3 Evaluation | Evaluator forms per kind (edit, delete); dataset examples field by field; experiment variants as rows; annotation rubric editor and run picker | "Evaluate" in the editor's bar |
| §4.4 Workspace | Unsaved-changes guard; folder tree; delete errors shown; Secrets moved to Settings | Rename and move files |
| §4.5 Other pages | Playground streams with Stop and keeps the chat; Audit (headers, dates, CSV, details); Members (headers, cancel invitation); Events (headers, role gating); Memory (confirm, expand, role gating); Knowledge (confirm on remove); Search block picks a knowledge base from a list | Events paging |
| §4.6 Dialogs | All native confirm/prompt replaced; toasts; workflow list rename/duplicate/delete/search/sort | — |
| §4.7 Labels | Planning ids out of the API docs; beta blocks marked; "stub" wording | — |
| §4.8 Docs | README and HANDOVER current; user-guide page for the shell and Settings | — |
| §6 Hardening | Egress enforced (proxy) or refused; MCP Origin/version checks; SQLite cascades; delete rules on Postgres | RLS migration split; CI framework cache |

## 1. What the survey found, in one paragraph each

**Copilot.** A hand-written tool-calling loop (`copilot/service.py`) over `bind_tools`, not an agent harness. Fifteen
tools, one proposal per turn, previewed as a read-only "ghost" on the canvas and applied as one undo step. It works
end to end on scripted models and has never been run against a real provider (`HANDOVER.md:385`). Text arrives per
model step, not per token; there is no stop button; only the last 24 text messages are re-sent and tool activity is
not persisted; the workflow JSON is cut at 30,000 characters mid-document; proposals are accept-all or reject-all;
removed blocks are not drawn in the preview; answers are plain text (no Markdown, no code blocks, no citations
although `search_guide` returns page and section). Four spec features were never built: partial accept (FR-COP-01),
attributed commits (FR-COP-07), lint and best-practice rules (FR-COP-04), and the Deep-Agent design with
`explain_trace`, `read_docs` and `write_workspace_file` (`02-technical-architecture.md:151`). The keyless dev server
answers with a canned `DemoCopilotModel`, and all three browser tests assert on its canned text.

**Settings.** There is no settings page. The nearest thing is `/setup`, reached from a top-bar link labelled "Model",
which stacks three unrelated forms: the project's default model, the Copilot, and trace export (admin-only, but on a
page only builders are shown a link to). Secrets live in the Workspace page's sidebar (a code page), the sandbox
policy is a raw JSON file, organisation retention has an API and no UI, projects cannot be created, renamed, switched
or deleted from the UI (the app silently picks one), and policies can only be toggled, not edited. Model configuration
is in eight places with their own rules: the project default, each model block, the Copilot's model, the Copilot's
question model, the Playground's select, the judge model (raw JSON, hard-coded default), the simulated-user model
(hard-coded default) and the knowledge embedding model (API only). Twenty-odd server settings a project admin would
expect to see (rate limits, retention, which frameworks are offered, local MCP, the sandbox backend) exist only as
environment variables. Two bugs: the Copilot "Use the workflow's model" option in fact uses the project default first,
and saving the Copilot form drops a separate model's `base_url`/`api_key` because the PUT replaces the whole object.

**The rest of the product.** The code has almost no TODO markers; the gaps are structural. No shared navigation: only
Home has the link bar, every other page has a brand link, so moving between pages goes through Home. No run or thread
history anywhere in the UI (the API has it; past runs are reached only through a `?thread=` link). Routes the UI never
calls: delete a workflow, edit an evaluator, dataset or queue, delete an experiment, org retention, move a project.
Raw JSON textareas for evaluator config, dataset examples and experiment overrides; run ids typed into a box to add
to an annotation queue; a rubric typed as "helpful:5, correct:2"; native `prompt()` and `confirm()` dialogs in 15
places; internal ids shown in the Inbox ("node n_x · thread th_y"); the Playground does not stream; the Workspace
loses unsaved edits when switching files; three pages never check the role so viewers see buttons the API then
refuses. The planning docs lag the code by several versions (`README.md` says v0.28.x, the code is 0.32.3; `HANDOVER.md`
§9 lists six items that are done; two spec docs still say "proposed" for things built in v0.27–0.28). `IR
settings.experimental` is a dead flag nothing reads, and seven beta block manifests are never marked in the palette.

## 2. The Copilot, redesigned

### 2.1 Decision: adopt Deep Agents as the harness, AG-UI as the wire, keep our own panel

Three choices were on the table.

| Option | What it gives | What it costs | Verdict |
| --- | --- | --- | --- |
| Keep the hand-written loop and add features one by one | No new dependency | Every feature (streaming, summarised history, planning, sub-tasks, HITL, skills) is written and maintained by us; this is what Deep Agents already is | No |
| **Deep Agents** (`deepagents`, LangChain, MIT; already a locked dependency at 0.7.19, unused) | A LangGraph graph with planning (todos), a virtual filesystem for large context, subagents, on-demand skills (`SKILL.md`), summarisation middleware for long conversations, human-in-the-loop approvals, MCP tools, and `CopilotKitMiddleware` support | Our `agentcanvas.copilot` package is import-linter-forbidden from importing langgraph/deepagents; the Copilot moves behind the adapter boundary like the runner already is | **Yes** |
| **CopilotKit** (MIT core) for the UI and runtime | A finished chat UI, generative UI, shared state, threads | Threads, middleware and auth need its separate Node runtime (`agents__unsafe_dev_only` is the only direct path); its chat is a generic widget, and our proposal/ghost/undo model is the product's differentiator, not a chat window | No for the UI. **Yes for the protocol**: AG-UI is the event stream Deep Agents emit natively (`ag-ui-langgraph` is a FastAPI bridge), and our runner already speaks an AG-UI-shaped vocabulary (`RUN_STARTED`, `STEP_*`, `STATE_DELTA`) |

Reasoning: the product already standardises on LangGraph for its own runtime, so the Copilot should run on the same
machinery, with the same checkpoints, time travel, tracing and interrupts the runs get. Deep Agents is LangChain's
own harness on top of that, and it is already in the lock file. CopilotKit's value is a chat widget plus a hosted
runtime; our chat has to show a canvas preview, a per-change diff, and an undo step, which no widget does.

### 2.2 Architecture

- **`CopilotAgent`** in `adapters/langchain/copilot.py`: `create_deep_agent(model, tools, middleware=[...])` with
  - our 15 tools re-registered as plain LangChain tools over the existing `ToolContext` (unchanged semantics: working
    copy, atomic batches, secret refusal, 80-command cap);
  - a **summarisation middleware** so a long conversation keeps its meaning instead of dropping at 24 messages;
  - the **filesystem backend** holding the workflow JSON, the knowledge pack, the guide and run recordings, so the agent
    reads what it needs with `read_file`/`grep` instead of us pasting 30k characters into every turn and cutting it;
  - **todos** (the agent's own plan), shown live in the panel as "What I'm doing";
  - **subagents**: `debugger` (reads runs and traces, proposes a fix), `tester` (writes test cases from the brief),
    `docs` (searches the guide and LangChain docs); the main agent stays short and delegates;
  - **skills** (`SKILL.md` files under `copilot/skills/`): one per framework ("building for CrewAI"), one per
    domain pattern (RAG, approval flow, router), one for debugging; loaded on demand, which replaces the 40k-token
    pack sent on every turn;
  - **interrupts** for `run_test` beyond dry-run and for `write_workspace_file` (the person approves in the panel).
- **Checkpointer**: the same Postgres saver the runner uses, keyed by `(workflow, user, conversation_id)`. This gives
  multi-replica safety (the `_busy` set goes away), time travel ("rewind to before that proposal"), and a real history.
- **Wire**: `POST /workflows/{id}/copilot/threads/{tid}/run` streams AG-UI events through `ag-ui-langgraph`'s FastAPI
  bridge, mounted inside our app behind the existing sign-in and role checks (no Node runtime; we own auth already).
  Events: `TEXT_MESSAGE_CONTENT` per token, `TOOL_CALL_*`, `STATE_DELTA` for todos and the open proposal, `RUN_FINISHED`
  with the interrupt outcome. `POST .../stop` cancels the run through the checkpointer.
- **Proposals** stay our concept, carried in agent state (`proposal` key) and rendered from `STATE_DELTA`.

### 2.3 The panel, finished

| Feature | Today | Done when |
| --- | --- | --- |
| Token streaming | per model step | text appears as it is generated; "Thinking…" only before the first token |
| Stop | none | a Stop button cancels the run; the partial answer stays and is marked "stopped" |
| Markdown and code | plain text | headings, lists, code blocks with copy, inline code; rendered with `react-markdown` (no HTML pass-through, CSP-safe) |
| Citations | none | guide answers end with "From: Guide › Section" links that open the help page |
| History | one conversation, "New chat" deletes it | a thread list (title, date, model), switch, rename, archive; tool activity and errors persist in the thread |
| Plan view | none | the agent's todos as a live checklist above the answer |
| Per-change review | counts + canvas colours | a change list: each added/changed/removed block and edge, with its config diff, a checkbox per change (**partial accept**, FR-COP-01), removed blocks drawn ghosted-red on the canvas |
| Inspect a ghost | not selectable | clicking a ghost block opens the inspector read-only |
| Attributed commit | applied as the user | the version history line reads "Copilot (accepted by Arun)" (FR-COP-07) |
| Context | none | the selected block(s) are sent as context; "Ask the Copilot about this block" in the inspector; `⌘/Ctrl+K` opens the panel with focus |
| Errors | one red line | an error card with the cause and a Retry; 429 shows the cap and a link to Setup |
| Model | chip with tooltip | a model switcher in the panel for this thread (within policy) |
| Cost | "% used" | tokens and estimated cost per turn and per thread |

### 2.4 Capabilities to add

- **Lint rules** (FR-COP-04): a `copilot/rules/` set run by the `get_problems` tool: approval before an irreversible
  tool, a loop without a guard, a prompt without a role or output format, a model with no fallback, a secret in a
  config literal, a router with no default. Each rule has what/why/fix text, like diagnostics do.
- **Real test runs** behind an interrupt: the agent asks "Run `Order refund` for real with these inputs? (uses
  OpenAI)"; the person approves in the panel.
- **Workspace files**: `write_workspace_file` behind the same interrupt, replacing the tool-less code-help endpoint;
  CodeHelp becomes a thread with the file as context.
- **Build for another framework**: the framework skill plus `get_template` per framework closes
  `17-framework-coverage-review.md:43`.
- **Publish, export, secrets, knowledge**: read-only tools (`list_secrets` names only, `list_knowledge_bases`,
  `export_plan`) so the agent can explain and point; writes stay with the person.

### 2.5 Evaluation, so we know it works

- The eval harness (`copilot/evals.py`) gains the run tools and grows from 2 cases to a suite: 20 build cases (one per
  template family), 25 planted-bug debug cases (already written), 10 Ask cases with expected citations.
- **A real-model gate**: `make copilot-evals MODEL=anthropic:claude-sonnet-5-5` (and one OpenAI model) run nightly
  in CI with a key from secrets, scored, with a threshold that blocks a release; results in `docs/copilot-evals.md`.
- The browser tests stop asserting on the demo model's canned text; the dev server's demo model replays recorded
  real-model transcripts instead.

### 2.6 Effort and order

1. Harness swap behind the adapter boundary, same tools, same tests passing on the scripted model — 4 days.
2. AG-UI stream, checkpointer, stop, threads API — 3 days.
3. Panel: streaming, stop, Markdown, thread list, plan view, error cards — 4 days.
4. Change list with partial accept, ghost inspection, removed blocks, attributed commits — 4 days.
5. Skills, subagents, filesystem context, summarisation — 3 days.
6. Lint rules, interrupts for real runs and workspace files, context from selection, shortcuts — 4 days.
7. Eval suite and the real-model gate — 3 days.

About five weeks. Items 1–3 alone (two weeks) make it feel like a finished assistant.

## 3. Settings: one place, three scopes

### 3.1 Decision: a Settings area at `/settings`, with a left rail by scope

Replace the "Model" link and the `/setup` page with **Settings** in every top bar (and in the editor's bar), opening
a page with a left rail:

| Rail | Sections | Who may change |
| --- | --- | --- |
| **Project** | General (name, rename, delete, move to workspace); **Models** (default model, and defaults for embeddings, judges and simulated users, with temperature, max tokens, timeout and fallbacks); **Secrets** (moved out of the Workspace sidebar, with a delete confirmation and "where it is used"); **Copilot** (model, question model, privacy, notes, cap; the separate model gets address and key fields); **Sandbox** (a form for egress, inject, timeout, memory, writing `sandbox.json`); **Knowledge** (embedding model, chunking defaults including overlap); **Observability** (trace export, moved from "Model") | builder; Observability and Sandbox admin |
| **Organisation** | General (name, conversation/audit/memory retention); **Members**; **Policies** (with real edit); **Environments** (the list that publish, gates, policies and rate limits all assume: `dev`, `staging`, `prod` by default); **Audit log** | admin |
| **Server** | read-only view of the effective server settings (auth mode, frameworks offered, sandbox backend, rate limits, local MCP allowed, tracing), each with its env var name and a note "set by the operator"; the five that an admin would reasonably change at run time (`ALLOW_LOCAL_MCP`, rate limits, the three retentions) become organisation settings with the env var as the default | admin, read-only |
| **You** | theme, Simple/Full default, keyboard shortcuts list, "forget this browser's debug aids"; saved server-side per user so they follow the person | everyone |

The first-run "Set up your model" flow stays, as a wizard the Home banner opens, but it writes to the same Project ›
Models section. Members, Policies and Audit become sections of this page instead of separate top-bar links; the top
bar gets shorter (Home, Playground, Inbox, Knowledge, Memory, Workspace, Events, Settings).

### 3.2 One model resolution rule, written down and used everywhere

Today each of the eight places resolves differently. The rule becomes: **block config → workflow default → project
default for that purpose → none, with an explanation**. "Purpose" is one of chat, embedding, judge, simulated user,
copilot, copilot-ask. The Copilot's "Use the workflow's model" option is renamed to what it does, or made to do what
it says (the plan prefers the latter: the workflow's first chat model, then the project default). Judge and
simulated-user models stop hard-coding `anthropic:claude-haiku-4-5` and use the project default for that purpose. One
API, `GET /projects/{id}/models/resolve?purpose=…`, answers "which model, and why", and the UI shows that answer next
to every model chip.

### 3.3 Fixes that ride along

- Copilot PUT merges instead of replacing, so API-set fields survive a save from the form (found).
- Publish takes an environment from the Environments list instead of the hard-coded `"prod"` (found).
- Policy edit of match/require/severity in place (found).
- Share link `input_field`, Playground temperature, workflow `trace_redact`/`node_defaults`/channels get controls
  (API exists, no UI).
- Role visibility: a viewer who opens Settings sees read-only sections with a sentence saying so, never a disabled
  form with no explanation.

### 3.4 Effort

Settings shell, rail, routing, shared top bar (see §5) — 3 days. Project sections — 4 days. Organisation sections and
environments — 3 days. Model resolution rule and resolve endpoint — 3 days. User preferences — 1 day. About three
weeks.

## 4. Finishing the rest of the product

Ordered by how often a person meets the gap.

### 4.1 Navigation and the app shell (3 days)

- One `AppShell` with a top bar used by every page: brand, project switcher (create, rename, switch; the first
  step towards removing the 58 `PROJECT_ID` uses), the page links (Workflows, Playground, Inbox, Knowledge, Memory,
  Workspace, Events), Settings, the person's menu (email, role, theme, sign out, org switch). The editor keeps its own
  bar but gains the same person's menu and a link back.
- Role-aware: links a role cannot use are not shown, and a page a viewer opens says "You can view this; a builder
  can change it".
- Simple mode no longer hides Publish, Evaluate and Workspace; it hides advanced *blocks*, not the product.

### 4.2 Runs and threads (4 days)

- A **Runs** tab in the editor and a **Runs** page per project: threads with status, started, duration, cost;
  open any past run in the Timeline; delete a thread; filter by status and date. The API has all of it.
- The Inbox shows workflow name, block label and a one-line summary of what is being asked, not ids; filter by
  workflow; a success toast instead of the row vanishing.

### 4.3 Evaluation page (5 days)

- Forms instead of JSON: an evaluator editor per kind (exact, regex, LLM judge with a model picker and rubric),
  a dataset example editor (input, reference, split) with "add from runs", experiment overrides as a block/field
  picker. Edit and delete for evaluators, datasets, examples, experiments and queues, each with the app's confirm
  dialog.
- Annotation queues: the rubric as rows (name, scale, description); add runs from the Runs list, not by id; items
  show the run's input summary and date.
- Reachable from the editor's bar ("Evaluate"), not only from More.

### 4.4 Workspace (3 days)

- Folder tree, rename and move, an unsaved-changes prompt on switching files, delete errors shown, an `events/`
  starter, role gating.
- Secrets move to Settings (§3); the Workspace shows "uses secret X" next to a file that references it.

### 4.5 Playground, Knowledge, Memory, Events, Audit, Members (4 days)

- Playground streams tokens, keeps the conversation while the app is open, lets the person pick tools from the
  workspace.
- Knowledge: embedding model picker (project default from §3), rename and description, confirm on remove, an
  empty state, and a knowledge-base picker in the Search block (today free text).
- Memory: confirm on delete, errors shown, expand long values, friendly namespace names.
- Events: paging, results rendered as a table, role gating.
- Audit: action as a select built from the known actions, a date range, CSV export, headers on the table, the
  detail JSON on expand. Members: table headers, resend and cancel an invitation.

### 4.6 Dialogs and feedback (2 days)

- One `Dialog` and one `Confirm` component replace the 15 native `prompt()`/`confirm()` calls; one `toast()` for
  success (today success is silent on most pages).
- Workflow list: delete, rename, duplicate, search, sort; paging instead of `limit=200`.

### 4.7 Honest labels (1 day)

- Internal sprint ids out of the OpenAPI descriptions (17 places, visible on `/docs`); "stub" and raw API paths out
  of user-facing text; beta blocks marked in the palette from their manifest's `stability`; the dead
  `settings.experimental` flag either read (gate beta blocks) or removed.

### 4.8 Docs that match the code (2 days)

- `README.md` status table to 0.32.x; `HANDOVER.md` §1 and §9 pruned of the six fixed items; `18-identity-and-tenancy-plan.md`,
  ADR 0019 and `17-framework-coverage-review.md` marked done where done; the three "Unreleased" changelog sections
  given a version when you tag.

### 4.9 Deliberately not in this plan

The GA backlog from the spec (multiplayer editing, Data Studio, learning loops, generative UI, Temporal, per-user
connections, trace import, the forty-odd unbuilt node types from the catalog, "Full" native engines for Mastra,
Strands, LlamaIndex and the Microsoft framework) stays where it is. None of it makes the product feel unfinished
to a person using it today; all of it is large. The plan above is about finishing what exists.

## 5. Design and consistency

The product reads as assembled because each page was built in its sprint with its own header, its own error style
and its own date format. The fix is a small design system, not a redesign:

- **Tokens** already exist in `styles.css` (the theme switcher proves it). Add spacing, radius, type scale and the
  semantic colours (success, warning, danger, info) as named tokens, and remove the per-page overrides.
- **Components**: `AppShell`, `PageHeader` (title, description, actions), `Table` (headers, empty state, row
  actions), `Form` field wrapper (label, hint, error), `Dialog`/`Confirm`, `Toast`, `EmptyState` (icon, sentence,
  primary action), `Badge` (status, role, beta). Each page is then rebuilt from these, which is where most of the
  §4 day counts go.
- **One voice**: errors as "Could not X: reason" with a Retry (the `QueryStatus` pattern, everywhere); dates
  relative ("3 min ago", with the full date on hover); ids never shown unless the person asked for them.
- **Phone**: the app is a desktop tool; make the pages that a person would open on a phone (Inbox, a share page,
  Runs) work at 390 px and say so for the rest, instead of "usable but not designed".
- **Visual direction**: keep the current calm palette; add the one thing that signals a finished product, a
  consistent icon set (Lucide, MIT, tree-shaken) instead of the mix of emoji and text glyphs ("✦ Copilot", "⌘K").

Effort: tokens and components 4 days; the rebuilds are counted in §4.

## 6. Hardening backlog (from `HARDENING.md` "Open", with a verdict each)

| Item | Verdict |
| --- | --- |
| Project selection in `queries.ts` (58 uses) | **Do, as part of §4.1**: the project switcher needs a store anyway |
| ~16 hand-written response types | Accepted (one-field reads); the Settings work replaces Policies/Setup/CodeHelp shapes with generated ones |
| React Compiler lint rules off (12 rules) | Do after §4 rebuilds, one rule at a time; the rebuilt pages will satisfy most |
| RLS migration locks 35 tables in one transaction | **Do**: split into one transaction per table with the advisory lock held across; 1 day |
| Duplicated engine/worker/packager code (~1,200 lines); `Resources` is LangChain's object | Do when the next framework lands, not before; the duplication is contained and tested |
| No framework-environment cache in CI | Do: `uv cache` and `actions/cache` keyed on the pins; half a day |
| Sandbox egress only enforced with LangSmith | **Design call for you**: enforce with a network namespace in the Docker backend (2 days) or document it as LangSmith-only and hide the field for other backends |
| Cross-org sweep: 7 routes unreached | Do with §4.2 (run traces) and §2 (agent code); the seed grows |
| Screen-reader pass | A person, one afternoon, after §5 |
| Matura `MaturaWebhookSink` placeholder, no per-tenant schema | Waits on Matura's API; unchanged |
| MCP: no `Origin` check, no `MCP-Protocol-Version` check; A2A without streaming | **Do** the two checks (half a day, security); A2A streaming with §2's AG-UI work |
| pgvector unused (vectors as bytes) | Do when a knowledge base exceeds ~50k chunks; add a test that measures search time |
| Python 3.15 (Dependabot #69) | Blocked on `google-re2` wheels; re-check monthly |

## 7. Order of work

Four tracks, roughly ten weeks for one person, less with two. Each track ships on its own branch and can be
merged alone.

| Weeks | Track | Why first |
| --- | --- | --- |
| 1–2 | **Shell and design system** (§4.1, §5 tokens and components, §4.6 dialogs and toasts) | Everything after it is built from these; the product looks finished the day the shared bar and dialogs land |
| 2–4 | **Settings** (§3) | The owner's first complaint; also removes the eight-way model confusion the Copilot work depends on |
| 4–8 | **Copilot** (§2, items 1–7) | The largest single feature; items 1–3 can ship at week 6 as a visible step |
| 6–10 | **Runs, Evaluation, the other pages, labels, docs** (§4.2–4.5, 4.7, 4.8) | Finishes the long tail; parallelisable with the Copilot track |
| throughout | **Hardening** (§6 "Do" rows) | Small, each a day or less; slot between tracks |

What I would do first, on my own, under the standing instruction: the shell and design system, then Settings,
because they need no decision from you. The Copilot harness swap (Deep Agents, AG-UI) and the sandbox egress question
are the two places where I want your yes before starting, since both change architecture.
